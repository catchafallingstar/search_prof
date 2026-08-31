from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
import unicodedata
from contextlib import nullcontext
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from ddgs import DDGS
from psycopg.types.json import Jsonb

from db import get_db_connection
from ingestion.search_budget import SearchBudgetWait, search_slot, provider_capacity
from ingestion.provider_quota import check_tavily_quota, check_searchapi_quota, brave_quota, save_remote_quota
from settings import setting, setting_int, setting_bool

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SUPPORTED_PROVIDERS = ("langsearch", "searchapi", "parallel", "searxng", "brave", "tavily", "ddgs")

_state_lock = threading.Lock()
_provider_semaphores: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_provider_next_start: dict[str, float] = {}
_provider_blocked_until: dict[str, float] = {}


class SearchProviderUnavailable(RuntimeError):
    """Raised when a configured search provider cannot serve a request."""

    def __init__(self, message: str, retry_after_seconds: int = 900) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(30, int(retry_after_seconds))


class SearchUnavailable(RuntimeError):
    """All configured providers failed; the identity decision must be retried."""

    def __init__(self, message: str, retry_after_seconds: int = 900) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(30, int(retry_after_seconds))


def _searxng_url() -> str:
    configured = setting("SEARXNG_URL").strip()
    if configured:
        return configured
    if setting("APP_ENV").strip().casefold() == "development":
        return "http://127.0.0.1:8080"
    return ""


def _cache_enabled() -> bool:
    configured = setting("SEARCH_CACHE_ENABLED").strip()
    if configured:
        return configured.casefold() in {"1", "true", "yes", "on"}
    return setting("APP_ENV").strip().casefold() == "development"


def _provider_strategy() -> str:
    configured = setting("SEARCH_PROVIDER_STRATEGY").strip().casefold()
    if configured in {"fallback", "balanced", "parallel"}:
        return configured
    if setting("APP_ENV").strip().casefold() == "development":
        return "fallback"
    return "fallback"


def _normalize_results(
    items: list[dict[str, Any]], max_results: int
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        href = str(item.get("href") or item.get("url") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        normalized.append(
            {
                "href": href,
                "title": str(item.get("title") or "").strip(),
                "body": str(
                    item.get("body")
                    or item.get("content")
                    or item.get("description")
                    or ""
                ).strip(),
            }
        )
        if len(normalized) >= max_results:
            break
    return normalized


def _query_anchor_tokens(query: str) -> list[str]:
    """Return the first quoted identity phrase, usually a person's name."""
    quoted = re.findall(r'"([^"\n]{3,})"', query)
    if not quoted:
        return []
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", quoted[0].casefold())
        if not unicodedata.combining(character)
    )
    return [token for token in re.findall(r"[a-z0-9]+", folded) if len(token) > 1]


def _results_match_query_anchor(
    query: str, results: list[dict[str, str]]
) -> bool:
    """Reject obviously poisoned results before they enter the shared cache."""
    tokens = _query_anchor_tokens(query)
    if not tokens:
        return True
    for result in results:
        text = " ".join(
            str(result.get(key) or "") for key in ("title", "body", "href")
        )
        folded = "".join(
            character
            for character in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(character)
        )
        normalized = set(re.findall(r"[a-z0-9]+", folded))
        if all(token in normalized for token in tokens):
            return True
        # Retrieval-only normalization for split names such as "Vari Ny".
        # Match the entire ordered name with optional internal whitespace;
        # never edit letters or use this alone to verify the fetched identity.
        name_pattern = r'\b' + r'\s+'.join(r'\s*'.join(map(re.escape, token)) for token in tokens) + r'\b'
        if re.search(name_pattern, folded):
            return True
        target = re.search(r'\bsite:([a-z0-9.-]+)', query, re.I)
        if target:
            parsed = urlparse(str(result.get('href') or ''))
            host = (parsed.hostname or '').lower()
            domain = target.group(1).lower()
            if (host == domain or host.endswith('.' + domain)) and re.search(r'/[^?#]*(?:faculty|directory|people|staff)', parsed.path, re.I):
                return True  # Bounded directory locator; not a name/role decision.
    return False


def _searxng_search(
    query: str, max_results: int, base_url: str
) -> list[dict[str, str]]:
    params: dict[str, object] = {
        "q": query[:400],
        "format": "json",
        "language": setting("SEARCH_LANGUAGE").strip() or "en-US",
        "safesearch": 1,
    }
    engines = setting("SEARXNG_ENGINES").strip()
    if engines:
        params["engines"] = engines
    response = requests.get(
        f"{base_url.rstrip('/')}/search",
        params=params,
        headers={"User-Agent": "ScholarRadar/1.0 (faculty identity indexer)"},
        timeout=setting_int("SEARCH_PROVIDER_TIMEOUT_SECONDS", 8, 3, 30),
    )
    response.raise_for_status()
    payload = response.json()
    results = _normalize_results(list(payload.get("results") or []), max_results)
    unresponsive = list(payload.get("unresponsive_engines") or [])
    if not results and unresponsive:
        summary = ", ".join(
            f"{str(item[0])}: {str(item[1])}"
            for item in unresponsive[:6]
            if isinstance(item, (list, tuple)) and len(item) >= 2
        )
        raise RuntimeError(
            "SearXNG returned no results because upstream engines were unavailable"
            + (f" ({summary})" if summary else "")
        )
    return results


def _brave_search(
    query: str, max_results: int, api_key: str
) -> list[dict[str, str]]:
    response = requests.get(
        BRAVE_SEARCH_URL,
        params={
            "q": query[:400],
            "count": max_results,
            "country": "US",
            "search_lang": "en",
            "safesearch": "moderate",
        },
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "ScholarRadar/1.0 (public research-opportunity indexer)",
        },
        timeout=setting_int("SEARCH_PROVIDER_TIMEOUT_SECONDS", 8, 3, 30),
    )
    reported = brave_quota(response.headers)
    if reported is not None:
        save_remote_quota("brave", *reported)
    response.raise_for_status()
    return _normalize_results(
        list(response.json().get("web", {}).get("results", [])), max_results
    )


