from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any

from db import _require_active_admin, get_db_connection
from settings import setting, setting_int


FACULTY_VERIFICATION_VERSION = 8
MIN_PUBLIC_FACULTY_VERIFICATION_VERSION = 8
RADAR_DISCOVERY_VERSION = 3


def _target_country_code() -> str:
    return setting("TARGET_COUNTRY_CODE").strip().upper() or "US"

# Keep aliases explicit. Generic singularization would corrupt valid field names
# such as physics, economics, and systems science.
TOPIC_QUERY_ALIASES = {
    "asian study": "asian studies",
}


def normalize_topic_query(query: str) -> str:
    clean = " ".join(re.findall(r"[a-z0-9]+", query.casefold()))
    clean = TOPIC_QUERY_ALIASES.get(clean, clean)
    if not 3 <= len(clean) <= 120:
        raise ValueError("Research area must contain between 3 and 120 characters.")
    return clean


def topic_key(query: str) -> str:
    return hashlib.sha256(normalize_topic_query(query).encode("utf-8")).hexdigest()


def ensure_radar_topic(
    query: str,
    desired_results: int = 100,
    *,
    enforce_hourly_limit: bool = True,
) -> dict[str, Any]:
    clean_display = " ".join(query.split())
    normalized = normalize_topic_query(query)
    desired_results = max(1, min(100, int(desired_results)))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM radar_topics WHERE topic_key = %s",
                (topic_key(query),),
            )
            existing = cursor.fetchone()
            if existing is None and enforce_hourly_limit:
                hourly_limit = setting_int("PUBLIC_TOPIC_HOURLY_LIMIT", 20, 1, 500)
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM radar_topics
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                    """
                )
                if int(cursor.fetchone()["count"]) >= hourly_limit:
                    raise RuntimeError(
                        "ScholarRadar is indexing many new research areas. Please try again later."
                    )
            cursor.execute(
                """
                INSERT INTO radar_topics (
                    topic_key, requested_query, normalized_query, desired_results,
                    search_count
                ) VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT (topic_key) DO UPDATE
                SET requested_query = EXCLUDED.requested_query,
                    desired_results = GREATEST(
                        radar_topics.desired_results, EXCLUDED.desired_results
                    ),
                    search_count = radar_topics.search_count + 1,
                    last_requested_at = NOW(),
                    updated_at = NOW()
                RETURNING *
                """,
                (topic_key(query), clean_display, normalized, desired_results),
            )
            return cursor.fetchone()


def enqueue_radar_job(
    job_type: str,
    *,
    radar_topic_id: int | None = None,
    professor_id: int | None = None,
    requested_by: int | None = None,
    priority: int = 50,
    max_attempts: int = 5,
) -> dict[str, Any]:
    # A professor's hiring-page refresh is shared across all topics and users.
    dedupe_topic = "-" if job_type == "CHECK_HIRING" and professor_id else (radar_topic_id or "-")
    dedupe = f"{job_type}:{dedupe_topic}:{professor_id or '-'}"
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (dedupe,))
            cursor.execute(
                """
                SELECT * FROM radar_jobs
                WHERE dedupe_key = %s AND status IN ('queued', 'running')
                ORDER BY id DESC LIMIT 1
                """,
                (dedupe,),
            )
            existing = cursor.fetchone()
            if existing:
                # Reusing a queued job must not leave an urgent user/admin
                # request stuck behind the low-priority maintenance queue.
                cursor.execute(
                    """
                    UPDATE radar_jobs
                    SET priority = CASE
                            WHEN status = 'queued' THEN GREATEST(priority, %s)
                            ELSE priority
                        END,
                        max_attempts = GREATEST(max_attempts, %s),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        max(0, min(100, int(priority))),
                        max(1, min(20, int(max_attempts))),
                        int(existing["id"]),
                    ),
                )
                return {**cursor.fetchone(), "reused": True}
            cursor.execute(
                """
                INSERT INTO radar_jobs (
                    radar_topic_id, professor_id, requested_by, job_type,
                    dedupe_key, priority, max_attempts
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    radar_topic_id,
                    professor_id,
                    requested_by,
                    job_type,
                    dedupe,
                    max(0, min(100, int(priority))),
                    max(1, min(20, int(max_attempts))),
                ),
            )
            return {**cursor.fetchone(), "reused": False}


def request_topic_index(
    query: str,
    requested_by: int | None = None,
    desired_results: int = 100,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Record demand and ensure exactly one useful indexing job is active."""
    topic = ensure_radar_topic(query, desired_results)
    imported_legacy_results = 0
    if int(topic.get("candidates_seen") or 0) == 0:
        imported_legacy_results = import_latest_run_into_topic(
            int(topic["id"]), str(topic["requested_query"])
        )
        topic = fetch_radar_topic_by_id(int(topic["id"])) or topic
    job: dict[str, Any] | None = None
    if imported_legacy_results or int(topic.get("candidates_seen") or 0) == 0:
        # Old foreground scans only retained their final verified slice. Keep
        # those immediately useful results, but still build the full shared
        # candidate index in the background.
        job = enqueue_radar_job(
            "DISCOVER_CANDIDATES",
            radar_topic_id=int(topic["id"]),
            requested_by=requested_by,
            priority=90,
        )
    elif int(topic.get("discovery_version") or 0) < RADAR_DISCOVERY_VERSION:
        # Upgrade an older cached topic only when it is requested. This avoids
        # launching a mass external reindex immediately after deployment.
        job = enqueue_radar_job(
            "REINDEX_RESEARCH",
            radar_topic_id=int(topic["id"]),
            requested_by=requested_by,
            priority=95,
        )
    elif (
        int(topic.get("verified_count") or 0) < int(topic["desired_results"])
        and not bool(topic.get("sources_exhausted"))
    ):
        job = enqueue_radar_job(
            "VERIFY_FACULTY",
            radar_topic_id=int(topic["id"]),
            requested_by=requested_by,
            priority=85,
            max_attempts=20,
        )
    elif (
        topic.get("next_refresh_at") is None
        or topic["next_refresh_at"] <= datetime.now(timezone.utc)
    ):
        job = enqueue_radar_job(
            "REINDEX_RESEARCH",
            radar_topic_id=int(topic["id"]),
            requested_by=requested_by,
            priority=60,
        )
    return topic, job


def fetch_topic_rebuild_status(limit: int = 250) -> list[dict[str, Any]]:
    """Report whether each topic has complete versioned paper evidence."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT topic.id, topic.requested_query, topic.status,
                       topic.discovery_version, topic.candidates_seen,
                       topic.verified_count, topic.papers_found,
                       topic.last_indexed_at,
                       (
                           SELECT COUNT(*)
                           FROM radar_topic_professors matched
                           WHERE matched.radar_topic_id = topic.id
                             AND matched.is_current_match = TRUE
                       ) AS current_professors,
                       (
                           SELECT COUNT(DISTINCT evidence.professor_id)
                           FROM radar_topic_professor_papers evidence
                           WHERE evidence.radar_topic_id = topic.id
                             AND evidence.is_current_match = TRUE
                       ) AS professors_with_evidence,
                       (
                           SELECT COUNT(*)
                           FROM radar_topic_professor_papers evidence
                           WHERE evidence.radar_topic_id = topic.id
                             AND evidence.is_current_match = TRUE
                       ) AS evidence_rows,
                       active_job.id AS rebuild_job_id,
                       active_job.status AS rebuild_job_status,
                       active_job.priority AS rebuild_job_priority
                FROM radar_topics topic
                LEFT JOIN LATERAL (
                    SELECT job.id, job.status, job.priority
                    FROM radar_jobs job
                    WHERE job.radar_topic_id = topic.id
                      AND job.job_type = 'REINDEX_RESEARCH'
                      AND job.status IN ('queued', 'running')
                    ORDER BY job.id DESC
                    LIMIT 1
                ) active_job ON TRUE
                ORDER BY topic.id
                LIMIT %s
                """,
                (max(1, min(1000, int(limit))),),
            )
            rows = list(cursor.fetchall())

    statuses: list[dict[str, Any]] = []
    for row in rows:
        current_professors = int(row.get("current_professors") or 0)
        professors_with_evidence = int(row.get("professors_with_evidence") or 0)
        needs_rebuild = (
            int(row.get("discovery_version") or 0) < RADAR_DISCOVERY_VERSION
            or professors_with_evidence < current_professors
        )
        if not needs_rebuild:
            rebuild_state = "ready"
        elif row.get("rebuild_job_status") == "running":
            rebuild_state = "working"
        elif row.get("rebuild_job_status") == "queued":
            rebuild_state = "queued"
        else:
            rebuild_state = "needs rebuild"
        statuses.append(
            {
                **row,
                "current_professors": current_professors,
                "professors_with_evidence": professors_with_evidence,
                "missing_professor_evidence": max(
                    0, current_professors - professors_with_evidence
                ),
                "needs_rebuild": needs_rebuild,
                "rebuild_state": rebuild_state,
            }
        )
    return statuses


def queue_outdated_topic_rebuilds(
    *, limit: int = 250, priority: int = 95
) -> dict[str, Any]:
    """Queue each outdated topic once and raise reused queued-job priority."""
    outdated = [
        topic
        for topic in fetch_topic_rebuild_status(limit=limit)
        if topic["needs_rebuild"]
    ]
    jobs: list[dict[str, Any]] = []
    for topic in outdated:
        job = enqueue_radar_job(
            "REINDEX_RESEARCH",
            radar_topic_id=int(topic["id"]),
            priority=priority,
            max_attempts=10,
        )
        jobs.append(
            {
                "topic_id": int(topic["id"]),
                "requested_query": topic["requested_query"],
                "job_id": int(job["id"]),
                "job_status": job["status"],
                "priority": int(job["priority"]),
                "reused": bool(job.get("reused")),
            }
        )
    return {
        "outdated_topics": len(outdated),
        "new_jobs": sum(1 for job in jobs if not job["reused"]),
        "reused_jobs": sum(1 for job in jobs if job["reused"]),
        "jobs": jobs,
    }


