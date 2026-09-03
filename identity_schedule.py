"""One minimum reuse rule for completed identity decisions (not search failures)."""
from datetime import datetime, timedelta, timezone


def recently_checked(
    candidate: dict,
    now: datetime | None = None,
    *,
    max_age_days: int = 30,
) -> bool:
    checked = candidate.get("faculty_checked_at")
    if checked is None:
        return False
    now = now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked > now - timedelta(days=max(1, int(max_age_days)))


def minimum_recheck_sql(alias: str = "p") -> str:
    # Callers use fixed internal aliases, never user input.
    return f"({alias}.faculty_status IS DISTINCT FROM 'CONFLICT' AND ({alias}.faculty_checked_at IS NULL OR {alias}.faculty_checked_at <= NOW() - INTERVAL '30 days'))"


def direct_identity_sql(alias: str = "p") -> str:
    """Only untried concrete locators can work while general search is paused."""
    return f"""(NOT {alias}.identity_search_pending AND (
        NULLIF({alias}.faculty_source_url, '') IS NOT NULL
        OR NULLIF({alias}.homepage_url, '') IS NOT NULL
        OR NULLIF({alias}.orcid_id, '') IS NOT NULL
    ))"""
