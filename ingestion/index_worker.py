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
from datetime import datetime, timezone
from typing import Any

from ingestion.check_grants import check_and_save_grants
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.topic_language import normalize_taxonomy
from ingestion.websearch import search_provider_runtime_state, identity_search_can_run
from ingestion.roster_topic_index import index_rostered_topic
from ingestion.research_classification import enrich_classify_paper
from ingestion.faculty_roster import crawl_directory
from ingestion.publication_discovery import (
    discover_faculty_publications,
    review_queued_scholar_candidates,
)
from ingestion.university_directory_discovery import discover_faculty_directories
from ingestion.program_gpa import check_program_gpa_for_professor
from radar_store import (
    claim_next_radar_job,
    complete_radar_job,
    enqueue_due_maintenance,
    enqueue_radar_job,
    fail_radar_job,
    fetch_radar_topic_by_id,
    fetch_topic_enrichment_ids,
    mark_professor_enrichment_checked,
    refresh_topic_coverage,
    reschedule_radar_job,
    save_topic_grant_checks,
    stop_worker_heartbeat,
    update_radar_job_progress,
    update_worker_heartbeat,
)
from settings import setting, setting_int
from ingestion.event_log import write_event


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
    **metadata: Any,
) -> None:
    if job.get("id") is None:
        return
    update_radar_job_progress(
        int(job["id"]), stage, professor_ids=professor_ids, detail=detail,
        **metadata,
    )


def log_event(event: str, **values: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **values,
    }
    write_event(payload)


def _job_outcome(job_type: str, result: dict[str, Any]) -> str:
    """Describe the data result separately from successful job execution."""
    if job_type == "DISCOVER_FACULTY_DIRECTORIES":
        return "APPROVED" if result.get("directories") else "REVIEW_REQUIRED"
    if job_type == "CRAWL_FACULTY_DIRECTORY":
        if int(result.get("profiles_pending") or 0) > 0:
            return "REVIEW_REQUIRED"
        return "APPROVED" if int(result.get("profiles_verified") or 0) > 0 else "NO_CHANGE"
    if job_type == "MATCH_FACULTY_PUBLICATIONS":
        status = str(result.get("status") or "")
        return {
            "OFFICIAL_PUBLICATIONS_FOUND": "APPROVED",
            "SCHOLAR_VERIFIED": "APPROVED",
            "NO_PUBLICATIONS_FOUND": "NO_PUBLICATIONS_FOUND",
            "SOURCE_UNAVAILABLE": "SOURCE_UNAVAILABLE",
            "REVIEW_REQUIRED": "REVIEW_REQUIRED",
            "SCHOLAR_REVIEW_QUEUED": "NO_CHANGE",
            "NOT_APPLICABLE": "NO_CHANGE",
        }.get(status, "REVIEW_REQUIRED")
    if job_type == "QWEN_REVIEW_PUBLICATION":
        status = str(result.get("status") or "")
        return {
            "SCHOLAR_VERIFIED": "APPROVED",
            "NO_PUBLICATIONS_FOUND": "NO_PUBLICATIONS_FOUND",
            "SOURCE_UNAVAILABLE": "SOURCE_UNAVAILABLE",
            "REVIEW_REQUIRED": "REVIEW_REQUIRED",
        }.get(status, "REVIEW_REQUIRED")
    if job_type == "ENRICH_CLASSIFY_PAPER":
        return "APPROVED" if int(result.get("categories_accepted") or 0) else "NO_CHANGE"
    if job_type == "CHECK_HIRING" and bool(result.get("timed_out")):
        return "SOURCE_UNAVAILABLE"
    if job_type in {"CHECK_HIRING", "CHECK_GRANTS", "CHECK_PROGRAM_GPA"}:
        return "NO_CHANGE" if not any(
            int(result.get(key) or 0)
            for key in ("signals_added", "grants_added", "requirements_found")
        ) else "APPROVED"
    return "SUCCEEDED"