def queue_seed_topics(
    topic_names: list[str],
    *,
    new_job_limit: int = 20,
    desired_results: int = 100,
    priority: int = 20,
) -> dict[str, Any]:
    """Gradually seed broad coverage without competing with visitor searches."""
    new_job_limit = max(1, min(100, int(new_job_limit)))
    unique_topics: list[str] = []
    seen: set[str] = set()
    for name in topic_names:
        normalized = normalize_topic_query(name)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_topics.append(" ".join(name.split()))

    jobs: list[dict[str, Any]] = []
    skipped: list[str] = []
    examined = 0
    new_jobs = 0
    for name in unique_topics:
        if new_jobs >= new_job_limit:
            break
        examined += 1
        topic = ensure_radar_topic(
            name,
            desired_results,
            enforce_hourly_limit=False,
        )
        if int(topic.get("candidates_seen") or 0) == 0:
            job_type = "DISCOVER_CANDIDATES"
        elif int(topic.get("discovery_version") or 0) < RADAR_DISCOVERY_VERSION:
            job_type = "REINDEX_RESEARCH"
        elif (
            int(topic.get("verified_count") or 0) < int(topic["desired_results"])
            and not bool(topic.get("sources_exhausted"))
        ):
            job_type = "VERIFY_FACULTY"
        else:
            skipped.append(name)
            continue
        job = enqueue_radar_job(
            job_type,
            radar_topic_id=int(topic["id"]),
            priority=priority,
            max_attempts=20 if job_type == "VERIFY_FACULTY" else 10,
        )
        is_new = not bool(job.get("reused"))
        new_jobs += int(is_new)
        jobs.append(
            {
                "requested_query": name,
                "topic_id": int(topic["id"]),
                "job_id": int(job["id"]),
                "job_type": job_type,
                "job_status": job["status"],
                "priority": int(job["priority"]),
                "reused": not is_new,
            }
        )
    return {
        "catalog_topics": len(unique_topics),
        "topics_examined": examined,
        "new_jobs": new_jobs,
        "reused_jobs": sum(1 for job in jobs if job["reused"]),
        "skipped_ready": len(skipped),
        "jobs": jobs,
    }


def import_latest_run_into_topic(radar_topic_id: int, query: str) -> int:
    """Preserve useful results created before the shared topic index existed."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, normalized_topic, papers_found
                FROM radar_runs
                WHERE status = 'completed'
                  AND LOWER(BTRIM(requested_query)) = LOWER(BTRIM(%s))
                ORDER BY completed_at DESC NULLS LAST, created_at DESC
                LIMIT 1
                """,
                (query,),
            )
            run = cursor.fetchone()
            if not run:
                return 0
            cursor.execute(
                """
                INSERT INTO radar_topic_professors (
                    radar_topic_id, professor_id, result_rank, research_score,
                    matching_papers, latest_paper_title, latest_paper_year,
                    latest_paper_url
                )
                SELECT %s, professor_id, result_rank, research_score,
                       matching_papers, latest_paper_title, latest_paper_year,
                       latest_paper_url
                FROM radar_run_professors
                WHERE radar_run_id = %s
                ON CONFLICT (radar_topic_id, professor_id) DO UPDATE
                SET result_rank = LEAST(
                        radar_topic_professors.result_rank, EXCLUDED.result_rank
                    ),
                    research_score = GREATEST(
                        radar_topic_professors.research_score,
                        EXCLUDED.research_score
                    ),
                    matching_papers = GREATEST(
                        radar_topic_professors.matching_papers,
                        EXCLUDED.matching_papers
                    ),
                    last_matched_at = NOW()
                """,
                (radar_topic_id, int(run["id"])),
            )
            imported = cursor.rowcount
            cursor.execute(
                """
                UPDATE radar_topics
                SET normalized_topic = COALESCE(%s, normalized_topic),
                    candidates_seen = %s, papers_found = GREATEST(papers_found, %s),
                    status = 'partial', last_indexed_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (
                    run.get("normalized_topic"),
                    imported,
                    int(run.get("papers_found") or 0),
                    radar_topic_id,
                ),
            )
    if imported:
        refresh_topic_coverage(radar_topic_id)
    return imported


def fetch_radar_topic(query: str) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT topic.*, active_job.job_type AS active_job_type,
                       active_job.status AS active_job_status,
                       active_job.attempts AS active_job_attempts
                FROM radar_topics topic
                LEFT JOIN LATERAL (
                    SELECT job_type, status, attempts
                    FROM radar_jobs
                    WHERE radar_topic_id = topic.id
                      AND status IN ('queued', 'running')
                    ORDER BY CASE status WHEN 'running' THEN 1 ELSE 2 END,
                             priority DESC, available_at, id
                    LIMIT 1
                ) active_job ON TRUE
                WHERE topic.topic_key = %s
                """,
                (topic_key(query),),
            )
            return cursor.fetchone()


def fetch_radar_topic_by_id(radar_topic_id: int) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT topic.*, active_job.job_type AS active_job_type,
                       active_job.status AS active_job_status,
                       active_job.attempts AS active_job_attempts
                FROM radar_topics topic
                LEFT JOIN LATERAL (
                    SELECT job_type, status, attempts
                    FROM radar_jobs
                    WHERE radar_topic_id = topic.id
                      AND status IN ('queued', 'running')
                    ORDER BY CASE status WHEN 'running' THEN 1 ELSE 2 END,
                             priority DESC, available_at, id
                    LIMIT 1
                ) active_job ON TRUE
                WHERE topic.id = %s
                """,
                (radar_topic_id,),
            )
            return cursor.fetchone()


def fetch_indexing_runtime_state() -> dict[str, int]:
    """Return public-safe worker availability and active-work counts."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE stopped_at IS NULL
                          AND last_seen_at > NOW() - INTERVAL '10 minutes'
                    ) AS healthy_workers
                FROM radar_worker_heartbeats
                """
            )
            worker_state = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*) AS running_jobs
                FROM radar_jobs
                WHERE status = 'running'
                  AND locked_at >= NOW() - INTERVAL '6 minutes'
                """
            )
            job_state = cursor.fetchone()
    return {
        "healthy_workers": int(worker_state.get("healthy_workers") or 0),
        "running_jobs": int(job_state.get("running_jobs") or 0),
    }


def fetch_topic_verification_progress(radar_topic_id: int) -> dict[str, int]:
    """Return checked and pending identity counts for one research topic."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE p.faculty_verification_method = 'manual_review'
                           OR (
                               p.faculty_verification_version >= %s
                               AND p.next_identity_check_at > NOW()
                           )
                    ) AS identities_checked,
                    COUNT(*) FILTER (
                        WHERE p.faculty_verification_method IS DISTINCT FROM 'manual_review'
                          AND (
                              p.faculty_verification_version < %s
                              OR p.next_identity_check_at IS NULL
                              OR p.next_identity_check_at <= NOW()
                          )
                    ) AS identities_pending
                FROM radar_topic_professors rtp
                JOIN professors p ON p.id = rtp.professor_id
                JOIN institutions institution ON institution.id = p.institution_id
                WHERE rtp.radar_topic_id = %s
                  AND rtp.is_current_match = TRUE
                  AND institution.country_code = %s
                """,
                (
                    FACULTY_VERIFICATION_VERSION,
                    FACULTY_VERIFICATION_VERSION,
                    radar_topic_id,
                    _target_country_code(),
                ),
            )
            counts = cursor.fetchone()
    return {
        "identities_checked": int(counts.get("identities_checked") or 0),
        "identities_pending": int(counts.get("identities_pending") or 0),
    }


def save_topic_candidates(
    radar_topic_id: int,
    prospects: list[dict[str, Any]],
) -> None:
    if not prospects:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            # Keep prior associations for audit, but only the newest successful
            # discovery set is eligible for verification and public results.
            cursor.execute(
                """
                UPDATE radar_topic_professors
                SET is_current_match = FALSE
                WHERE radar_topic_id = %s
                """,
                (radar_topic_id,),
            )
            cursor.execute(
                """
                UPDATE radar_topic_professor_papers
                SET is_current_match = FALSE
                WHERE radar_topic_id = %s
                """,
                (radar_topic_id,),
            )
            for rank, prospect in enumerate(prospects, start=1):
                cursor.execute(
                    """
                    INSERT INTO radar_topic_professors (
                        radar_topic_id, professor_id, result_rank, research_score,
                        matching_papers, latest_paper_title, latest_paper_year,
                        latest_paper_url, is_current_match
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (radar_topic_id, professor_id) DO UPDATE
                    SET result_rank = EXCLUDED.result_rank,
                        research_score = EXCLUDED.research_score,
                        matching_papers = EXCLUDED.matching_papers,
                        latest_paper_title = EXCLUDED.latest_paper_title,
                        latest_paper_year = EXCLUDED.latest_paper_year,
                        latest_paper_url = EXCLUDED.latest_paper_url,
                        is_current_match = TRUE,
                        last_matched_at = NOW()
                    """,
                    (
                        radar_topic_id,
                        int(prospect["professor_id"]),
                        rank,
                        float(prospect.get("research_score") or 0),
                        int(prospect.get("matching_papers") or 0),
                        prospect.get("latest_paper_title"),
                        prospect.get("latest_paper_year"),
                        prospect.get("latest_paper_url"),
                    ),
                )
                for evidence in prospect.get("supporting_papers") or []:
                    cursor.execute(
                        """
                        INSERT INTO radar_topic_professor_papers (
                            radar_topic_id, professor_id, paper_id,
                            relevance_score, matched_query, is_current_match
                        ) VALUES (%s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (radar_topic_id, professor_id, paper_id)
                        DO UPDATE SET
                            relevance_score = EXCLUDED.relevance_score,
                            matched_query = EXCLUDED.matched_query,
                            is_current_match = TRUE,
                            last_matched_at = NOW()
                        """,
                        (
                            radar_topic_id,
                            int(prospect["professor_id"]),
                            int(evidence["paper_id"]),
                            float(evidence.get("relevance_score") or 0),
                            str(evidence.get("matched_query") or ""),
                        ),
                    )


