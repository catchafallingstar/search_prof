from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.websearch import search_web
from settings import setting, setting_int

OPENALEX_INSTITUTIONS_URL = "https://api.openalex.org/institutions"
FACULTY_VERIFICATION_VERSION = 3
FACULTY_TITLE_PATTERN = re.compile(
    r"\b(?:tenure[- ]track\s+)?(?:assistant|associate|full|research|clinical|teaching|adjunct|visiting)?\s*"
    r"professor(?:\s+of\s+practice)?\b|\bmember\s+of\s+the\s+(?:graduate\s+)?faculty\b",
    re.IGNORECASE,
)
NON_FACULTY_PATTERN = re.compile(
    r"\b(?:ph\.?d\.?|doctoral|graduate|undergraduate)\s+(?:student|candidate|researcher)\b|"
    r"\bpostdoctoral\s+(?:fellow|researcher|associate)\b|\bdata\s+scientist\b|"
    r"\bresearch\s+assistant\b|\balumn(?:us|a|i)\b",
    re.IGNORECASE,
)
NEW_FACULTY_PATTERN = re.compile(
    r"\b(?:incoming|newly\s+appointed|joined|joining|starting)\b.{0,100}\b(?:assistant\s+)?professor\b|"
    r"\b(?:assistant\s+)?professor\b.{0,100}\b(?:incoming|joined|joining|starting)\b",
    re.IGNORECASE,
)
NON_PROFILE_URL_PATTERN = re.compile(
    r"(?:doctoral|graduate|recent)[-_ /]*(?:graduates?|students?)|"
    r"alumni|commencement|dissertation|theses",
    re.IGNORECASE,
)
TITLE_AFTER_NAME_TOKENS = {
    "at", "directory", "faculty", "homepage", "md", "phd", "profile",
    "professional", "s", "website",
}
INSTITUTION_STOPWORDS = {
    "and", "at", "college", "of", "school", "system", "the", "university",
}


def _name_tokens(name: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", name.casefold()) if len(token) > 1]


def _identity_matches(name: str, text: str) -> bool:
    tokens = _name_tokens(name)
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
    return bool(tokens and re.search(rf"\b{re.escape(' '.join(tokens))}\b", normalized))


def _profile_title_matches(name: str, page_title: str) -> bool:
    """Require the profile title to identify this exact person, not a list entry."""
    expected = _name_tokens(name)
    if not expected:
        return False
    for segment in re.split(r"\s*(?:\||·|—|–)\s*", page_title):
        tokens = _name_tokens(segment)
        while tokens and tokens[0] in {"dr", "prof", "professor"}:
            tokens.pop(0)
        if tokens[: len(expected)] != expected:
            continue
        remainder = tokens[len(expected):]
        if not remainder or all(token in TITLE_AFTER_NAME_TOKENS for token in remainder[:3]):
            return True
    return False


def _institution_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in INSTITUTION_STOPWORDS
    }


def _institution_continuity(
    claimed_institution: str,
    current_institution: str,
    page_text: str,
) -> bool:
    """Link an old publication affiliation to a current official appointment."""
    claimed = _institution_tokens(claimed_institution)
    current = _institution_tokens(current_institution)
    if claimed and current and (len(claimed & current) / min(len(claimed), len(current))) >= 0.5:
        return True
    page_tokens = set(re.findall(r"[a-z0-9]+", page_text.casefold()))
    required = 2 if len(claimed) >= 2 else 1
    return bool(claimed and len(claimed & page_tokens) >= required)


def _identity_context(name: str, text: str, window: int = 350) -> str:
    lowered = text.casefold()
    variants = [name.casefold(), " ".join(_name_tokens(name))]
    positions: list[int] = []
    for value in variants:
        if not value:
            continue
        positions.extend(match.start() for match in re.finditer(re.escape(value), lowered))
    positions = sorted(set(positions))
    if not positions:
        return ""
    # Repeated page titles and navigation often contain the name before the
    # actual profile block. Prefer an occurrence whose nearby text contains a
    # role, while keeping the wider context for evidence and new-faculty dates.
    for pattern in (FACULTY_TITLE_PATTERN, NON_FACULTY_PATTERN):
        for position in positions:
            nearby = text[max(0, position - 100): position + len(name) + 180]
            if pattern.search(nearby):
                return text[max(0, position - window): position + len(name) + window]
    position = positions[0]
    return text[max(0, position - window): position + len(name) + window]


