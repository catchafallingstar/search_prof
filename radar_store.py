from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any

from db import _require_active_admin, get_db_connection
from settings import setting_int


FACULTY_VERIFICATION_VERSION = 3


def normalize_topic_query(query: str) -> str:
    clean = " ".join(re.findall(r"[a-z0-9]+", query.casefold()))
    if not 3 <= len(clean) <= 120:
        raise ValueError("Research area must contain between 3 and 120 characters.")
    return clean


def topic_key(query: str) -> str:
    return hashlib.sha256(normalize_topic_query(query).encode("utf-8")).hexdigest()


def ensure_radar_topic(
    query: str,
    desired_results: int = 100,
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
            if existing is None:
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
    dedupe = f"{job_type}:{radar_topic_id or '-'}:{professor_id or '-'}"
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
                return {**existing, "reused": True}
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


def save_topic_candidates(
    radar_topic_id: int,
    prospects: list[dict[str, Any]],
) -> None:
    if not prospects:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for rank, prospect in enumerate(prospects, start=1):
                cursor.execute(
                    """
                    INSERT INTO radar_topic_professors (
                        radar_topic_id, professor_id, result_rank, research_score,
                        matching_papers, latest_paper_title, latest_paper_year,
                        latest_paper_url
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                        latest_paper_title = CASE
                            WHEN EXCLUDED.latest_paper_year IS NULL THEN
                                radar_topic_professors.latest_paper_title
                            WHEN radar_topic_professors.latest_paper_year IS NULL
                                 OR EXCLUDED.latest_paper_year >= radar_topic_professors.latest_paper_year
                                THEN EXCLUDED.latest_paper_title
                            ELSE radar_topic_professors.latest_paper_title
                        END,
                        latest_paper_year = GREATEST(
                            radar_topic_professors.latest_paper_year,
                            EXCLUDED.latest_paper_year
                        ),
                        latest_paper_url = CASE
                            WHEN EXCLUDED.latest_paper_year IS NULL THEN
                                radar_topic_professors.latest_paper_url
                            WHEN radar_topic_professors.latest_paper_year IS NULL
                                 OR EXCLUDED.latest_paper_year >= radar_topic_professors.latest_paper_year
                                THEN EXCLUDED.latest_paper_url
                            ELSE radar_topic_professors.latest_paper_url
                        END,
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
                    candidates_seen = GREATEST(candidates_seen, %s),
                    papers_found = GREATEST(papers_found, %s),
                    last_error = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (normalized_topic, candidates_seen, papers_found, radar_topic_id),
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
                WHERE rtp.radar_topic_id = %s
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
                WHERE rtp.radar_topic_id = %s
                """,
                (
                    FACULTY_VERIFICATION_VERSION,
                    FACULTY_VERIFICATION_VERSION,
                    radar_topic_id,
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
    interval = "14 days" if source_kind == "hiring" else "30 days"
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT rtp.professor_id
                FROM radar_topic_professors rtp
                JOIN professors p ON p.id = rtp.professor_id
                WHERE rtp.radar_topic_id = %s
                  AND p.faculty_status = 'VERIFIED'
                  AND (
                      p.{timestamp_column} IS NULL
                      OR p.{timestamp_column} <= NOW() - INTERVAL '{interval}'
                  )
                ORDER BY p.{timestamp_column} NULLS FIRST, rtp.result_rank
                LIMIT %s
                """,
                (radar_topic_id, max(1, min(100, int(limit)))),
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
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    opportunity_filters = [
        "candidate.professor_id = p.id",
        "candidate.status = 'active'",
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
    professor_filters = ["rtp.radar_topic_id = %s", "p.faculty_status = 'VERIFIED'"]
    professor_params: list[Any] = [int(topic["id"])]
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
                    p.name AS professor_name, p.institution_name,
                    p.research_domain, p.homepage_url, p.openalex_id, p.career_stage,
                    p.faculty_status, p.faculty_title, p.faculty_source_url,
                    p.faculty_verified_at, p.official_institution_domain,
                    COALESCE(f.active_grants, 0) AS active_grants,
                    f.grant_title, f.funder, f.amount AS grant_amount,
                    f.expiration_date AS grant_expiration, f.source_url AS grant_url,
                    o.id AS opportunity_id, o.position_type, o.description,
                    os.source_url AS hiring_source_url,
                    os.evidence_text AS hiring_evidence,
                    os.confidence AS hiring_confidence,
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
                        WHEN o.id IS NOT NULL THEN 'public_hiring_signal'
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
                            WHEN o.id IS NOT NULL THEN 35
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
                WHERE {professor_where}
                  AND (
                      p.faculty_verification_method = 'manual_review'
                      OR p.faculty_verification_version >= %s
                  )
                ORDER BY
                    CASE
                        WHEN o.id IS NOT NULL
                             AND o.source_kind IN ('verified_post', 'university_post') THEN 1
                        WHEN o.id IS NOT NULL THEN 2
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
                    *professor_params,
                    FACULTY_VERIFICATION_VERSION,
                    limit,
                    offset,
                ],
            )
            return topic, list(cursor.fetchall())


def claim_next_radar_job(worker_id: str) -> dict[str, Any] | None:
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
                    ORDER BY priority DESC, available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE radar_jobs job
                SET status = 'running', attempts = attempts + 1,
                    locked_at = NOW(), locked_by = %s,
                    started_at = COALESCE(started_at, NOW()), updated_at = NOW()
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.*
                """,
                (worker_id,),
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
                SET status = 'queued', available_at = NOW() + (%s * INTERVAL '1 second'),
                    locked_at = NULL, locked_by = NULL, updated_at = NOW(),
                    result_json = %s::jsonb
                WHERE id = %s
                """,
                (max(0, int(delay_seconds)), json.dumps(result or {}), job_id),
            )


def fail_radar_job(job: dict[str, Any], error: Exception | str) -> None:
    attempts = int(job.get("attempts") or 1)
    max_attempts = int(job.get("max_attempts") or 5)
    retry = attempts < max_attempts
    delay = min(3600, 15 * (2 ** max(0, attempts - 1)))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_jobs
                SET status = %s,
                    available_at = CASE WHEN %s THEN NOW() + (%s * INTERVAL '1 second') ELSE available_at END,
                    completed_at = CASE WHEN %s THEN NULL ELSE NOW() END,
                    locked_at = NULL, locked_by = NULL,
                    last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (
                    "queued" if retry else "failed",
                    retry,
                    delay,
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
                WHERE next_refresh_at IS NOT NULL AND next_refresh_at <= NOW()
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


def list_radar_operations(admin_user_id: int, limit: int = 100) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id)
            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM radar_jobs GROUP BY status ORDER BY status
                """
            )
            job_counts = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT job.id, job.job_type, job.status, job.priority,
                       job.attempts, job.max_attempts, job.available_at,
                       job.started_at, job.completed_at, job.last_error,
                       topic.requested_query, p.name AS professor_name,
                       job.created_at
                FROM radar_jobs job
                LEFT JOIN radar_topics topic ON topic.id = job.radar_topic_id
                LEFT JOIN professors p ON p.id = job.professor_id
                ORDER BY
                    CASE job.status WHEN 'running' THEN 1 WHEN 'queued' THEN 2
                         WHEN 'failed' THEN 3 ELSE 4 END,
                    job.priority DESC, job.created_at DESC
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            jobs = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT id, requested_query, status, desired_results,
                       candidates_seen, verified_count, papers_found,
                       sources_exhausted, search_count, last_error,
                       last_requested_at, last_indexed_at, next_refresh_at
                FROM radar_topics
                ORDER BY last_requested_at DESC
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            topics = list(cursor.fetchall())
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
                SELECT id, name, institution_name, faculty_status,
                       faculty_checked_at, next_identity_check_at,
                       faculty_source_url
                FROM professors
                WHERE faculty_status IN ('MANUAL_REVIEW', 'CONFLICT')
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (max(1, min(250, int(limit))),),
            )
            identity_review = list(cursor.fetchall())
    return {
        "job_counts": job_counts,
        "jobs": jobs,
        "topics": topics,
        "workers": workers,
        "identity_review": identity_review,
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
