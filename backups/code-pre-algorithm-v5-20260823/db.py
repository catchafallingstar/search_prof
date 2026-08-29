import hashlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from settings import setting, setting_int


def database_url() -> str:
    value = setting("DATABASE_URL").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured.")
    return value


@contextmanager
def get_db_connection() -> Iterator[psycopg.Connection]:
    """Return a short-lived PostgreSQL connection with safe commit/rollback."""
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5) as connection:
        yield connection


def database_is_configured() -> bool:
    return bool(setting("DATABASE_URL").strip())


def database_is_ready() -> bool:
    if not database_is_configured():
        return False
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_regclass('public.opportunities') AS opportunities,
                        to_regclass('public.users') AS users,
                        to_regclass('public.site_admins') AS site_admins,
                        to_regclass('public.admin_audit_log') AS admin_audit_log
                    """
                )
                row = cursor.fetchone()
                return bool(row and all(row.values()))
    except Exception:
        return False


def _with_effective_verification_status(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Treat a past verification expiry as expired even before a maintenance job runs."""
    if not row:
        return None
    expires_at = row.get("verification_expires_at")
    if (
        row.get("verification_status") == "verified"
        and expires_at
        and expires_at <= datetime.now(timezone.utc)
    ):
        return {**row, "verification_status": "expired"}
    return row


def fetch_active_opportunities(
    research_area: str = "",
    position_type: str = "All",
    gpa_policy: str = "All",
) -> list[dict[str, Any]]:
    where = [
        "o.status = 'active'",
        "o.source_kind IN ('verified_post', 'university_post')",
        "(o.expires_at IS NULL OR o.expires_at > NOW())",
    ]
    params: list[Any] = []

    if research_area.strip():
        where.append("(o.research_area ILIKE %s OR o.title ILIKE %s OR o.description ILIKE %s)")
        term = f"%{research_area.strip()}%"
        params.extend([term, term, term])
    if position_type != "All":
        where.append("o.position_type = %s")
        params.append(position_type)
    if gpa_policy != "All":
        where.append("o.gpa_policy = %s")
        params.append(gpa_policy)

    query = f"""
        SELECT
            o.id, o.title, o.institution_name, o.professor_name, o.research_area,
            o.position_type, o.description, o.funding_status, o.gpa_policy,
            o.international_eligible, o.start_term, o.application_deadline,
            o.application_url, o.source_kind, o.published_at, o.expires_at,
            o.organic_score,
            COALESCE(pp.verification_status, im.verification_status, 'unclaimed') AS verification_status,
            EXISTS (
                SELECT 1 FROM sponsorships s
                WHERE s.opportunity_id = o.id AND s.status = 'active'
                  AND NOW() BETWEEN s.starts_at AND s.ends_at
            ) AS sponsored,
            src.source_url, src.evidence_text, src.last_checked_at
        FROM opportunities o
        LEFT JOIN professors p ON p.id = o.professor_id
        LEFT JOIN professor_profiles pp ON pp.professor_id = p.id
        LEFT JOIN institution_memberships im ON im.user_id = o.submitted_by
        LEFT JOIN LATERAL (
            SELECT os.source_url, os.evidence_text, os.last_checked_at
            FROM opportunity_sources os
            WHERE os.opportunity_id = o.id
            ORDER BY os.observed_at DESC
            LIMIT 1
        ) src ON TRUE
        WHERE {' AND '.join(where)}
        ORDER BY o.organic_score DESC, sponsored DESC,
                 o.published_at DESC NULLS LAST, o.created_at DESC
        LIMIT 100
    """
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())


def count_active_opportunities() -> int:
    """Return the total public, unexpired index size without applying search filters."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM opportunities
                WHERE status = 'active'
                  AND source_kind IN ('verified_post', 'university_post')
                  AND (expires_at IS NULL OR expires_at > NOW())
                """
            )
            return int(cursor.fetchone()["count"])


