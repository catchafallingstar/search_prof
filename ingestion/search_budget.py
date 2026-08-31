"""Durable search reservations. Never sleep through a worker job deadline."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import math

from db import get_db_connection
from settings import setting_int


class SearchBudgetWait(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


def provider_limits(provider: str) -> tuple[float, int, int, int]:
    """Independent limits: one provider's quota cannot block another provider."""
    if provider == 'langsearch':
        # Published free-tier ceilings: 1/s, 60/min, 1000/day. Default below
        # those ceilings; count failed requests too. No perpetual 1000-use cap.
        return (
            setting_int('LANGSEARCH_MIN_INTERVAL_MS', 2000, 1000, 3600000) / 1000,
            setting_int('LANGSEARCH_DAILY_LIMIT', 900, 1, 1000),
            setting_int('LANGSEARCH_MONTHLY_LIMIT', 0, 0, 1000000),
            setting_int('LANGSEARCH_TOTAL_LIMIT', 0, 0, 1000000),
        )
    paid_api = provider in {"brave", "tavily", "searchapi", "parallel"}
    interval_default = 2000 if paid_api else setting_int("SEARCH_MIN_INTERVAL_MS", 60000, 1000, 3600000)
    daily_default = 200 if paid_api else setting_int("SEARCH_DAILY_LIMIT", 200, 1, 10000)
    return (
        setting_int(f"{provider.upper()}_MIN_INTERVAL_MS", interval_default, 1000, 3600000) / 1000,
        setting_int(f"{provider.upper()}_DAILY_LIMIT", daily_default, 1, 10000),
        setting_int(f"{provider.upper()}_MONTHLY_LIMIT", 1000 if paid_api else 0, 0, 1000000),
        setting_int(f"{provider.upper()}_TOTAL_LIMIT", 1000 if paid_api else 0, 0, 1000000),
    )


def reservation_delay(row, interval: int, daily_limit: int, monthly_limit: int = 0, total_limit: int = 0) -> tuple[int, str]:
    """Use PostgreSQL's clock/UTC day, not a worker's local timezone."""
    now = row["now"]
    if row.get("blocked_until") and row["blocked_until"] > now:
        return math.ceil((row["blocked_until"] - now).total_seconds()), "Provider cooldown"
    if row.get("remote_remaining") == 0 and row.get("remote_reset_at") and row["remote_reset_at"] > now:
        return math.ceil((row["remote_reset_at"] - now).total_seconds()), "Provider reports quota exhausted"
    if row.get("remote_remaining") is None and row.get("remote_checked_at") and row.get("remote_reset_at") and row["remote_reset_at"] > now:
        return math.ceil((row["remote_reset_at"] - now).total_seconds()), "Provider quota could not be checked"
    if total_limit and row.get("requests_total", 0) >= total_limit:
        return 86400, "Operator request cap reached; increase this provider's TOTAL_LIMIT to resume"
    if monthly_limit and row.get("usage_month") == row.get("utc_month") and row.get("requests_this_month", 0) >= monthly_limit:
        return math.ceil((row["next_utc_month"] - now).total_seconds()), "Monthly search budget reached"
    if row.get("usage_day") == row["utc_day"] and row["requests_today"] >= daily_limit:
        return math.ceil((row["next_utc_day"] - now).total_seconds()), "Daily search budget reached"
    if row.get("next_request_at") and row["next_request_at"] > now:
        return math.ceil((row["next_request_at"] - now).total_seconds()), "Waiting for the next search slot"
    return 0, ""


_CAPACITY_SQL = """SELECT *, clock_timestamp() AS now,
    (clock_timestamp() AT TIME ZONE 'UTC')::date AS utc_day,
    date_trunc('month', clock_timestamp() AT TIME ZONE 'UTC')::date AS utc_month,
    (date_trunc('day', clock_timestamp() AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC' AS next_utc_day,
    (date_trunc('month', clock_timestamp() AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC' AS next_utc_month
    FROM web_search_provider_health WHERE provider_name = %s"""


def provider_capacity(provider: str) -> dict:
    """Read-only scheduling hint; search_slot makes the authoritative reservation."""
    limits = provider_limits(provider)
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(_CAPACITY_SQL, (provider,))
                row = cursor.fetchone()
        delay, reason = reservation_delay(row, *limits) if row else (0, "")
        return {"retry_after_seconds": delay, "reason": reason,
                "requests_today": int(row.get("requests_today") or 0) if row and row.get("usage_day") == row.get("utc_day") else 0,
                "requests_total": int((row or {}).get("requests_total") or 0),
                "remote_remaining": (row or {}).get("remote_remaining"),
                "remote_checked_at": (row or {}).get("remote_checked_at")}
    except Exception:
        return {"retry_after_seconds": 60, "reason": "Search budget database unavailable"}


@contextmanager
def search_slot(provider: str):
    """One in-flight request per provider, across processes and threads.

    The session lock covers HTTP as well as reservation. A killed child releases
    the lock; its already committed timestamp/quota still survives the restart.
    Database failure is fail-closed, never permission for unpaced requests.
    """
    interval, daily_limit, monthly_limit, total_limit = provider_limits(provider)
    lock_key = int.from_bytes(hashlib.sha256(f"search:{provider}".encode()).digest()[:8], "big", signed=True)
    try:
        with get_db_connection() as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key,))
                if not cursor.fetchone()["acquired"]:
                    raise SearchBudgetWait("Another worker is using the search provider", 60)
                try:
                    with connection.transaction():
                        cursor.execute(
                            "INSERT INTO web_search_provider_health(provider_name) VALUES (%s) ON CONFLICT DO NOTHING",
                            (provider,),
                        )
                        cursor.execute(_CAPACITY_SQL + " FOR UPDATE", (provider,))
                        row = cursor.fetchone()
                        delay, reason = reservation_delay(row, interval, daily_limit, monthly_limit, total_limit)
                        if delay:
                            raise SearchBudgetWait(reason, delay)
                        used = row["requests_today"] if row.get("usage_day") == row["utc_day"] else 0
                        monthly_used = row["requests_this_month"] if row.get("usage_month") == row["utc_month"] else 0
                        cursor.execute(
                            """UPDATE web_search_provider_health
                               SET next_request_at = %s, usage_day = %s,
                                   requests_today = %s, usage_month = %s, requests_this_month = %s,
                                   requests_total = requests_total + 1,
                                   remote_remaining = CASE WHEN remote_remaining > 0 THEN remote_remaining - 1 ELSE remote_remaining END,
                                   updated_at = NOW()
                               WHERE provider_name = %s""",
                            (row["now"] + timedelta(seconds=interval), row["utc_day"], used + 1,
                             row["utc_month"], monthly_used + 1, provider),
                        )
                    yield
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    except SearchBudgetWait:
        raise
    except Exception as error:
        # Network errors raised by the caller must reach its circuit breaker.
        if isinstance(error, (OSError,)):
            raise
        raise