def _fallback_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Use DDGS without holding a process-wide lock during the network call."""
    proxy_url = setting("ROTATING_PROXY_URL")
    kwargs: dict[str, object] = {
        "timeout": setting_int("SEARCH_PROVIDER_TIMEOUT_SECONDS", 8, 3, 30)
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    try:
        items = list(DDGS(**kwargs).text(query, max_results=max_results, backend="duckduckgo"))
    except Exception as error:
        # DDGS raises an exception for some valid zero-result queries. A narrow
        # professor search returning nothing is not an upstream outage and must
        # not place the provider into a shared cooldown.
        if str(error).strip().casefold() in {"no results found", "no results found."}:
            return []
        raise
    return _normalize_results(items, max_results)


def _tavily_search(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
    # Basic is one credit; never let automatic parameters silently select advanced.
    response = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query[:400], "search_depth": "basic", "topic": "general",
              "max_results": max_results, "auto_parameters": False,
              "include_answer": False, "include_raw_content": False},
        timeout=setting_int("SEARCH_PROVIDER_TIMEOUT_SECONDS", 8, 3, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("results"), list):
        raise RuntimeError("Tavily response missing results; not a valid empty search")
    return _normalize_results(payload["results"], max_results)


def _searchapi_search(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
    """One SearchAPI.io Google request; never put credentials in URLs/logs."""
    response = requests.get(
        'https://www.searchapi.io/api/v1/search',
        params={'engine': 'google', 'q': query[:400], 'gl': 'us', 'hl': 'en'},
        headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
        timeout=setting_int('SEARCHAPI_TIMEOUT_SECONDS', 20, 3, 60),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('SearchAPI returned an invalid response')
    status = (payload.get('search_metadata') or {}).get('status')
    if payload.get('error') or payload.get('errors') or str(status).casefold() != 'success':
        # Do not log arbitrary provider text: it may contain request details.
        raise ValueError('SearchAPI did not report a successful search')
    items = payload.get('organic_results', [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('SearchAPI returned invalid organic results')
    return _normalize_results([
        {'href': item.get('link'), 'title': item.get('title'), 'body': item.get('snippet')}
        for item in items
    ], max_results)


def _langsearch_search(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
    """One bounded LangSearch request; returned summaries are not role proof."""
    response = requests.post(
        'https://api.langsearch.com/v1/web-search',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'query': query[:400], 'freshness': 'noLimit', 'summary': False,
              'count': max(1, min(10, max_results))},
        timeout=setting_int('LANGSEARCH_TIMEOUT_SECONDS', 20, 3, 60),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('LangSearch returned an invalid response')
    code = payload.get('code')
    # Some API failures are conveyed inside an HTTP-200 JSON envelope. Route
    # those through the existing auth/quota/Retry-After circuit breaker too.
    if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
        failure = requests.Response()
        failure.status_code = code
        failure.headers = response.headers
        raise requests.HTTPError(f'LangSearch API error (code {code})', response=failure)
    data = payload.get('data')
    pages = data.get('webPages') if isinstance(data, dict) else None
    items = pages.get('value') if isinstance(pages, dict) else None
    if (code != 200 or not isinstance(items, list) or
            any(not isinstance(item, dict) or not isinstance(item.get('url'), str)
                for item in items)):
        raise ValueError('LangSearch returned invalid search results; not a valid empty search')
    return _normalize_results([
        {'href': item['url'], 'title': item.get('name'), 'body': item.get('snippet')}
        for item in items
    ], max(1, min(10, max_results)))


def _parallel_search(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
    """Parallel.ai is a service, independent of the concurrency strategy name."""
    response = requests.post(
        'https://api.parallel.ai/v1/search',
        headers={'x-api-key': api_key, 'Content-Type': 'application/json'},
        json={'search_queries': [query[:400]], 'mode': 'fast', 'max_chars_total': 8000},
        timeout=setting_int('PARALLEL_TIMEOUT_SECONDS', 20, 3, 60),
    )
    response.raise_for_status()
    payload = response.json()
    if (not isinstance(payload, dict) or payload.get('error') or
            not isinstance(payload.get('results'), list)):
        raise ValueError('Parallel returned an invalid or unsuccessful search response')
    items = []
    for result in payload['results']:
        if not isinstance(result, dict):
            raise ValueError('Parallel returned invalid search results')
        excerpts = result.get('excerpts') or []
        if not isinstance(excerpts, list) or any(not isinstance(text, str) for text in excerpts):
            raise ValueError('Parallel returned invalid excerpts')
        items.append({'href': result.get('url'), 'title': result.get('title'),
                      'body': '\n'.join(excerpts)[:8000]})
    return _normalize_results(items, max_results)


def _provider_names() -> list[str]:
    configured = [
        value.strip().casefold()
        for value in setting("SEARCH_PROVIDERS").split(",")
        if value.strip()
    ]
    if not configured:
        # Preserve old installations until the operator explicitly enables the
        # provider pool. Never assume production SearXNG lives on localhost.
        if setting("LANGSEARCH_API_KEY").strip():
            configured.append("langsearch")
        if setting("SEARCHAPI_API_KEY").strip():
            configured.append("searchapi")
        if setting("PARALLEL_API_KEY").strip():
            configured.append("parallel")
        if setting("BRAVE_SEARCH_API_KEY").strip():
            configured.append("brave")
        if setting("TAVILY_API_KEY").strip():
            configured.append("tavily")
        if _searxng_url():
            configured.append("searxng")
        if (
            setting("APP_ENV").strip().casefold() == "development"
            or not configured
        ):
            configured.append("ddgs")

    available: list[str] = []
    for provider in configured:
        if provider not in SUPPORTED_PROVIDERS or provider in available:
            continue
        if provider == "langsearch" and not setting("LANGSEARCH_API_KEY").strip():
            continue
        if provider == "searchapi" and not setting("SEARCHAPI_API_KEY").strip():
            continue
        if provider == "parallel" and not setting("PARALLEL_API_KEY").strip():
            continue
        if provider == "searxng" and not _searxng_url():
            continue
        if provider == "brave" and not setting("BRAVE_SEARCH_API_KEY").strip():
            continue
        if provider == "brave" and not setting_bool("BRAVE_STORAGE_ALLOWED", False):
            continue  # This app persists search evidence; require suitable plan rights.
        if provider == "tavily" and not setting("TAVILY_API_KEY").strip():
            continue
        available.append(provider)
    return [provider for provider in available if provider != "ddgs"] + ["ddgs"]


def search_provider_runtime_state() -> dict[str, Any]:
    """Return capacity without sending a search request.

    Persistent cooldowns are shared by the worker's isolated job processes.
    Retrying is useful as soon as the earliest configured provider is due.
    """
    providers = _provider_names()
    capacity = {provider: provider_capacity(provider) for provider in providers}
    blocked = {provider: max(_persistent_block_remaining(provider), int(capacity[provider]["retry_after_seconds"]))
               for provider in providers}
    available = [provider for provider, seconds in blocked.items() if seconds <= 0]
    waits = [seconds for seconds in blocked.values() if seconds > 0]
    return {
        "providers": providers,
        "capacity": capacity,
        "available": available,
        "blocked": {
            provider: seconds for provider, seconds in blocked.items() if seconds > 0
        },
        "retry_after_seconds": min(waits) if waits else 0,
    }


def configured_search_providers() -> list[dict[str, str]]:
    """Return non-secret provider configuration for diagnostics."""
    strategy = _provider_strategy()
    return [
        {"provider": provider, "strategy": strategy}
        for provider in _provider_names()
    ]


def _provider_limit(provider: str) -> int:
    return setting_int(f"{provider.upper()}_MAX_CONCURRENCY", 1, 1, 8)


def _provider_semaphore(provider: str) -> threading.BoundedSemaphore:
    limit = _provider_limit(provider)
    key = (provider, limit)
    with _state_lock:
        return _provider_semaphores.setdefault(key, threading.BoundedSemaphore(limit))


def _pace(provider: str) -> None:
    minimum = setting_int("SEARCH_MIN_INTERVAL_MS", 60000, 1000, 3600000) / 1000
    with _state_lock:
        now = time.monotonic()
        start_at = max(now, _provider_next_start.get(provider, 0.0))
        _provider_next_start[provider] = start_at + minimum
    delay = start_at - time.monotonic()
    if delay > 0:
        time.sleep(delay + random.uniform(0, min(0.08, minimum / 4)))


def _persistent_block_remaining(provider: str) -> int:
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT GREATEST(
                        0,
                        CEIL(EXTRACT(EPOCH FROM (blocked_until - NOW())))
                    )::INTEGER AS remaining
                    FROM web_search_provider_health
                    WHERE provider_name = %s
                      AND status = 'blocked'
                      AND blocked_until > NOW()
                    """,
                    (provider,),
                )
                row = cursor.fetchone()
        return int((row or {}).get("remaining") or 0)
    except Exception:
        return 0