def _radar_query_key(query: str, target_professors: int, web_check_limit: int) -> str:
    normalized = " ".join(re.findall(r"[a-z0-9]+", query.casefold()))
    identity = f"{normalized}|professors={target_professors}|web={web_check_limit}|v=7-deep-pool"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def start_or_reuse_radar_run(
    query: str,
    target_professors: int,
    web_check_limit: int,
    requested_by: int | None = None,
    force_new: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Return a cached/running scan, or atomically create a new targeted scan."""
    clean_query = " ".join(query.split())
    if not 3 <= len(clean_query) <= 120:
        raise ValueError("Research area must contain between 3 and 120 characters.")
    target_professors = int(target_professors)
    if target_professors not in {10, 25, 50, 100}:
        raise ValueError("Professor result count must be 10, 25, 50, or 100.")
    web_check_limit = max(1, min(25, int(web_check_limit)))
    cache_hours = setting_int("PUBLIC_RADAR_CACHE_HOURS", 24, 1, 168)
    hourly_limit = setting_int("PUBLIC_RADAR_HOURLY_LIMIT", 30, 1, 500)
    query_key = _radar_query_key(clean_query, target_professors, web_check_limit)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (query_key,))
            cursor.execute(
                """
                UPDATE radar_runs
                SET status = 'failed', stage = 'Interrupted', completed_at = NOW(),
                    error_message = 'The previous worker stopped before completion.'
                WHERE status = 'running'
                  AND heartbeat_at < NOW() - INTERVAL '2 minutes'
                """
            )
            cursor.execute(
                """
                SELECT * FROM radar_runs
                WHERE query_key = %s
                  AND (
                    (status = 'completed' AND completed_at > NOW() - (%s * INTERVAL '1 hour'))
                    OR (status = 'running' AND heartbeat_at > NOW() - INTERVAL '2 minutes')
                  )
                ORDER BY created_at DESC LIMIT 1
                """,
                (query_key, cache_hours),
            )
            reusable = cursor.fetchone()
            if reusable and not force_new:
                return reusable, True
            cursor.execute(
                "SELECT COUNT(*) AS count FROM radar_runs WHERE created_at > NOW() - INTERVAL '1 hour'"
            )
            if int(cursor.fetchone()["count"]) >= hourly_limit:
                raise RuntimeError("The public radar is busy. Please try again later.")
            cursor.execute(
                """
                INSERT INTO radar_runs (
                    query_key, requested_query, requested_by, max_papers,
                    target_professors, web_check_limit
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    query_key, clean_query, requested_by, target_professors,
                    target_professors, web_check_limit,
                ),
            )
            return cursor.fetchone(), False


def save_radar_prospects(run_id: int, prospects: list[dict[str, Any]]) -> None:
    """Persist every research-matched professor, not only hiring-signal hits."""
    if not prospects:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for rank, prospect in enumerate(prospects, start=1):
                cursor.execute(
                    """
                    INSERT INTO radar_run_professors (
                        radar_run_id, professor_id, result_rank, research_score,
                        matching_papers, latest_paper_title, latest_paper_year,
                        latest_paper_url
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (radar_run_id, professor_id) DO UPDATE
                    SET result_rank = EXCLUDED.result_rank,
                        research_score = EXCLUDED.research_score,
                        matching_papers = EXCLUDED.matching_papers,
                        latest_paper_title = EXCLUDED.latest_paper_title,
                        latest_paper_year = EXCLUDED.latest_paper_year,
                        latest_paper_url = EXCLUDED.latest_paper_url
                    """,
                    (
                        run_id,
                        prospect["professor_id"],
                        rank,
                        prospect["research_score"],
                        prospect["matching_papers"],
                        prospect.get("latest_paper_title"),
                        prospect.get("latest_paper_year"),
                        prospect.get("latest_paper_url"),
                    ),
                )