def update_topic_after_discovery(
    radar_topic_id: int,
    normalized_topic: str,
    candidates_seen: int,
    papers_found: int,
) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_topics
                SET normalized_topic = %s, status = 'indexing',
                    discovery_version = %s,
                    candidates_seen = GREATEST(candidates_seen, %s),
                    papers_found = GREATEST(papers_found, %s),
                    verified_count = (
                        SELECT COUNT(*)
                        FROM radar_topic_professors current_match
                        JOIN professors candidate
                          ON candidate.id = current_match.professor_id
                        JOIN institutions institution
                          ON institution.id = candidate.institution_id
                        WHERE current_match.radar_topic_id = radar_topics.id
                          AND current_match.is_current_match = TRUE
                          AND institution.country_code = %s
                          AND candidate.faculty_status = 'VERIFIED'
                          AND (
                              candidate.faculty_verification_method = 'manual_review'
                              OR candidate.faculty_verification_version >= %s
                          )
                    ),
                    last_error = NULL,
                    next_refresh_at = NOW() + INTERVAL '30 days',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    normalized_topic,
                    RADAR_DISCOVERY_VERSION,
                    candidates_seen,
                    papers_found,
                    _target_country_code(),
                    MIN_PUBLIC_FACULTY_VERIFICATION_VERSION,
                    radar_topic_id,
                ),
            )


def fetch_topic_candidate_ids(
    radar_topic_id: int,
    limit: int = 24,
) -> list[int]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rtp.professor_id
                FROM radar_topic_professors rtp
                JOIN professors p ON p.id = rtp.professor_id
                JOIN institutions institution ON institution.id = p.institution_id
                WHERE rtp.radar_topic_id = %s
                  AND rtp.is_current_match = TRUE
                  AND institution.country_code = %s
                  AND p.faculty_verification_method IS DISTINCT FROM 'manual_review'
                  AND (
                      p.faculty_verification_version < %s
                      OR p.next_identity_check_at IS NULL
                      OR p.next_identity_check_at <= NOW()
                  )
                ORDER BY rtp.result_rank
                LIMIT %s
                """,
                (
                    radar_topic_id,
                    _target_country_code(),
                    FACULTY_VERIFICATION_VERSION,
                    max(1, min(100, int(limit))),
                ),
            )
            return [int(row["professor_id"]) for row in cursor.fetchall()]


def refresh_topic_coverage(radar_topic_id: int) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE p.faculty_status = 'VERIFIED'
                          AND (
                              p.faculty_verification_method = 'manual_review'
                              OR p.faculty_verification_version >= %s
                          )
                    ) AS verified_count,
                    COUNT(*) FILTER (
                        WHERE p.faculty_verification_method IS DISTINCT FROM 'manual_review'
                          AND (
                              p.faculty_verification_version < %s
                              OR p.next_identity_check_at IS NULL
                              OR p.next_identity_check_at <= NOW()
                          )
                    ) AS due_count,
                    COUNT(*) AS candidates_seen
                FROM radar_topic_professors rtp
                JOIN professors p ON p.id = rtp.professor_id
                JOIN institutions institution ON institution.id = p.institution_id
                WHERE rtp.radar_topic_id = %s
                  AND rtp.is_current_match = TRUE
                  AND institution.country_code = %s
                """,
                (
                    MIN_PUBLIC_FACULTY_VERIFICATION_VERSION,
                    FACULTY_VERIFICATION_VERSION,
                    radar_topic_id,
                    _target_country_code(),
                ),
            )
            counts = cursor.fetchone()
            verified = int(counts["verified_count"] or 0)
            due = int(counts["due_count"] or 0)
            candidates = int(counts["candidates_seen"] or 0)
            cursor.execute(
                "SELECT desired_results FROM radar_topics WHERE id = %s",
                (radar_topic_id,),
            )
            desired = int(cursor.fetchone()["desired_results"])
            exhausted = due == 0 and verified < desired
            status = "ready" if verified >= desired else "partial"
            cursor.execute(
                """
                UPDATE radar_topics
                SET verified_count = %s, candidates_seen = %s,
                    sources_exhausted = %s, status = %s,
                    last_indexed_at = NOW(),
                    next_refresh_at = NOW() + INTERVAL '30 days',
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (verified, candidates, exhausted, status, radar_topic_id),
            )
            return cursor.fetchone()


def mark_topic_failed(radar_topic_id: int, error: str) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_topics
                SET status = 'failed', last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (str(error)[:1000], radar_topic_id),
            )


def fetch_topic_enrichment_ids(
    radar_topic_id: int,
    source_kind: str,
    limit: int = 20,
) -> list[int]:
    timestamp_column = {
        "hiring": "public_hiring_checked_at",
        "grants": "grant_checked_at",
    }.get(source_kind)
    if timestamp_column is None:
        raise ValueError("Unknown enrichment source kind.")
    interval = "24 hours" if source_kind == "hiring" else "30 days"
    additional_due = (
        "OR p.public_hiring_check_status = 'NOT_CHECKED'"
        if source_kind == "hiring"
        else ""
    )
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT rtp.professor_id
                FROM radar_topic_professors rtp
                JOIN professors p ON p.id = rtp.professor_id
                JOIN institutions institution ON institution.id = p.institution_id
                WHERE rtp.radar_topic_id = %s
                  AND rtp.is_current_match = TRUE
                  AND institution.country_code = %s
                  AND p.faculty_status = 'VERIFIED'
                  AND (
                      p.{timestamp_column} IS NULL
                      OR p.{timestamp_column} <= NOW() - INTERVAL '{interval}'
                      {additional_due}
                  )
                ORDER BY p.{timestamp_column} NULLS FIRST, rtp.result_rank
                LIMIT %s
                """,
                (
                    radar_topic_id,
                    _target_country_code(),
                    max(1, min(100, int(limit))),
                ),
            )
            return [int(row["professor_id"]) for row in cursor.fetchall()]


def mark_professor_enrichment_checked(
    professor_ids: list[int],
    source_kind: str,
) -> None:
    if not professor_ids:
        return
    column = {
        "hiring": "public_hiring_checked_at",
        "grants": "grant_checked_at",
    }.get(source_kind)
    if column is None:
        raise ValueError("Unknown enrichment source kind.")
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE professors SET {column} = NOW(), updated_at = NOW() WHERE id = ANY(%s)",
                (professor_ids,),
            )