def _record_provider_success(provider: str) -> None:
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_search_provider_health (
                        provider_name, status, consecutive_failures,
                        blocked_until, last_success_at, last_error
                    ) VALUES (%s, 'healthy', 0, NULL, NOW(), NULL)
                    ON CONFLICT (provider_name) DO UPDATE SET
                        status = 'healthy',
                        consecutive_failures = 0,
                        blocked_until = NULL,
                        last_success_at = NOW(),
                        last_error = NULL,
                        updated_at = NOW()
                    """,
                    (provider,),
                )
    except Exception:
        pass


def _block_provider(provider: str, error: BaseException) -> int:
    seconds = setting_int("SEARCH_PROVIDER_BACKOFF_SECONDS", 3600, 30, 21600)
    response = getattr(error, "response", None)
    if response is None and isinstance(error, (requests.Timeout, requests.ConnectionError)):
        # A single transport failure does not prove exhausted credits or a block.
        seconds = setting_int("SEARCH_NETWORK_BACKOFF_SECONDS", 60, 30, 3600)
    restricted = any(word in str(error).casefold() for word in ("captcha", "ratelimit", "rate limit", "too many", "429", "403"))
    if restricted or (response is not None and getattr(response, "status_code", None) in {403, 429}):
        seconds = max(
            seconds,
            setting_int("SEARCH_RATE_LIMIT_BACKOFF_SECONDS", 3600, 300, 86400),
        )
    if response is not None:
        if getattr(response, "status_code", None) in {401, 402, 432, 433}:
            seconds = max(seconds, 86400)  # auth / credits exhausted: use another provider
        retry_after = str(response.headers.get("Retry-After", "")).strip()
        try:
            requested = int(retry_after) if retry_after.isdigit() else int(
                (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
            )
            seconds = max(seconds, requested)
        except (ValueError, TypeError, OverflowError):
            pass
    with _state_lock:
        _provider_blocked_until[provider] = time.monotonic() + seconds
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_search_provider_health (
                        provider_name, status, consecutive_failures,
                        blocked_until, last_failure_at, last_error
                    ) VALUES (
                        %s, 'blocked', 1,
                        NOW() + (%s * INTERVAL '1 second'), NOW(), %s
                    )
                    ON CONFLICT (provider_name) DO UPDATE SET
                        status = 'blocked',
                        consecutive_failures =
                            web_search_provider_health.consecutive_failures + 1,
                        blocked_until = GREATEST(
                            COALESCE(
                                web_search_provider_health.blocked_until, NOW()
                            ),
                            NOW() + (%s * INTERVAL '1 second')
                        ),
                        last_failure_at = NOW(),
                        last_error = EXCLUDED.last_error,
                        updated_at = NOW()
                    """,
                    (provider, seconds, str(error)[:1000], seconds),
                )
    except Exception:
        pass
    return seconds


