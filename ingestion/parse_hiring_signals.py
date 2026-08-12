import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.homepagefinder import get_professor_homepage, is_public_http_url
from ingestion.matchers import clean_and_extract_hiring_quote, extract_roles_and_funding, get_text_hash, is_valid_signal_text
from ingestion.socialradar import check_social_hiring

NEW_AP_PATTERN = re.compile(
    r"(?:joining|starting\s+(?:my\s+)?lab|new\s+assistant\s+professor|incoming\s+(?:faculty|professor))",
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


def fetch_and_parse_homepage(homepage_url: str) -> list[str]:
    if not is_public_http_url(homepage_url, resolve_dns=True):
        return []
    try:
        time.sleep(random.uniform(0.2, 0.5))
        response = _fetch_with_safe_redirects(homepage_url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return []
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
        return matches[:5]
    except (OSError, requests.RequestException) as error:
        print(f"Homepage fetch failed for {homepage_url}: {error}")
        return []


def save_signal_to_db(professor_id: int, signal_type: str, raw_quote: str, source_url: str) -> bool:
    roles, has_funding = extract_roles_and_funding(raw_quote)
    is_new_ap = bool(NEW_AP_PATTERN.search(raw_quote))
    score_boost = 40 + (20 if has_funding else 0) + (30 if is_new_ap else 0)
    quote_hash = get_text_hash(f"{professor_id}|{source_url}|{raw_quote}")
    confidence = "high" if signal_type == "homepage" else "medium"
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO hiring_signals (
                    professor_id, raw_text_hash, signal_type, confidence, raw_text,
                    source_url, last_checked_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL '120 days')
                ON CONFLICT (raw_text_hash) DO NOTHING
                RETURNING id
                """,
                (professor_id, quote_hash, signal_type, confidence, raw_quote, source_url),
            )
            signal_row = cursor.fetchone()
            inserted = signal_row is not None
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
                cursor.execute(
                    """
                    SELECT name, institution_id, institution_name, research_domain
                    FROM professors WHERE id = %s
                    """,
                    (professor_id,),
                )
                professor = cursor.fetchone()
                position_type = {
                    "PhD": "PhD",
                    "Postdoc": "Postdoc",
                    "Research Assistant": "Research Assistant",
                    "Intern": "Internship",
                }.get(roles[0] if roles else "", "PhD")
                cursor.execute(
                    """
                    INSERT INTO opportunities (
                        professor_id, institution_id, title, institution_name,
                        professor_name, research_area, position_type, description,
                        funding_status, gpa_policy, international_eligible,
                        application_url, source_kind, status, published_at, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        'unknown', 'not_stated', NULL, %s, 'public_signal',
                        'pending', NULL, NOW() + INTERVAL '120 days'
                    ) RETURNING id
                    """,
                    (
                        professor_id,
                        professor["institution_id"],
                        f"{position_type} recruiting signal in {professor['research_domain'] or 'the lab'}",
                        professor["institution_name"],
                        professor["name"],
                        professor["research_domain"] or "Not classified",
                        position_type,
                        raw_quote,
                        source_url,
                    ),
                )
                opportunity_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO opportunity_sources (
                        opportunity_id, source_external_id, source_type, source_url,
                        evidence_text, last_checked_at, confidence
                    ) VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                    """,
                    (opportunity_id, quote_hash, signal_type, source_url, raw_quote, confidence),
                )
            else:
                cursor.execute(
                    """
                    UPDATE hiring_signals
                    SET last_checked_at = NOW(), expires_at = NOW() + INTERVAL '120 days'
                    WHERE raw_text_hash = %s
                    """,
                    (quote_hash,),
                )
                cursor.execute(
                    """
                    UPDATE opportunity_sources
                    SET last_checked_at = NOW()
                    WHERE source_external_id = %s
                    """,
                    (quote_hash,),
                )
            return inserted


def process_single_professor(professor: dict[str, Any], domain_name: str | None = None) -> tuple[str, int, str, str] | None:
    homepage = get_professor_homepage(
        professor["name"],
        professor["institution_name"],
        openalex_homepage=professor.get("homepage_url"),
    )
    if homepage:
        sentences = fetch_and_parse_homepage(homepage)
        quote = clean_and_extract_hiring_quote(". ".join(sentences))
        if quote:
            return "homepage", professor["id"], quote, homepage

    social_text, social_url = check_social_hiring(professor["name"], professor["institution_name"])
    quote = clean_and_extract_hiring_quote(social_text or "")
    if quote and social_url:
        return "social", professor["id"], quote, social_url
    return None


def scan_hiring_signals(
    domain_name: str | None = None,
    stop_check_callback: Callable[[], bool] | None = None,
) -> dict[str, int]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if domain_name:
                cursor.execute(
                    "SELECT id, name, institution_name, homepage_url FROM professors WHERE research_domain = %s",
                    (domain_name,),
                )
            else:
                cursor.execute("SELECT id, name, institution_name, homepage_url FROM professors")
            professors = list(cursor.fetchall())

    max_workers = max(1, min(8, int(os.getenv("RADAR_MAX_WORKERS", "2"))))
    hits = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_professor, professor, domain_name): professor for professor in professors}
        for future in as_completed(futures):
            if stop_check_callback and stop_check_callback():
                for pending in futures:
                    pending.cancel()
                break
            professor = futures[future]
            try:
                result = future.result()
                if result:
                    signal_type, professor_id, quote, source_url = result
                    if save_signal_to_db(professor_id, signal_type, quote, source_url):
                        hits += 1
                        print(f"Hiring evidence found for {professor['name']}: {source_url}")
            except Exception as error:
                print(f"Signal scan failed for {professor['name']}: {error}")
    return {"professors_checked": len(professors), "signals_added": hits}


if __name__ == "__main__":
    print(scan_hiring_signals())