def mark_radar_prospects_checked(
    run_id: int,
    professor_ids: list[int],
    source_kind: str,
) -> None:
    column = {
        "grants": "grant_sources_checked",
        "public": "public_sources_checked",
    }.get(source_kind)
    if not column:
        raise ValueError("Unknown radar source kind.")
    if not professor_ids:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE radar_run_professors SET {column} = TRUE
                WHERE radar_run_id = %s AND professor_id = ANY(%s)
                """,
                (run_id, professor_ids),
            )
            timestamp_column = {
                "grants": "grant_checked_at",
                "public": "public_hiring_checked_at",
            }[source_kind]
            cursor.execute(
                f"""
                UPDATE professors SET {timestamp_column} = NOW(), updated_at = NOW()
                WHERE id = ANY(%s)
                """,
                (professor_ids,),
            )


def prioritize_professors_for_enrichment(professor_ids: list[int]) -> list[int]:
    """Put never/stale-checked professors first while preserving research rank."""
    if not professor_ids:
        return []
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM professors
                WHERE id = ANY(%s)
                ORDER BY
                    CASE
                        WHEN public_hiring_checked_at IS NULL OR grant_checked_at IS NULL THEN 0
                        ELSE 1
                    END,
                    LEAST(public_hiring_checked_at, grant_checked_at) NULLS FIRST,
                    array_position(%s::bigint[], id)
                """,
                (professor_ids, professor_ids),
            )
            return [int(row["id"]) for row in cursor.fetchall()]


def update_radar_run(run_id: int, stage: str, progress: int, **counters: int) -> None:
    allowed = {
        "professors_found", "papers_found", "candidates_ranked",
        "faculty_identities_checked", "professors_checked",
        "grants_added", "signals_added",
    }
    assignments = ["stage = %s", "progress = %s", "heartbeat_at = NOW()"]
    params: list[Any] = [stage[:160], max(0, min(100, int(progress)))]
    for key, value in counters.items():
        if key in allowed:
            assignments.append(f"{key} = %s")
            params.append(max(0, int(value)))
    params.append(run_id)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE radar_runs SET {', '.join(assignments)} WHERE id = %s",
                params,
            )


def finish_radar_run(
    run_id: int,
    normalized_topic: str,
    counters: dict[str, int],
) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_runs
                SET normalized_topic = %s, status = 'completed', stage = 'Complete',
                    progress = 100, professors_found = %s, papers_found = %s,
                    candidates_ranked = %s, faculty_identities_checked = %s,
                    professors_checked = %s, grants_added = %s, signals_added = %s,
                    heartbeat_at = NOW(), completed_at = NOW(), error_message = NULL
                WHERE id = %s
                """,
                (
                    normalized_topic,
                    counters.get("professors_found", 0), counters.get("papers_found", 0),
                    counters.get("candidates_ranked", 0),
                    counters.get("faculty_identities_checked", 0),
                    counters.get("professors_checked", 0), counters.get("grants_added", 0),
                    counters.get("signals_added", 0), run_id,
                ),
            )


def fail_radar_run(run_id: int, error: str) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE radar_runs SET status = 'failed', stage = 'Failed',
                    error_message = %s, heartbeat_at = NOW(), completed_at = NOW()
                WHERE id = %s
                """,
                (str(error)[:1000], run_id),
            )


def fetch_radar_run(run_id: int) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM radar_runs WHERE id = %s", (run_id,))
            return cursor.fetchone()


