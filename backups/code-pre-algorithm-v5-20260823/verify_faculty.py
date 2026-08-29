from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.homepagefinder import is_public_http_url
from ingestion.identity_ai import assess_identity_with_gemini
from ingestion.websearch import search_web
from settings import setting, setting_int

OPENALEX_INSTITUTIONS_URL = "https://api.openalex.org/institutions"
OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
FACULTY_VERIFICATION_VERSION = 4
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
HISTORICAL_APPOINTMENT_URL_PATTERN = re.compile(
    r"(?:^|/)(?:news|events?|press|stories|announcements?)(?:/|$)|"
    r"new[-_/ ]*faculty|faculty[-_/ ]*hires?",
    re.IGNORECASE,
)
CURRENT_FACULTY_LISTING_PATTERN = re.compile(
    r"faculty|directory|people|profile|biograph|academic",
    re.IGNORECASE,
)
TITLE_AFTER_NAME_TOKENS = {
    "at", "directory", "faculty", "homepage", "md", "phd", "profile",
    "professional", "s", "website",
}
INSTITUTION_STOPWORDS = {
    "and", "at", "college", "of", "school", "system", "the", "university",
}
INSTITUTION_HINT_PATTERN = re.compile(
    r"\b(?:"
    r"University\s+of\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*"
    r"(?:\s+(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*|of|the|at|and)){0,5}"
    r"|(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*\s+){1,5}(?:University|College)"
    r"|(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*\s+){1,5}Institute\s+of\s+Technology"
    r")\b"
)


def _ascii_fold(value: str) -> str:
    compact = value.translate(str.maketrans("", "", "'’ʻ`"))
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", compact)
        if not unicodedata.combining(character)
    )


def _name_tokens(name: str) -> list[str]:
    folded = _ascii_fold(name).casefold()
    return [token for token in re.findall(r"[a-z0-9]+", folded) if len(token) > 1]


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
    folded = _ascii_fold(value).casefold()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", folded)
        if len(token) > 2 and token not in INSTITUTION_STOPWORDS
    }


def _institution_similarity(left: str, right: str) -> float:
    left_tokens = _institution_tokens(left)
    right_tokens = _institution_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _institution_continuity(
    claimed_institution: str,
    current_institution: str,
    page_text: str,
) -> bool:
    """Link an old publication affiliation to a current official appointment."""
    claimed = _institution_tokens(claimed_institution)
    current = _institution_tokens(current_institution)
    if claimed and current and _institution_similarity(
        claimed_institution, current_institution
    ) >= 0.5:
        return True
    page_tokens = set(
        re.findall(r"[a-z0-9]+", _ascii_fold(page_text).casefold())
    )
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


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _paper_identity_link(candidate: dict[str, Any], page_text: str, quote: str) -> bool:
    """Require a DOI or a distinctive recent title to connect a moved scholar."""
    searchable = _normalized_text(f"{page_text} {quote}")
    for paper in list(candidate.get("recent_papers") or []):
        doi = str(paper.get("doi") or "").strip().casefold()
        if doi and doi in searchable:
            return True
        title_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", str(paper.get("title") or "").casefold())
            if len(token) > 3
        ]
        if len(title_tokens) < 4:
            continue
        present = sum(1 for token in set(title_tokens) if token in searchable)
        if present / len(set(title_tokens)) >= 0.8:
            return True
    return False


def _openalex_author_id(value: str) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1].upper()


def _same_normalized_name(left: str, right: str) -> bool:
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    return bool(
        left_tokens
        and right_tokens
        and (left_tokens == right_tokens or left_tokens == list(reversed(right_tokens)))
    )


