import random
import re
import time
from calendar import monthrange
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.identity_sources import canonical_source_url
from ingestion.matchers import clean_and_extract_hiring_quote, extract_roles_and_funding, get_text_hash, is_valid_signal_text
from ingestion.name_normalization import name_tokens
from ingestion.websearch import SearchUnavailable, search_web
from settings import setting_int

NEW_AP_PATTERN = re.compile(
    r"(?:joining|starting\s+(?:my\s+)?lab|new\s+assistant\s+professor|incoming\s+(?:faculty|professor))",
    re.IGNORECASE,
)
RELATED_ACADEMIC_LINK_PATTERN = re.compile(
    r"(?:\blab\b|laborator|research\s+group|opening|prospective|join\s+us|"
    r"personal\s+(?:site|website|homepage)|\bwebsite\b|\bhomepage\b)",
    re.IGNORECASE,
)
NO_GPA_CUTOFF_PATTERN = re.compile(
    r"(?:no\s+(?:minimum|required)\s+gpa|no\s+(?:hard\s+)?gpa\s+cutoff|"
    r"do\s+not\s+(?:use|have|require)\s+(?:a\s+)?(?:minimum\s+)?gpa)",
    re.IGNORECASE,
)
HOLISTIC_GPA_PATTERN = re.compile(r"(?:holistic(?:ally)?|whole\s+application).{0,80}\bgpa\b|\bgpa\b.{0,80}holistic", re.IGNORECASE)
GPA_MINIMUM_PATTERN = re.compile(
    r"(?:minimum|required|at\s+least)\s+(?:cumulative\s+)?gpa(?:\s+of)?\s*[:=]?\s*([234](?:\.\d{1,2})?)",
    re.IGNORECASE,
)
UNTRUSTED_HIRING_HOSTS = {
    "academia.edu", "amacad.org", "biopharmadive.com", "cell.com",
    "coursicle.com", "indeed.com", "linkedin.com", "phdportal.com",
    "quora.com", "researchgate.net", "scispace.com", "scite.ai",
    "zoominfo.com",
}
UNTRUSTED_HIRING_PATH_PATTERN = re.compile(
    r"/(?:news|events?|articles?|publications?|papers?|doi|jobs?-search|directory-entry)/",
    re.IGNORECASE,
)
MONTHS = {
    name.casefold(): number
    for number, name in enumerate(
        ("", "January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December")
    )
}
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
SEASON_RANGES = {
    "spring": (1, 5), "summer": (6, 8), "fall": (9, 12),
    "autumn": (9, 12), "winter": (12, 12),
}
DATE_NEAR_SIGNAL_PATTERN = re.compile(
    r"(?:recruit|hiring|opening|position|student|postdoc|apply|application|deadline|due|join|accept)",
    re.IGNORECASE,
)
DEADLINE_PATTERN = re.compile(
    r"\b(?:deadline|apply\s+by|applications?\s+(?:are\s+)?due)\b", re.IGNORECASE
)


def _month_number(value: str) -> int:
    normalized = value.strip().rstrip(".").casefold()
    for name, number in MONTHS.items():
        if name and (normalized == name or normalized == name[:3]):
            return number
    raise ValueError(f"Unknown month: {value}")