def request_visible_hiring_refreshes(
    radar_topic_id: int,
    professor_ids: list[int],
    requested_by: int | None = None,
) -> int:
    """Queue fresh checks only for visible professors whose evidence is stale."""
    if not professor_ids:
        return 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM professors
                WHERE id = ANY(%s)
                  AND faculty_status = 'VERIFIED'
                  AND (
                      public_hiring_check_status = 'NOT_CHECKED'
                      OR
                      public_hiring_checked_at IS NULL
                      OR public_hiring_checked_at <= NOW() - INTERVAL '24 hours'
                      OR public_hiring_next_check_at <= NOW()
                  )
                ORDER BY public_hiring_checked_at NULLS FIRST, id
                """,
                (professor_ids,),
            )
            due_ids = [int(row["id"]) for row in cursor.fetchall()]
    if not due_ids:
        return 0
    # One topic batch lets the worker check several visible professors in
    # parallel. Per-professor timestamps prevent redundant checks when the same
    # person appears in another topic.
    job = enqueue_radar_job(
        "CHECK_HIRING",
        radar_topic_id=radar_topic_id,
        requested_by=requested_by,
        priority=95,
        max_attempts=20,
    )
    return int(not job.get("reused"))


def fetch_indexed_professors(
    query: str,
    position_type: str = "All",
    gpa_policy: str = "All",
    institution: str = "",
    limit: int = 25,
    offset: int = 0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    topic = fetch_radar_topic(query)
    if not topic:
        return None, []
    if int(topic.get("discovery_version") or 0) < RADAR_DISCOVERY_VERSION:
        # Do not present an older relevance model's cached cards while the
        # requested topic is being upgraded. The page still receives the topic
        # so it can show concise indexing progress.
        return topic, []
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    opportunity_filters = [
        "candidate.professor_id = p.id",
        "candidate.status = 'active'",
        "candidate.source_kind IN ('verified_post', 'university_post')",
        "(candidate.expires_at IS NULL OR candidate.expires_at > NOW())",
    ]
    opportunity_params: list[Any] = []
    if position_type != "All":
        opportunity_filters.append("candidate.position_type = %s")
        opportunity_params.append(position_type)
    if gpa_policy != "All":
        opportunity_filters.append("candidate.gpa_policy = %s")
        opportunity_params.append(gpa_policy)
    where_opportunity = " AND ".join(opportunity_filters)
    signal_filters = [
        "candidate_signal.professor_id = p.id",
        "candidate_signal.attribution_status = 'VERIFIED'",
        "(candidate_signal.expires_at IS NULL OR candidate_signal.expires_at > NOW())",
    ]
    signal_params: list[Any] = []
    if position_type != "All":
        signal_filters.append("candidate_signal.position_type = %s")
        signal_params.append(position_type)
    where_signal = " AND ".join(signal_filters)
    professor_filters = [
        "rtp.radar_topic_id = %s",
        "rtp.is_current_match = TRUE",
        "p.faculty_status = 'VERIFIED'",
        "institution_record.country_code = %s",
    ]
    professor_params: list[Any] = [int(topic["id"]), _target_country_code()]
    if institution.strip():
        professor_filters.append("p.institution_name ILIKE %s")
        professor_params.append(f"%{institution.strip()}%")
    professor_where = " AND ".join(professor_filters)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    rtp.professor_id, rtp.result_rank, rtp.research_score,
                    rtp.matching_papers, rtp.latest_paper_title,
                    rtp.latest_paper_year, rtp.latest_paper_url,
                    (p.grant_checked_at IS NOT NULL) AS grant_sources_checked,
                    (p.public_hiring_checked_at IS NOT NULL) AS public_sources_checked,
                    p.public_hiring_checked_at, p.grant_checked_at,
                    p.public_hiring_check_status, p.public_hiring_failure_count,
                    p.public_hiring_next_check_at,
                    p.lab_gpa_policy, p.lab_gpa_evidence_text,
                    p.lab_gpa_source_url, p.lab_gpa_minimum, p.program_gpa_minimum,
                    p.program_gpa_source_url, p.gpa_last_checked_at,
                    (
                        p.public_hiring_check_status = 'NOT_CHECKED'
                        OR p.public_hiring_checked_at IS NULL
                        OR p.public_hiring_checked_at <= NOW() - INTERVAL '24 hours'
                        OR p.public_hiring_next_check_at <= NOW()
                    ) AS hiring_refresh_needed,
                    EXISTS (
                        SELECT 1 FROM radar_jobs pending_hiring
                        WHERE pending_hiring.job_type = 'CHECK_HIRING'
                          AND pending_hiring.status IN ('queued', 'running')
                          AND (
                              pending_hiring.professor_id = p.id
                              OR (
                                  pending_hiring.professor_id IS NULL
                                  AND pending_hiring.radar_topic_id = rtp.radar_topic_id
                              )
                          )
                    ) AS hiring_check_pending,
                    p.name AS professor_name, p.institution_name,
                    p.research_domain, p.homepage_url, p.openalex_id, p.career_stage,
                    p.faculty_status, p.faculty_title, p.faculty_source_url,
                    p.faculty_verified_at, p.official_institution_domain,
                    COALESCE(f.active_grants, 0) AS active_grants,
                    f.grant_title, f.funder, f.amount AS grant_amount,
                    f.expiration_date AS grant_expiration, f.source_url AS grant_url,
                    o.id AS opportunity_id,
                    COALESCE(o.position_type, hs.position_type) AS position_type,
                    o.description,
                    COALESCE(os.source_url, o.application_url, hs.source_url) AS hiring_source_url,
                    COALESCE(os.evidence_text, o.description, hs.raw_text) AS hiring_evidence,
                    COALESCE(os.confidence, hs.confidence) AS hiring_confidence,
                    hs.id AS hiring_signal_id, hs.signal_type AS hiring_signal_type,
                    hs.observed_at AS hiring_first_found_at,
                    hs.last_checked_at AS hiring_last_checked_at,
                    o.status AS opportunity_status,
                    o.source_kind AS opportunity_source_kind,
                    COUNT(*) OVER () AS total_count,
                    COUNT(*) OVER (
                        PARTITION BY rtp.radar_topic_id, rtp.latest_paper_url
                    ) AS shared_latest_paper_count,
                    CASE
                        WHEN o.id IS NOT NULL
                             AND o.source_kind IN ('verified_post', 'university_post')
                            THEN 'confirmed_opening'
                        WHEN hs.id IS NOT NULL THEN 'public_hiring_signal'
                        WHEN COALESCE(f.active_grants, 0) > 0
                             AND (
                                 p.career_stage = 'NEW_AP'
                                 OR LOWER(COALESCE(p.faculty_title, '')) LIKE '%%assistant professor%%'
                             ) THEN 'early_career_funded'
                        WHEN p.career_stage = 'NEW_AP'
                             OR LOWER(COALESCE(p.faculty_title, '')) LIKE '%%assistant professor%%'
                            THEN 'early_career'
                        WHEN COALESCE(f.active_grants, 0) > 0 THEN 'funded_lab'
                        ELSE 'research_match'
                    END AS result_category,
                    LEAST(
                        100,
                        rtp.research_score
                        + CASE
                            WHEN o.id IS NOT NULL
                                 AND o.source_kind IN ('verified_post', 'university_post') THEN 50
                            WHEN hs.id IS NOT NULL THEN 35
                            ELSE 0
                          END
                        + CASE WHEN COALESCE(f.active_grants, 0) > 0 THEN 15 ELSE 0 END
                        + CASE
                            WHEN p.career_stage = 'NEW_AP'
                                 OR LOWER(COALESCE(p.faculty_title, '')) LIKE '%%assistant professor%%'
                                THEN 10
                            ELSE 0
                          END
                    ) AS opportunity_score
                FROM radar_topic_professors rtp
                JOIN radar_topics topic ON topic.id = rtp.radar_topic_id
                JOIN professors p ON p.id = rtp.professor_id
                JOIN institutions institution_record
                  ON institution_record.id = p.institution_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) OVER () AS active_grants, grant_title, funder,
                           amount, expiration_date, source_url
                    FROM fundings
                    WHERE professor_id = p.id
                      AND (expiration_date IS NULL OR expiration_date >= CURRENT_DATE)
                      AND COALESCE(topic.normalized_topic, topic.normalized_query)
                          = ANY(research_domains)
                    ORDER BY expiration_date DESC NULLS LAST, created_at DESC
                    LIMIT 1
                ) f ON TRUE
                LEFT JOIN LATERAL (
                    SELECT candidate.*
                    FROM opportunities candidate
                    WHERE {where_opportunity}
                    ORDER BY candidate.organic_score DESC, candidate.created_at DESC
                    LIMIT 1
                ) o ON TRUE
                LEFT JOIN LATERAL (
                    SELECT source_url, evidence_text, confidence
                    FROM opportunity_sources
                    WHERE opportunity_id = o.id
                    ORDER BY observed_at DESC LIMIT 1
                ) os ON TRUE
                LEFT JOIN LATERAL (
                    SELECT candidate_signal.*
                    FROM hiring_signals candidate_signal
                    WHERE {where_signal}
                      AND p.public_hiring_checked_at > NOW() - INTERVAL '24 hours'
                      AND p.public_hiring_check_status = 'PRESENT'
                      AND candidate_signal.check_status = 'PRESENT'
                    ORDER BY candidate_signal.last_checked_at DESC NULLS LAST,
                             candidate_signal.observed_at DESC
                    LIMIT 1
                ) hs ON TRUE
                WHERE {professor_where}
                  AND (
                      p.faculty_verification_method = 'manual_review'
                      OR p.faculty_verification_version >= %s
                  )
                ORDER BY
                    CASE
                        WHEN o.id IS NOT NULL
                             AND o.source_kind IN ('verified_post', 'university_post') THEN 1
                        WHEN hs.id IS NOT NULL THEN 2
                        WHEN COALESCE(f.active_grants, 0) > 0
                             AND (
                                 p.career_stage = 'NEW_AP'
                                 OR LOWER(COALESCE(p.faculty_title, '')) LIKE '%%assistant professor%%'
                             ) THEN 3
                        WHEN p.career_stage = 'NEW_AP'
                             OR LOWER(COALESCE(p.faculty_title, '')) LIKE '%%assistant professor%%' THEN 4
                        WHEN COALESCE(f.active_grants, 0) > 0 THEN 5
                        ELSE 6
                    END,
                    opportunity_score DESC,
                    rtp.result_rank
                LIMIT %s OFFSET %s
                """,
                [
                    *opportunity_params,
                    *signal_params,
                    *professor_params,
                    MIN_PUBLIC_FACULTY_VERIFICATION_VERSION,
                    limit,
                    offset,
                ],
            )
            return topic, list(cursor.fetchall())


def claim_next_radar_job(
    worker_id: str,
    excluded_job_types: list[str] | None = None,
) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
                    available_at = CASE
                        WHEN attempts >= max_attempts THEN available_at ELSE NOW()
                    END,
                    last_error = COALESCE(last_error, '') ||
                        CASE WHEN last_error IS NULL OR last_error = '' THEN '' ELSE E'\n' END ||
                        'Recovered after a worker stopped before completion.',
                    locked_at = NULL, locked_by = NULL, updated_at = NOW(),
                    completed_at = CASE WHEN attempts >= max_attempts THEN NOW() ELSE NULL END
                WHERE status = 'running'
                  AND locked_at < NOW() - INTERVAL '10 minutes'
                """
            )
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM radar_jobs
                    WHERE status = 'queued' AND available_at <= NOW()
                      AND attempts < max_attempts
                      AND NOT (job_type = ANY(%s::TEXT[]))
                    ORDER BY priority DESC, available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE radar_jobs job
                SET status = 'running', attempts = attempts + 1,
                    locked_at = NOW(), locked_by = %s,
                    started_at = NOW(), completed_at = NULL,
                    last_error = NULL, updated_at = NOW()
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.*
                """,
                (list(excluded_job_types or []), worker_id),
            )
            return cursor.fetchone()


def complete_radar_job(job_id: int, result: dict[str, Any] | None = None) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = 'completed', completed_at = NOW(), updated_at = NOW(),
                    locked_at = NULL, locked_by = NULL, last_error = NULL,
                    result_json = %s::jsonb
                WHERE id = %s
                """,
                (json.dumps(result or {}), job_id),
            )


def reschedule_radar_job(
    job_id: int,
    delay_seconds: int = 2,
    result: dict[str, Any] | None = None,
) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = 'queued', attempts = 0,
                    available_at = NOW() + (%s * INTERVAL '1 second'),
                    locked_at = NULL, locked_by = NULL, started_at = NULL,
                    last_error = NULL, updated_at = NOW(),
                    result_json = %s::jsonb
                WHERE id = %s
                """,
                (max(0, int(delay_seconds)), json.dumps(result or {}), job_id),
            )


