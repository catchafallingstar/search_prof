from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.matchers import clean_and_extract_hiring_quote
from ingestion.websearch import search_web
from settings import setting_int


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _result_names_candidate(name: str, result: dict[str, Any]) -> bool:
    words = _normalized_words(name)
    expected = words.split()
    allowed_after_name = {
        "faculty", "group", "hiring", "homepage", "lab", "laboratory",
        "openings", "phd", "positions", "professor", "profile", "research",
    }
    for segment in re.split(r"\s*(?:\||·|—|–|:)\s*", str(result.get("title") or "")):
        title_tokens = _normalized_words(segment).split()
        if title_tokens[: len(expected)] == expected:
            remainder = title_tokens[len(expected):]
            if not remainder or remainder[0] in allowed_after_name:
                return True
    compact_name = words.replace(" ", "")
    compact_url = re.sub(r"[^a-z0-9]", "", str(result.get("href") or "").casefold())
    return len(compact_name) >= 7 and compact_name in compact_url


def discover_hiring_first_leads(
    research_area: str,
    professor_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Find explicit hiring pages before ordinary research-rank enrichment.

    This lane is intentionally small: it makes two field-level web searches and
    connects strong snippets to already-discovered author identities. Faculty
    verification remains mandatory before any lead can be displayed.
    """
    ordered_ids = list(dict.fromkeys(int(value) for value in professor_ids))
    if not ordered_ids:
        return {}
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name FROM professors WHERE id = ANY(%s)",
                (ordered_ids,),
            )
            candidates = list(cursor.fetchall())

    per_query = setting_int("HIRING_FIRST_RESULTS_PER_QUERY", 10, 3, 10)
    queries = [
        f'"{research_area}" ("PhD students" OR "PhD positions" OR "join my lab" OR recruiting)',
        f'"{research_area}" ("prospective students" OR "fully funded PhD" OR "actively recruiting") professor',
    ]
    leads: dict[int, dict[str, Any]] = {}
    seen_urls: set[str] = set()
    for query in queries:
        try:
            results = search_web(query, max_results=per_query)
        except Exception:
            continue
        for result in results:
            url = str(result.get("href") or "").strip()
            if not url or url in seen_urls or not is_public_http_url(url):
                continue
            seen_urls.add(url)
            quote = clean_and_extract_hiring_quote(
                " ".join(str(result.get(key) or "") for key in ("title", "body"))
            )
            if not quote:
                continue
            for candidate in candidates:
                professor_id = int(candidate["id"])
                if professor_id in leads or not _result_names_candidate(candidate["name"], result):
                    continue
                host = (urlparse(url).hostname or "").casefold()
                leads[professor_id] = {
                    "professor_id": professor_id,
                    "quote": quote,
                    "source_url": url,
                    "signal_type": "homepage" if host.endswith(".edu") else "social",
                }
    return leads
