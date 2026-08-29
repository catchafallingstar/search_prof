from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import queue
import signal
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy
from ingestion.verify_faculty import verify_faculty_candidates
from ingestion.websearch import search_provider_runtime_state
from radar_store import (
    claim_next_radar_job,
    complete_radar_job,
    enqueue_due_maintenance,
    enqueue_radar_job,
    fail_radar_job,
    fetch_radar_topic_by_id,
    fetch_topic_candidate_ids,
    fetch_topic_enrichment_ids,
    mark_professor_enrichment_checked,
    refresh_topic_coverage,
    reschedule_radar_job,
    save_topic_candidates,
    stop_worker_heartbeat,
    update_topic_after_discovery,
    update_radar_job_progress,
    update_worker_heartbeat,
)
from settings import setting, setting_int


SEARCH_DEPENDENT_JOB_TYPES = (
    "VERIFY_FACULTY",
    "REFRESH_FACULTY",
    "CHECK_HIRING",
    "ENRICH_PROFESSORS",
)


class RetryableJobError(RuntimeError):
    """External dependency failure with a caller-supplied retry delay."""

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(30, int(retry_after_seconds))


def _publish_job_progress(
    job: dict[str, Any],
    stage: str,
    professor_ids: list[int] | None = None,
    detail: str = "",
) -> None:
    if job.get("id") is None:
        return
    update_radar_job_progress(
        int(job["id"]), stage, professor_ids=professor_ids, detail=detail
    )


def log_event(event: str, **values: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **values,
    }
    print(json.dumps(payload, default=str), flush=True)


def _topic_for_job(job: dict[str, Any]) -> dict[str, Any]:
    radar_topic_id = job.get("radar_topic_id")
    if radar_topic_id is None:
        raise RuntimeError(f"{job['job_type']} requires a radar topic.")
    topic = fetch_radar_topic_by_id(int(radar_topic_id))
    if not topic:
        raise RuntimeError("The radar topic no longer exists.")
    return topic


def _discover(job: dict[str, Any]) -> dict[str, Any]:
    if not setting("OPENALEX_API_KEY").strip():
        raise RuntimeError("OPENALEX_API_KEY is required by the indexing worker.")
    topic = _topic_for_job(job)
    _publish_job_progress(
        job,
        "DISCOVER_CANDIDATES",
        detail="Searching OpenAlex for relevant papers and extracting candidate authors.",
    )
    taxonomy = normalize_taxonomy(str(topic["requested_query"]))
    normalized_topic = str(taxonomy.get("topic_name") or topic["normalized_query"])
    discovery = fetch_professors_by_keywords(taxonomy, target_professors=100)
    prospects = list(discovery.get("prospects") or [])
    save_topic_candidates(int(topic["id"]), prospects)
    update_topic_after_discovery(
        int(topic["id"]),
        normalized_topic,
        int(discovery.get("candidates_ranked") or len(prospects)),
        int(discovery.get("papers") or 0),
    )
    enqueue_radar_job(
        "VERIFY_FACULTY",
        radar_topic_id=int(topic["id"]),
        requested_by=job.get("requested_by"),
        priority=85,
        max_attempts=20,
    )
    return {
        "candidates_ranked": int(discovery.get("candidates_ranked") or len(prospects)),
        "papers_found": int(discovery.get("papers") or 0),
    }


