"""Check configured search providers, honoring the same durable budget as jobs."""

from __future__ import annotations

import sys

from ingestion.websearch import SearchUnavailable, _provider_names, search_web


def main() -> int:
    print("Configured providers:", ", ".join(_provider_names()))
    try:
        results = search_web('"Jingdi Chen" "University of Arizona"', max_results=3)
    except SearchUnavailable as error:
        print(f"Search waiting: {error}; retry in {error.retry_after_seconds} seconds.")
        return 2
    except Exception as error:
        print(f"Search failed: {type(error).__name__}: {error}")
        return 1
    if not results:
        print("Search answered, but this query returned no matching result.")
        return 1
    print(f"Search results available (possibly cached): {len(results)}. This is not a guarantee of provider health.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