def _assert_provider_available(provider: str) -> None:
    with _state_lock:
        memory_remaining = max(
            0,
            int(_provider_blocked_until.get(provider, 0.0) - time.monotonic()),
        )
    persistent_remaining = _persistent_block_remaining(provider)
    remaining = max(memory_remaining, persistent_remaining)
    if remaining > 0:
        raise SearchProviderUnavailable(
            f"{provider} is paused after an upstream error",
            retry_after_seconds=remaining,
        )


def _run_provider(
    provider: str, query: str, max_results: int
) -> list[dict[str, str]]:
    _assert_provider_available(provider)

    functions: dict[str, Callable[[], list[dict[str, str]]]] = {
        "langsearch": lambda: _langsearch_search(query, max_results, setting("LANGSEARCH_API_KEY").strip()),
        "searchapi": lambda: _searchapi_search(query, max_results, setting("SEARCHAPI_API_KEY").strip()),
        "parallel": lambda: _parallel_search(query, max_results, setting("PARALLEL_API_KEY").strip()),
        "searxng": lambda: _searxng_search(
            query, max_results, _searxng_url()
        ),
        "brave": lambda: _brave_search(
            query, max_results, setting("BRAVE_SEARCH_API_KEY").strip()
        ),
        "ddgs": lambda: _fallback_search(query, max_results),
        "tavily": lambda: _tavily_search(query, max_results, setting("TAVILY_API_KEY").strip()),
    }
    semaphore = _provider_semaphore(provider)
    if not semaphore.acquire(blocking=False):
        raise SearchProviderUnavailable(f"{provider} is serving another candidate", 2)
    try:
        try:
            # Another thread or job may have blocked this provider while this
            # request waited for its concurrency slot.
            _assert_provider_available(provider)
            if provider == "tavily":
                check_tavily_quota()
            elif provider == "searchapi":
                check_searchapi_quota()
            with search_slot(provider):
                try:
                    results = functions[provider]()
                except Exception as error:
                    # Persist the cooldown while the cross-process lock is held,
                    # before releasing the provider semaphore or search slot.
                    retry_after = _block_provider(provider, error)
                    if getattr(getattr(error, 'response', None), 'status_code', None) == 402:
                        raise SearchProviderUnavailable(
                            f"{provider} has insufficient available credits (HTTP 402); using fallback",
                            retry_after,
                        ) from error
                    raise SearchProviderUnavailable(
                        f"{provider} search failed: {error}", retry_after
                    ) from error
                with _state_lock:
                    _provider_blocked_until.pop(provider, None)
                _record_provider_success(provider)
        except SearchBudgetWait as error:
            # A reserved slot/budget is not an upstream error or failed query.
            raise SearchProviderUnavailable(str(error), error.retry_after_seconds) from error
        except SearchProviderUnavailable:
            raise
        except Exception as error:
            # Record the cooldown before releasing the provider semaphore.
            # Waiting threads will then stop at their second availability
            # check instead of sending another request during the race window.
            retry_after = _block_provider(provider, error)
            raise SearchProviderUnavailable(
                f"{provider} search failed: {error}",
                retry_after_seconds=retry_after,
            ) from error
        return results
    finally:
        semaphore.release()