def _verify(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    topic = _topic_for_job(job)
    # Identity checks can involve several slow official pages. Keep each job
    # small enough to finish comfortably before the worker's hard deadline.
    # Each completed candidate is committed independently by the verifier, so
    # the next rescheduled job continues with the first unfinished identity.
    configured_batch_size = setting_int("INDEX_VERIFY_BATCH_SIZE", 3, 1, 6)
    provider_state = search_provider_runtime_state()
    available_providers = list(provider_state.get("available") or [])
    if not available_providers:
        raise RetryableJobError(
            "Faculty verification is waiting for a configured search provider to recover.",
            int(provider_state.get("retry_after_seconds") or 300),
        )
    # When only one provider is healthy, process one identity. This avoids
    # fanning one upstream problem out across every candidate in a batch.
    batch_size = 1 if len(available_providers) == 1 else configured_batch_size
    professor_ids = fetch_topic_candidate_ids(int(topic["id"]), batch_size)
    if professor_ids:
        _publish_job_progress(
            job,
            "VERIFY_FACULTY",
            professor_ids=professor_ids,
            detail=(
                "Checking the target university first, then using bounded paper "
                "affiliation evidence for unresolved identities. "
                f"Processing {len(professor_ids)} candidate(s) with "
                f"{len(available_providers)} available search provider(s)."
            ),
        )
        verification = verify_faculty_candidates(professor_ids)
    else:
        verification = {
            "verified_ids": [], "checked": 0, "evaluated": 0, "verified": 0
        }
    coverage = refresh_topic_coverage(int(topic["id"]))
    more_due = bool(fetch_topic_candidate_ids(int(topic["id"]), 1))
    needs_more = (
        int(coverage.get("verified_count") or 0)
        < int(coverage.get("desired_results") or 100)
        and more_due
    )
    # Do not start opportunity checks while the identity set is still moving.
    # Once the requested number of professors is verified (or candidates are
    # exhausted), grants and hiring pages can safely run in parallel.
    if not needs_more and int(coverage.get("verified_count") or 0) > 0:
        enqueue_radar_job(
            "ENRICH_PROFESSORS",
            radar_topic_id=int(topic["id"]),
            requested_by=job.get("requested_by"),
            priority=45,
            max_attempts=20,
        )
    return {
        "evaluated": int(verification.get("evaluated") or 0),
        "newly_verified": int(verification.get("verified") or 0),
        "verified_count": int(coverage.get("verified_count") or 0),
        "candidates_seen": int(coverage.get("candidates_seen") or 0),
    }, needs_more


def _check_grants(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    topic = _topic_for_job(job)
    limit = setting_int("INDEX_ENRICH_BATCH_SIZE", 10, 1, 25)
    professor_ids = fetch_topic_enrichment_ids(int(topic["id"]), "grants", limit)
    if not professor_ids:
        return {"professors_checked": 0, "grants_added": 0}, False
    _publish_job_progress(
        job,
        "CHECK_GRANTS",
        professor_ids=professor_ids,
        detail="Checking relevant public grant records for this professor batch.",
    )
    taxonomy = normalize_taxonomy(str(topic["requested_query"]))
    result = check_and_save_grants(taxonomy, professor_ids=professor_ids)
    mark_professor_enrichment_checked(professor_ids, "grants")
    more = bool(fetch_topic_enrichment_ids(int(topic["id"]), "grants", 1))
    return {
        "professors_checked": len(professor_ids),
        "grants_added": int(result.get("grants_added") or 0),
    }, more


def _check_hiring(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    topic = _topic_for_job(job)
    if job.get("professor_id") is not None:
        professor_ids = [int(job["professor_id"])]
        _publish_job_progress(
            job,
            "CHECK_HIRING",
            professor_ids=professor_ids,
            detail="Checking the professor or lab page for a public recruiting statement.",
        )
        result = scan_hiring_signals(
            domain_name=str(topic.get("normalized_topic") or topic["normalized_query"]),
            professor_ids=professor_ids,
            radar_run_id=None,
        )
        return {
            "professors_checked": int(result.get("professors_checked") or 0),
            "signals_added": int(result.get("signals_added") or 0),
            "timed_out": bool(result.get("timed_out")),
        }, False
    limit = setting_int("INDEX_ENRICH_BATCH_SIZE", 10, 1, 25)
    professor_ids = fetch_topic_enrichment_ids(int(topic["id"]), "hiring", limit)
    if not professor_ids:
        return {"professors_checked": 0, "signals_added": 0}, False
    _publish_job_progress(
        job,
        "CHECK_HIRING",
        professor_ids=professor_ids,
        detail="Checking professor and lab pages for public recruiting statements.",
    )
    result = scan_hiring_signals(
        domain_name=str(topic.get("normalized_topic") or topic["normalized_query"]),
        professor_ids=professor_ids,
        radar_run_id=None,
    )
    checked_ids = [int(value) for value in result.get("checked_professor_ids") or []]
    mark_professor_enrichment_checked(checked_ids, "hiring")
    more = bool(fetch_topic_enrichment_ids(int(topic["id"]), "hiring", 1))
    return {
        "professors_checked": int(result.get("professors_checked") or 0),
        "signals_added": int(result.get("signals_added") or 0),
        "timed_out": bool(result.get("timed_out")),
    }, more


def _enrich_professors(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Check grants and public hiring pages concurrently after verification."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        grants_future = executor.submit(_check_grants, job)
        hiring_future = executor.submit(_check_hiring, job)
        grants_result, grants_more = grants_future.result()
        hiring_result, hiring_more = hiring_future.result()
    return {
        "grants": grants_result,
        "hiring": hiring_result,
    }, bool(grants_more or hiring_more)


def process_job(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    job_type = str(job["job_type"])
    if job_type in {"DISCOVER_CANDIDATES", "REINDEX_RESEARCH"}:
        return _discover(job), False
    if job_type == "VERIFY_FACULTY":
        return _verify(job)
    if job_type == "REFRESH_FACULTY":
        professor_id = job.get("professor_id")
        if professor_id is None:
            raise RuntimeError("REFRESH_FACULTY requires a professor.")
        result = verify_faculty_candidates([int(professor_id)])
        return result, False
    if job_type == "CHECK_GRANTS":
        return _check_grants(job)
    if job_type == "CHECK_HIRING":
        return _check_hiring(job)
    if job_type == "ENRICH_PROFESSORS":
        return _enrich_professors(job)
    raise RuntimeError(f"Unknown radar job type: {job_type}")


def _job_process_entry(
    job: dict[str, Any], output: multiprocessing.queues.Queue
) -> None:
    """Run external-source work outside the durable worker process.

    Search libraries and remote servers do not always honor socket timeouts.
    Process isolation lets the parent enforce a real whole-job deadline rather
    than leaving a database row in ``running`` forever.
    """
    try:
        result, needs_more = process_job(job)
        output.put({"ok": True, "result": result, "needs_more": needs_more})
    except BaseException as error:
        output.put(
            {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "retry_after_seconds": int(
                    getattr(error, "retry_after_seconds", 0) or 0
                ),
            }
        )


def run_job_with_deadline(
    job: dict[str, Any],
    worker_id: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], bool]:
    """Run one job with a hard deadline while maintaining worker health."""
    context = multiprocessing.get_context("fork")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_job_process_entry,
        args=(dict(job), output),
        daemon=False,
    )
    process.start()
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    try:
        while process.is_alive() and time.monotonic() < deadline:
            process.join(timeout=2)
            update_worker_heartbeat(worker_id, int(job["id"]))
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            raise TimeoutError(
                f"Job exceeded its {int(timeout_seconds)}-second deadline."
            )
        try:
            message = output.get(timeout=2)
        except queue.Empty as error:
            raise RuntimeError(
                f"Job process exited with code {process.exitcode} without a result."
            ) from error
        if not message.get("ok"):
            error_message = str(message.get("error") or "Background job failed.")
            retry_after = int(message.get("retry_after_seconds") or 0)
            if retry_after:
                raise RetryableJobError(error_message, retry_after)
            raise RuntimeError(error_message)
        return dict(message.get("result") or {}), bool(message.get("needs_more"))
    finally:
        output.close()
        output.join_thread()


def run_worker(
    *,
    once: bool = False,
    max_jobs: int | None = None,
    poll_seconds: float = 3.0,
) -> int:
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    jobs_processed = 0
    job_timeout = setting_int("INDEX_JOB_TIMEOUT_SECONDS", 300, 30, 1800)
    search_jobs_paused_until = 0.0
    update_worker_heartbeat(worker_id)
    log_event("worker_started", worker_id=worker_id)
    try:
        while not stopping:
            if max_jobs is not None and jobs_processed >= max_jobs:
                break
            update_worker_heartbeat(worker_id)
            provider_state = search_provider_runtime_state()
            search_jobs_paused = (
                time.monotonic() < search_jobs_paused_until
                or not provider_state.get("available")
            )
            excluded_job_types = (
                list(SEARCH_DEPENDENT_JOB_TYPES) if search_jobs_paused else []
            )
            job = claim_next_radar_job(
                worker_id, excluded_job_types=excluded_job_types
            )
            if not job:
                enqueue_due_maintenance()
                if once:
                    break
                time.sleep(max(0.25, poll_seconds))
                continue
            update_worker_heartbeat(worker_id, int(job["id"]))
            log_event(
                "job_started",
                worker_id=worker_id,
                job_id=int(job["id"]),
                job_type=job["job_type"],
                attempt=int(job["attempts"]),
            )
            try:
                result, needs_more = run_job_with_deadline(
                    job, worker_id, job_timeout
                )
                if needs_more:
                    reschedule_radar_job(int(job["id"]), 2, result)
                    status = "rescheduled"
                else:
                    complete_radar_job(int(job["id"]), result)
                    status = "completed"
                log_event(
                    "job_finished",
                    worker_id=worker_id,
                    job_id=int(job["id"]),
                    status=status,
                    result=result,
                )
            except RetryableJobError as error:
                fail_radar_job(job, error)
                if str(job.get("job_type")) in SEARCH_DEPENDENT_JOB_TYPES:
                    search_jobs_paused_until = max(
                        search_jobs_paused_until,
                        time.monotonic() + error.retry_after_seconds,
                    )
                log_event(
                    "job_deferred",
                    worker_id=worker_id,
                    job_id=int(job["id"]),
                    reason=str(error),
                    retry_after_seconds=error.retry_after_seconds,
                )
            except Exception as error:
                fail_radar_job(job, error)
                log_event(
                    "job_failed",
                    worker_id=worker_id,
                    job_id=int(job["id"]),
                    error=str(error),
                )
            jobs_processed += 1
            update_worker_heartbeat(worker_id)
            if once:
                break
    finally:
        stop_worker_heartbeat(worker_id)
        log_event(
            "worker_stopped", worker_id=worker_id, jobs_processed=jobs_processed
        )
    return jobs_processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ScholarRadar index worker.")
    parser.add_argument("--once", action="store_true", help="Process at most one ready job.")
    parser.add_argument("--max-jobs", type=int, help="Stop after this many claimed jobs.")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()
    run_worker(
        once=args.once,
        max_jobs=max(1, args.max_jobs) if args.max_jobs else None,
        poll_seconds=max(0.25, args.poll_seconds),
    )


if __name__ == "__main__":
    main()
