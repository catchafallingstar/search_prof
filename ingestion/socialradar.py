import threading
from datetime import datetime, timedelta, timezone

import requests

from ingestion.homepagefinder import is_public_http_url, result_matches_institution
from ingestion.matchers import is_valid_signal_text
from ingestion.websearch import SearchUnavailable, search_web

EXCLUDED_DOMAINS = {
    "glassdoor.com",
    "indeed.com",
    "ratemyprofessors.com",
    "researchgate.net",
    "roberthalf.com",
    "scholar.google.com",
    "wikipedia.org",
    "ziprecruiter.com",
}
_bluesky_state_lock = threading.Lock()
_bluesky_unavailable = False


def _identity_matches(prof_name: str, *values: str) -> bool:
    parts = [part.casefold() for part in prof_name.split() if len(part) > 1]
    if not parts:
        return False
    combined = " ".join(value or "" for value in values).casefold()
    if len(parts) == 1:
        return parts[0] in combined
    return parts[0] in combined and parts[-1] in combined


def check_bluesky_hiring(prof_name: str, institution: str) -> tuple[str | None, str | None]:
    global _bluesky_unavailable
    if not prof_name:
        return None, None
    with _bluesky_state_lock:
        if _bluesky_unavailable:
            return None, None
    query = f'"{prof_name}" (hiring OR recruiting OR "PhD position" OR "prospective students")'
    try:
        response = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": query, "limit": 10},
            headers={
                "Accept": "application/json",
                "User-Agent": "ScholarRadar/1.0 (public research-opportunity indexer)",
            },
            timeout=8,
        )
        if response.status_code in {401, 403, 429}:
            with _bluesky_state_lock:
                first_failure = not _bluesky_unavailable
                _bluesky_unavailable = True
            if first_failure:
                print(
                    f"Bluesky search is unavailable for this scan (HTTP {response.status_code}); "
                    "continuing with homepage and web search."
                )
            return None, None
        response.raise_for_status()
        cutoff = datetime.now(timezone.utc) - timedelta(days=400)
        for post in response.json().get("posts", []):
            record = post.get("record", {})
            text = str(record.get("text", ""))
            author = post.get("author", {})
            handle = str(author.get("handle", ""))
            display_name = str(author.get("displayName", ""))
            indexed_at = post.get("indexedAt")
            if indexed_at:
                observed = datetime.fromisoformat(str(indexed_at).replace("Z", "+00:00"))
                if observed < cutoff:
                    continue
            if not _identity_matches(prof_name, handle, display_name):
                continue
            if not is_valid_signal_text(text):
                continue
            uri = str(post.get("uri", ""))
            record_key = uri.rsplit("/", 1)[-1] if uri else ""
            url = f"https://bsky.app/profile/{handle}/post/{record_key}"
            return " ".join(text.split()), url
    except Exception as error:
        print(f"Bluesky search failed for {prof_name}: {error}")
    return None, None


def check_social_hiring(
    prof_name: str | None = None,
    institution: str | None = None,
    **_: object,
) -> tuple[str | None, str | None]:
    if not prof_name:
        return None, None

    bluesky_text, bluesky_url = check_bluesky_hiring(prof_name, institution or "")
    if bluesky_text:
        return bluesky_text, bluesky_url

    clean_institution = (institution or "").split("(")[0].strip()
    queries = [f'"{prof_name}" "{clean_institution}" hiring recruiting PhD students']
    for query in queries:
        try:
            for result in search_web(query, max_results=3):
                snippet = str(result.get("body", ""))
                title = str(result.get("title", ""))
                url = str(result.get("href", ""))
                host = (url.split("/")[2] if "://" in url else "").casefold()
                if any(host == domain or host.endswith(f".{domain}") for domain in EXCLUDED_DOMAINS):
                    continue
                if not is_public_http_url(url):
                    continue
                if not _identity_matches(prof_name, snippet, title, url):
                    continue
                if not result_matches_institution(institution or "", snippet, title, url):
                    continue
                if is_valid_signal_text(snippet):
                    return " ".join(snippet.split()), url
        except SearchUnavailable:
            # Do not turn a search-engine outage into "no hiring signal".
            raise
        except Exception as error:
            print(f"Web hiring search failed for {prof_name}: {error}")
    return None, None