def fetch_radar_results(run_id: int, position_type: str = "All") -> list[dict[str, Any]]:
    params: list[Any] = [run_id]
    position_filter = ""
    if position_type != "All":
        position_filter = "AND o.position_type = %s"
        params.append(position_type)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT o.id, o.title, o.professor_name, o.institution_name,
                       o.research_area, o.position_type, o.description,
                       o.application_url, o.gpa_policy, o.funding_status,
                       o.status, o.source_kind, o.organic_score,
                       os.source_url, os.evidence_text, os.confidence,
                       os.last_checked_at
                FROM radar_run_results rr
                JOIN opportunities o ON o.id = rr.opportunity_id
                LEFT JOIN LATERAL (
                    SELECT source_url, evidence_text, confidence, last_checked_at
                    FROM opportunity_sources
                    WHERE opportunity_id = o.id
                    ORDER BY observed_at DESC LIMIT 1
                ) os ON TRUE
                WHERE rr.radar_run_id = %s
                  AND o.status <> 'rejected'
                  {position_filter}
                ORDER BY o.organic_score DESC, o.created_at DESC
                """,
                params,
            )
            return list(cursor.fetchall())


def fetch_radar_prospects(run_id: int) -> list[dict[str, Any]]:
    """Return verified faculty in evidence-first, mutually exclusive categories."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    rrp.professor_id, rrp.result_rank, rrp.research_score,
                    rrp.matching_papers, rrp.latest_paper_title,
                    rrp.latest_paper_year, rrp.latest_paper_url,
                    rrp.grant_sources_checked, rrp.public_sources_checked,
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
                    COUNT(*) OVER (
                        PARTITION BY rrp.radar_run_id, rrp.latest_paper_url
                    ) AS shared_latest_paper_count,
                    CASE
                        WHEN o.id IS NOT NULL
                             AND o.status = 'active'
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
                        rrp.research_score
                        + CASE
                            WHEN o.id IS NOT NULL
                                 AND o.status = 'active'
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
                FROM radar_run_professors rrp
                JOIN radar_runs radar ON radar.id = rrp.radar_run_id
                JOIN professors p ON p.id = rrp.professor_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) OVER () AS active_grants, grant_title, funder,
                           amount, expiration_date, source_url
                    FROM fundings
                    WHERE professor_id = p.id
                      AND (expiration_date IS NULL OR expiration_date >= CURRENT_DATE)
                      AND COALESCE(radar.normalized_topic, radar.requested_query)
                          = ANY(research_domains)
                    ORDER BY expiration_date DESC NULLS LAST, created_at DESC
                    LIMIT 1
                ) f ON TRUE
                LEFT JOIN LATERAL (
                    SELECT candidate.*
                    FROM radar_run_results rr
                    JOIN opportunities candidate ON candidate.id = rr.opportunity_id
                    WHERE rr.radar_run_id = rrp.radar_run_id
                      AND candidate.professor_id = p.id
                      AND candidate.status <> 'rejected'
                    ORDER BY candidate.organic_score DESC, candidate.created_at DESC
                    LIMIT 1
                ) o ON TRUE
                LEFT JOIN LATERAL (
                    SELECT source_url, evidence_text, confidence
                    FROM opportunity_sources
                    WHERE opportunity_id = o.id
                    ORDER BY observed_at DESC LIMIT 1
                ) os ON TRUE
                WHERE rrp.radar_run_id = %s
                  AND p.faculty_status = 'VERIFIED'
                  AND (
                      p.faculty_verification_method = 'manual_review'
                      OR p.faculty_verification_version >= 4
                  )
                ORDER BY
                    CASE
                        WHEN o.id IS NOT NULL
                             AND o.status = 'active'
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
                    rrp.result_rank
                """,
                (run_id,),
            )
            return list(cursor.fetchall())


def list_recent_radar_runs(admin_user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id)
            cursor.execute(
                "SELECT * FROM radar_runs ORDER BY created_at DESC LIMIT %s",
                (max(1, min(100, limit)),),
            )
            return list(cursor.fetchall())


def upsert_authenticated_user(subject: str, email: str, display_name: str) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (oidc_subject, email, display_name)
                VALUES (%s, LOWER(%s), %s)
                ON CONFLICT (oidc_subject) DO UPDATE
                SET email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    updated_at = NOW()
                RETURNING *
                """,
                (subject, email, display_name),
            )
            return cursor.fetchone()