def fail_radar_job(job: dict[str, Any], error: Exception | str) -> None:
    attempts = int(job.get("attempts") or 1)
    max_attempts = int(job.get("max_attempts") or 5)
    exponential_delay = min(3600, 15 * (2 ** max(0, attempts - 1)))
    requested_delay = int(getattr(error, "retry_after_seconds", 0) or 0)
    # A declared retry delay means an external dependency is temporarily
    # unavailable. Do not exhaust the job's attempts during provider cooldown.
    dependency_outage = requested_delay > 0
    retry = dependency_outage or attempts < max_attempts
    delay = min(21600, max(exponential_delay, requested_delay))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = %s,
                    attempts = CASE
                        WHEN %s THEN GREATEST(attempts - 1, 0)
                        ELSE attempts
                    END,
                    available_at = CASE WHEN %s THEN NOW() + (%s * INTERVAL '1 second') ELSE available_at END,
                    completed_at = CASE WHEN %s THEN NULL ELSE NOW() END,
                    locked_at = NULL, locked_by = NULL,
                    started_at = CASE WHEN %s THEN NULL ELSE started_at END,
                    last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (
                    "queued" if retry else "failed",
                    dependency_outage,
                    retry,
                    delay,
                    retry,
                    retry,
                    str(error)[:2000],
                    int(job["id"]),
                ),
            )
    if job.get("radar_topic_id") and not retry:
        mark_topic_failed(int(job["radar_topic_id"]), str(error))


