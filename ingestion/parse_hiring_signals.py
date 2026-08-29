import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.matchers import clean_and_extract_hiring_quote, extract_roles_and_funding, get_text_hash, is_valid_signal_text
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


def _fetch_with_safe_redirects(url: str, max_redirects: int = 5) -> requests.Response:
    """Validate every redirect target before the server connects to it."""
    current_url = url
    for _ in range(max_redirects + 1):
        if not is_public_http_url(current_url, resolve_dns=True):
            raise requests.RequestException("URL or redirect target is not public HTTP(S).")
        response = requests.get(
            current_url,
            headers={"User-Agent": "ScholarRadar/1.0 (research opportunity indexer)"},
            timeout=10,
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


def _fetch_and_parse_homepage_status(homepage_url: str) -> tuple[list[str], bool]:
    if not is_public_http_url(homepage_url, resolve_dns=True):
        return [], False
    try:
        time.sleep(random.uniform(0.2, 0.5))
        response = _fetch_with_safe_redirects(homepage_url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return [], True
        soup = BeautifulSoup(response.text, "html.parser")
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
        return matches[:5], True
    except (OSError, requests.RequestException) as error:
        print(f"Homepage fetch failed for {homepage_url}: {error}")
        return [], False


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


def discover_linked_research_pages(homepage_url: str, max_links: int = 3) -> list[str]:
    """Follow a few lab/openings links that an official faculty page endorses."""
    if not is_public_http_url(homepage_url, resolve_dns=True):
        return []
    try:
        response = _fetch_with_safe_redirects(homepage_url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[str] = []
        seen: set[str] = {homepage_url.rstrip("/")}
        for anchor in soup.find_all("a", href=True):
            label = " ".join(anchor.get_text(" ", strip=True).split())
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
    except (OSError, requests.RequestException):
        return []


def save_signal_to_db(
    professor_id: int,
    signal_type: str,
    raw_quote: str,
    source_url: str,
    radar_run_id: int | None = None,
) -> dict[str, Any]:
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
                    check_status, consecutive_check_failures, next_check_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'VERIFIED',
                    NOW(), NOW(), NOW(), 'PRESENT', 0,
                    NOW() + INTERVAL '24 hours', NOW() + INTERVAL '120 days'
                )
                ON CONFLICT (raw_text_hash) DO UPDATE
                SET position_type = EXCLUDED.position_type,
                    attribution_status = 'VERIFIED',
                    last_seen_at = NOW(), last_checked_at = NOW(),
                    check_status = 'PRESENT', consecutive_check_failures = 0,
                    next_check_at = NOW() + INTERVAL '24 hours',
                    expires_at = NOW() + INTERVAL '120 days'
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    professor_id, quote_hash, signal_type, confidence, raw_quote,
                    source_url, position_type,
                ),
            )
            signal_row = cursor.fetchone()
            inserted = bool(signal_row and signal_row.get("inserted"))
            opportunity_id = None
            if inserted:
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
            }


def process_single_professor(professor: dict[str, Any], domain_name: str | None = None) -> dict[str, Any]:
    # Only pages already anchored to the verified faculty identity are eligible
    # for automatic public display. Search-engine guesses and social results can
    # belong to a namesake and therefore must not create a public signal.
    trusted_pages: list[tuple[str, str]] = []
    # The verified faculty source is the trust root. Personal/lab pages become
    # eligible only when that official page links to them.
    for signal_type, candidate in (
        ("official_profile", professor.get("faculty_source_url")),
    ):
        page = str(candidate or "").strip()
        if page and page not in {url for _, url in trusted_pages}:
            trusted_pages.append((signal_type, page))

    any_accessible = False
    gpa_evidence: dict[str, Any] | None = None
    for signal_type, homepage in trusted_pages:
        sentences, accessible = _fetch_and_parse_homepage_status(homepage)
        any_accessible = any_accessible or accessible
        if accessible and gpa_evidence is None:
            gpa_evidence = extract_gpa_evidence(sentences, homepage)
        quote = clean_and_extract_hiring_quote(". ".join(sentences))
        if quote:
            return {
                "signal": (signal_type, professor["id"], quote, homepage),
                "check_status": "PRESENT",
                "gpa": gpa_evidence,
            }
        for linked_page in discover_linked_research_pages(homepage):
            linked_sentences, linked_accessible = _fetch_and_parse_homepage_status(linked_page)
            any_accessible = any_accessible or linked_accessible
            if linked_accessible and gpa_evidence is None:
                gpa_evidence = extract_gpa_evidence(linked_sentences, linked_page)
            quote = clean_and_extract_hiring_quote(". ".join(linked_sentences))
            if quote:
                return {
                    "signal": ("homepage", professor["id"], quote, linked_page),
                    "check_status": "PRESENT",
                    "gpa": gpa_evidence,
                }
    return {
        "signal": None,
        "check_status": "NOT_FOUND" if any_accessible else "SOURCE_UNAVAILABLE",
        "gpa": gpa_evidence,
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
                    SELECT id, name, institution_name, homepage_url, faculty_source_url
                    FROM professors
                    WHERE id = ANY(%s) AND faculty_status = 'VERIFIED'
                    ORDER BY id
                    """,
                    (professor_ids,),
                )
            elif domain_name:
                cursor.execute(
                    "SELECT id, name, institution_name, homepage_url, faculty_source_url FROM professors WHERE research_domain = %s AND faculty_status = 'VERIFIED'",
                    (domain_name,),
                )
            else:
                cursor.execute("SELECT id, name, institution_name, homepage_url, faculty_source_url FROM professors WHERE faculty_status = 'VERIFIED'")
            professors = list(cursor.fetchall())

    max_workers = setting_int("RADAR_MAX_WORKERS", 2, 1, 8)
    hits = 0
    checked = 0
    results: list[dict[str, Any]] = []
    checked_professor_ids: list[int] = []
    timeout_seconds = setting_int("PUBLIC_RADAR_TIMEOUT_SECONDS", 55, 15, 180)
    deadline = time.monotonic() + timeout_seconds
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(process_single_professor, professor, domain_name): professor
        for professor in professors
    }
    pending = set(futures)
    timed_out = False
    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            completed, pending = wait(
                pending,
                timeout=min(1.0, remaining),
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
                    timed_out = True
                    pending.clear()
                    break
                try:
                    outcome = future.result()
                    checked += 1
                    checked_professor_ids.append(int(professor["id"]))
                    signal = outcome.get("signal")
                    if signal:
                        signal_type, professor_id, quote, source_url = signal
                        saved = save_signal_to_db(
                            professor_id, signal_type, quote, source_url, radar_run_id
                        )
                        results.append(saved)
                        if saved["inserted"]:
                            hits += 1
                            print(f"Hiring evidence found for {professor['name']}: {source_url}")
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
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return {
        "professors_checked": checked,
        "signals_added": hits,
        "results": results,
        "timed_out": timed_out,
        "checked_professor_ids": checked_professor_ids,
    }


if __name__ == "__main__":
    print(scan_hiring_signals())