def get_site_admin(user_id: int) -> dict[str, Any] | None:
    """Return the active site-admin record for a user, if one exists."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT sa.user_id, sa.admin_role, sa.granted_by, sa.created_at,
                       u.email, u.display_name
                FROM site_admins sa
                JOIN users u ON u.id = sa.user_id
                WHERE sa.user_id = %s AND sa.revoked_at IS NULL
                """,
                (user_id,),
            )
            return cursor.fetchone()


def _require_active_admin(cursor: psycopg.Cursor, user_id: int, owner_only: bool = False) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT user_id, admin_role
        FROM site_admins
        WHERE user_id = %s AND revoked_at IS NULL
        """,
        (user_id,),
    )
    admin = cursor.fetchone()
    if not admin:
        raise PermissionError("An active site-administrator account is required.")
    if owner_only and admin["admin_role"] != "owner":
        raise PermissionError("Only the site owner can manage moderator accounts.")
    return admin


def bootstrap_site_owner(email: str) -> dict[str, Any]:
    """Create the first and only owner. Intended for the local bootstrap script."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email, display_name FROM users WHERE LOWER(email) = LOWER(%s)", (email.strip(),))
            user = cursor.fetchone()
            if not user:
                raise ValueError("The owner must sign in once before owner bootstrap can run.")
            cursor.execute(
                "SELECT user_id FROM site_admins WHERE admin_role = 'owner' AND revoked_at IS NULL"
            )
            existing_owner = cursor.fetchone()
            if existing_owner and existing_owner["user_id"] != user["id"]:
                raise PermissionError("An active owner already exists. Refusing to replace it.")
            cursor.execute(
                """
                INSERT INTO site_admins (user_id, admin_role, granted_by, revoked_at)
                VALUES (%s, 'owner', NULL, NULL)
                ON CONFLICT (user_id) DO UPDATE
                SET admin_role = 'owner', granted_by = NULL, revoked_at = NULL
                RETURNING user_id, admin_role, created_at
                """,
                (user["id"],),
            )
            owner = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO admin_audit_log (actor_user_id, action, target_type, target_id, notes)
                VALUES (%s, 'bootstrap_owner', 'user', %s, 'Initial owner bootstrap')
                """,
                (user["id"], user["id"]),
            )
            return {**owner, "email": user["email"], "display_name": user["display_name"]}


def list_users_for_admin(owner_user_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                SELECT u.id, u.display_name, u.email, u.account_role,
                       sa.admin_role, sa.created_at AS admin_since, sa.revoked_at
                FROM users u
                LEFT JOIN site_admins sa ON sa.user_id = u.id
                ORDER BY u.created_at DESC
                """
            )
            return list(cursor.fetchall())


def grant_site_moderator(owner_user_id: int, target_user_id: int) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute("SELECT id FROM users WHERE id = %s", (target_user_id,))
            if not cursor.fetchone():
                raise ValueError("Target user does not exist.")
            cursor.execute(
                "SELECT admin_role FROM site_admins WHERE user_id = %s AND revoked_at IS NULL",
                (target_user_id,),
            )
            current = cursor.fetchone()
            if current and current["admin_role"] == "owner":
                raise PermissionError("The owner cannot be converted into a moderator.")
            cursor.execute(
                """
                INSERT INTO site_admins (user_id, admin_role, granted_by, revoked_at)
                VALUES (%s, 'moderator', %s, NULL)
                ON CONFLICT (user_id) DO UPDATE
                SET admin_role = 'moderator', granted_by = EXCLUDED.granted_by, revoked_at = NULL,
                    created_at = NOW()
                """,
                (target_user_id, owner_user_id),
            )
            cursor.execute(
                """
                INSERT INTO admin_audit_log (actor_user_id, action, target_type, target_id, notes)
                VALUES (%s, 'grant_moderator', 'user', %s, 'Moderator access granted by owner')
                """,
                (owner_user_id, target_user_id),
            )