def _dated_interval(text: str) -> dict[str, Any] | None:
    """Return the first meaningful date with honest partial-date precision."""
    value = " ".join(str(text or "").split())
    patterns = (
        ("DAY", re.compile(rf"\b({MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", re.I)),
        ("DAY_ISO", re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")),
        ("MONTH", re.compile(rf"\b({MONTH_PATTERN})\.?\s+(20\d{{2}})\b", re.I)),
        ("SEASON", re.compile(r"\b(Spring|Summer|Fall|Autumn|Winter)\s+(20\d{2})\b", re.I)),
        ("YEAR", re.compile(r"\b(20\d{2})\b")),
    )
    for kind, pattern in patterns:
        match = pattern.search(value)
        if not match:
            continue
        try:
            if kind == "DAY":
                month = _month_number(match.group(1))
                day, year = int(match.group(2)), int(match.group(3))
                start = end = date(year, month, day)
                precision = "DAY"
            elif kind == "DAY_ISO":
                year, month, day = map(int, match.groups())
                start = end = date(year, month, day)
                precision = "DAY"
            elif kind == "MONTH":
                month, year = _month_number(match.group(1)), int(match.group(2))
                start = date(year, month, 1)
                end = date(year, month, monthrange(year, month)[1])
                precision = "MONTH"
            elif kind == "SEASON":
                season, year = match.group(1).casefold(), int(match.group(2))
                first_month, last_month = SEASON_RANGES[season]
                start = date(year, first_month, 1)
                end = date(year, last_month, monthrange(year, last_month)[1])
                precision = "SEASON"
            else:
                year = int(match.group(1))
                start, end, precision = date(year, 1, 1), date(year, 12, 31), "YEAR"
        except ValueError:
            continue
        return {
            "source_date": start,
            "interval_end": end,
            "source_date_precision": precision,
            "source_date_text": match.group(0),
        }
    return None


def _hiring_signal_freshness(
    quote: str, snapshot: dict[str, Any], today: date | None = None
) -> dict[str, Any]:
    today = today or date.today()
    published = str(snapshot.get("source_date_text") or "").strip()
    dated = _dated_interval(published) if published else None
    if dated is None and DATE_NEAR_SIGNAL_PATTERN.search(quote):
        dated = _dated_interval(quote)
    if dated is None:
        return {
            "freshness_status": "UNDATED", "source_date": None,
            "source_date_precision": None, "source_date_text": None,
        }
    end = dated["interval_end"]
    if dated["source_date"] > today:
        freshness = "UPCOMING"
    elif DEADLINE_PATTERN.search(quote) and end < today:
        freshness = "EXPIRED"
    else:
        age = (today - end).days
        freshness = "CURRENT" if age <= 30 else "OLDER" if age <= 180 else "HISTORICAL"
    dated["freshness_status"] = freshness
    dated.pop("interval_end", None)
    return dated


def _host_matches_domain(host: str, domain: str) -> bool:
    host = host.casefold().removeprefix("www.")
    domain = domain.casefold().removeprefix("www.")
    return bool(domain and (host == domain or host.endswith("." + domain)))


def _eligible_hiring_search_result(url: str) -> bool:
    """Reject aggregators and news pages before spending a page fetch."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not host or any(
        host == value or host.endswith("." + value)
        for value in UNTRUSTED_HIRING_HOSTS
    ):
        return False
    return not bool(UNTRUSTED_HIRING_PATH_PATTERN.search(parsed.path))


def _fetch_with_safe_redirects(url: str, max_redirects: int = 5) -> requests.Response:
    """Validate every redirect target before the server connects to it."""
    current_url = url
    for _ in range(max_redirects + 1):
        if not is_public_http_url(current_url, resolve_dns=True):
            raise requests.RequestException("URL or redirect target is not public HTTP(S).")
        response = requests.get(
            current_url,
            headers={"User-Agent": "ScholarRadar/1.0 (research opportunity indexer)"},
            timeout=setting_int("PUBLIC_PAGE_REQUEST_TIMEOUT_SECONDS", 25, 10, 90),
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise requests.RequestException("Redirect response did not include a location.")
            current_url = urljoin(current_url, location)
            continue
        return response
    raise requests.TooManyRedirects(f"More than {max_redirects} redirects")


def _fetch_hiring_page_snapshot(homepage_url: str) -> dict[str, Any]:
    """Fetch once and retain bounded identity, links, and recruiting text."""
    if not is_public_http_url(homepage_url, resolve_dns=True):
        return {"sentences": [], "accessible": False, "text": "", "title": "", "links": []}
    try:
        time.sleep(random.uniform(0.2, 0.5))
        response = _fetch_with_safe_redirects(homepage_url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return {"sentences": [], "accessible": True, "text": "", "title": "", "links": []}
        soup = BeautifulSoup(response.text, "html.parser")
        title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
        source_date_text = ""
        for selector, attribute in (
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="date"]', "content"),
            ('meta[name="dcterms.date"]', "content"),
            ('meta[itemprop="datePublished"]', "content"),
            ('time[datetime]', "datetime"),
        ):
            element = soup.select_one(selector)
            if element and str(element.get(attribute) or "").strip():
                source_date_text = str(element.get(attribute)).strip()
                break
        links = [
            {
                "href": urljoin(homepage_url, str(anchor.get("href") or "")),
                "label": " ".join(anchor.get_text(" ", strip=True).split()),
            }
            for anchor in soup.find_all("a", href=True)[:500]
        ]
        identity_soup = BeautifulSoup(response.text, "html.parser")
        for element in identity_soup(["script", "style", "noscript"]):
            element.decompose()
        identity_text = " ".join(identity_soup.get_text(" ", strip=True).split())[:100_000]
        for element in soup(["script", "style", "nav", "footer", "noscript"]):
            element.decompose()
        # Preserve block boundaries. Many lab pages use cards or list items
        # without final punctuation; flattening the whole page into one string
        # can hide an otherwise clear recruiting sentence.
        chunks = [
            element.get_text(" ", strip=True)
            for element in soup.select("p, li, h1, h2, h3, h4, h5, h6")
        ]
        if not chunks:
            chunks = list(soup.stripped_strings)
        matches: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            for sentence in re.split(r"(?<=[.!?])\s+|[;\n\r\t]+", chunk):
                cleaned = " ".join(sentence.split())
                if (
                    15 < len(cleaned) < 500
                    and cleaned not in seen
                    and is_valid_signal_text(cleaned)
                ):
                    seen.add(cleaned)
                    matches.append(cleaned)
        return {
            "sentences": matches[:5],
            "accessible": True,
            "text": identity_text,
            "title": title,
            "links": links,
            "source_date_text": source_date_text,
        }
    except (OSError, requests.RequestException) as error:
        print(f"Homepage fetch failed for {homepage_url}: {error}")
        return {"sentences": [], "accessible": False, "text": "", "title": "", "links": []}


def _fetch_and_parse_homepage_status(homepage_url: str) -> tuple[list[str], bool]:
    snapshot = _fetch_hiring_page_snapshot(homepage_url)
    return list(snapshot["sentences"]), bool(snapshot["accessible"])


def fetch_and_parse_homepage(homepage_url: str) -> list[str]:
    """Compatibility wrapper used by tests and the standalone parser."""
    matches, _accessible = _fetch_and_parse_homepage_status(homepage_url)
    return matches


def extract_gpa_evidence(sentences: list[str], source_url: str) -> dict[str, Any] | None:
    """Keep GPA extraction conservative and tied to quoted source text."""
    for sentence in sentences:
        if not re.search(r"\bgpa\b", sentence, re.IGNORECASE):
            continue
        if NO_GPA_CUTOFF_PATTERN.search(sentence):
            return {"policy": "no_lab_cutoff", "evidence": sentence, "source_url": source_url}
        if HOLISTIC_GPA_PATTERN.search(sentence):
            return {"policy": "holistic_review", "evidence": sentence, "source_url": source_url}
        minimum = GPA_MINIMUM_PATTERN.search(sentence)
        if minimum:
            scope = (
                "program"
                if re.search(r"\b(?:program|department|graduate\s+school|admission)\b", sentence, re.IGNORECASE)
                else "lab"
            )
            return {
                "policy": "minimum",
                "evidence": sentence,
                "source_url": source_url,
                "minimum": float(minimum.group(1)),
                "scope": scope,
            }
    return None


def _linked_research_pages_from_links(
    homepage_url: str,
    links: list[dict[str, str]],
    max_links: int = 3,
) -> list[str]:
    results: list[str] = []
    seen: set[str] = {homepage_url.rstrip("/")}
    for anchor in links:
        label = " ".join(str(anchor.get("label") or "").split())
        href = str(anchor.get("href") or "").strip()
        candidate = urldefrag(urljoin(homepage_url, href))[0]
        if not RELATED_ACADEMIC_LINK_PATTERN.search(f"{label} {href}"):
            continue
        if candidate.rstrip("/") in seen or not is_public_http_url(candidate):
            continue
        seen.add(candidate.rstrip("/"))
        results.append(candidate)
        if len(results) >= max(1, min(max_links, 5)):
            break
    return results


def discover_linked_research_pages(homepage_url: str, max_links: int = 3) -> list[str]:
    """Follow a few lab/openings links that an official faculty page endorses."""
    snapshot = _fetch_hiring_page_snapshot(homepage_url)
    if not snapshot["accessible"]:
        return []
    return _linked_research_pages_from_links(
        homepage_url, list(snapshot["links"]), max_links=max_links
    )


def _normalized_tokens(value: str) -> list[str]:
    return [token for token in name_tokens(value) if len(token) > 1]


def _ordered_name_present(name: str, text: str) -> bool:
    expected = _normalized_tokens(name)
    observed = name_tokens(text)
    if len(expected) < 2:
        return False
    for start in range(len(observed) - len(expected) + 1):
        if observed[start:start + len(expected)] == expected:
            return True
    return False


def _institution_present(institution: str, text: str) -> bool:
    ignored = {"and", "at", "college", "of", "school", "system", "the", "university"}
    expected = {token for token in _normalized_tokens(institution) if token not in ignored}
    observed = set(name_tokens(text))
    required = min(2, len(expected))
    return bool(expected and len(expected & observed) >= required)


def _supporting_paper_present(titles: list[str], text: str) -> bool:
    observed = set(name_tokens(text))
    for title in titles:
        expected = {token for token in _normalized_tokens(title) if len(token) >= 4}
        if len(expected) >= 4 and len(expected & observed) / len(expected) >= 0.7:
            return True
    return False


def _saved_page_is_attributed(
    professor: dict[str, Any], snapshot: dict[str, Any]
) -> bool:
    text = f"{snapshot.get('title') or ''} {snapshot.get('text') or ''}"
    return bool(
        _ordered_name_present(str(professor.get("name") or ""), text)
        and (
            _institution_present(str(professor.get("institution_name") or ""), text)
            or _supporting_paper_present(
                list(professor.get("supporting_paper_titles") or []), text
            )
        )
    )


def save_signal_to_db(
    professor_id: int,
    signal_type: str,
    raw_quote: str,
    source_url: str,
    radar_run_id: int | None = None,
    freshness: dict[str, Any] | None = None,
    check_status: str = "PRESENT",
) -> dict[str, Any]:
    freshness = freshness or {"freshness_status": "UNDATED"}
    freshness_status = str(freshness.get("freshness_status") or "UNDATED")
    public_current = freshness_status in {"CURRENT", "UPCOMING", "UNDATED"}
    check_status = "PRESENT" if check_status == "PRESENT" and public_current else "NOT_FOUND"
    roles, has_funding = extract_roles_and_funding(raw_quote)
    role_priority = ("PhD", "Postdoc", "Research Assistant", "Intern")
    primary_role = next((role for role in role_priority if role in roles), None)
    position_type = {
        "PhD": "PhD",
        "Postdoc": "Postdoc",
        "Research Assistant": "Research Assistant",
        "Intern": "Internship",
    }.get(primary_role or "", "PhD")
    is_new_ap = bool(NEW_AP_PATTERN.search(raw_quote))
    score_boost = 40 + (20 if has_funding else 0) + (30 if is_new_ap else 0)
    quote_hash = get_text_hash(f"{professor_id}|{source_url}|{raw_quote}")
    confidence = "high" if signal_type in {"homepage", "official_profile"} else "medium"
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO hiring_signals (
                    professor_id, raw_text_hash, signal_type, confidence, raw_text,
                    source_url, position_type, attribution_status,
                    first_seen_at, last_seen_at, last_checked_at,
                    check_status, consecutive_check_failures, next_check_at, expires_at,
                    source_date, source_date_precision, source_date_text, freshness_status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'VERIFIED',
                    NOW(), NOW(), NOW(), %s, 0,
                    NOW() + INTERVAL '24 hours',
                    CASE WHEN %s THEN NOW() + INTERVAL '120 days' ELSE NOW() END,
                    %s, %s, %s, %s
                )
                ON CONFLICT (raw_text_hash) DO UPDATE
                SET position_type = EXCLUDED.position_type,
                    attribution_status = 'VERIFIED',
                    last_seen_at = NOW(), last_checked_at = NOW(),
                    check_status = EXCLUDED.check_status, consecutive_check_failures = 0,
                    next_check_at = NOW() + INTERVAL '24 hours',
                    expires_at = EXCLUDED.expires_at,
                    source_date = EXCLUDED.source_date,
                    source_date_precision = EXCLUDED.source_date_precision,
                    source_date_text = EXCLUDED.source_date_text,
                    freshness_status = EXCLUDED.freshness_status
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    professor_id, quote_hash, signal_type, confidence, raw_quote,
                    source_url, position_type, check_status, public_current,
                    freshness.get("source_date"), freshness.get("source_date_precision"),
                    freshness.get("source_date_text"), freshness_status,
                ),
            )
            signal_row = cursor.fetchone()
            inserted = bool(signal_row and signal_row.get("inserted"))
            opportunity_id = None
            if inserted and public_current:
                role_text = ", ".join(roles) if roles else "unspecified role"
                cursor.execute(
                    """
                    UPDATE professors
                    SET radar_score = radar_score + %s,
                        career_stage = CASE WHEN %s THEN 'NEW_AP' ELSE career_stage END,
                        score_breakdown = score_breakdown || %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (score_boost, is_new_ap, f" +{score_boost} ({signal_type}; {role_text})", professor_id),
                )
            return {
                "inserted": inserted,
                "opportunity_id": opportunity_id,
                "professor_id": professor_id,
                "signal_type": signal_type,
                "quote": raw_quote,
                "source_url": source_url,
                "freshness_status": freshness_status,
                "source_date": freshness.get("source_date"),
                "source_date_precision": freshness.get("source_date_precision"),
                "source_date_text": freshness.get("source_date_text"),
            }