def _edu_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    labels = host.split(".")
    if len(labels) >= 2 and labels[-1] == "edu":
        return ".".join(labels[-2:])
    return ""


def _fetch_official_page(url: str, max_redirects: int = 4) -> tuple[str, str]:
    current_url = url
    for _ in range(max_redirects + 1):
        if not _edu_domain(current_url) or not is_public_http_url(current_url, resolve_dns=True):
            return "", ""
        response = requests.get(
            current_url,
            headers={"User-Agent": "ScholarRadar/1.0 (faculty verification indexer)"},
            timeout=8,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                return "", ""
            current_url = urljoin(current_url, location)
            continue
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return "", ""
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "noscript"]):
            element.decompose()
        title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
        return " ".join(soup.get_text(" ", strip=True).split())[:200_000], title
    return "", ""


def _institution_name_from_title(title: str) -> str:
    """Extract a conservative institution name from an official root title."""
    for part in re.split(r"\s*(?:\||·|—|–)\s*", title):
        clean = " ".join(part.split()).strip(" -")
        if (
            4 <= len(clean) <= 100
            and re.search(r"\b(?:university|college|institute of technology)\b", clean, re.I)
            and not re.search(r"\b(?:department|school|faculty|admissions|home)\b", clean, re.I)
        ):
            return clean
    return ""


@lru_cache(maxsize=256)
def _institution_for_domain(domain: str) -> str:
    if not domain:
        return ""
    term = domain.split(".")[0].replace("-", " ")
    try:
        params: dict[str, object] = {"search": term, "per_page": 10}
        email = setting("OPENALEX_EMAIL").strip()
        if email:
            params["mailto"] = email
        api_key = setting("OPENALEX_API_KEY").strip()
        if api_key:
            params["api_key"] = api_key
        response = requests.get(OPENALEX_INSTITUTIONS_URL, params=params, timeout=8)
        response.raise_for_status()
        for institution in response.json().get("results", []):
            homepage_domain = _edu_domain(str(institution.get("homepage_url") or ""))
            if (
                homepage_domain == domain
                and str(institution.get("type") or "").casefold() == "education"
                and str(institution.get("country_code") or "").upper() == "US"
            ):
                return str(institution.get("display_name") or "").strip()
    except (OSError, ValueError, requests.RequestException):
        pass
    # OpenAlex and the works search share a public rate limit.  The official
    # university root page gives us a reliable fallback without maintaining a
    # fragile hand-written list of thousands of institutions.
    try:
        _, root_title = _fetch_official_page(f"https://{domain}/")
        return _institution_name_from_title(root_title)
    except (OSError, ValueError, requests.RequestException):
        return ""


def _extract_appointment_year(text: str) -> int | None:
    current_year = datetime.now(timezone.utc).year
    for match in re.finditer(r"\b(20\d{2})\b", text):
        year = int(match.group(1))
        nearby = text[max(0, match.start() - 120): match.end() + 120]
        if current_year - 6 <= year <= current_year + 1 and NEW_FACULTY_PATTERN.search(nearby):
            return year
    return None