def update_worker_heartbeat(worker_id: str, current_job_id: int | None = None) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO radar_worker_heartbeats (
                    worker_id, process_id, hostname, current_job_id
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (worker_id) DO UPDATE
                SET process_id = EXCLUDED.process_id,
                    hostname = EXCLUDED.hostname,
                    current_job_id = EXCLUDED.current_job_id,
                    last_seen_at = NOW(), stopped_at = NULL
                """,
                (worker_id, os.getpid(), socket.gethostname(), current_job_id),
            )


def update_radar_job_progress(
    job_id: int,
    stage: str,
    professor_ids: list[int] | None = None,
    detail: str = "",
) -> None:
    """Persist the current batch so staff can see work without terminal access."""
    ordered_ids = list(dict.fromkeys(int(value) for value in professor_ids or []))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            professors: list[dict[str, Any]] = []
            if ordered_ids:
                cursor.execute(
                    """
                    SELECT id, name, institution_name, homepage_url
                    FROM professors
                    WHERE id = ANY(%s)
                    ORDER BY array_position(%s::BIGINT[], id)
                    """,
                    (ordered_ids, ordered_ids),
                )
                professors = list(cursor.fetchall())
            progress = {
                "live_stage": stage,
                "live_detail": detail,
                "live_professors": professors,
                "live_updated_at": datetime.now(timezone.utc).isoformat(),
            }
            cursor.execute(
                """
                UPDATE radar_jobs
                SET result_json = COALESCE(result_json, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s AND status = 'running'
                """,
                (json.dumps(progress, default=str), int(job_id)),
            )


def stop_worker_heartbeat(worker_id: str) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_worker_heartbeats
                SET current_job_id = NULL, last_seen_at = NOW(), stopped_at = NOW()
                WHERE worker_id = %s
                """,
                (worker_id,),
            )


def enqueue_due_maintenance(limit: int = 20) -> int:
    queued = 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM radar_topics
                WHERE next_refresh_at IS NOT NULL
                  AND next_refresh_at <= NOW()
                  AND NOT EXISTS (
                      SELECT 1
                      FROM radar_jobs active_job
                      WHERE active_job.radar_topic_id = radar_topics.id
                        AND active_job.status IN ('queued', 'running')
                  )
                ORDER BY next_refresh_at
                LIMIT %s
                """,
                (max(1, min(100, int(limit))),),
            )
            topic_ids = [int(row["id"]) for row in cursor.fetchall()]
    for radar_topic_id in topic_ids:
        job = enqueue_radar_job(
            "REINDEX_RESEARCH", radar_topic_id=radar_topic_id, priority=30
        )
        queued += int(not job.get("reused"))
    return queued


def _attach_identity_review_context(cursor: Any, identities: list[dict[str, Any]]) -> None:
    """Attach the research trail a moderator needs to distinguish namesakes."""
    rows_by_id: dict[int, list[dict[str, Any]]] = {}
    for identity in identities:
        professor_id = int(identity["id"])
        rows_by_id.setdefault(professor_id, []).append(identity)
        identity["identity_papers"] = []
        identity["matching_topics"] = []
        identity["topic_paper_evidence"] = []
        identity["historical_topics"] = []
        identity["identity_evidence"] = []
    if not rows_by_id:
        return

    professor_ids = list(rows_by_id)
    cursor.execute(
        """
        WITH ranked_papers AS (
            SELECT pp.professor_id, paper.openalex_id, paper.title,
                   paper.publication_year, paper.doi, pp.author_position,
                   ROW_NUMBER() OVER (
                       PARTITION BY pp.professor_id
                       ORDER BY paper.publication_year DESC NULLS LAST,
                                paper.citation_count DESC,
                                paper.id
                   ) AS evidence_rank
            FROM professor_papers pp
            JOIN papers paper ON paper.id = pp.paper_id
            WHERE pp.professor_id = ANY(%s)
        )
        SELECT professor_id, openalex_id, title, publication_year, doi,
               author_position
        FROM ranked_papers
        WHERE evidence_rank <= 5
        ORDER BY professor_id, evidence_rank
        """,
        (professor_ids,),
    )
    for paper in cursor.fetchall():
        context = {
            "openalex_id": paper["openalex_id"],
            "title": paper["title"],
            "publication_year": paper["publication_year"],
            "doi": paper["doi"],
            "author_position": paper["author_position"],
        }
        for identity in rows_by_id[int(paper["professor_id"])]:
            identity["identity_papers"].append(context)

    cursor.execute(
        """
        SELECT rtp.professor_id,
               ARRAY_AGG(DISTINCT topic.requested_query ORDER BY topic.requested_query)
                   AS matching_topics
        FROM radar_topic_professors rtp
        JOIN radar_topics topic ON topic.id = rtp.radar_topic_id
        WHERE rtp.professor_id = ANY(%s)
          AND rtp.is_current_match = TRUE
        GROUP BY rtp.professor_id
        """,
        (professor_ids,),
    )
    for topic_row in cursor.fetchall():
        for identity in rows_by_id[int(topic_row["professor_id"])]:
            identity["matching_topics"] = list(topic_row["matching_topics"] or [])

    cursor.execute(
        """
        WITH ranked_topic_papers AS (
            SELECT evidence.professor_id, topic.requested_query,
                   paper.openalex_id, paper.title, paper.publication_year,
                   paper.doi, pp.author_position, evidence.relevance_score,
                   evidence.matched_query,
                   ROW_NUMBER() OVER (
                       PARTITION BY evidence.professor_id
                       ORDER BY evidence.relevance_score DESC,
                                paper.publication_year DESC NULLS LAST,
                                paper.id
                   ) AS evidence_rank
            FROM radar_topic_professor_papers evidence
            JOIN radar_topic_professors topic_professor
              ON topic_professor.radar_topic_id = evidence.radar_topic_id
             AND topic_professor.professor_id = evidence.professor_id
             AND topic_professor.is_current_match = TRUE
            JOIN radar_topics topic ON topic.id = evidence.radar_topic_id
            JOIN professor_papers pp
              ON pp.professor_id = evidence.professor_id
             AND pp.paper_id = evidence.paper_id
            JOIN papers paper ON paper.id = evidence.paper_id
            WHERE evidence.professor_id = ANY(%s)
              AND evidence.is_current_match = TRUE
        )
        SELECT professor_id, requested_query, openalex_id, title,
               publication_year, doi, author_position, relevance_score,
               matched_query
        FROM ranked_topic_papers
        WHERE evidence_rank <= 10
        ORDER BY professor_id, evidence_rank
        """,
        (professor_ids,),
    )
    for paper in cursor.fetchall():
        context = {
            "research_area": paper["requested_query"],
            "openalex_id": paper["openalex_id"],
            "title": paper["title"],
            "publication_year": paper["publication_year"],
            "doi": paper["doi"],
            "author_position": paper["author_position"],
            "relevance_score": float(paper["relevance_score"] or 0),
            "matched_query": paper["matched_query"],
        }
        for identity in rows_by_id[int(paper["professor_id"])]:
            identity["topic_paper_evidence"].append(context)

    cursor.execute(
        """
        SELECT rtp.professor_id,
               ARRAY_AGG(DISTINCT topic.requested_query ORDER BY topic.requested_query)
                   AS historical_topics
        FROM radar_topic_professors rtp
        JOIN radar_topics topic ON topic.id = rtp.radar_topic_id
        WHERE rtp.professor_id = ANY(%s)
          AND rtp.is_current_match = FALSE
        GROUP BY rtp.professor_id
        """,
        (professor_ids,),
    )
    for topic_row in cursor.fetchall():
        for identity in rows_by_id[int(topic_row["professor_id"])]:
            identity["historical_topics"] = list(
                topic_row["historical_topics"] or []
            )

    cursor.execute(
        """
        WITH ranked_evidence AS (
            SELECT evidence.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY evidence.professor_id
                       ORDER BY evidence.checked_at DESC, evidence.id DESC
                   ) AS evidence_rank
            FROM faculty_verification_evidence evidence
            WHERE evidence.professor_id = ANY(%s)
        )
        SELECT professor_id, source_url, observed_title, observed_institution,
               evidence_text, verification_status, confidence,
               decision_method, checked_at
        FROM ranked_evidence
        WHERE evidence_rank <= 5
        ORDER BY professor_id, evidence_rank
        """,
        (professor_ids,),
    )
    for evidence in cursor.fetchall():
        context = {
            "source_url": evidence["source_url"],
            "observed_title": evidence["observed_title"],
            "observed_institution": evidence["observed_institution"],
            "evidence_text": evidence["evidence_text"],
            "verification_status": evidence["verification_status"],
            "confidence": evidence["confidence"],
            "decision_method": evidence["decision_method"],
            "checked_at": evidence["checked_at"],
        }
        for identity in rows_by_id[int(evidence["professor_id"])]:
            identity["identity_evidence"].append(context)


def fetch_live_indexing_status(admin_user_id: int, recent_limit: int = 20) -> dict[str, Any]:
    """Return a small, frequently refreshed view of active indexing work."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id, owner_only=True)
            cursor.execute(
                """
                SELECT worker.worker_id, worker.started_at AS worker_started_at,
                       worker.last_seen_at, worker.current_job_id,
                       job.job_type, job.started_at AS job_started_at,
                       job.result_json AS progress,
                       topic.requested_query, professor.name AS professor_name
                FROM radar_worker_heartbeats worker
                LEFT JOIN radar_jobs job ON job.id = worker.current_job_id
                LEFT JOIN radar_topics topic ON topic.id = job.radar_topic_id
                LEFT JOIN professors professor ON professor.id = job.professor_id
                WHERE worker.stopped_at IS NULL
                  AND worker.last_seen_at > NOW() - INTERVAL '2 minutes'
                ORDER BY worker.last_seen_at DESC
                LIMIT 1
                """
            )
            worker = cursor.fetchone()

            cursor.execute(
                """
                WITH current_candidates AS (
                    SELECT DISTINCT match.professor_id
                    FROM radar_topic_professors match
                    JOIN professors scoped_professor
                      ON scoped_professor.id = match.professor_id
                    JOIN institutions scoped_institution
                      ON scoped_institution.id = scoped_professor.institution_id
                    WHERE match.is_current_match = TRUE
                      AND scoped_institution.country_code = %s
                )
                SELECT COUNT(*) AS unique_candidates,
                       COUNT(*) FILTER (
                           WHERE professor.faculty_verification_method
                                     IS DISTINCT FROM 'manual_review'
                             AND (
                                 professor.faculty_verification_version < %s
                                 OR professor.next_identity_check_at IS NULL
                                 OR professor.next_identity_check_at <= NOW()
                             )
                       ) AS pending_identities,
                       COUNT(*) FILTER (
                           WHERE professor.faculty_status = 'VERIFIED'
                       ) AS verified_faculty,
                       COUNT(*) FILTER (
                           WHERE %s::TIMESTAMPTZ IS NOT NULL
                             AND professor.faculty_checked_at >= %s
                       ) AS checked_since_worker_start,
                       COUNT(*) FILTER (
                           WHERE %s::TIMESTAMPTZ IS NOT NULL
                             AND professor.faculty_checked_at >= %s
                             AND professor.faculty_status = 'VERIFIED'
                       ) AS verified_since_worker_start
                FROM current_candidates candidate
                JOIN professors professor ON professor.id = candidate.professor_id
                """,
                (
                    _target_country_code(),
                    FACULTY_VERIFICATION_VERSION,
                    worker.get("worker_started_at") if worker else None,
                    worker.get("worker_started_at") if worker else None,
                    worker.get("worker_started_at") if worker else None,
                    worker.get("worker_started_at") if worker else None,
                ),
            )
            totals = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT 'VERIFY_FACULTY' AS stage, professor.id AS professor_id,
                       professor.name, professor.institution_name,
                       professor.faculty_checked_at AS activity_at,
                       professor.faculty_status AS result_status,
                       professor.faculty_title AS result_detail,
                       COALESCE(evidence.source_url, professor.faculty_source_url)
                           AS source_url,
                       evidence.observed_title, evidence.observed_institution,
                       evidence.evidence_text,
                       ARRAY(
                           SELECT DISTINCT topic.requested_query
                           FROM radar_topic_professors match
                           JOIN radar_topics topic ON topic.id = match.radar_topic_id
                           WHERE match.professor_id = professor.id
                             AND match.is_current_match = TRUE
                           ORDER BY topic.requested_query
                       ) AS research_areas
                FROM professors professor
                JOIN institutions institution
                  ON institution.id = professor.institution_id
                LEFT JOIN LATERAL (
                    SELECT source_url, observed_title, observed_institution,
                           evidence_text
                    FROM faculty_verification_evidence
                    WHERE professor_id = professor.id
                    ORDER BY checked_at DESC
                    LIMIT 1
                ) evidence ON TRUE
                WHERE professor.faculty_checked_at IS NOT NULL
                  AND institution.country_code = %s
                  AND EXISTS (
                      SELECT 1 FROM radar_topic_professors current_match
                      WHERE current_match.professor_id = professor.id
                        AND current_match.is_current_match = TRUE
                  )
                ORDER BY professor.faculty_checked_at DESC
                LIMIT %s
                """,
                (
                    _target_country_code(),
                    max(1, min(50, int(recent_limit))),
                ),
            )
            activity_logs = list(cursor.fetchall())

            cursor.execute(
                """
                SELECT 'CHECK_HIRING' AS stage, professor.id AS professor_id,
                       professor.name, professor.institution_name,
                       professor.public_hiring_checked_at AS activity_at,
                       professor.public_hiring_check_status AS result_status,
                       signal.raw_text AS result_detail,
                       signal.source_url, NULL::TEXT AS observed_title,
                       NULL::TEXT AS observed_institution,
                       signal.raw_text AS evidence_text,
                       ARRAY(
                           SELECT DISTINCT topic.requested_query
                           FROM radar_topic_professors match
                           JOIN radar_topics topic ON topic.id = match.radar_topic_id
                           WHERE match.professor_id = professor.id
                             AND match.is_current_match = TRUE
                           ORDER BY topic.requested_query
                       ) AS research_areas
                FROM professors professor
                JOIN institutions institution
                  ON institution.id = professor.institution_id
                LEFT JOIN LATERAL (
                    SELECT raw_text, source_url
                    FROM hiring_signals
                    WHERE professor_id = professor.id
                    ORDER BY last_seen_at DESC
                    LIMIT 1
                ) signal ON TRUE
                WHERE professor.public_hiring_checked_at IS NOT NULL
                  AND institution.country_code = %s
                ORDER BY professor.public_hiring_checked_at DESC
                LIMIT %s
                """,
                (
                    _target_country_code(),
                    max(1, min(50, int(recent_limit))),
                ),
            )
            activity_logs.extend(cursor.fetchall())

            cursor.execute(
                """
                SELECT 'CHECK_GRANTS' AS stage, professor.id AS professor_id,
                       professor.name, professor.institution_name,
                       professor.grant_checked_at AS activity_at,
                       CASE WHEN funding.id IS NULL THEN 'NOT_FOUND' ELSE 'FOUND' END
                           AS result_status,
                       funding.grant_title AS result_detail,
                       funding.source_url, NULL::TEXT AS observed_title,
                       NULL::TEXT AS observed_institution,
                       CASE WHEN funding.id IS NULL
                           THEN NULL
                           ELSE CONCAT(funding.funder, ': ', funding.grant_title)
                       END AS evidence_text,
                       ARRAY(
                           SELECT DISTINCT topic.requested_query
                           FROM radar_topic_professors match
                           JOIN radar_topics topic ON topic.id = match.radar_topic_id
                           WHERE match.professor_id = professor.id
                             AND match.is_current_match = TRUE
                           ORDER BY topic.requested_query
                       ) AS research_areas
                FROM professors professor
                JOIN institutions institution
                  ON institution.id = professor.institution_id
                LEFT JOIN LATERAL (
                    SELECT id, grant_title, funder, source_url
                    FROM fundings
                    WHERE professor_id = professor.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) funding ON TRUE
                WHERE professor.grant_checked_at IS NOT NULL
                  AND institution.country_code = %s
                ORDER BY professor.grant_checked_at DESC
                LIMIT %s
                """,
                (
                    _target_country_code(),
                    max(1, min(50, int(recent_limit))),
                ),
            )
            activity_logs.extend(cursor.fetchall())
            activity_logs.sort(
                key=lambda row: row.get("activity_at")
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            activity_logs = activity_logs[: max(1, min(50, int(recent_limit)))]

            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM radar_jobs
                WHERE job_type IN ('VERIFY_FACULTY', 'REFRESH_FACULTY')
                  AND status IN ('queued', 'running')
                GROUP BY status
                """
            )
            queue_counts = {
                str(row["status"]): int(row["count"])
                for row in cursor.fetchall()
            }

            cursor.execute(
                """
                SELECT provider_name, status, blocked_until, last_success_at,
                       last_failure_at, last_error
                FROM web_search_provider_health
                WHERE provider_name IN ('searxng', 'brave', 'ddgs')
                ORDER BY CASE WHEN provider_name = 'searxng' THEN 0 ELSE 1 END,
                         provider_name
                """
            )
            providers = list(cursor.fetchall())

    return {
        "worker": worker,
        "totals": totals,
        "activity_logs": activity_logs,
        "queue_counts": queue_counts,
        "providers": providers,
    }


