from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy
from ingestion.verify_faculty import verify_faculty_candidates
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
    update_worker_heartbeat,
)
from settings import setting, setting_int


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
    batch_size = setting_int("INDEX_VERIFY_BATCH_SIZE", 30, 3, 30)
    professor_ids = fetch_topic_candidate_ids(int(topic["id"]), batch_size)
    if professor_ids:
        verification = verify_faculty_candidates(professor_ids)
    else:
        verification = {
            "verified_ids": [], "checked": 0, "evaluated": 0, "verified": 0
        }
    coverage = refresh_topic_coverage(int(topic["id"]))
    if int(coverage.get("verified_count") or 0) > 0:
        enqueue_radar_job(
            "CHECK_GRANTS",
            radar_topic_id=int(topic["id"]),
            priority=45,
            max_attempts=20,
        )
        enqueue_radar_job(
            "CHECK_HIRING",
            radar_topic_id=int(topic["id"]),
            priority=40,
            max_attempts=20,
        )
    more_due = bool(fetch_topic_candidate_ids(int(topic["id"]), 1))
    needs_more = (
        int(coverage.get("verified_count") or 0)
        < int(coverage.get("desired_results") or 100)
        and more_due
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
    limit = setting_int("INDEX_ENRICH_BATCH_SIZE", 10, 1, 25)
    professor_ids = fetch_topic_enrichment_ids(int(topic["id"]), "hiring", limit)
    if not professor_ids:
        return {"professors_checked": 0, "signals_added": 0}, False
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
    raise RuntimeError(f"Unknown radar job type: {job_type}")


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
    update_worker_heartbeat(worker_id)
    log_event("worker_started", worker_id=worker_id)
    try:
        while not stopping:
            if max_jobs is not None and jobs_processed >= max_jobs:
                break
            update_worker_heartbeat(worker_id)
            job = claim_next_radar_job(worker_id)
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
                result, needs_more = process_job(job)
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