def inspect_faculty_result(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Classify an exact-name individual profile on an official university page."""
    url = str(result.get("href") or "").strip()
    domain = _edu_domain(url)
    if (
        not domain
        or not is_public_http_url(url)
        or NON_PROFILE_URL_PATTERN.search(urlparse(url).path)
    ):
        return {"status": "UNVERIFIED"}
    summary = " ".join(
        str(result.get(key) or "") for key in ("title", "body", "href")
    )
    if not _identity_matches(str(candidate["name"]), summary):
        return {"status": "UNVERIFIED"}

    try:
        page_text, page_title = _fetch_official_page(url)
    except (OSError, requests.RequestException):
        page_text, page_title = "", ""
    if not page_text or not _profile_title_matches(str(candidate["name"]), page_title):
        return {"status": "UNVERIFIED"}
    context = _identity_context(str(candidate["name"]), f"{page_title} {page_text}")
    if not context or not _identity_matches(str(candidate["name"]), context):
        return {"status": "UNVERIFIED"}
    title_match = FACULTY_TITLE_PATTERN.search(context)
    negative_match = NON_FACULTY_PATTERN.search(context)
    institution = _institution_for_domain(domain)
    continuity = _institution_continuity(
        str(candidate.get("institution_name") or ""), institution, page_text
    )
    # Faculty identity and research relevance are deliberately independent.
    # Sparse directory pages often omit research keywords, so absence of a
    # query term must never turn a confirmed faculty identity into CONFLICT.
    # Topic relevance is ranked from the candidate's matching publications.
    if title_match and continuity:
        appointment_year = _extract_appointment_year(context)
        return {
            "status": "VERIFIED",
            "title": " ".join(title_match.group(0).split()).title(),
            "source_url": url,
            "source_domain": domain,
            "institution_name": institution or str(candidate.get("institution_name") or ""),
            "evidence_text": " ".join(context.split())[:700],
            "confidence": 0.97,
            "appointment_year": appointment_year,
            "career_stage": "NEW_AP" if (
                "assistant professor" in title_match.group(0).casefold()
                and (appointment_year or NEW_FACULTY_PATTERN.search(context))
            ) else None,
        }
    if title_match and not continuity:
        return {
            "status": "CONFLICT",
            "source_url": url,
            "source_domain": domain,
            "institution_name": institution or str(candidate.get("institution_name") or ""),
            "evidence_text": (
                "The official faculty page does not match the candidate's "
                "institution. This may be a different person with the same name."
            ),
            "confidence": 0.92,
        }
    if negative_match and not title_match:
        return {
            "status": "NOT_FACULTY",
            "source_url": url,
            "source_domain": domain,
            "institution_name": institution or str(candidate.get("institution_name") or ""),
            "evidence_text": " ".join(context.split())[:700],
            "confidence": 0.90 if page_text else 0.78,
        }
    return {"status": "UNVERIFIED"}


def verify_faculty_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    name = str(candidate["name"])
    institution = str(candidate.get("institution_name") or "").strip()
    # Search the person's current role first. Publication affiliations can be
    # several years old, so leading with the paper institution can hide a
    # newly appointed professor at a different university.
    queries = [
        f'"{name}" faculty professor',
        f'"{name}" "{institution}" professor faculty' if institution else "",
    ]
    seen_urls: set[str] = set()
    negative: dict[str, Any] | None = None
    for query in (value for value in queries if value):
        try:
            results = search_web(query, max_results=3)
        except Exception:
            continue
        for result in results:
            url = str(result.get("href") or "").strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)
            inspected = inspect_faculty_result(candidate, result)
            if inspected.get("status") == "VERIFIED":
                return inspected
            if inspected.get("status") in {"NOT_FACULTY", "CONFLICT"} and negative is None:
                negative = inspected
    return negative or {"status": "UNVERIFIED", "confidence": 0.0}


def _save_result(candidate: dict[str, Any], result: dict[str, Any]) -> None:
    professor_id = int(candidate["id"])
    status = str(result.get("status") or "UNVERIFIED")
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            institution_name = str(result.get("institution_name") or candidate["institution_name"])
            institution_id = candidate.get("institution_id")
            if status == "VERIFIED" and institution_name:
                cursor.execute(
                    """
                    INSERT INTO institutions (name, country_code)
                    VALUES (%s, 'US')
                    ON CONFLICT (name) DO UPDATE
                    SET country_code = COALESCE(institutions.country_code, 'US')
                    RETURNING id
                    """,
                    (institution_name,),
                )
                institution_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                UPDATE professors
                SET faculty_status = CASE
                        WHEN faculty_status = 'VERIFIED' AND %s = 'UNVERIFIED'
                            THEN 'MANUAL_REVIEW'
                        ELSE %s
                    END,
                    faculty_title = CASE
                        WHEN %s = 'VERIFIED' THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE faculty_title
                    END,
                    faculty_source_url = CASE
                        WHEN %s = 'VERIFIED' THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE faculty_source_url
                    END,
                    faculty_verification_method = CASE
                        WHEN %s = 'VERIFIED' THEN 'official_directory'
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE faculty_verification_method
                    END,
                    faculty_verification_version = %s,
                    faculty_confidence = %s,
                    faculty_checked_at = NOW(),
                    faculty_verified_at = CASE WHEN %s = 'VERIFIED' THEN NOW() ELSE NULL END,
                    next_identity_check_at = NOW() + CASE
                        WHEN %s = 'VERIFIED' THEN INTERVAL '90 days'
                        WHEN %s = 'NOT_FACULTY' THEN INTERVAL '75 days'
                        WHEN %s = 'CONFLICT' THEN INTERVAL '45 days'
                        ELSE INTERVAL '30 days'
                    END,
                    official_institution_domain = CASE
                        WHEN %s = 'VERIFIED' THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE official_institution_domain
                    END,
                    appointment_year = COALESCE(%s, appointment_year),
                    career_stage = COALESCE(%s, career_stage),
                    previous_institutions = CASE
                        WHEN %s = 'VERIFIED'
                             AND institution_name <> %s
                             AND NOT (institution_name = ANY(previous_institutions))
                            THEN array_append(previous_institutions, institution_name)
                        ELSE previous_institutions
                    END,
                    institution_id = CASE WHEN %s = 'VERIFIED' THEN %s ELSE institution_id END,
                    institution_name = CASE WHEN %s = 'VERIFIED' THEN %s ELSE institution_name END,
                    homepage_url = CASE WHEN %s = 'VERIFIED' THEN %s ELSE homepage_url END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    status, status,
                    status, result.get("title"),
                    status, result.get("source_url"),
                    status,
                    FACULTY_VERIFICATION_VERSION,
                    float(result.get("confidence") or 0),
                    status,
                    status, status, status,
                    status, result.get("source_domain"),
                    result.get("appointment_year"),
                    result.get("career_stage"),
                    status, institution_name,
                    status, institution_id,
                    status, institution_name,
                    status, result.get("source_url"),
                    professor_id,
                ),
            )
            if result.get("source_url"):
                cursor.execute(
                    """
                    INSERT INTO faculty_verification_evidence (
                        professor_id, source_url, source_domain, observed_title,
                        observed_institution, evidence_text, verification_status,
                        confidence, checked_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (professor_id, source_url) DO UPDATE
                    SET observed_title = EXCLUDED.observed_title,
                        observed_institution = EXCLUDED.observed_institution,
                        evidence_text = EXCLUDED.evidence_text,
                        verification_status = EXCLUDED.verification_status,
                        confidence = EXCLUDED.confidence,
                        checked_at = NOW()
                    """,
                    (
                        professor_id,
                        result["source_url"],
                        result.get("source_domain") or "",
                        result.get("title"),
                        institution_name,
                        result.get("evidence_text"),
                        status,
                        float(result.get("confidence") or 0),
                    ),
                )


def verify_faculty_candidates(
    professor_ids: list[int],
    max_candidates: int | None = None,
) -> dict[str, Any]:
    """Verify a bounded set and return only candidates allowed in public results."""
    if not professor_ids:
        return {"verified_ids": [], "checked": 0, "evaluated": 0, "verified": 0}
    ordered_ids = list(dict.fromkeys(int(value) for value in professor_ids))
    if max_candidates is not None:
        ordered_ids = ordered_ids[: max(1, int(max_candidates))]
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, institution_id, institution_name, homepage_url,
                       research_domain, faculty_status, faculty_checked_at,
                       next_identity_check_at,
                       faculty_verification_method, faculty_verification_version
                FROM professors
                WHERE id = ANY(%s)
                """,
                (ordered_ids,),
            )
            by_id = {int(row["id"]): row for row in cursor.fetchall()}

    verified_ids: list[int] = []
    pending: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for professor_id in ordered_ids:
        candidate = by_id.get(professor_id)
        if not candidate:
            continue
        age_days = (
            (now - candidate["faculty_checked_at"]).total_seconds() / 86_400
            if candidate.get("faculty_checked_at") else None
        )
        refresh_is_current = (
            candidate.get("next_identity_check_at") is not None
            and candidate["next_identity_check_at"] > now
        )
        if (
            candidate["faculty_status"] == "VERIFIED"
            and (
                candidate.get("faculty_verification_method") == "manual_review"
                or int(candidate.get("faculty_verification_version") or 0)
                    >= FACULTY_VERIFICATION_VERSION
            )
            and (
                candidate.get("faculty_verification_method") == "manual_review"
                or refresh_is_current
                or (candidate.get("next_identity_check_at") is None
                    and age_days is not None and age_days <= 90)
            )
        ):
            verified_ids.append(professor_id)
        elif (
            candidate["faculty_status"] in {
                "NOT_FACULTY", "UNVERIFIED", "CONFLICT", "MANUAL_REVIEW"
            }
            and int(candidate.get("faculty_verification_version") or 0) >= FACULTY_VERIFICATION_VERSION
            and (
                refresh_is_current
                or (candidate.get("next_identity_check_at") is None
                    and age_days is not None and age_days <= 30)
            )
        ):
            continue
        else:
            pending.append(candidate)

    workers = setting_int("FACULTY_VERIFY_MAX_WORKERS", 3, 1, 6)
    checked = 0
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as executor:
        futures = {executor.submit(verify_faculty_candidate, candidate): candidate for candidate in pending}
        for future in as_completed(futures):
            candidate = futures[future]
            checked += 1
            try:
                result = future.result()
            except Exception:
                result = {"status": "UNVERIFIED", "confidence": 0.0}
            _save_result(candidate, result)
            if result.get("status") == "VERIFIED":
                verified_ids.append(int(candidate["id"]))

    verified_set = set(verified_ids)
    verified_ids = [value for value in ordered_ids if value in verified_set]
    return {
        "verified_ids": verified_ids,
        "checked": checked,
        "evaluated": len(ordered_ids),
        "verified": len(verified_ids),
    }


def get_cached_faculty_decisions(professor_ids: list[int]) -> dict[str, list[int]]:
    """Return current positive and negative decisions without launching web checks."""
    ordered_ids = list(dict.fromkeys(int(value) for value in professor_ids))
    if not ordered_ids:
        return {"verified_ids": [], "decided_ids": []}
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, faculty_status FROM professors
                WHERE id = ANY(%s)
                  AND faculty_verification_version >= %s
                  AND (
                      (faculty_status = 'VERIFIED' AND (
                           next_identity_check_at > NOW()
                           OR (next_identity_check_at IS NULL
                               AND faculty_checked_at >= NOW() - INTERVAL '90 days')
                       ))
                      OR
                      (faculty_status IN (
                           'NOT_FACULTY', 'UNVERIFIED', 'CONFLICT', 'MANUAL_REVIEW'
                       )
                       AND (
                           next_identity_check_at > NOW()
                           OR (next_identity_check_at IS NULL
                               AND faculty_checked_at >= NOW() - INTERVAL '30 days')
                       ))
                  )
                """,
                (ordered_ids, FACULTY_VERIFICATION_VERSION),
            )
            rows = list(cursor.fetchall())
    decided = {int(row["id"]) for row in rows}
    verified = {
        int(row["id"]) for row in rows if row["faculty_status"] == "VERIFIED"
    }
    return {
        "verified_ids": [value for value in ordered_ids if value in verified],
        "decided_ids": [value for value in ordered_ids if value in decided],
    }


def get_cached_verified_faculty_ids(professor_ids: list[int]) -> list[int]:
    """Compatibility wrapper for callers that only need positive decisions."""
    return get_cached_faculty_decisions(professor_ids)["verified_ids"]