def list_radar_operations(admin_user_id: int, limit: int = 100) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id)
            cursor.execute(
                """
                SELECT display_status AS status, COUNT(*) AS count
                FROM (
                    SELECT CASE
                        WHEN status = 'running'
                             AND locked_at < NOW() - INTERVAL '6 minutes'
                            THEN 'stalled'
                        ELSE status
                    END AS display_status
                    FROM radar_jobs
                ) displayed
                GROUP BY display_status ORDER BY display_status
                """
            )
            job_counts = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT job.id, job.job_type,
                       CASE
                           WHEN job.status = 'running'
                                AND job.locked_at < NOW() - INTERVAL '6 minutes'
                               THEN 'stalled'
                           ELSE job.status
                       END AS status,
                       job.status AS database_status,
                       CASE WHEN job.status = 'running'
                           THEN FLOOR(EXTRACT(EPOCH FROM (NOW() - job.locked_at)))::int
                           ELSE NULL
                       END AS running_seconds,
                       job.priority,
                       job.attempts, job.max_attempts, job.available_at,
                       job.started_at, job.completed_at, job.last_error,
                       topic.requested_query, p.name AS professor_name,
                       job.created_at
                FROM radar_jobs job
                LEFT JOIN radar_topics topic ON topic.id = job.radar_topic_id
                LEFT JOIN professors p ON p.id = job.professor_id
                ORDER BY
                    CASE
                        WHEN job.status = 'running'
                             AND job.locked_at < NOW() - INTERVAL '6 minutes' THEN 1
                        WHEN job.status = 'running' THEN 2
                        WHEN job.status = 'queued' THEN 3
                        WHEN job.status = 'failed' THEN 4 ELSE 5
                    END,
                    job.priority DESC, job.created_at DESC
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            jobs = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT topic.id, topic.requested_query, topic.status,
                       topic.desired_results, topic.discovery_version,
                       topic.candidates_seen, topic.verified_count,
                       topic.papers_found, topic.sources_exhausted,
                       topic.search_count, topic.last_error,
                       topic.last_requested_at, topic.last_indexed_at,
                       topic.next_refresh_at,
                       (
                           SELECT COUNT(DISTINCT evidence.professor_id)
                           FROM radar_topic_professor_papers evidence
                           JOIN radar_topic_professors matched
                             ON matched.radar_topic_id = evidence.radar_topic_id
                            AND matched.professor_id = evidence.professor_id
                            AND matched.is_current_match = TRUE
                           JOIN professors professor
                             ON professor.id = evidence.professor_id
                            AND professor.faculty_status = 'VERIFIED'
                           WHERE evidence.radar_topic_id = topic.id
                             AND evidence.is_current_match = TRUE
                       ) AS exact_evidence_professors,
                       (
                           SELECT COUNT(DISTINCT matched.professor_id)
                           FROM radar_topic_professors matched
                           JOIN professors professor
                             ON professor.id = matched.professor_id
                           WHERE matched.radar_topic_id = topic.id
                             AND matched.is_current_match = TRUE
                             AND professor.faculty_status = 'VERIFIED'
                             AND professor.public_hiring_checked_at
                                 > NOW() - INTERVAL '24 hours'
                       ) AS fresh_hiring_checked,
                       (
                           SELECT COUNT(*)
                           FROM radar_jobs failed_job
                           WHERE failed_job.radar_topic_id = topic.id
                             AND failed_job.status = 'failed'
                             AND (
                                 topic.last_indexed_at IS NULL
                                 OR failed_job.updated_at >= topic.last_indexed_at
                             )
                       ) AS failed_jobs,
                       (
                           SELECT COUNT(DISTINCT matched.professor_id)
                           FROM radar_topic_professors matched
                           JOIN professors professor
                             ON professor.id = matched.professor_id
                           WHERE matched.radar_topic_id = topic.id
                             AND matched.is_current_match = TRUE
                             AND professor.faculty_status IN (
                                 'CONFLICT', 'MANUAL_REVIEW'
                             )
                       ) AS identities_needing_review,
                       (
                           SELECT COUNT(DISTINCT matched.professor_id)
                           FROM radar_topic_professors matched
                           JOIN professors professor
                             ON professor.id = matched.professor_id
                           WHERE matched.radar_topic_id = topic.id
                             AND matched.is_current_match = TRUE
                             AND professor.faculty_status = 'VERIFIED'
                             AND (
                                 professor.public_hiring_check_status
                                     = 'SOURCE_UNAVAILABLE'
                                 OR professor.public_hiring_failure_count > 0
                             )
                       ) AS hiring_source_problems,
                       active_job.job_type AS active_job_type,
                       active_job.status AS active_job_status
                FROM radar_topics topic
                LEFT JOIN LATERAL (
                    SELECT job.job_type, job.status, job.priority
                    FROM radar_jobs job
                    WHERE job.radar_topic_id = topic.id
                      AND job.status IN ('queued', 'running')
                    ORDER BY
                        CASE WHEN job.status = 'running' THEN 0 ELSE 1 END,
                        job.priority DESC, job.created_at
                    LIMIT 1
                ) active_job ON TRUE
                ORDER BY topic.last_requested_at DESC
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            topics = list(cursor.fetchall())
            for topic in topics:
                topic["problem_count"] = sum(
                    int(topic.get(key) or 0)
                    for key in (
                        "failed_jobs",
                        "identities_needing_review",
                        "hiring_source_problems",
                    )
                )
                job_type = str(topic.get("active_job_type") or "")
                if job_type in {"DISCOVER_CANDIDATES", "REINDEX_RESEARCH"}:
                    stage = "Finding researchers"
                elif job_type in {"VERIFY_FACULTY", "REFRESH_FACULTY"}:
                    stage = "Verifying faculty"
                elif job_type in {
                    "CHECK_GRANTS", "CHECK_HIRING", "ENRICH_PROFESSORS"
                }:
                    stage = "Checking opportunities"
                elif int(topic.get("failed_jobs") or 0) > 0:
                    stage = "Needs attention"
                elif (
                    int(topic.get("discovery_version") or 0)
                        >= RADAR_DISCOVERY_VERSION
                    and int(topic.get("exact_evidence_professors") or 0)
                        >= int(topic.get("verified_count") or 0)
                    and (
                        int(topic.get("verified_count") or 0)
                            >= int(topic.get("desired_results") or 100)
                        or bool(topic.get("sources_exhausted"))
                    )
                ):
                    stage = "Ready"
                elif int(topic.get("candidates_seen") or 0) > 0:
                    stage = "Partial"
                else:
                    stage = "Waiting"
                topic["coverage_stage"] = stage
            cursor.execute(
                """
                SELECT worker_id, process_id, hostname, current_job_id,
                       started_at, last_seen_at, stopped_at,
                       (stopped_at IS NULL AND last_seen_at > NOW() - INTERVAL '10 minutes')
                           AS healthy
                FROM radar_worker_heartbeats
                ORDER BY last_seen_at DESC
                """
            )
            workers = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT provider_name, status, blocked_until,
                       last_success_at, last_failure_at, last_error,
                       (status = 'blocked' AND blocked_until > NOW())
                           AS actively_blocked
                FROM web_search_provider_health
                ORDER BY provider_name
                """
            )
            search_providers = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT p.id, p.openalex_id, p.name, p.institution_name,
                       p.research_domain, p.faculty_title, p.faculty_status,
                       p.faculty_confidence, p.faculty_verification_method,
                       faculty_checked_at, next_identity_check_at,
                       faculty_source_url,
                       evidence.evidence_text AS review_reason,
                       evidence.decision_method, evidence.model_name
                FROM professors p
                JOIN institutions institution ON institution.id = p.institution_id
                LEFT JOIN LATERAL (
                    SELECT evidence_text, decision_method, model_name
                    FROM faculty_verification_evidence
                    WHERE professor_id = p.id
                    ORDER BY checked_at DESC
                    LIMIT 1
                ) evidence ON TRUE
                WHERE p.faculty_status IN ('MANUAL_REVIEW', 'CONFLICT')
                  AND institution.country_code = %s
                ORDER BY p.updated_at DESC
                LIMIT %s
                """,
                (_target_country_code(), max(1, min(250, int(limit)))),
            )
            identity_review = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT p.id, p.openalex_id, p.name, p.institution_name,
                       p.research_domain, p.faculty_title, p.faculty_status,
                       p.faculty_confidence, p.faculty_verification_method,
                       p.faculty_checked_at, p.next_identity_check_at,
                       p.faculty_source_url,
                       evidence.evidence_text AS decision_reason,
                       evidence.decision_method, evidence.model_name,
                       evidence.checked_at AS evidence_checked_at
                FROM professors p
                JOIN institutions institution ON institution.id = p.institution_id
                LEFT JOIN LATERAL (
                    SELECT evidence_text, decision_method, model_name, checked_at
                    FROM faculty_verification_evidence
                    WHERE professor_id = p.id
                    ORDER BY checked_at DESC
                    LIMIT 1
                ) evidence ON TRUE
                WHERE p.faculty_checked_at IS NOT NULL
                  AND institution.country_code = %s
                  AND p.faculty_verification_method IS DISTINCT FROM 'manual_review'
                ORDER BY p.faculty_checked_at DESC
                LIMIT %s
                """,
                (_target_country_code(), max(1, min(250, int(limit)))),
            )
            identity_decisions = list(cursor.fetchall())
            _attach_identity_review_context(
                cursor, [*identity_review, *identity_decisions]
            )
            cursor.execute(
                """
                SELECT request_count, updated_at
                FROM ai_usage_daily
                WHERE usage_date = CURRENT_DATE
                  AND provider = 'gemini'
                  AND feature = 'faculty_identity'
                """
            )
            identity_ai_usage = cursor.fetchone() or {
                "request_count": 0,
                "updated_at": None,
            }
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE public_hiring_check_status = 'PRESENT') AS present,
                    COUNT(*) FILTER (WHERE public_hiring_check_status = 'NOT_FOUND') AS not_found,
                    COUNT(*) FILTER (WHERE public_hiring_check_status = 'SOURCE_UNAVAILABLE') AS unavailable,
                    COUNT(*) FILTER (
                        WHERE public_hiring_checked_at IS NULL
                           OR public_hiring_checked_at <= NOW() - INTERVAL '24 hours'
                    ) AS stale_or_unchecked
                FROM professors
                WHERE faculty_status = 'VERIFIED'
                """
            )
            hiring_metrics = cursor.fetchone()
            cursor.execute(
                """
                SELECT id, name, institution_name, public_hiring_check_status,
                       public_hiring_checked_at, public_hiring_failure_count,
                       public_hiring_next_check_at, faculty_source_url
                FROM professors
                WHERE faculty_status = 'VERIFIED'
                  AND (
                      public_hiring_check_status = 'SOURCE_UNAVAILABLE'
                      OR public_hiring_failure_count > 0
                  )
                ORDER BY public_hiring_failure_count DESC,
                         public_hiring_checked_at NULLS FIRST
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            hiring_issues = list(cursor.fetchall())
    return {
        "job_counts": job_counts,
        "jobs": jobs,
        "topics": topics,
        "workers": workers,
        "search_providers": search_providers,
        "identity_review": identity_review,
        "identity_decisions": identity_decisions,
        "identity_ai_usage": identity_ai_usage,
        "hiring_metrics": hiring_metrics,
        "hiring_issues": hiring_issues,
    }


def retry_radar_job(owner_user_id: int, job_id: int) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = 'queued', attempts = 0, available_at = NOW(),
                    completed_at = NULL, locked_at = NULL, locked_by = NULL,
                    last_error = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'failed'
                """,
                (job_id,),
            )