def revoke_site_moderator(owner_user_id: int, target_user_id: int) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, owner_user_id, owner_only=True)
            cursor.execute(
                """
                UPDATE site_admins
                SET revoked_at = NOW()
                WHERE user_id = %s AND admin_role = 'moderator' AND revoked_at IS NULL
                """,
                (target_user_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("That user is not an active moderator.")
            cursor.execute(
                """
                INSERT INTO admin_audit_log (actor_user_id, action, target_type, target_id, notes)
                VALUES (%s, 'revoke_moderator', 'user', %s, 'Moderator access revoked by owner')
                """,
                (owner_user_id, target_user_id),
            )


def fetch_admin_audit_log(admin_user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id)
            cursor.execute(
                """
                SELECT aal.id, aal.action, aal.target_type, aal.target_id,
                       aal.notes, aal.created_at,
                       u.display_name AS actor_name, u.email AS actor_email
                FROM admin_audit_log aal
                LEFT JOIN users u ON u.id = aal.actor_user_id
                ORDER BY aal.created_at DESC
                LIMIT %s
                """,
                (max(1, min(limit, 500)),),
            )
            return list(cursor.fetchall())


def submit_professor_profile(
    user_id: int,
    institution_name: str,
    title: str,
    department: str,
    official_profile_url: str,
) -> int:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO institutions (name)
                VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (institution_name.strip(),),
            )
            institution_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO professor_profiles (
                    owner_user_id, institution_id, title, department,
                    official_profile_url, verification_status
                ) VALUES (%s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (owner_user_id) DO UPDATE
                SET institution_id = EXCLUDED.institution_id,
                    title = EXCLUDED.title,
                    department = EXCLUDED.department,
                    official_profile_url = EXCLUDED.official_profile_url,
                    verification_status = 'pending',
                    updated_at = NOW()
                RETURNING id
                """,
                (user_id, institution_id, title.strip(), department.strip(), official_profile_url.strip()),
            )
            profile_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO role_verifications (
                    user_id, professor_profile_id, method, evidence_url, status
                ) VALUES (%s, %s, 'official_directory', %s, 'pending')
                """,
                (user_id, profile_id, official_profile_url.strip()),
            )
            return profile_id


def get_professor_profile(user_id: int) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.*, i.name AS institution_name
                FROM professor_profiles pp
                LEFT JOIN institutions i ON i.id = pp.institution_id
                WHERE pp.owner_user_id = %s
                """,
                (user_id,),
            )
            return _with_effective_verification_status(cursor.fetchone())


def submit_institution_membership(
    user_id: int,
    institution_name: str,
    title: str,
    department: str,
    official_profile_url: str,
) -> int:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO institutions (name)
                VALUES (%s)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (institution_name.strip(),),
            )
            institution_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO institution_memberships (
                    user_id, institution_id, title, department,
                    official_profile_url, verification_status
                ) VALUES (%s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (user_id) DO UPDATE
                SET institution_id = EXCLUDED.institution_id,
                    title = EXCLUDED.title,
                    department = EXCLUDED.department,
                    official_profile_url = EXCLUDED.official_profile_url,
                    verification_status = 'pending',
                    updated_at = NOW()
                RETURNING id
                """,
                (user_id, institution_id, title.strip(), department.strip(), official_profile_url.strip()),
            )
            membership_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO role_verifications (
                    user_id, institution_membership_id, method, evidence_url, status
                ) VALUES (%s, %s, 'institution_admin', %s, 'pending')
                """,
                (user_id, membership_id, official_profile_url.strip()),
            )
            return membership_id


def get_institution_membership(user_id: int) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT im.*, i.name AS institution_name
                FROM institution_memberships im
                JOIN institutions i ON i.id = im.institution_id
                WHERE im.user_id = %s
                """,
                (user_id,),
            )
            return _with_effective_verification_status(cursor.fetchone())


