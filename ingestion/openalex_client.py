from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from db import get_db_connection
from settings import setting_int


PROVIDER_NAME = "openalex"
_local_lock = threading.Lock()
_local_next_request_at = 0.0


class OpenAlexUnavailable(RuntimeError):
    """OpenAlex has asked the shared worker pool to pause temporarily."""

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(30, int(retry_after_seconds))


def _retry_after_seconds(response: requests.Response, fallback: int) -> int:
    value = str(response.headers.get("Retry-After") or "").strip()
    if value:
        try:
            return max(30, int(float(value)))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(
                    30,
                    int((target - datetime.now(timezone.utc)).total_seconds()),
                )
            except (TypeError, ValueError):
                pass
    return max(30, int(fallback))


def _reserve_local_slot(interval_seconds: float) -> float:
    global _local_next_request_at
    with _local_lock:
        now = time.monotonic()
        reserved = max(now, _local_next_request_at)
        _local_next_request_at = reserved + interval_seconds
        return max(0.0, reserved - now)


def _reserve_shared_slot(interval_seconds: float) -> float:
    """Reserve one OpenAlex request time across every worker process."""
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_search_provider_health (
                        provider_name, status, consecutive_failures, updated_at
                    ) VALUES (%s, 'healthy', 0, NOW())
                    ON CONFLICT (provider_name) DO NOTHING
                    """,
                    (PROVIDER_NAME,),
                )
                cursor.execute(
                    """
                    SELECT status, blocked_until, next_request_at,
                           GREATEST(
                               0,
                               EXTRACT(EPOCH FROM (blocked_until - NOW()))
                           ) AS blocked_seconds,
                           GREATEST(
                               0,
                               EXTRACT(EPOCH FROM (next_request_at - NOW()))
                           ) AS wait_seconds
                    FROM web_search_provider_health
                    WHERE provider_name = %s
                    FOR UPDATE
                    """,
                    (PROVIDER_NAME,),
                )
                row = cursor.fetchone() or {}
                blocked_seconds = int(float(row.get("blocked_seconds") or 0))
                if (
                    str(row.get("status") or "") == "blocked"
                    and row.get("blocked_until")
                    and blocked_seconds > 0
                ):
                    raise OpenAlexUnavailable(
                        "OpenAlex is temporarily rate-limited; discovery is safely paused.",
                        blocked_seconds,
                    )
                wait_seconds = max(0.0, float(row.get("wait_seconds") or 0))
                cursor.execute(
                    """
                    UPDATE web_search_provider_health
                    SET next_request_at = GREATEST(
                            NOW(), COALESCE(next_request_at, NOW())
                        ) + (%s * INTERVAL '1 second'),
                        updated_at = NOW()
                    WHERE provider_name = %s
                    """,
                    (interval_seconds, PROVIDER_NAME),
                )
                return wait_seconds
    except OpenAlexUnavailable:
        raise
    except Exception:
        # Tests and one-off maintenance commands may run before the schema is
        # installed. Keep local pacing as a safe fallback, while production
        # workers normally use the durable PostgreSQL reservation above.
        return _reserve_local_slot(interval_seconds)


def _record_failure(message: str, retry_after_seconds: int) -> None:
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_search_provider_health (
                        provider_name, status, consecutive_failures,
                        blocked_until, last_failure_at, last_error, updated_at
                    ) VALUES (
                        %s, 'blocked', 1,
                        NOW() + (%s * INTERVAL '1 second'), NOW(), %s, NOW()
                    )
                    ON CONFLICT (provider_name) DO UPDATE
                    SET status = 'blocked',
                        consecutive_failures =
                            web_search_provider_health.consecutive_failures + 1,
                        blocked_until = GREATEST(
                            COALESCE(web_search_provider_health.blocked_until, NOW()),
                            NOW() + (%s * INTERVAL '1 second')
                        ),
                        last_failure_at = NOW(),
                        last_error = EXCLUDED.last_error,
                        updated_at = NOW()
                    """,
                    (
                        PROVIDER_NAME,
                        retry_after_seconds,
                        message,
                        retry_after_seconds,
                    ),
                )
    except Exception:
        pass


def _record_success() -> None:
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE web_search_provider_health
                    SET status = CASE
                            WHEN blocked_until > NOW() THEN status
                            ELSE 'healthy'
                        END,
                        consecutive_failures = CASE
                            WHEN blocked_until > NOW() THEN consecutive_failures
                            ELSE 0
                        END,
                        blocked_until = CASE
                            WHEN blocked_until > NOW() THEN blocked_until
                            ELSE NULL
                        END,
                        last_success_at = NOW(),
                        last_error = CASE
                            WHEN blocked_until > NOW() THEN last_error
                            ELSE NULL
                        END,
                        updated_at = NOW()
                    WHERE provider_name = %s
                    """,
                    (PROVIDER_NAME,),
                )
    except Exception:
        pass


def openalex_get_json(
    url: str,
    *,
    params: dict[str, object],
    timeout: int = 20,
) -> dict[str, Any]:
    """Make one paced OpenAlex request without leaking query credentials."""
    interval_ms = setting_int("OPENALEX_MIN_INTERVAL_MS", 500, 100, 10_000)
    wait_seconds = _reserve_shared_slot(interval_ms / 1000)
    if wait_seconds:
        time.sleep(wait_seconds + random.uniform(0.02, 0.12))

    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as error:
        retry_after = setting_int(
            "OPENALEX_NETWORK_BACKOFF_SECONDS", 300, 30, 3600
        )
        message = f"OpenAlex network request failed ({type(error).__name__})."
        _record_failure(message, retry_after)
        raise OpenAlexUnavailable(message, retry_after) from error

    try:
        if response.status_code == 429:
            retry_after = _retry_after_seconds(
                response,
                setting_int(
                    "OPENALEX_RATE_LIMIT_BACKOFF_SECONDS", 3600, 60, 86_400
                ),
            )
            message = "OpenAlex returned HTTP 429; discovery is safely paused."
            _record_failure(message, retry_after)
            raise OpenAlexUnavailable(message, retry_after)
        if response.status_code >= 500:
            retry_after = setting_int(
                "OPENALEX_NETWORK_BACKOFF_SECONDS", 300, 30, 3600
            )
            message = f"OpenAlex returned HTTP {response.status_code}."
            _record_failure(message, retry_after)
            raise OpenAlexUnavailable(message, retry_after)
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAlex rejected a request with HTTP {response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as error:
            retry_after = setting_int(
                "OPENALEX_NETWORK_BACKOFF_SECONDS", 300, 30, 3600
            )
            message = "OpenAlex returned an invalid response."
            _record_failure(message, retry_after)
            raise OpenAlexUnavailable(message, retry_after) from error
    finally:
        response.close()

    _record_success()
    return dict(payload or {})