def _topic_for_job(job: dict[str, Any]) -> dict[str, Any]:
    radar_topic_id = job.get("radar_topic_id")
    if radar_topic_id is None:
        raise RuntimeError(f"{job['job_type']} requires a radar topic.")
    topic = fetch_radar_topic_by_id(int(radar_topic_id))
    if not topic:
        raise RuntimeError("The radar topic no longer exists.")
    return topic


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
    save_topic_grant_checks(int(topic["id"]), list(result.get("source_checks") or []))
    mark_professor_enrichment_checked(professor_ids, "grants")
    more = bool(fetch_topic_enrichment_ids(int(topic["id"]), "grants", 1))
    return {
        "professors_checked": len(professor_ids),
        "grants_added": int(result.get("grants_added") or 0),
    }, more


def _check_hiring(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if job.get("professor_id") is not None:
        professor_ids = [int(job["professor_id"])]
        _publish_job_progress(
            job,
            "CHECK_HIRING",
            professor_ids=professor_ids,
            detail="Checking the professor or lab page for a public recruiting statement.",
        )
        result = scan_hiring_signals(
            domain_name=None,
            professor_ids=professor_ids,
            radar_run_id=None,
        )
        return {
            "professors_checked": int(result.get("professors_checked") or 0),
            "signals_added": int(result.get("signals_added") or 0),
            "timed_out": bool(result.get("timed_out")),
        }, False
    topic = _topic_for_job(job)
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


def process_job(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    job_type = str(job["job_type"])
    if job_type == "DISCOVER_FACULTY_DIRECTORIES":
        institution_id = job.get("institution_id")
        if institution_id is None:
            raise RuntimeError("DISCOVER_FACULTY_DIRECTORIES requires an institution.")
        _publish_job_progress(
            job, job_type,
            detail="Inspecting the official university domain for faculty directories.",
        )
        result = discover_faculty_directories(
            int(institution_id),
            progress_callback=lambda url, current, total: _publish_job_progress(
                job, job_type,
                detail=f"Validating directory candidate {current} of {total}.",
                source_url=url,
            ),
        )
        for directory in result.get("directories") or []:
            enqueue_radar_job(
                "CRAWL_FACULTY_DIRECTORY",
                faculty_directory_id=int(directory["directory_id"]),
                priority=90,
                max_attempts=8,
            )
        return result, False
    if job_type == "CRAWL_FACULTY_DIRECTORY":
        directory_id = job.get("faculty_directory_id")
        if directory_id is None:
            raise RuntimeError("CRAWL_FACULTY_DIRECTORY requires a directory.")
        _publish_job_progress(job, job_type, detail="Extracting current faculty from an approved official roster.")
        result = crawl_directory(
            int(directory_id),
            progress_callback=lambda member, current, total: _publish_job_progress(
                job, job_type,
                detail=f"Saving roster member {current} of {total}.",
                member_name=member.name,
                source_url=member.profile_url,
            ),
        )
        return result, False
    if job_type == "MATCH_FACULTY_PUBLICATIONS":
        professor_id = job.get("professor_id")
        if professor_id is None:
            raise RuntimeError("MATCH_FACULTY_PUBLICATIONS requires a professor.")
        _publish_job_progress(
            job, job_type, professor_ids=[int(professor_id)],
            detail="Checking official profile and linked research pages; Scholar is fallback only.",
        )
        result = discover_faculty_publications(
            int(professor_id),
            progress_callback=lambda title, current, total: _publish_job_progress(
                job, job_type, professor_ids=[int(professor_id)],
                detail=f"Importing paper {current} of {total}.", paper_title=title,
            ),
            activity_callback=lambda stage, metadata: _publish_job_progress(
                job, job_type, professor_ids=[int(professor_id)],
                detail=("Reviewing Google Scholar identity evidence with Qwen."
                        if stage == "QWEN_SCHOLAR_REVIEW"
                        else "Checking the official faculty profile for publication evidence."),
                **metadata,
            ),
        )
        if result.get("status") == "SCHOLAR_REVIEW_QUEUED":
            enqueue_radar_job(
                "QWEN_REVIEW_PUBLICATION",
                professor_id=int(professor_id),
                priority=70,
                max_attempts=1,
            )
        return result, False
    if job_type == "QWEN_REVIEW_PUBLICATION":
        professor_id = job.get("professor_id")
        if professor_id is None:
            raise RuntimeError("QWEN_REVIEW_PUBLICATION requires a professor.")
        _publish_job_progress(
            job, job_type, professor_ids=[int(professor_id)],
            detail="Qwen is reviewing one saved Scholar identity candidate.",
        )
        result = review_queued_scholar_candidates(
            int(professor_id),
            progress_callback=lambda title, current, total: _publish_job_progress(
                job, job_type, professor_ids=[int(professor_id)],
                detail=f"Publication identity verified — importing paper {current} of {total}.",
                paper_title=title,
            ),
            activity_callback=lambda stage, metadata: _publish_job_progress(
                job, stage, professor_ids=[int(professor_id)],
                detail="Qwen is checking the candidate; this request may take up to five minutes.",
                **metadata,
            ),
        )
        return result, False
    if job_type == "CHECK_PROGRAM_GPA":
        professor_id = job.get("professor_id")
        if professor_id is None:
            raise RuntimeError("CHECK_PROGRAM_GPA requires a representative professor.")
        return check_program_gpa_for_professor(int(professor_id)), False
    if job_type == "ENRICH_CLASSIFY_PAPER":
        paper_id = job.get("paper_id")
        if paper_id is None:
            raise RuntimeError("ENRICH_CLASSIFY_PAPER requires a paper.")
        _publish_job_progress(
            job, job_type,
            detail="Resolving the paper abstract and assigning evidence-backed research categories.",
            paper_id=int(paper_id),
        )
        return enrich_classify_paper(int(paper_id)), False
    if job_type == "INDEX_ROSTER_TOPIC":
        topic = _topic_for_job(job)
        _publish_job_progress(
            job, "INDEX_ROSTER_TOPIC",
            detail="Matching confirmed roster faculty using direct paper-title and abstract evidence.",
        )
        result = index_rostered_topic(int(topic["id"]))
        refresh_topic_coverage(int(topic["id"]))
        enqueue_radar_job(
            "CHECK_GRANTS", radar_topic_id=int(topic["id"]),
            priority=35, max_attempts=5,
        )
        return result, False
    if job_type == "CHECK_GRANTS":
        return _check_grants(job)
    if job_type == "CHECK_HIRING":
        return _check_hiring(job)
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


def run_job_isolated(
    job: dict[str, Any],
    worker_id: str,
) -> tuple[dict[str, Any], bool]:
    """Run one job in isolation and heartbeat until its bounded requests finish."""
    context = multiprocessing.get_context("fork")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_job_process_entry,
        args=(dict(job), output),
        daemon=False,
    )
    process.start()
    try:
        while process.is_alive():
            process.join(timeout=2)
            update_worker_heartbeat(worker_id, int(job["id"]))
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
    announced_wait = False
    update_worker_heartbeat(worker_id)
    log_event("worker_started", worker_id=worker_id,
              search_providers=search_provider_runtime_state()['providers'],
              ddgs_backend=setting('DDGS_BACKEND').strip() or 'auto')
    try:
        while not stopping:
            if max_jobs is not None and jobs_processed >= max_jobs:
                break
            update_worker_heartbeat(worker_id)
            # Search availability is checked only at the actual web-search step.
            # Direct pages, cached metadata and grants may still be usable.
            capacity = search_provider_runtime_state()
            search_ready = identity_search_can_run(capacity)
            if not search_ready and not announced_wait:
                log_event("search_waiting", retry_after_seconds=capacity["retry_after_seconds"],
                          detail="Directory and Scholar recovery searches are parked; direct official pages and grants can continue.")
                announced_wait = True
            elif search_ready:
                announced_wait = False
            job = claim_next_radar_job(worker_id, search_ready=search_ready)
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
                result, needs_more = run_job_isolated(job, worker_id)
                if needs_more:
                    reschedule_radar_job(int(job["id"]), max(2, int(result.get("retry_after_seconds") or 2)), result)
                    status = "rescheduled"
                else:
                    complete_radar_job(
                        int(job["id"]), result,
                        outcome_status=_job_outcome(str(job["job_type"]), result),
                    )
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