@lru_cache(maxsize=1024)
def _openalex_exact_name_profiles(
    name: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return exact-name OpenAlex fragments and their known institutions.

    OpenAlex sometimes splits one scholar into several author IDs as their
    affiliation changes. These fragments are supporting evidence only; an
    official university page is still required for a positive faculty decision.
    """
    try:
        params: dict[str, object] = {"search": name, "per_page": 25}
        email = setting("OPENALEX_EMAIL").strip()
        if email:
            params["mailto"] = email
        api_key = setting("OPENALEX_API_KEY").strip()
        if api_key:
            params["api_key"] = api_key
        response = requests.get(OPENALEX_AUTHORS_URL, params=params, timeout=8)
        response.raise_for_status()
        profiles: list[tuple[str, tuple[str, ...]]] = []
        for author in response.json().get("results", []):
            if not _same_normalized_name(
                name, str(author.get("display_name") or "")
            ):
                continue
            institutions: list[str] = []
            for institution in author.get("last_known_institutions") or []:
                if str(institution.get("type") or "").casefold() != "education":
                    continue
                display_name = str(institution.get("display_name") or "").strip()
                if display_name:
                    institutions.append(display_name)
            profiles.append(
                (
                    _openalex_author_id(str(author.get("id") or "")),
                    tuple(dict.fromkeys(institutions)),
                )
            )
        return tuple(profiles)
    except (OSError, TypeError, ValueError, requests.RequestException):
        return ()


def _openalex_move_corroborates(
    candidate: dict[str, Any], observed_institution: str
) -> bool:
    """Link a rare-name OpenAlex fragment to a new official appointment."""
    name = str(candidate.get("name") or "")
    name_tokens = _name_tokens(name)
    if len(name_tokens) < 2 or sum(map(len, name_tokens)) < 10:
        return False
    profiles = _openalex_exact_name_profiles(name)
    maximum = setting_int("FACULTY_MOVE_MAX_EXACT_NAME_RECORDS", 8, 1, 25)
    if not profiles or len(profiles) > maximum:
        return False
    candidate_id = _openalex_author_id(str(candidate.get("openalex_id") or ""))
    if not candidate_id or candidate_id not in {row[0] for row in profiles}:
        return False
    institutions = [institution for _, values in profiles for institution in values]
    claimed = str(candidate.get("institution_name") or "")
    claimed_supported = any(
        _institution_similarity(claimed, institution) >= 0.5
        for institution in institutions
    )
    observed_supported = any(
        _institution_similarity(observed_institution, institution) >= 0.5
        for institution in institutions
    )
    return claimed_supported and observed_supported


def _directory_role_after_name(name: str, context: str) -> re.Match[str] | None:
    """Accept a tight `Name … Professor` row on an official directory page."""
    tokens = _name_tokens(name)
    if not tokens:
        return None
    name_pattern = re.compile(
        r"\b" + r"[\W_]+".join(map(re.escape, tokens)) + r"\b",
        re.IGNORECASE,
    )
    for name_match in name_pattern.finditer(_ascii_fold(context)):
        after = context[name_match.end(): name_match.end() + 140]
        role_match = FACULTY_TITLE_PATTERN.search(after)
        if not role_match or role_match.start() > 90:
            continue
        bridge = after[: role_match.start()]
        if re.search(
            r"\b[A-Z][A-Za-z'’.-]{2,}\s+[A-Z][A-Za-z'’.-]{2,}\b",
            bridge,
        ):
            continue
        return role_match
    return None


def _is_current_faculty_listing(url: str, page_title: str) -> bool:
    path = urlparse(url).path
    if HISTORICAL_APPOINTMENT_URL_PATTERN.search(f"{path} {page_title}"):
        return False
    return bool(CURRENT_FACULTY_LISTING_PATTERN.search(f"{path} {page_title}"))


def _institution_hints(name: str, results: list[dict[str, Any]]) -> list[str]:
    """Extract employer clues from snippets without treating them as evidence."""
    hints: list[str] = []
    for result in results:
        summary = " ".join(
            str(result.get(key) or "") for key in ("title", "body")
        )
        if not _identity_matches(name, summary):
            continue
        for match in INSTITUTION_HINT_PATTERN.finditer(summary):
            hint = " ".join(match.group(0).split()).strip(" ,.-")
            if hint and hint not in hints:
                hints.append(hint)
    return hints


def validate_ai_identity_assessment(
    candidate: dict[str, Any],
    pages: list[dict[str, Any]],
    assessment: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert an AI extraction into a decision only after deterministic checks."""
    if not assessment:
        return None
    decision = str(assessment.get("decision") or "").strip().upper()
    if decision not in {"VERIFIED", "NOT_FACULTY", "CONFLICT", "UNVERIFIED"}:
        return None
    try:
        confidence = float(assessment.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    minimum = setting_int("GEMINI_IDENTITY_MIN_CONFIDENCE_PERCENT", 85, 50, 99) / 100
    if confidence < minimum:
        return None

    selected_url = str(assessment.get("selected_source_url") or "").strip()
    page = next(
        (item for item in pages if str(item.get("source_url") or "") == selected_url),
        None,
    )
    if not page or not _edu_domain(selected_url):
        return None
    page_text = str(page.get("_page_text") or "")
    identity_quote = " ".join(
        str(assessment.get("identity_evidence_quote") or "").split()
    )
    link_quote = " ".join(str(assessment.get("identity_link_quote") or "").split())
    normalized_page = _normalized_text(page_text)
    if (
        not identity_quote
        or _normalized_text(identity_quote) not in normalized_page
        or not _identity_matches(str(candidate.get("name") or ""), identity_quote)
    ):
        return None
    if link_quote and _normalized_text(link_quote) not in normalized_page:
        return None

    title = " ".join(str(assessment.get("observed_title") or "").split())
    institution = " ".join(
        str(assessment.get("observed_institution") or "").split()
    ) or str(page.get("institution_name") or candidate.get("institution_name") or "")
    common = {
        "status": decision,
        "title": title or None,
        "source_url": selected_url,
        "source_domain": _edu_domain(selected_url),
        "institution_name": institution,
        "evidence_text": (
            f"AI-assisted evidence extraction: {str(assessment.get('reason') or '').strip()} "
            f'Evidence: "{identity_quote}"'
        )[:700],
        "confidence": min(0.94, confidence),
        "method": "gemini_assisted",
        "model_name": str(assessment.get("model_name") or ""),
        "prompt_version": int(assessment.get("prompt_version") or 0),
        "page_title": str(page.get("page_title") or ""),
        "_page_text": page_text,
    }

    if decision == "VERIFIED":
        if (
            not title
            or _normalized_text(title) not in normalized_page
            or _normalized_text(title) not in _normalized_text(identity_quote)
            or not FACULTY_TITLE_PATTERN.search(title)
            or NON_FACULTY_PATTERN.search(f"{title} {identity_quote}")
        ):
            return None
        same_institution = _institution_continuity(
            str(candidate.get("institution_name") or ""), institution, page_text
        )
        if not same_institution and not _paper_identity_link(candidate, page_text, link_quote):
            # The model may suspect a career move, but an exact publication or
            # DOI connection is required before making that decision public.
            common["status"] = "CONFLICT"
            common["evidence_text"] = (
                "AI found a plausible faculty page, but ScholarRadar could not "
                "connect it to the candidate using an institution, paper title, or DOI."
            )
            return common
        appointment_year = _extract_appointment_year(page_text)
        common["appointment_year"] = appointment_year
        common["career_stage"] = "NEW_AP" if (
            "assistant professor" in title.casefold()
            and (appointment_year or NEW_FACULTY_PATTERN.search(page_text))
        ) else None
        return common

    if decision == "NOT_FACULTY":
        if not NON_FACULTY_PATTERN.search(f"{title} {identity_quote}"):
            return None
        return common
    return common


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
    if not page_text:
        return {"status": "UNVERIFIED"}
    context = _identity_context(str(candidate["name"]), f"{page_title} {page_text}")
    if not context or not _identity_matches(str(candidate["name"]), context):
        return {"status": "UNVERIFIED"}
    title_match = FACULTY_TITLE_PATTERN.search(context)
    directory_title_match = (
        _directory_role_after_name(str(candidate["name"]), context)
        if _is_current_faculty_listing(url, page_title)
        else None
    )
    if directory_title_match is not None:
        title_match = directory_title_match
    negative_match = NON_FACULTY_PATTERN.search(context)
    institution = _institution_for_domain(domain)
    continuity = _institution_continuity(
        str(candidate.get("institution_name") or ""), institution, page_text
    )
    evidence_context = " ".join(context.split())[:700]
    page_evidence = {
        "source_url": url,
        "source_domain": domain,
        "institution_name": institution or str(candidate.get("institution_name") or ""),
        "evidence_text": evidence_context,
        "page_title": page_title,
        "_page_text": page_text,
    }
    # A person-specific profile title is required for the rule-only positive
    # decision. The optional AI extractor may still inspect a supplied official
    # directory page, but its output must pass stricter quotation/link checks.
    profile_title_matches = _profile_title_matches(str(candidate["name"]), page_title)
    role_is_person_specific = profile_title_matches or directory_title_match is not None
    # Faculty identity and research relevance are deliberately independent.
    # Sparse directory pages often omit research keywords, so absence of a
    # query term must never turn a confirmed faculty identity into CONFLICT.
    # Topic relevance is ranked from the candidate's matching publications.
    move_corroborated = bool(
        title_match
        and not continuity
        and role_is_person_specific
        and _openalex_move_corroborates(candidate, page_evidence["institution_name"])
    )
    if title_match and role_is_person_specific and (continuity or move_corroborated):
        appointment_year = _extract_appointment_year(context)
        return {
            **page_evidence,
            "status": "VERIFIED",
            "title": " ".join(title_match.group(0).split()).title(),
            "confidence": 0.94 if move_corroborated else 0.97,
            "method": (
                "official_directory_openalex_history"
                if move_corroborated
                else "official_directory"
            ),
            "evidence_text": (
                "A current official faculty listing is corroborated by rare-name "
                "OpenAlex affiliation records at both the previous and current "
                "institutions."
                if move_corroborated
                else evidence_context
            ),
            "appointment_year": appointment_year,
            "career_stage": "NEW_AP" if (
                "assistant professor" in title_match.group(0).casefold()
                and (appointment_year or NEW_FACULTY_PATTERN.search(context))
            ) else None,
        }
    if title_match and not role_is_person_specific:
        return {
            **page_evidence,
            "status": "UNVERIFIED",
            "title": " ".join(title_match.group(0).split()).title(),
            "confidence": 0.0,
            "method": "automatic_search",
        }
    if title_match and not continuity:
        return {
            **page_evidence,
            "status": "CONFLICT",
            "title": " ".join(title_match.group(0).split()).title(),
            "evidence_text": (
                "The official faculty page does not match the candidate's "
                "institution. This may be a different person with the same name."
            ),
            "confidence": 0.92,
            "method": "official_directory",
        }
    if negative_match and not title_match and profile_title_matches and continuity:
        return {
            **page_evidence,
            "status": "NOT_FACULTY",
            "title": " ".join(negative_match.group(0).split()).title(),
            "confidence": 0.90 if page_text else 0.78,
            "method": "official_directory",
        }
    return {
        **page_evidence,
        "status": "UNVERIFIED",
        "confidence": 0.0,
        "method": "automatic_search",
    }


def verify_faculty_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    name = str(candidate["name"])
    institution = str(candidate.get("institution_name") or "").strip()
    # Search the person's current role first. Publication affiliations can be
    # several years old, so leading with the paper institution can hide a
    # newly appointed professor at a different university.
    queries = [
        f'"{name}" "{institution}" professor faculty' if institution else "",
        f'"{name}" faculty professor',
    ]
    recent_papers = list(candidate.get("recent_papers") or [])
    if recent_papers and str(recent_papers[0].get("title") or "").strip():
        queries.append(f'"{name}" "{str(recent_papers[0]["title"])[:180]}"')
    seen_urls: set[str] = set()
    negative: dict[str, Any] | None = None
    ambiguous_pages: list[dict[str, Any]] = []
    mismatch_pages: list[dict[str, Any]] = []
    search_results: list[dict[str, Any]] = []

    def inspect_query(query: str) -> dict[str, Any] | None:
        nonlocal negative
        try:
            results = search_web(
                query,
                max_results=setting_int(
                    "FACULTY_VERIFY_RESULTS_PER_QUERY", 5, 3, 10
                ),
            )
        except Exception:
            return None
        search_results.extend(results)
        for result in results:
            url = str(result.get("href") or "").strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)
            inspected = inspect_faculty_result(candidate, result)
            if inspected.get("status") == "VERIFIED":
                return inspected
            if inspected.get("source_url") and inspected.get("_page_text"):
                ambiguous_pages.append(inspected)
            if inspected.get("status") == "CONFLICT":
                mismatch_pages.append(inspected)
            if inspected.get("status") == "NOT_FACULTY" and negative is None:
                negative = inspected
        return None

    for query in (value for value in queries if value):
        verified = inspect_query(query)
        if verified:
            return verified

    # Non-university snippets may reveal a possible current employer, but they
    # are clues only. Every positive decision below still requires an official
    # university page and independent identity corroboration.
    hint_limit = setting_int("FACULTY_VERIFY_MAX_INSTITUTION_HINTS", 2, 0, 4)
    hints = [
        hint
        for hint in _institution_hints(name, search_results)
        if _institution_similarity(hint, institution) < 0.5
    ][:hint_limit]
    for hint in hints:
        for query in (
            f'"{name}" "{hint}" professor faculty',
            f'site:.edu "{name}" "{hint}" professor',
        ):
            verified = inspect_query(query)
            if verified:
                return verified
    # An explicit official student/postdoc result is already a strong rule-based
    # decision. Spend AI quota only on unresolved pages or identity conflicts.
    if negative and negative.get("status") == "NOT_FACULTY":
        return negative
    assessment = assess_identity_with_gemini(candidate, ambiguous_pages)
    validated = validate_ai_identity_assessment(candidate, ambiguous_pages, assessment)
    if validated and validated.get("status") in {"VERIFIED", "NOT_FACULTY"}:
        return validated

    # A single different-university page is usually a search miss or a
    # namesake, not a task for staff. Escalate only when multiple official
    # domains contain plausible faculty profiles for the same person name.
    conflict_domains = {
        str(page.get("source_domain") or "")
        for page in mismatch_pages
        if page.get("source_domain")
    }
    if len(conflict_domains) >= 2:
        conflict = mismatch_pages[0]
        conflict["evidence_text"] = (
            "Multiple official universities list a faculty member with this name, "
            "and the available publication or affiliation evidence cannot select "
            "one identity safely."
        )
        conflict["alternative_evidence"] = [
            {
                key: page.get(key)
                for key in (
                    "status",
                    "title",
                    "source_url",
                    "source_domain",
                    "institution_name",
                    "evidence_text",
                    "confidence",
                    "method",
                )
            }
            for page in mismatch_pages
            if page.get("source_url")
        ]
        return conflict
    unresolved = mismatch_pages or ambiguous_pages
    if unresolved:
        best = unresolved[0]
        return {
            **best,
            "status": "UNVERIFIED",
            "confidence": 0.0,
            "method": "automatic_search",
            "evidence_text": (
                "A possible official page was found, but the current evidence does "
                "not safely connect it to this OpenAlex author."
            ),
        }
    return {
        "status": "UNVERIFIED",
        "confidence": 0.0,
        "method": "automatic_search",
    }


def _save_result(candidate: dict[str, Any], result: dict[str, Any]) -> None:
    professor_id = int(candidate["id"])
    status = str(result.get("status") or "UNVERIFIED")
    method = str(result.get("method") or (
        "official_directory" if result.get("source_url") else "automatic_search"
    ))
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
                    faculty_verification_method = %s,
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
                    method,
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
            evidence_results = [result, *list(result.get("alternative_evidence") or [])]
            saved_urls: set[str] = set()
            for evidence_result in evidence_results:
                source_url = str(evidence_result.get("source_url") or "").strip()
                if not source_url or source_url in saved_urls:
                    continue
                saved_urls.add(source_url)
                evidence_status = str(evidence_result.get("status") or status)
                evidence_method = str(
                    evidence_result.get("method") or method
                )
                evidence_institution = str(
                    evidence_result.get("institution_name") or institution_name
                )
                cursor.execute(
                    """
                    INSERT INTO faculty_verification_evidence (
                        professor_id, source_url, source_domain, observed_title,
                        observed_institution, evidence_text, verification_status,
                        confidence, decision_method, model_name, prompt_version,
                        checked_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (professor_id, source_url) DO UPDATE
                    SET observed_title = EXCLUDED.observed_title,
                        observed_institution = EXCLUDED.observed_institution,
                        evidence_text = EXCLUDED.evidence_text,
                        verification_status = EXCLUDED.verification_status,
                        confidence = EXCLUDED.confidence,
                        decision_method = EXCLUDED.decision_method,
                        model_name = EXCLUDED.model_name,
                        prompt_version = EXCLUDED.prompt_version,
                        checked_at = NOW()
                    """,
                    (
                        professor_id,
                        source_url,
                        evidence_result.get("source_domain") or "",
                        evidence_result.get("title"),
                        evidence_institution,
                        evidence_result.get("evidence_text"),
                        evidence_status,
                        float(evidence_result.get("confidence") or 0),
                        evidence_method,
                        evidence_result.get("model_name"),
                        evidence_result.get("prompt_version"),
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
                SELECT id, openalex_id, name, institution_id, institution_name, homepage_url,
                       research_domain, faculty_status, faculty_checked_at,
                       next_identity_check_at,
                       faculty_verification_method, faculty_verification_version
                FROM professors
                WHERE id = ANY(%s)
                """,
                (ordered_ids,),
            )
            by_id = {int(row["id"]): row for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT ranked.professor_id, ranked.title, ranked.publication_year,
                       ranked.doi, ranked.author_position
                FROM (
                    SELECT pp.professor_id, paper.title, paper.publication_year,
                           paper.doi, pp.author_position,
                           ROW_NUMBER() OVER (
                               PARTITION BY pp.professor_id
                               ORDER BY paper.publication_year DESC NULLS LAST,
                                        paper.citation_count DESC
                           ) AS position
                    FROM professor_papers pp
                    JOIN papers paper ON paper.id = pp.paper_id
                    WHERE pp.professor_id = ANY(%s)
                ) ranked
                WHERE ranked.position <= 5
                ORDER BY ranked.professor_id, ranked.position
                """,
                (ordered_ids,),
            )
            papers_by_professor: dict[int, list[dict[str, Any]]] = {}
            for paper in cursor.fetchall():
                papers_by_professor.setdefault(int(paper["professor_id"]), []).append(paper)
            for professor_id, candidate in by_id.items():
                candidate["recent_papers"] = papers_by_professor.get(professor_id, [])

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