def process_single_professor(professor: dict[str, Any], domain_name: str | None = None) -> dict[str, Any]:
    """Check sources in trust order and buy at most one web-search query."""
    any_accessible = False
    gpa_evidence: dict[str, Any] | None = None
    seen: set[str] = set()
    checked_sources: list[dict[str, Any]] = []
    stale_signals: list[tuple[str, int, str, str, dict[str, Any]]] = []

    def check_page(
        signal_type: str, url: str, *, validate_saved: bool = False
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        nonlocal any_accessible, gpa_evidence
        page = canonical_source_url(url)
        if not page or page in seen:
            return None, {"sentences": [], "accessible": False, "links": []}
        seen.add(page)
        snapshot = _fetch_hiring_page_snapshot(page)
        accessible = bool(snapshot["accessible"])
        any_accessible = any_accessible or accessible
        attributed = not validate_saved or _saved_page_is_attributed(professor, snapshot)
        source_check = {
            "source_type": signal_type,
            "url": page,
            "accessible": accessible,
            "attributed": attributed if accessible else False,
            "result": "SOURCE_UNAVAILABLE" if not accessible else "NO_HIRING_SIGNAL",
        }
        checked_sources.append(source_check)
        if not accessible or not attributed:
            if accessible and not attributed:
                source_check["result"] = "ATTRIBUTION_FAILED"
            return None, snapshot
        sentences = list(snapshot["sentences"])
        if gpa_evidence is None:
            gpa_evidence = extract_gpa_evidence(sentences, page)
        quote = clean_and_extract_hiring_quote(". ".join(sentences))
        if not quote:
            return None, snapshot
        freshness = _hiring_signal_freshness(quote, snapshot)
        source_check.update(freshness)
        if freshness["freshness_status"] in {"OLDER", "HISTORICAL", "EXPIRED"}:
            source_check["result"] = f"{freshness['freshness_status']}_HIRING_SIGNAL"
            source_check["quote"] = quote
            stale_signals.append((signal_type, professor["id"], quote, page, freshness))
            return None, snapshot
        source_check["result"] = "HIRING_SIGNAL_FOUND"
        source_check["quote"] = quote
        return {
            "signal": (signal_type, professor["id"], quote, page),
            "signal_freshness": freshness,
            "historical_signals": list(stale_signals),
            "check_status": "PRESENT",
            "gpa": gpa_evidence,
            "sources_checked": list(checked_sources),
        }, snapshot

    # 1. Recheck a previously attributed hiring source first.
    for prior in professor.get("prior_hiring_sources") or []:
        outcome, _snapshot = check_page("homepage", str(prior))
        if outcome:
            return outcome

    # 2. Check the official faculty source.
    official_url = str(professor.get("official_faculty_source_url") or "").strip()
    if not official_url:
        fallback = str(professor.get("faculty_source_url") or "").strip()
        fallback_host = (urlparse(fallback).hostname or "").casefold()
        official_domain = str(
            professor.get("official_institution_domain") or ""
        ).strip()
        if (
            fallback
            and _eligible_hiring_search_result(fallback)
            and (
                _host_matches_domain(fallback_host, official_domain)
                or fallback_host.endswith(".edu")
            )
        ):
            official_url = fallback
    official_snapshot: dict[str, Any] = {"links": []}
    if official_url:
        outcome, official_snapshot = check_page("official_profile", official_url)
        if outcome:
            return outcome

    # 3. Follow pages explicitly linked by that official profile.
    for linked_page in _linked_research_pages_from_links(
        official_url, list(official_snapshot.get("links") or [])
    ) if official_url else []:
        outcome, _snapshot = check_page("homepage", linked_page)
        if outcome:
            return outcome

    # 4. Reuse personal/lab URLs returned by identity verification. These are
    # leads until the fetched page matches the name and institution or paper.
    for saved in professor.get("saved_profile_sources") or []:
        outcome, _snapshot = check_page(
            "homepage", str(saved.get("source_url") or ""), validate_saved=True
        )
        if outcome:
            return outcome

    # 5. Use exactly one targeted search query only after stored sources fail.
    name = str(professor.get("name") or "").strip()
    institution = str(professor.get("institution_name") or "").strip()
    if name and institution:
        query = (
            f'"{name}" "{institution}" '
            '("PhD students" OR recruiting OR openings OR "join my lab")'
        )
        try:
            results = search_web(query, max_results=10)
        except SearchUnavailable as error:
            print(f"Hiring-page search unavailable for {name}: {error}")
            checked_sources.append({
                "source_type": "targeted_search", "url": None,
                "accessible": False, "attributed": False,
                "result": "SEARCH_UNAVAILABLE", "query": query,
                "error": str(error),
            })
            results = []
        except Exception as error:
            print(f"Hiring-page search failed for {name}: {type(error).__name__}")
            checked_sources.append({
                "source_type": "targeted_search", "url": None,
                "accessible": False, "attributed": False,
                "result": "SEARCH_FAILED", "query": query,
                "error": str(error),
            })
            results = []
        for result in results:
            result_url = str(result.get("href") or "")
            if not _eligible_hiring_search_result(result_url):
                continue
            outcome, _snapshot = check_page(
                "homepage", result_url, validate_saved=True
            )
            if outcome:
                return outcome
    return {
        "signal": None,
        "historical_signals": stale_signals,
        "check_status": "NOT_FOUND" if any_accessible else "SOURCE_UNAVAILABLE",
        "gpa": gpa_evidence,
        "sources_checked": checked_sources,
    }


def record_professor_hiring_check(
    professor_id: int,
    check_status: str,
    gpa_evidence: dict[str, Any] | None,
) -> None:
    """Record the inspection separately from whether recruiting text was seen."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if check_status == "SOURCE_UNAVAILABLE":
                cursor.execute(
                    """
                    UPDATE professors
                    SET public_hiring_checked_at = NOW(),
                        public_hiring_check_status = 'SOURCE_UNAVAILABLE',
                        public_hiring_failure_count = public_hiring_failure_count + 1,
                        public_hiring_next_check_at = NOW() + INTERVAL '6 hours',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (professor_id,),
                )
                cursor.execute(
                    """
                    UPDATE hiring_signals
                    SET last_checked_at = NOW(), check_status = 'SOURCE_UNAVAILABLE',
                        consecutive_check_failures = consecutive_check_failures + 1,
                        next_check_at = NOW() + INTERVAL '6 hours'
                    WHERE professor_id = %s AND attribution_status = 'VERIFIED'
                    """,
                    (professor_id,),
                )
                return

            cursor.execute(
                """
                UPDATE professors
                SET public_hiring_checked_at = NOW(),
                    public_hiring_check_status = %s,
                    public_hiring_failure_count = 0,
                    public_hiring_next_check_at = NOW() + INTERVAL '24 hours',
                    lab_gpa_policy = %s,
                    lab_gpa_evidence_text = %s,
                    lab_gpa_source_url = %s,
                    lab_gpa_minimum = %s,
                    program_gpa_minimum = %s,
                    program_gpa_source_url = %s,
                    gpa_last_checked_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (
                    check_status,
                    (
                        (gpa_evidence or {}).get("policy", "not_stated")
                        if (gpa_evidence or {}).get("scope", "lab") == "lab"
                        else "not_stated"
                    ),
                    (gpa_evidence or {}).get("evidence"),
                    (gpa_evidence or {}).get("source_url"),
                    (gpa_evidence or {}).get("minimum") if (gpa_evidence or {}).get("scope", "lab") == "lab" else None,
                    (gpa_evidence or {}).get("minimum") if (gpa_evidence or {}).get("scope") == "program" else None,
                    (gpa_evidence or {}).get("source_url") if (gpa_evidence or {}).get("scope") == "program" else None,
                    professor_id,
                ),
            )
            if check_status == "NOT_FOUND":
                cursor.execute(
                    """
                    UPDATE hiring_signals
                    SET last_checked_at = NOW(), check_status = 'NOT_FOUND',
                        consecutive_check_failures = 0,
                        next_check_at = NOW() + INTERVAL '24 hours'
                    WHERE professor_id = %s AND attribution_status = 'VERIFIED'
                    """,
                    (professor_id,),
                )


def scan_hiring_signals(
    domain_name: str | None = None,
    professor_ids: list[int] | None = None,
    stop_check_callback: Callable[[], bool] | None = None,
    progress_callback: Callable[[str, int, dict[str, int]], None] | None = None,
    radar_run_id: int | None = None,
) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if professor_ids is not None:
                if not professor_ids:
                    return {
                        "professors_checked": 0,
                        "signals_added": 0,
                        "results": [],
                        "timed_out": False,
                        "checked_professor_ids": [],
                    }
                cursor.execute(
                    """
                    SELECT id, name, institution_name, homepage_url,
                           faculty_source_url, official_institution_domain
                    FROM professors
                    WHERE id = ANY(%s) AND faculty_status = 'VERIFIED'
                    ORDER BY id
                    """,
                    (professor_ids,),
                )
            elif domain_name:
                cursor.execute(
                    "SELECT id, name, institution_name, homepage_url, faculty_source_url, official_institution_domain FROM professors WHERE research_domain = %s AND faculty_status = 'VERIFIED'",
                    (domain_name,),
                )
            else:
                cursor.execute("SELECT id, name, institution_name, homepage_url, faculty_source_url, official_institution_domain FROM professors WHERE faculty_status = 'VERIFIED'")
            professors = list(cursor.fetchall())
            professor_ids_for_sources = [int(row["id"]) for row in professors]
            if professor_ids_for_sources:
                cursor.execute(
                    """
                    SELECT professor_id, source_url, source_type,
                           verification_status, supports_decision
                    FROM faculty_verification_evidence
                    WHERE professor_id = ANY(%s)
                      AND source_type IN (
                          'OFFICIAL_UNIVERSITY_PAGE',
                          'PERSONAL_WEBSITE', 'LAB_WEBSITE'
                      )
                      AND lookup_status NOT IN ('BLOCKED', 'DELETED', 'HTTP_ERROR')
                    ORDER BY professor_id, supports_decision DESC,
                             checked_at DESC, id DESC
                    """,
                    (professor_ids_for_sources,),
                )
                saved_by_professor: dict[int, list[dict[str, Any]]] = {}
                official_by_professor: dict[int, str] = {}
                for source in cursor.fetchall():
                    professor_id = int(source["professor_id"])
                    if source["source_type"] == "OFFICIAL_UNIVERSITY_PAGE":
                        if (
                            professor_id not in official_by_professor
                            and source["verification_status"]
                            in ("VERIFIED", "OUT_OF_SCOPE")
                        ):
                            official_by_professor[professor_id] = source["source_url"]
                        continue
                    values = saved_by_professor.setdefault(professor_id, [])
                    if len(values) < 10 and source["source_url"] not in {
                        item["source_url"] for item in values
                    }:
                        values.append(dict(source))

                cursor.execute(
                    """
                    SELECT professor_id, source_url
                    FROM hiring_signals
                    WHERE professor_id = ANY(%s)
                      AND attribution_status = 'VERIFIED'
                    ORDER BY professor_id, last_seen_at DESC, id DESC
                    """,
                    (professor_ids_for_sources,),
                )
                prior_by_professor: dict[int, list[str]] = {}
                for source in cursor.fetchall():
                    values = prior_by_professor.setdefault(int(source["professor_id"]), [])
                    if len(values) < 5 and source["source_url"] not in values:
                        values.append(source["source_url"])

                cursor.execute(
                    """
                    SELECT evidence.professor_id, paper.title
                    FROM radar_topic_professor_papers evidence
                    JOIN papers paper ON paper.id = evidence.paper_id
                    WHERE evidence.professor_id = ANY(%s)
                      AND evidence.is_current_match = TRUE
                    ORDER BY evidence.professor_id, evidence.relevance_score DESC,
                             paper.publication_year DESC NULLS LAST
                    """,
                    (professor_ids_for_sources,),
                )
                papers_by_professor: dict[int, list[str]] = {}
                for paper in cursor.fetchall():
                    values = papers_by_professor.setdefault(int(paper["professor_id"]), [])
                    if len(values) < 8 and paper["title"] not in values:
                        values.append(paper["title"])

                for professor in professors:
                    professor_id = int(professor["id"])
                    professor["official_faculty_source_url"] = (
                        official_by_professor.get(professor_id)
                    )
                    professor["saved_profile_sources"] = saved_by_professor.get(professor_id, [])
                    professor["prior_hiring_sources"] = prior_by_professor.get(professor_id, [])
                    professor["supporting_paper_titles"] = papers_by_professor.get(professor_id, [])

    max_workers = setting_int("RADAR_MAX_WORKERS", 2, 1, 8)
    hits = 0
    checked = 0
    results: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    checked_professor_ids: list[int] = []
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(process_single_professor, professor, domain_name): professor
        for professor in professors
    }
    pending = set(futures)
    cancelled = False
    try:
        while pending:
            completed, pending = wait(
                pending,
                timeout=1.0,
                return_when=FIRST_COMPLETED,
            )
            if not completed:
                if progress_callback:
                    progress_callback(
                        f"Checking public sources ({checked}/{len(professors)})",
                        55 + int(35 * checked / max(1, len(professors))),
                        {"professors_checked": checked, "signals_added": hits},
                    )
                continue
            for future in completed:
                professor = futures[future]
                if stop_check_callback and stop_check_callback():
                    cancelled = True
                    pending.clear()
                    break
                try:
                    outcome = future.result()
                    checked += 1
                    checked_professor_ids.append(int(professor["id"]))
                    signal = outcome.get("signal")
                    check_detail = {
                        "professor_id": int(professor["id"]),
                        "professor_name": professor["name"],
                        "check_status": str(outcome.get("check_status") or "SOURCE_UNAVAILABLE"),
                        "sources_checked": list(outcome.get("sources_checked") or []),
                        "signal": None,
                    }
                    if signal:
                        signal_type, professor_id, quote, source_url = signal
                        saved = save_signal_to_db(
                            professor_id, signal_type, quote, source_url, radar_run_id,
                            outcome.get("signal_freshness"), "PRESENT",
                        )
                        results.append(saved)
                        if saved["inserted"]:
                            hits += 1
                            print(f"Hiring evidence found for {professor['name']}: {source_url}")
                        check_detail["signal"] = {
                            "type": signal_type,
                            "quote": quote,
                            "source_url": source_url,
                            "freshness": outcome.get("signal_freshness"),
                        }
                    for historical in outcome.get("historical_signals") or []:
                        historical_type, historical_id, historical_quote, historical_url, historical_freshness = historical
                        saved_historical = save_signal_to_db(
                            historical_id, historical_type, historical_quote,
                            historical_url, radar_run_id, historical_freshness, "NOT_FOUND",
                        )
                        results.append(saved_historical)
                    checks.append(check_detail)
                    record_professor_hiring_check(
                        int(professor["id"]),
                        str(outcome.get("check_status") or "SOURCE_UNAVAILABLE"),
                        outcome.get("gpa"),
                    )
                    if progress_callback:
                        total = max(1, len(professors))
                        progress_callback(
                            f"Checking public sources ({checked}/{len(professors)})",
                            55 + int(35 * checked / total),
                            {"professors_checked": checked, "signals_added": hits},
                        )
                except Exception as error:
                    checked += 1
                    checked_professor_ids.append(int(professor["id"]))
                    print(f"Signal scan failed for {professor['name']}: {error}")
                    checks.append({
                        "professor_id": int(professor["id"]),
                        "professor_name": professor["name"],
                        "check_status": "SOURCE_UNAVAILABLE",
                        "sources_checked": [],
                        "signal": None,
                        "error": str(error),
                    })
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return {
        "professors_checked": checked,
        "signals_added": hits,
        "results": results,
        "checks": checks,
        # Kept for compatibility with callers and older reports. A section-wide
        # deadline no longer exists; only an explicit cancellation can stop it.
        "timed_out": False,
        "cancelled": cancelled,
        "checked_professor_ids": checked_professor_ids,
    }


if __name__ == "__main__":
    print(scan_hiring_signals())
