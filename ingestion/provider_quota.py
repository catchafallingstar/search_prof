"""Provider-reported limits supplement, never replace, local budget ceilings."""
import math

import requests

from db import get_db_connection
from ingestion.search_budget import SearchBudgetWait
from settings import setting, setting_bool


def tavily_remaining(payload: dict, allow_paid: bool = False) -> int:
    account = payload.get("account") or {}
    key = payload.get("key") or {}
    if not all(isinstance(account.get(k), (float, int)) for k in ("plan_limit", "plan_usage")):
        raise ValueError("Tavily usage response is missing account limits")
    remaining = max(0, int(account["plan_limit"] - account["plan_usage"]))
    if allow_paid:
        remaining += max(0, int(account.get("paygo_limit") or 0) - int(account.get("paygo_usage") or 0))
    if isinstance(key.get("limit"), (float, int)) and isinstance(key.get("usage"), (float, int)):
        remaining = min(remaining, max(0, int(key["limit"] - key["usage"])))
    return remaining


def save_remote_quota(provider: str, remaining: int | None, seconds: int) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO web_search_provider_health
                (provider_name, remote_remaining, remote_checked_at, remote_reset_at)
                VALUES (%s, %s, NOW(), NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (provider_name) DO UPDATE SET remote_remaining = EXCLUDED.remote_remaining,
                    remote_checked_at = NOW(), remote_reset_at = EXCLUDED.remote_reset_at""",
                (provider, remaining, max(1, seconds)))


def check_tavily_quota() -> None:
    _check_remote_quota('tavily', 'https://api.tavily.com/usage', 'TAVILY_API_KEY',
        lambda payload: tavily_remaining(payload, setting_bool('TAVILY_ALLOW_PAID_USAGE', False)), 72830102)


def searchapi_remaining(payload: dict) -> int:
    """Use reported credits, not monthly_allowance (free accounts can report 0)."""
    remaining = payload.get('account', {}).get('remaining_credits')
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining):
        raise ValueError('SearchAPI account response is missing remaining credits')
    remaining = max(0, int(remaining))
    usage = payload.get('api_usage') or {}
    hourly, used = usage.get('hourly_rate_limit'), usage.get('searches_this_hour')
    if isinstance(hourly, (int, float)) and isinstance(used, (int, float)) and hourly > 0:
        remaining = min(remaining, max(0, int(hourly - used)))
    return remaining


def check_searchapi_quota() -> None:
    _check_remote_quota('searchapi', 'https://www.searchapi.io/api/v1/me',
                       'SEARCHAPI_API_KEY', searchapi_remaining, 72830103)


def _check_remote_quota(provider, url, key_name, parse_remaining, lock_id) -> None:
    """Cache GET /usage for five minutes; unknown quota fails closed to fallback."""
    with get_db_connection() as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_id,))
            if not cursor.fetchone()["acquired"]:
                raise SearchBudgetWait(f"{provider} quota check in progress", 10)
            try:
                cursor.execute("""SELECT remote_remaining, remote_checked_at, remote_reset_at, last_error,
                    clock_timestamp() AS now FROM web_search_provider_health WHERE provider_name = %s""", (provider,))
                row = cursor.fetchone()
                if row and row.get("remote_reset_at") and row["remote_reset_at"] > row["now"]:
                    if row["remote_remaining"] is not None and row["remote_remaining"] > 0:
                        return
                    delay = math.ceil((row["remote_reset_at"] - row["now"]).total_seconds())
                    reason = (row.get('last_error') if row['remote_remaining'] is None else None)
                    raise SearchBudgetWait(reason or f"{provider} quota exhausted or unavailable; using fallback", delay)
                try:
                    response = requests.get(url,
                        headers={"Authorization": f"Bearer {setting(key_name).strip()}"}, timeout=8)
                    response.raise_for_status()
                    remaining = parse_remaining(response.json())
                except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
                    status = getattr(getattr(error, 'response', None), 'status_code', None)
                    if status in (401, 403):
                        reason = f"{provider} authentication/access failed (HTTP {status}); check {key_name}"
                    elif status == 429:
                        reason = f"{provider} account endpoint rate-limited (HTTP 429); retry later"
                    elif status:
                        reason = f"{provider} account endpoint failed (HTTP {status}); using fallback"
                    elif isinstance(error, requests.RequestException):
                        reason = f"{provider} account connection failed ({type(error).__name__}); using fallback"
                    else:
                        reason = f"{provider} account response could not be parsed; using fallback"
                    save_remote_quota(provider, None, 300)
                    cursor.execute("UPDATE web_search_provider_health SET last_error = %s WHERE provider_name = %s", (reason, provider))
                    raise SearchBudgetWait(reason, 300) from error
                save_remote_quota(provider, remaining, 300)
                if remaining <= 0:
                    raise SearchBudgetWait(f"{provider} credits or hourly allowance exhausted; using fallback", 300)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))


def brave_quota(headers) -> tuple[int, int] | None:
    """Use corresponding limit/remaining/reset windows; zero limit is unlimited."""
    try:
        remaining = [int(v.strip()) for v in headers.get("X-RateLimit-Remaining", "").split(",")]
        resets = [int(v.strip()) for v in headers.get("X-RateLimit-Reset", "").split(",")]
        limits = [int(v.strip()) for v in headers.get("X-RateLimit-Limit", "").split(",")]
    except (ValueError, TypeError, AttributeError):
        return None
    if not (len(remaining) == len(resets) == len(limits)):
        return None
    windows = [(left, max(1, reset)) for left, reset, limit in zip(remaining, resets, limits) if limit > 0]
    exhausted = [window for window in windows if window[0] <= 0]
    return max(exhausted or windows, key=lambda pair: pair[1]) if windows else None