def recover_stalled_radar_jobs(owner_user_id: int) -> int:
    """Requeue abandoned work without taking a job from a healthy worker."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                UPDATE radar_jobs job
                SET status = CASE
                        WHEN job.attempts >= job.max_attempts THEN 'failed'
                        ELSE 'queued'
                    END,
                    available_at = CASE
                        WHEN job.attempts >= job.max_attempts
                            THEN job.available_at
                        ELSE NOW()
                    END,
                    last_error = CONCAT_WS(
                        E'\n', NULLIF(job.last_error, ''),
                        'Recovered by the site owner after the worker stopped.'
                    ),
                    locked_at = NULL,
                    locked_by = NULL,
                    completed_at = CASE
                        WHEN job.attempts >= job.max_attempts THEN NOW()
                        ELSE NULL
                    END,
                    updated_at = NOW()
                WHERE job.status = 'running'
                  AND job.locked_at < NOW() - INTERVAL '6 minutes'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM radar_worker_heartbeats worker
                      WHERE worker.worker_id = job.locked_by
                        AND worker.stopped_at IS NULL
                        AND worker.last_seen_at > NOW() - INTERVAL '2 minutes'
                  )
                RETURNING job.id
                """
            )
            recovered_ids = [int(row["id"]) for row in cursor.fetchall()]
            if recovered_ids:
                cursor.execute(
                    """
                    INSERT INTO admin_audit_log (
                        actor_user_id, action, target_type, notes
                    ) VALUES (%s, 'recover_stalled_jobs', 'radar_job', %s)
                    """,
                    (
                        owner_user_id,
                        f"Recovered {len(recovered_ids)} abandoned background task(s).",
                    ),
                )
            return len(recovered_ids)


def retry_unresolved_identities(owner_user_id: int, limit: int = 100) -> int:
    """Send unresolved automatic decisions back through the current verifier."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                SELECT professor.id
                FROM professors professor
                JOIN institutions institution
                  ON institution.id = professor.institution_id
                WHERE professor.faculty_status IN ('CONFLICT', 'MANUAL_REVIEW')
                  AND institution.country_code = %s
                  AND professor.faculty_verification_method IS DISTINCT FROM 'manual_review'
                ORDER BY professor.updated_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (_target_country_code(), max(1, min(250, int(limit)))),
            )
            professor_ids = [int(row["id"]) for row in cursor.fetchall()]
            if professor_ids:
                cursor.execute(
                    """
                    UPDATE professors
                    SET faculty_status = 'UNVERIFIED', faculty_confidence = 0,
                        faculty_verification_method = NULL,
                        next_identity_check_at = NOW(), updated_at = NOW()
                    WHERE id = ANY(%s)
                    """,
                    (professor_ids,),
                )
            cursor.execute(
                """
                INSERT INTO admin_audit_log (
                    actor_user_id, action, target_type, notes
                ) VALUES (%s, 'identity_retry_batch', 'professor_identity', %s)
                """,
                (owner_user_id, f"Queued {len(professor_ids)} unresolved identities."),
            )
    for professor_id in professor_ids:
        enqueue_radar_job(
            "REFRESH_FACULTY",
            professor_id=professor_id,
            requested_by=owner_user_id,
            priority=95,
            max_attempts=5,
        )
    return len(professor_ids)


def review_faculty_identity(
    owner_user_id: int,
    professor_id: int,
    decision: str,
    *,
    institution_name: str = "",
    faculty_title: str = "",
    source_url: str = "",
) -> None:
    """Resolve a hidden identity record or send it back to automatic checking."""
    normalized_decision = decision.strip().upper()
    if normalized_decision not in {"VERIFIED", "NOT_FACULTY", "RETRY"}:
        raise ValueError("Unknown faculty identity decision.")

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                "SELECT * FROM professors WHERE id = %s FOR UPDATE",
                (professor_id,),
            )
            professor = cursor.fetchone()
            if not professor:
                raise ValueError("Professor record was not found.")

            if normalized_decision == "VERIFIED":
                clean_institution = institution_name.strip()
                clean_title = faculty_title.strip()
                clean_url = source_url.strip()
                if not all((clean_institution, clean_title, clean_url)):
                    raise ValueError(
                        "Institution, faculty title, and official source are required."
                    )
                cursor.execute(
                    """
                    UPDATE professors
                    SET institution_name = %s, faculty_title = %s,
                        faculty_source_url = %s, homepage_url = COALESCE(homepage_url, %s),
                        faculty_status = 'VERIFIED', faculty_confidence = 1,
                        faculty_verification_method = 'manual_review',
                        faculty_verification_version = %s,
                        faculty_checked_at = NOW(), faculty_verified_at = NOW(),
                        next_identity_check_at = NOW() + INTERVAL '90 days',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        clean_institution,
                        clean_title,
                        clean_url,
                        clean_url,
                        FACULTY_VERIFICATION_VERSION,
                        professor_id,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO faculty_verification_evidence (
                        professor_id, source_url, source_domain, observed_title,
                        observed_institution, evidence_text, verification_status,
                        confidence, checked_at
                    ) VALUES (
                        %s, %s, split_part(%s, '/', 3), %s, %s,
                        'Confirmed by the site owner.', 'VERIFIED', 1, NOW()
                    )
                    ON CONFLICT (professor_id, source_url) DO UPDATE
                    SET observed_title = EXCLUDED.observed_title,
                        observed_institution = EXCLUDED.observed_institution,
                        evidence_text = EXCLUDED.evidence_text,
                        verification_status = 'VERIFIED', confidence = 1,
                        checked_at = NOW()
                    """,
                    (
                        professor_id,
                        clean_url,
                        clean_url,
                        clean_title,
                        clean_institution,
                    ),
                )
            elif normalized_decision == "NOT_FACULTY":
                cursor.execute(
                    """
                    UPDATE professors
                    SET faculty_status = 'NOT_FACULTY', faculty_confidence = 1,
                        faculty_verification_method = 'manual_review',
                        faculty_verification_version = %s,
                        faculty_checked_at = NOW(), faculty_verified_at = NULL,
                        next_identity_check_at = NOW() + INTERVAL '75 days',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (FACULTY_VERIFICATION_VERSION, professor_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE professors
                    SET faculty_status = 'UNVERIFIED', faculty_confidence = 0,
                        faculty_verification_method = NULL,
                        next_identity_check_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                    """,
                    (professor_id,),
                )

            cursor.execute(
                """
                UPDATE radar_topics topic
                SET verified_count = (
                        SELECT COUNT(*)
                        FROM radar_topic_professors rtp
                        JOIN professors candidate ON candidate.id = rtp.professor_id
                        WHERE rtp.radar_topic_id = topic.id
                          AND rtp.is_current_match = TRUE
                          AND candidate.faculty_status = 'VERIFIED'
                          AND (
                              candidate.faculty_verification_method = 'manual_review'
                              OR candidate.faculty_verification_version >= %s
                          )
                    ),
                    updated_at = NOW()
                WHERE EXISTS (
                    SELECT 1 FROM radar_topic_professors matched
                    WHERE matched.radar_topic_id = topic.id
                      AND matched.professor_id = %s
                      AND matched.is_current_match = TRUE
                )
                """,
                (MIN_PUBLIC_FACULTY_VERIFICATION_VERSION, professor_id),
            )
            cursor.execute(
                """
                INSERT INTO admin_audit_log (
                    actor_user_id, action, target_type, target_id, notes
                ) VALUES (%s, %s, 'professor_identity', %s, %s)
                """,
                (
                    owner_user_id,
                    f"identity_{normalized_decision.casefold()}",
                    professor_id,
                    f"Manual identity decision for {professor['name']}",
                ),
            )

    if normalized_decision == "RETRY":
        enqueue_radar_job(
            "REFRESH_FACULTY",
            professor_id=professor_id,
            requested_by=owner_user_id,
            priority=100,
            max_attempts=5,
        )


def cancel_radar_job(owner_user_id: int, job_id: int) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = 'cancelled', completed_at = NOW(),
                    locked_at = NULL, locked_by = NULL, updated_at = NOW()
                WHERE id = %s AND status IN ('queued', 'failed')
                """,
                (job_id,),
            )
