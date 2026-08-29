"""Check that SearXNG and at least one upstream engine return useful results."""

from __future__ import annotations

import sys

import requests

from ingestion.websearch import _searxng_url
from settings import setting, setting_int


def main() -> int:
    url = _searxng_url()
    if not url:
        print("SearXNG is not configured.")
        return 1
    try:
        params: dict[str, object] = {
            "q": 'site:arizona.edu "Jingdi Chen"',
            "format": "json",
            "safesearch": 1,
        }
        engines = setting("SEARXNG_ENGINES").strip()
        if engines:
            params["engines"] = engines
        response = requests.get(
            f"{url.rstrip('/')}/search",
            params=params,
            timeout=setting_int("SEARCH_PROVIDER_TIMEOUT_SECONDS", 8, 3, 30),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"SearXNG API failed: {type(error).__name__}: {error}")
        return 1

    results = list(payload.get("results") or [])
    unavailable = list(payload.get("unresponsive_engines") or [])
    if not results:
        print("SearXNG is running, but no upstream engine returned a result.")
        if unavailable:
            print("Unavailable engines:")
            for item in unavailable[:10]:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    print(f"  - {item[0]}: {item[1]}")
        return 1

    print(f"SearXNG usable; results: {len(results)}")
    if unavailable:
        print(f"Warning: {len(unavailable)} upstream engine(s) were unavailable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