def submit_opportunity(user_id: int, values: dict[str, Any]) -> int:
    profile = get_professor_profile(user_id)
    membership = get_institution_membership(user_id)
    verified_profile = bool(profile and profile["verification_status"] == "verified")
    verified_membership = bool(membership and membership["verification_status"] == "verified")
    if not verified_profile and not verified_membership:
        raise PermissionError("A verified faculty or university role is required before submitting an opening.")

    owner = profile if verified_profile else membership
    source_kind = "verified_post" if verified_profile else "university_post"
    source_type = "professor_attestation" if verified_profile else "university_attestation"

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO opportunities (
                    professor_id, submitted_by, institution_id, title,
                    institution_name, professor_name, research_area, position_type,
                    description, funding_status, gpa_policy, international_eligible,
                    start_term, application_deadline, application_url,
                    source_kind, organic_score, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, 'pending'
                ) RETURNING id
                """,
                (
                    profile["professor_id"] if verified_profile else None,
                    user_id, owner["institution_id"],
                    values["title"].strip(), owner["institution_name"],
                    values["professor_name"].strip(), values["research_area"].strip(),
                    values["position_type"], values["description"].strip(),
                    values["funding_status"], values["gpa_policy"],
                    values["international_eligible"], values["start_term"].strip(),
                    values.get("application_deadline") or None,
                    values["application_url"].strip(),
                    source_kind,
                    100 if verified_profile else 95,
                ),
            )
            opportunity_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO opportunity_sources (
                    opportunity_id, source_type, source_url, evidence_text, confidence
                ) VALUES (%s, %s, %s, %s, 'high')
                """,
                (
                    opportunity_id,
                    source_type,
                    values["application_url"].strip(),
                    "Submitted by a role-verified faculty or university account.",
                ),
            )
            return opportunity_id


def fetch_pending_reviews(admin_user_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, admin_user_id)
            cursor.execute(
                """
                SELECT 'profile' AS review_type, pp.id, u.display_name, u.email,
                       i.name AS institution_name, pp.title AS subject, pp.department AS detail,
                       pp.official_profile_url AS evidence_url, pp.created_at
                FROM professor_profiles pp
                JOIN users u ON u.id = pp.owner_user_id
                LEFT JOIN institutions i ON i.id = pp.institution_id
                WHERE pp.verification_status = 'pending'
                UNION ALL
                SELECT 'membership' AS review_type, im.id, u.display_name, u.email,
                       i.name AS institution_name, im.title AS subject, im.department AS detail,
                       im.official_profile_url AS evidence_url, im.created_at
                FROM institution_memberships im
                JOIN users u ON u.id = im.user_id
                JOIN institutions i ON i.id = im.institution_id
                WHERE im.verification_status = 'pending'
                UNION ALL
                SELECT 'opportunity' AS review_type, o.id, u.display_name, u.email,
                       o.institution_name, o.title AS subject,
                       o.research_area || ' - ' || LEFT(o.description, 300) AS detail,
                       o.application_url AS evidence_url, o.created_at
                FROM opportunities o
                LEFT JOIN users u ON u.id = o.submitted_by
                WHERE o.status = 'pending'
                  AND o.source_kind IN ('verified_post', 'university_post')
                ORDER BY created_at ASC
                """
            )
            return list(cursor.fetchall())