def _cache_key(query: str, max_results: int) -> str:
    normalized = " ".join(query.casefold().split())
    # A newly enabled provider must not inherit a different provider's empty query.
    pool = ",".join(_provider_names()) + "|" + _provider_strategy()
    return hashlib.sha256(f"{pool}|{normalized}|{max_results}".encode("utf-8")).hexdigest()


def _read_cache(query: str, max_results: int) -> list[dict[str, str]] | None:
    if not _cache_enabled():
        return None
    try:
        normalized = " ".join(query.casefold().split())
        legacy_key = hashlib.sha256(f"{normalized}|{max_results}".encode("utf-8")).hexdigest()
        current_key = _cache_key(query, max_results)
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT results_json, provider_names
                    FROM web_search_cache
                    WHERE query_key = ANY(%s) AND expires_at > NOW()
                    ORDER BY (query_key = %s) DESC
                    """,
                    ([current_key, legacy_key], current_key),
                )
                rows = cursor.fetchall()
        for row in rows:
            value = row.get("results_json")
            if isinstance(value, str):
                value = json.loads(value)
            results = _normalize_results(list(value or []), max_results)
            if results and _results_match_query_anchor(query, results):
                return results  # Preserve usable pre-upgrade cached queries.
            if not results and set(_provider_names()).issubset(set(row.get("provider_names") or [])):
                return []  # A new provider still gets a chance on an old empty query.
        return None
    except Exception:
        # Search continues during first-time setup or a temporary DB outage.
        return None


def _write_cache(
    query: str,
    max_results: int,
    results: list[dict[str, str]],
    providers: list[str],
) -> None:
    if not _cache_enabled():
        return
    hours = setting_int("SEARCH_CACHE_HOURS", 168, 1, 720) if results else setting_int("SEARCH_EMPTY_CACHE_HOURS", 24, 1, 168)
    now = datetime.now(timezone.utc)
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO web_search_cache (
                        query_key, normalized_query, results_json,
                        provider_names, searched_at, expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (query_key) DO UPDATE SET
                        normalized_query = EXCLUDED.normalized_query,
                        results_json = EXCLUDED.results_json,
                        provider_names = EXCLUDED.provider_names,
                        searched_at = EXCLUDED.searched_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        _cache_key(query, max_results),
                        " ".join(query.split()),
                        Jsonb(results),
                        providers,
                        now,
                        now + timedelta(hours=hours),
                    ),
                )
    except Exception:
        return


def _merge_results(
    batches: list[list[dict[str, str]]], max_results: int
) -> list[dict[str, str]]:
    return _normalize_results(
        [result for batch in batches for result in batch], max_results
    )


def search_web(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Search configured providers with caching, pacing, fallback, and backoff.

    ``balanced`` distributes different queries across providers and falls back
    after an empty/error response. ``parallel`` asks a bounded number of
    providers at once. SearXNG may itself aggregate several engines.
    """
    max_results = max(1, min(20, int(max_results)))
    from ingestion.verification_audit import CURRENT, remaining_seconds, emit, record_retrieval
    identity_audit = CURRENT.get()
    cached = _read_cache(query, max_results)
    if cached is not None:
        if identity_audit is not None:
            useful = _results_match_query_anchor(query, cached)
            record_retrieval(query, 'cache', cached, 'RESULTS' if useful else 'NO_USEFUL_RESULTS')
            if not useful:
                return []
        return cached

    providers = _provider_names()
    from ingestion.verification_audit import CURRENT, remaining_seconds, emit
    identity_audit = CURRENT.get()
    if identity_audit is not None and identity_audit.get('version', 1) >= 2 and providers:
        # A short normal pacing interval should not trigger a paid fallback or
        # requeue the candidate. Long cooldowns/quota failures still fall back.
        delay = provider_capacity(providers[0]).get('retry_after_seconds', 0)
        if 0 < delay <= 5 and remaining_seconds() > delay + 1:
            emit('identity_search_pacing', provider=providers[0], seconds=delay)
            time.sleep(delay + 0.05)
        remaining_seconds()
    strategy = _provider_strategy()
    used: list[str] = []
    batches: list[list[dict[str, str]]] = []
    provider_responded = False
    unavailable: list[SearchProviderUnavailable] = []

    if strategy == "parallel" and len(providers) > 1:
        maximum = setting_int("SEARCH_PARALLEL_MAX_PROVIDERS", 2, 1, 3)
        # Parallelize API providers only; DDGS remains the last-resort fallback.
        selected = [provider for provider in providers if provider != "ddgs"][:maximum]
        if not selected:
            selected = ["ddgs"]
        with ThreadPoolExecutor(max_workers=len(selected)) as executor:
            futures = {
                executor.submit(_run_provider, provider, query, max_results): provider
                for provider in selected
            }
            for future in as_completed(futures):
                provider = futures[future]
                used.append(provider)
                try:
                    batch = future.result()
                    if not batch:
                        provider_responded = True
                    elif _results_match_query_anchor(query, batch):
                        provider_responded = True
                        batches.append(batch)
                    else:
                        unavailable.append(SearchProviderUnavailable(
                            f"{provider} returned results unrelated to the quoted identity.",
                            retry_after_seconds=300,
                        ))
                except SearchProviderUnavailable as error:
                    unavailable.append(error)
                    print(str(error))
        # Exhaustion of the parallel API group must still reach the remaining
        # configured providers, including the final DuckDuckGo fallback.
        if not batches:
            for provider in [provider for provider in providers if provider not in selected]:
                used.append(provider)
                try:
                    batch = _run_provider(provider, query, max_results)
                    if batch and _results_match_query_anchor(query, batch):
                        batches.append(batch)
                        break
                except SearchProviderUnavailable as error:
                    unavailable.append(error)
    else:
        if strategy == "balanced" and len(providers) > 1:
            digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
            apis = [provider for provider in providers if provider != "ddgs"]
            if apis:
                offset = int(digest[:8], 16) % len(apis)
                providers = apis[offset:] + apis[:offset] + ["ddgs"]
        for provider in providers:
            used.append(provider)
            try:
                batch = _run_provider(provider, query, max_results)
            except SearchProviderUnavailable as error:
                unavailable.append(error)
                print(str(error))
                continue
            if identity_audit is not None:
                useful = bool(batch) and _results_match_query_anchor(query, batch)
                record_retrieval(query, provider, batch,
                                 'RESULTS' if useful else 'NO_USEFUL_RESULTS')
                # The service answered successfully. Change the identity query,
                # not its availability state; don't buy a paid fallback for noise.
                if not useful:
                    return []
            if not batch:
                provider_responded = True
                continue
            if _results_match_query_anchor(query, batch):
                provider_responded = True
                batches.append(batch)
                break
            unavailable.append(SearchProviderUnavailable(
                f"{provider} returned results unrelated to the quoted identity.",
                retry_after_seconds=300,
            ))

    if not batches and unavailable:
        # A successful empty fallback must not hide a primary provider outage.
        retry_after = min(error.retry_after_seconds for error in unavailable)
        raise SearchUnavailable(
            "Web search waiting: " + "; ".join(str(error) for error in unavailable)[:700],
            retry_after_seconds=retry_after,
        )

    results = _merge_results(batches, max_results)
    # Expanded identity passes save selected evidence, not all 100 raw links.
    # Existing cached responses remain reusable; other search callers still cache.
    from ingestion.verification_audit import CURRENT
    identity_audit = CURRENT.get()
    if identity_audit is None or identity_audit.get('version', 1) < 2:
        _write_cache(query, max_results, results, used)
    return results
