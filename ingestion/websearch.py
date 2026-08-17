from __future__ import annotations

import random
import threading
import time
from typing import Any

import requests
from ddgs import DDGS

from settings import setting

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_search_lock = threading.Lock()


def _brave_search(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
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
        timeout=6,
    )
    response.raise_for_status()
    results = response.json().get("web", {}).get("results", [])
    return [
        {
            "href": str(item.get("url") or ""),
            "title": str(item.get("title") or ""),
            "body": str(item.get("description") or ""),
        }
        for item in results[:max_results]
    ]


def _fallback_search(query: str, max_results: int) -> list[dict[str, Any]]:
    proxy_url = setting("ROTATING_PROXY_URL")
    kwargs: dict[str, object] = {"timeout": 5}
    if proxy_url:
        kwargs["proxy"] = proxy_url
    with _search_lock:
        time.sleep(random.uniform(0.4, 0.9))
        return list(DDGS(**kwargs).text(query, max_results=max_results))


def search_web(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Search through Brave when configured, otherwise use the local fallback."""
    max_results = max(1, min(10, int(max_results)))
    api_key = setting("BRAVE_SEARCH_API_KEY")
    if api_key:
        try:
            return _brave_search(query, max_results, api_key)
        except (OSError, ValueError, requests.RequestException) as error:
            print(f"Brave Search failed; trying the local fallback: {error}")
    return _fallback_search(query, max_results)