def review_item(review_type: str, item_id: int, approve: bool, reviewer_id: int, notes: str) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            _require_active_admin(cursor, reviewer_id)
            if review_type == "profile":
                status = "verified" if approve else "rejected"
                professor_id = None
                if approve:
                    cursor.execute(
                        """
                        SELECT pp.owner_user_id, pp.institution_id, i.name AS institution_name,
                               u.display_name, pp.title, pp.official_profile_url
                        FROM professor_profiles pp
                        JOIN users u ON u.id = pp.owner_user_id
                        LEFT JOIN institutions i ON i.id = pp.institution_id
                        WHERE pp.id = %s
                        """,
                        (item_id,),
                    )
                    profile = cursor.fetchone()
                    if not profile:
                        raise ValueError("Professor profile was not found.")
                    cursor.execute(
                        """
                        INSERT INTO professors (
                            name, institution_id, institution_name, faculty_status,
                            faculty_title, faculty_source_url,
                            faculty_verification_method, faculty_verification_version,
                            faculty_confidence,
                            faculty_checked_at, faculty_verified_at,
                            next_identity_check_at, homepage_url
                        )
                        VALUES (%s, %s, %s, 'VERIFIED', %s, %s,
                                'manual_review', 3, 1.0, NOW(), NOW(),
                                NOW() + INTERVAL '1 year', %s)
                        ON CONFLICT (name, institution_name) DO UPDATE
                        SET institution_id = EXCLUDED.institution_id,
                            faculty_status = 'VERIFIED',
                            faculty_title = EXCLUDED.faculty_title,
                            faculty_source_url = EXCLUDED.faculty_source_url,
                            faculty_verification_method = 'manual_review',
                            faculty_verification_version = 4,
                            faculty_confidence = 1.0,
                            faculty_checked_at = NOW(),
                            faculty_verified_at = NOW(),
                            next_identity_check_at = NOW() + INTERVAL '1 year',
                            homepage_url = EXCLUDED.homepage_url,
                            updated_at = NOW()
                        RETURNING id
                        """,
                        (
                            profile["display_name"], profile["institution_id"],
                            profile["institution_name"], profile["title"],
                            profile["official_profile_url"], profile["official_profile_url"],
                        ),
                    )
                    professor_id = cursor.fetchone()["id"]
                    cursor.execute(
                        "UPDATE users SET account_role = 'professor', updated_at = NOW() WHERE id = %s",
                        (profile["owner_user_id"],),
                    )
                cursor.execute(
                    """
                    UPDATE professor_profiles
                    SET verification_status = %s,
                        professor_id = COALESCE(%s, professor_id),
                        verified_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                        verification_expires_at = CASE WHEN %s THEN NOW() + INTERVAL '1 year' ELSE NULL END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, professor_id, approve, approve, item_id),
                )
                cursor.execute(
                    """
                    UPDATE role_verifications
                    SET status = %s, reviewed_by = %s, reviewer_notes = %s, reviewed_at = NOW()
                    WHERE professor_profile_id = %s AND status = 'pending'
                    """,
                    (status, reviewer_id, notes.strip(), item_id),
                )
            elif review_type == "membership":
                status = "verified" if approve else "rejected"
                cursor.execute(
                    """
                    UPDATE institution_memberships
                    SET verification_status = %s,
                        verified_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                        verification_expires_at = CASE WHEN %s THEN NOW() + INTERVAL '1 year' ELSE NULL END,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING user_id
                    """,
                    (status, approve, approve, item_id),
                )
                membership = cursor.fetchone()
                if approve and membership:
                    cursor.execute(
                        "UPDATE users SET account_role = 'institution_admin', updated_at = NOW() WHERE id = %s",
                        (membership["user_id"],),
                    )
                cursor.execute(
                    """
                    UPDATE role_verifications
                    SET status = %s, reviewed_by = %s, reviewer_notes = %s, reviewed_at = NOW()
                    WHERE institution_membership_id = %s AND status = 'pending'
                    """,
                    (status, reviewer_id, notes.strip(), item_id),
                )
            elif review_type == "opportunity":
                status = "active" if approve else "rejected"
                cursor.execute(
                    """
                    UPDATE opportunities
                    SET status = %s,
                        published_at = CASE WHEN %s THEN NOW() ELSE published_at END,
                        expires_at = CASE WHEN %s THEN NOW() + INTERVAL '90 days' ELSE expires_at END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, approve, approve, item_id),
                )
            else:
                raise ValueError(f"Unknown review type: {review_type}")
            cursor.execute(
                """
                INSERT INTO admin_audit_log (actor_user_id, action, target_type, target_id, notes)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    reviewer_id,
                    "approve" if approve else "reject",
                    review_type,
                    item_id,
                    notes.strip(),
                ),
            )
