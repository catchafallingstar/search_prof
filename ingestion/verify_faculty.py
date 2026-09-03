from __future__ import annotations

import re
import json
import unicodedata
import time
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from calendar import monthrange
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from db import get_db_connection
from identity_schedule import recently_checked
from ingestion.affiliation_extract import institutions_in_text
from ingestion.homepagefinder import is_public_http_url
from ingestion.identity_ai import assess_identity_with_gemini
from ingestion.orcid_evidence import fetch_orcid_clues
from ingestion.openalex_client import OpenAlexUnavailable, openalex_get_json
from ingestion.paper_affiliations import (
    enrich_candidate_metadata_affiliations,
    enrich_candidate_paper_affiliations,
)
from ingestion.websearch import SearchUnavailable, search_web
from ingestion import verification_audit as audit_log
from ingestion.name_normalization import fold_name_text, name_tokens
from ingestion.institution_classifier import lookup_institution_classification
from ingestion.identity_sources import (source_kind, cv_homepage, linked_profile_leads,
                                        canonical_source_url, text_url_leads, excluded_profile_source)
from ingestion.institution_domains import (record_for_name, record_for_host,
    canonical_institution, academic_domain_hint, offshore_appointment,
    institutions_equivalent)
from settings import setting, setting_bool, setting_int

OPENALEX_INSTITUTIONS_URL = "https://api.openalex.org/institutions"
OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
FACULTY_VERIFICATION_VERSION = 18
FACULTY_TITLE_PATTERN = re.compile(
    r"\b(?:(?:tenure[- ]track|assistant|asst\.?|associate|assoc\.?|full|research|clinical|teaching|adjunct|adj\.?|visiting|distinguished|endowed)\s+){0,3}"
    r"(?:professor|prof\.)(?:\s+of\s+practice)?(?=\s|$|[.,;:)\]])|"
    r"\b(?:senior\s+|principal\s+|clinical\s+|teaching\s+|adjunct\s+|visiting\s+)?"
    r"(?:lecturer|lect\.|instructor|instr\.)(?=\s|$|[.,;:)\]])|"
    r"\bmember\s+of\s+the\s+(?:graduate\s+)?faculty\b",
    re.IGNORECASE,
)
NON_FACULTY_PATTERN = re.compile(
    r"\b(?:ph\.?\s*d\.?|doctoral|graduate|grad\.?|undergraduate|undergrad\.?)\s+"
    r"(?:students?|candidates?|researchers?)\b|"
    r"\bpost[- ]?doc\b|\bpost[- ]?doctoral\s+(?:fellows?|researchers?|associates?|scholars?)\b|"
    r"\bdata\s+scientists?\b|\bresearch\s+(?:assistants?|asst\.?)\b|"
    r"\balumn(?:us|a|i|ae)\b",
    re.IGNORECASE,
)
CURRENT_NONFACULTY_ROLE_PATTERN = re.compile(
    r"\b(?:(?:senior|staff|principal|lead)\s+){0,2}(?:machine\s+learning\s+|"
    r"ai\s+|computer\s+vision\s+)?(?:research\s+scientist|research\s+engineer|"
    r"software\s+engineer|data\s+scientist|scientist|engineer)\b|"
    r"\bpost[- ]?doc(?:toral)?\s+(?:scientist|fellow|researcher|associate|scholar)\b|"
    r"\b(?:industry|industrial|national[- ]lab)\s+researcher\b|"
    r"\b(?:[a-z][a-z-]*\s+){0,3}researcher\b|"
    r"\b(?:research|visiting)\s+scientist\b|\btechnical\s+lead\b|"
    r"\b(?:(?:senior|staff|principal|lead|chief|associate|assistant)\s+){0,2}"
    r"(?:engineering|product|project|program|operations?|software|systems?|data|"
    r"security|technology|technical|business|policy|education|school|laboratory|lab)?\s*"
    r"(?:manager|developer|analyst|consultant|architect|specialist|officer|"
    r"technician|teacher|educator|administrator|coordinator)\b",
    re.IGNORECASE,
)
CURRENT_ROLE_CUE_PATTERN = re.compile(
    r"\b(?:I\s+am|I\s*m|currently|current\s+appointment|present\s+position|"
    r"works?\s+as|serves?\s+as|joined)\b|"
    r"\b(?:manager|developer|analyst|consultant|architect|specialist|officer|"
    r"technician|teacher|educator|administrator|coordinator|engineer|scientist|"
    r"researcher)\s+(?:at|@)\b",
    re.IGNORECASE,
)
LINKEDIN_FACULTY_WORDING_PATTERN = re.compile(
    r"\b(?:professor|faculty|lecturer|instructor|dean|academic\s+faculty)\b",
    re.IGNORECASE,
)
NON_US_LOCATION_PATTERN = re.compile(
    r"\b(?:China|Canada|United\s+Kingdom|Australia|India|Japan|South\s+Korea|"
    r"Singapore|Germany|France|Italy|Spain|Netherlands|Switzerland|Sweden|"
    r"Norway|Denmark|Finland|Belgium|Austria|Ireland|New\s+Zealand|Brazil|"
    r"Mexico|United\s+Arab\s+Emirates|Qatar|Saudi\s+Arabia|Israel|Taiwan|"
    r"Hong\s+Kong)\b",
    re.IGNORECASE,
)
NON_APPOINTMENT_ROLE_PATTERN = re.compile(
    r"\b(?:guest|invited|event|seminar|keynote)\s+(?:speaker|lecturer)\b|"
    r"\b(?:panelist|conference\s+speaker)\b",
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
STALE_FACULTY_PROFILE_HINT_PATTERN = re.compile(
    r"honou?red[-_/ ]?faculty|emerit(?:us|a|i)|retired[-_/ ]?faculty|"
    r"former[-_/ ]?faculty|past[-_/ ]?faculty|faculty[-_/ ]?archive|legacy[-_/ ]?profile",
    re.IGNORECASE,
)
HISTORICAL_APPOINTMENT_URL_PATTERN = re.compile(
    r"(?:^|/)(?:news|events?|press|stories|announcements?)(?:/|$)|"
    r"(?:^|/)(?:seminars?|talks?|conferences?)(?:/|$)|"
    r"new[-_/ ]*faculty|faculty[-_/ ]*hires?",
    re.IGNORECASE,
)
CURRENT_FACULTY_LISTING_PATTERN = re.compile(
    r"faculty|directory|people|profile|biograph|academic",
    re.IGNORECASE,
)
PROFILE_RESULT_PATTERN = re.compile(
    r"faculty|directory|people|person|profile|staff|academic|/~|/users?/",
    re.IGNORECASE,
)
TITLE_AFTER_NAME_TOKENS = {
    "at", "directory", "faculty", "homepage", "md", "phd", "profile",
    "professional", "s", "website", "jr", "sr", "ii", "iii", "iv",
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
INSTITUTION_NAME_PATTERN = re.compile(
    r"\b(?:university|universite|universitat|universiteit|universidade|universita|"
    r"universidad|universitet|universitas|"
    r"college|institute\s+of\s+technology|polytechnic)\b",
    re.IGNORECASE,
)
VERIFIED_EMAIL_DOMAIN_PATTERN = re.compile(
    r"\bverified\s+email\s+at\s+([a-z0-9.-]+\.[a-z]{2,})\b",
    re.IGNORECASE,
)
PAPER_LINKED_PROFILE_BLOCKED_DOMAINS = {
    "academia.edu", "facebook.com", "github.com", "google.com",
    "linkedin.com", "ratemyprofessors.com", "researchgate.net",
    "scholar.google.com", "wikipedia.org",
}


def _ascii_fold(value: str) -> str:
    return fold_name_text(value)


def _name_tokens(name: str) -> list[str]:
    return name_tokens(name)


def _identity_matches(name: str, text: str) -> bool:
    expected = _name_tokens(name)
    observed = _name_tokens(text)
    if not expected or not observed:
        return False
    # A profile may omit middle names/initials or join a compound given name.
    # Never reduce a multi-token identity to a one-token surname match.
    minimum = 2 if len(expected) >= 2 else 1
    maximum = min(len(observed), len(expected) + 2)
    for start in range(len(observed)):
        for width in range(minimum, maximum + 1):
            window = observed[start:start + width]
            if len(window) < width:
                continue
            if _name_tokens_compatible(expected, window):
                return True
    return False


def _name_tokens_compatible(expected: list[str], observed: list[str]) -> bool:
    """Compare safe structural variants without guessing a person's ethnicity.

    Handles surname-first directory headings, omitted middle initials, and
    joined/split compound names such as ``Guo-Wei`` / ``Guowei``.  These forms
    only locate identity evidence; role and institution checks still decide it.
    """
    def forms(tokens: list[str]) -> set[tuple[str, ...]]:
        if not tokens:
            return set()
        # Single-letter middle initials are optional.  The shared tokenizer
        # already drops most of them, but retain this for callers/tests that
        # supply tokens directly.
        compact = [
            value for index, value in enumerate(tokens)
            if not (0 < index < len(tokens) - 1 and len(value) == 1)
        ]
        variants = {tuple(tokens), tuple(compact)}
        for values in (tokens, compact):
            if len(values) >= 2:
                # Western directory form: Family, Given Middle.
                variants.add(tuple([values[-1], *values[:-1]]))
                # Names natively stored family-name first but displayed
                # given-name first by another source.
                variants.add(tuple([*values[1:], values[0]]))
        return {variant for variant in variants if variant}

    if len(expected) < 2 or len(observed) < 2:
        return expected == observed
    # Candidate-specific pages commonly abbreviate one given name, for example
    # ``Levent Burak Kara`` -> ``L. Burak Kara``. Permit initials only while
    # the remaining ordered name (including the full surname) still matches.
    def initial_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
        if len(left) != len(right) or len(left) < 2 or left[-1] != right[-1]:
            return False
        exact_full_tokens = 0
        for left_token, right_token in zip(left, right):
            if left_token == right_token:
                if len(left_token) > 1:
                    exact_full_tokens += 1
                continue
            if min(len(left_token), len(right_token)) != 1:
                return False
            if left_token[0] != right_token[0]:
                return False
        return exact_full_tokens >= 1
    expected_forms, observed_forms = forms(expected), forms(observed)
    if expected_forms & observed_forms:
        return True
    if any(initial_compatible(left, right)
           for left in expected_forms for right in observed_forms):
        return True
    # Joining tokens is safe here because the complete ordered letter string
    # must match, not merely one token: Guo + Wei == Guowei.
    return bool(
        {"".join(value) for value in expected_forms}
        & {"".join(value) for value in observed_forms}
    )


def _profile_title_matches(name: str, page_title: str) -> bool:
    """Require the profile title to identify this exact person, not a list entry."""
    expected = _name_tokens(name)
    if not expected:
        return False
    # Pronouns and an explicitly quoted nickname are not another identity.
    page_title = re.sub(r'\b(?:she/her|he/him|they/them)\b', '', page_title, flags=re.I)
    page_title = re.sub(r'[“"]\w+[”"]', '', page_title)
    page_title = unicodedata.normalize('NFKC', page_title)
    page_title = re.sub(r'\bPh\s*\.\s*D\.?', 'PhD', page_title, flags=re.I)
    page_title = re.sub(r'\bM\s*\.\s*D\.?', 'MD', page_title, flags=re.I)
    for raw_segment in re.split(r"\s*(?:\||·|—|–)\s*|\s+-\s+", page_title):
        # A parenthetical alias after the primary name is still the same
        # person's profile: ``Rui-Feng Wang (Swee-Fong Wong)``.  Evaluate both
        # the full segment and a version without the alias.
        segments = [raw_segment]
        without_alias = re.sub(r"\s*\([^)]{1,120}\)\s*", " ", raw_segment).strip()
        if without_alias and without_alias != raw_segment:
            segments.append(without_alias)
        for segment in segments:
        # A faculty role is a suffix, not part of the name. Only strip an
        # explicit role suffix; do not skip an arbitrary preceding name.
            role = FACULTY_TITLE_PATTERN.search(segment)
            if role and role.start() > 0:
                segment = segment[:role.start()].rstrip(' ,:-')
            tokens = _name_tokens(re.sub(r"['’]s\b", "", segment, flags=re.IGNORECASE))
            while tokens and tokens[0] in {"dr", "prof", "professor"}:
                tokens.pop(0)
            for start in (0,):
                if start >= len(tokens):
                    continue
                for width in range(
                    2 if len(expected) >= 2 else 1,
                    min(len(tokens) - start, len(expected) + 2) + 1,
                ):
                    observed = tokens[start:start + width]
                    if not _name_tokens_compatible(expected, observed):
                        continue
                    remainder = tokens[start + width:]
                    if not remainder or all(
                        token in TITLE_AFTER_NAME_TOKENS for token in remainder[:3]
                    ):
                        return True
    return False


def _institution_tokens(value: str) -> set[str]:
    folded = _ascii_fold(canonical_institution(value)).casefold()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", folded)
        if len(token) > 2 and token not in INSTITUTION_STOPWORDS
    }


def _institution_similarity(left: str, right: str) -> float:
    return 1.0 if institutions_equivalent(left, right) else 0.0


def _institution_search_aliases(institution: str) -> list[str]:
    """Create a few familiar search forms such as NUS, UAlberta, and PolyU."""
    words = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", _ascii_fold(institution))
        if token.casefold() not in {"of", "the", "at", "and"}
    ]
    if not words:
        return []
    aliases: list[str] = []
    lowered = [word.casefold() for word in words]
    if "polytechnic" in lowered and "university" in lowered:
        aliases.append("PolyU")
    if lowered[0] in {"university", "universite", "universitat", "universiteit"}:
        distinctive = next(
            (word for word in reversed(words[1:]) if len(word) >= 4), ""
        )
        if distinctive:
            aliases.append(f"U{distinctive}")
    acronym = "".join(word[0] for word in words).upper()
    if 2 <= len(acronym) <= 6:
        aliases.append(acronym)
    return list(dict.fromkeys(aliases))


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
    text = unicodedata.normalize('NFKC', text)
    name = unicodedata.normalize('NFKC', name)
    lowered = text.casefold()
    variants = [name.casefold(), " ".join(_name_tokens(name))]
    positions: list[int] = []
    for value in variants:
        if not value:
            continue
        positions.extend(match.start() for match in re.finditer(re.escape(value), lowered))
    positions = sorted(set(positions))
    # Use the same optional-middle-initial, surname-first, and joined-compound
    # normalization as name matching.
    tokens = _name_tokens(name)
    if len(tokens) >= 2:
        orders = [tokens, [tokens[-1], *tokens[:-1]], [*tokens[1:], tokens[0]]]
        if len(tokens) >= 3:
            # Current bios sometimes use a first initial plus the complete
            # middle/given name and surname: ``L. Burak Kara``.
            orders.extend(([tokens[0][0], *tokens[1:]], tokens[1:]))
        for order in orders:
            # ``*`` permits a joined compound (GuoWei) as well as hyphenated or
            # spaced spellings. The complete ordered name must still match.
            pattern = r'\b' + r'[\W_]*(?:[A-Za-z]\.?[\W_]*)?'.join(
                map(re.escape, order)
            ) + r'\b'
            positions.extend(m.start() for m in re.finditer(pattern, text, re.I))
        positions = sorted(set(positions))
    if not positions:
        return ""
    # Repeated page titles and navigation often contain the name before the
    # actual profile block. Prefer an occurrence whose nearby text contains a
    # role, while keeping the wider context for evidence and new-faculty dates.
    for pattern in (FACULTY_TITLE_PATTERN, NON_FACULTY_PATTERN, CURRENT_NONFACULTY_ROLE_PATTERN):
        for position in positions:
            nearby = text[max(0, position - 100): position + len(name) + 180]
            if pattern.search(nearby):
                return text[max(0, position - window): position + len(name) + window]
    position = positions[0]
    return text[max(0, position - window): position + len(name) + window]


def _edu_domain(url: str) -> str:
    """Return a conventional academic registrable domain.

    The old verifier recognized only ``university.edu``. That silently
    excluded common international forms such as ``sdu.edu.cn`` and
    ``cam.ac.uk``.
    """
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    labels = host.split(".")
    if len(labels) >= 2 and labels[-1] == "edu":
        return ".".join(labels[-2:])
    if len(labels) >= 3 and labels[-2] in {"ac", "edu"}:
        return ".".join(labels[-3:])
    return ""


def _host_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _domain_matches_institution(url: str, institution: str) -> bool:
    """Recognize an institution-owned host without trusting every country domain."""
    known = record_for_name(institution)
    if known:
        host = _host_domain(url)
        return host == known[1] or host.endswith('.' + known[1])
    base = _edu_domain(url) or _host_domain(url)
    host = re.sub(r"[^a-z0-9]", "", _ascii_fold(base).casefold())
    if not host:
        return False
    words = [
        token
        for token in re.findall(r"[a-z0-9]+", _ascii_fold(institution).casefold())
        if len(token) > 1 and token not in {"of", "the", "at", "and"}
    ]
    if any(len(token) >= 4 and token not in INSTITUTION_STOPWORDS and token in host for token in words):
        return True
    acronym = "".join(token[0] for token in words)
    first_label = re.sub(r"[^a-z0-9]", "", base.split(".")[0])
    return bool(
        len(first_label) >= 2
        and acronym
        and (
            acronym == first_label
            or (len(first_label) == 2 and acronym.startswith(first_label)
                and base.rsplit('.', 1)[-1] in {'nl', 'de', 'fr', 'ca', 'uk'})
            or (
                len(first_label) == len(acronym)
                and sorted(first_label) == sorted(acronym)
            )
        )
    )


def _possible_official_domain(
    url: str, candidate_institution: str, result_summary: str
) -> str:
    registered = record_for_host(_host_domain(url))
    if registered:
        return registered[1]
    conventional = _edu_domain(url)
    if conventional:
        return conventional
    host = _host_domain(url)
    if _domain_matches_institution(url, candidate_institution):
        return host
    for match in INSTITUTION_HINT_PATTERN.finditer(result_summary):
        if _domain_matches_institution(url, match.group(0)):
            return host
    return ""


def _country_code_for_domain(domain: str) -> str | None:
    if domain.endswith(".edu"):
        return "US"
    suffix = domain.rsplit(".", 1)[-1].upper() if "." in domain else ""
    return {
        "AU": "AU",
        "BR": "BR",
        "CA": "CA",
        "CN": "CN",
        "HK": "HK",
        "IN": "IN",
        "JP": "JP",
        "KR": "KR",
        "NZ": "NZ",
        "SG": "SG",
        "TW": "TW",
        "UK": "GB",
        "ZA": "ZA",
    }.get(suffix)


def _fetch_official_page(url: str, max_redirects: int = 4) -> tuple[str, str]:
    """Fetch a public candidate page; callers decide if its host is official."""
    url = canonical_source_url(url)
    audit_log.remaining_seconds()
    audit = audit_log.CURRENT.get()
    documents = audit.setdefault('_documents', {}) if audit is not None else {}
    if url in documents and documents[url].get('finished'):
        return documents[url].get('text', ''), documents[url].get('title', '')
    document = documents.setdefault(url, {'links': []})
    audit_log.emit('identity_page_fetch', url=url)
    current_url = url
    for _ in range(max_redirects + 1):
        current_url = canonical_source_url(current_url)
        prior = documents.get(current_url)
        if prior and prior is not document and prior.get('finished'):
            documents[url] = prior
            return prior.get('text', ''), prior.get('title', '')
        if not is_public_http_url(current_url, resolve_dns=True, allow_pdf=True):
            document.update(finished=True, failure_code='UNSAFE_OR_EXCLUDED_URL',
                            reason='URL is not an allowed public document')
            return "", ""
        response = requests.get(
            current_url,
            headers={"User-Agent": "ScholarRadar/1.0 (faculty verification indexer)"},
            timeout=max(0.1, min(8, audit_log.remaining_seconds())),
            allow_redirects=False,
            stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                return "", ""
            current_url = urljoin(current_url, location)
            continue
        document.update(http_status=response.status_code, final_url=current_url,
                        content_type=response.headers.get('content-type', ''))
        if response.status_code >= 400:
            document.update(finished=True, failure_code='SOURCE_BLOCKED' if response.status_code in {401, 403, 429} else 'SOURCE_UNAVAILABLE',
                            reason=f'Source returned HTTP {response.status_code}')
            response.close()
            response.raise_for_status()
        payload = bytearray()
        try:
            for chunk in response.iter_content(65_536):
                audit_log.remaining_seconds()
                payload.extend(chunk)
                if len(payload) > 8_000_000:
                    document.update(finished=True, failure_code='DOCUMENT_TOO_LARGE', reason='Document exceeds the 8 MB inspection limit')
                    return '', ''
        finally:
            response.close()
        response._content = bytes(payload)
        response._content_consumed = True
        document['response_bytes'] = len(payload)
        if 'application/pdf' in document['content_type'].lower() or response.content[:5] == b'%PDF':
            try:
                reader = PdfReader(io.BytesIO(response.content), strict=False)
                pages = reader.pages[:3]
                text = '\n'.join(p.extract_text() or '' for p in pages)[:100_000]
                urls = []
                for page in pages:
                    for reference in (page.get('/Annots') or [])[:100]:
                        action = reference.get_object().get('/A') or {}
                        if action.get('/URI'):
                            urls.append(str(action['/URI']))
                # Embedded links are authoritative URL text; extracted display
                # text is a fallback and must never join separate PDF lines.
                urls.extend(text_url_leads(text))
                document.update(text=text, title='CV document', finished=True,
                    text_excerpt=text[:600], links=[{'href': u, 'label': 'Homepage'} for u in dict.fromkeys(urls)
                    if audit_log.safe_source_link(u)][:12])
                if not text.strip():
                    document.update(failure_code='PDF_NO_TEXT', reason='PDF downloaded but contains no extractable text')
                documents[current_url] = document
                return text, 'CV document'
            except Exception as error:
                document.update(finished=True, failure_code='PDF_PARSE_FAILED', reason=f'PDF extraction failed: {type(error).__name__}')
                return '', ''
            finally:
                response.close()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            document.update(text='', title='', finished=True, failure_code='UNSUPPORTED_CONTENT',
                            reason='This response is not readable HTML; CV links may still lead to a homepage')
            response.close()
            return "", ""
        # requests can default HTML to ISO-8859-1 even when the document is
        # UTF-8. Prefer BOM/meta declarations, then UTF-8, then an explicit
        # non-default transport charset. Never normalize already broken text.
        raw_bytes = bytes(payload)
        declared = re.search(br'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)', raw_bytes[:8192])
        encoding = declared.group(1).decode('ascii') if declared else 'utf-8-sig'
        try:
            raw_html = raw_bytes.decode(encoding)
        except (UnicodeError, LookupError):
            encoding = response.encoding or 'windows-1252'
            try:
                raw_html = raw_bytes.decode(encoding)
            except (UnicodeError, LookupError):
                raw_html = raw_bytes.decode('utf-8', errors='replace')
        soup = BeautifulSoup(raw_html, "html.parser")
        document['encoding'] = encoding
        response.close()
        # Keep bounded link metadata in memory; never store the full HTML in the audit.
        document['links'] = [{'href': urljoin(current_url, str(a.get('href') or '')),
                             'label': a.get_text(' ', strip=True)[:300]}
                            for a in soup.find_all('a', href=True)[:500]]
        document['headings'] = [h.get_text(' ', strip=True)[:300] for h in soup.find_all(re.compile('^h[1-4]$'))[:80]]
        # Preserve only the candidate's section of a team page. A professor in
        # the next section must not become the student's role.
        name = (audit or {}).get('candidate_name', '')
        sections = []
        for heading in soup.find_all(re.compile('^h[1-4]$')):
            if name and _profile_title_matches(name, heading.get_text(' ', strip=True)):
                parts = [heading.get_text(' ', strip=True)]
                for sibling in heading.next_elements:
                    if getattr(sibling, 'name', '') and re.fullmatch('h[1-4]', sibling.name):
                        break
                    if isinstance(sibling, str) and getattr(sibling.parent, 'name', '') not in {'script', 'style'}:
                        parts.append(str(sibling).strip())
                    if sum(map(len, parts)) > 1800:
                        break
                sections.append(' '.join(parts)[:1800])
        document['profile_sections'] = sections[:3]
        for element in soup(["script", "style", "nav", "footer", "noscript"]):
            element.decompose()
        title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
        text = " ".join(soup.get_text(" ", strip=True).split())[:200_000]
        document.update(finished=True, text_excerpt=text[:600])
        documents[current_url] = document
        explicit_challenge = re.search(r'access denied|request unsuccessful|verify (?:that )?you are human|complete the captcha|just a moment', title + ' ' + text, re.I)
        challenge_shell = len(text) < 80 and re.search(r'/_Incapsula_Resource|challenge-platform', raw_html, re.I)
        if (len(text) < 3000 and explicit_challenge) or challenge_shell:
            document.update(text='', title=title, failure_code='SOURCE_BLOCKED',
                            reason='The source returned an access restriction or bot challenge, not the profile')
            return '', title
        if re.search(r'\b(?:no faculty (?:member )?found|profile not found|this profile (?:was|has been) removed|person not found)\b', text, re.I) and len(text) < 4000:
            document.update(text='', title=title, failure_code='PROFILE_REMOVED',
                            reason='The source returned a missing/removed profile message, not a person record')
            return '', title
        document.update(text=text, title=title)
        if len(text) < 80:
            document.update(failure_code='NO_READABLE_CONTENT', rendering_hint='Browser rendering may be needed; no challenge bypass attempted',
                            reason='The response has almost no readable profile text; inspect its saved title and response size')
        return text, title
    return "", ""


def _fetch_related_publication_text(url: str, max_links: int = 2) -> str:
    """Read a bounded number of same-site Papers/Publications/CV links."""
    audit = audit_log.CURRENT.get()
    if audit is not None:
        # Reuse the already fetched profile. These requests share its page budget.
        document = audit.get('_documents', {}).get(url, {})
        texts = []
        visited = {canonical_source_url(url)}
        for anchor in document.get('links', []):
            target = canonical_source_url(anchor['href'])
            if target in visited:
                continue
            if not re.search(r'\b(?:papers?|publications?)\b', anchor['label'], re.I) or _host_domain(target) != _host_domain(url):
                continue
            visited.add(target)
            if len(texts) >= max_links or audit.get('_pages_used', 0) >= audit.get('_page_limit', 20):
                break
            if target not in audit.get('_documents', {}):
                audit['_pages_used'] = audit.get('_pages_used', 0) + 1
            try:
                text, _ = _fetch_official_page(target)
                texts.append(text)
                audit_log.record_page(target, {'status': 'CLUE', 'reason': 'Checked a same-site publications page for supporting papers'})
            except (OSError, requests.RequestException):
                audit_log.record_page(target, {'status': 'UNVERIFIED', 'reason': 'Linked publications page unavailable'})
        return ' '.join(texts)
    if not is_public_http_url(url, resolve_dns=True):
        return ""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ScholarRadar/1.0 (faculty verification indexer)"},
            timeout=8,
            allow_redirects=False,
        )
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "").casefold():
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
    except (OSError, requests.RequestException):
        return ""
    source_host = _host_domain(url)
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = f"{anchor.get_text(' ', strip=True)} {anchor.get('href') or ''}"
        if not re.search(r"\b(?:cv|papers?|publications?)\b", label, re.IGNORECASE):
            continue
        candidate_url = urljoin(url, str(anchor.get("href") or ""))
        if _host_domain(candidate_url) != source_host or candidate_url in links:
            continue
        links.append(candidate_url)
        if len(links) >= max_links:
            break
    texts: list[str] = []
    for linked_url in links:
        try:
            text, _title = _fetch_official_page(linked_url)
        except (OSError, requests.RequestException):
            continue
        if text:
            texts.append(text)
    return " ".join(texts)


def _institution_name_from_title(title: str) -> str:
    """Extract a conservative institution name from an official root title."""
    for part in re.split(r"\s*(?:\||·|—|–)\s*", title):
        clean = " ".join(part.split()).strip(" -")
        if (
            4 <= len(clean) <= 100
            and INSTITUTION_NAME_PATTERN.search(_ascii_fold(clean))
            and not re.search(r"\b(?:department|school|faculty|admissions|home)\b", clean, re.I)
        ):
            return clean
    return ""


@lru_cache(maxsize=256)
def _institution_for_domain(domain: str) -> str:
    if not domain:
        return ""
    registered = record_for_host(domain)
    if registered:
        return registered[0]
    if setting_bool("FACULTY_VERIFY_OPENALEX_SUPPORT_ENABLED", False):
        term = domain.split(".")[0].replace("-", " ")
        try:
            params: dict[str, object] = {"search": term, "per_page": 10}
            email = setting("OPENALEX_EMAIL").strip()
            if email:
                params["mailto"] = email
            api_key = setting("OPENALEX_API_KEY").strip()
            if api_key:
                params["api_key"] = api_key
            for institution in openalex_get_json(
                OPENALEX_INSTITUTIONS_URL,
                params=params,
                timeout=8,
            ).get("results", []):
                homepage_domain = _edu_domain(str(institution.get("homepage_url") or ""))
                if (
                    homepage_domain == domain
                    and str(institution.get("type") or "").casefold() == "education"
                ):
                    return str(institution.get("display_name") or "").strip()
        except (OSError, ValueError, RuntimeError, OpenAlexUnavailable):
            pass
    # OpenAlex and the works search share a public rate limit.  The official
    # university root page gives us a reliable fallback without maintaining a
    # fragile hand-written list of thousands of institutions.
    try:
        _, root_title = _fetch_official_page(f"https://{domain}/")
        return _institution_name_from_title(root_title)
    except (OSError, ValueError, requests.RequestException):
        return ""


MONTH_NUMBER = {
    name: number for number, name in enumerate(
        ('', 'january', 'february', 'march', 'april', 'may', 'june',
         'july', 'august', 'september', 'october', 'november', 'december')
    ) if name
}
MONTH_NUMBER.update({name[:3]: number for name, number in list(MONTH_NUMBER.items())})


def _partial_date(year: int, month: int | None = None, day: int | None = None,
                  *, end: bool = False) -> tuple[date, str]:
    """Represent only the precision supplied by a source without inventing it."""
    if day is not None and month is not None:
        return date(year, month, day), 'DAY'
    if month is not None:
        return date(year, month, monthrange(year, month)[1] if end else 1), 'MONTH'
    return date(year, 12, 31) if end else date(year, 1, 1), 'YEAR'


def _extract_appointment_date(text: str) -> tuple[date | None, str | None]:
    """Read a dated appointment cue at year, month, or day precision."""
    value = ' '.join(str(text or '').split())
    cue = r'(?:joined|joins|appointed|became|starting|starts|since|beginning|began)'
    month_names = '|'.join(sorted(MONTH_NUMBER, key=len, reverse=True))
    patterns = (
        rf'\b{cue}\b[^.;\n]{{0,100}}?\b({month_names})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b',
        rf'\b{cue}\b[^.;\n]{{0,100}}?\b({month_names})\s+(20\d{{2}})\b',
        rf'\b{cue}\b[^.;\n]{{0,100}}?\b(20\d{{2}})\b',
        r'\b(20\d{2})\s*(?:[-–—]|to)\s*(?:present|current|now)\b',
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, value, re.I)
        if not match:
            continue
        try:
            if index == 0:
                month = MONTH_NUMBER[match.group(1).casefold()]
                return _partial_date(int(match.group(3)), month, int(match.group(2)))
            if index == 1:
                month = MONTH_NUMBER[match.group(1).casefold()]
                return _partial_date(int(match.group(2)), month)
            return _partial_date(int(match.group(1)))
        except (KeyError, ValueError):
            continue
    return None, None


def _extract_appointment_year(text: str) -> int | None:
    appointment_date, _precision = _extract_appointment_date(text)
    if appointment_date:
        return appointment_date.year
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
    if not setting_bool("FACULTY_VERIFY_OPENALEX_SUPPORT_ENABLED", False):
        return ()
    try:
        params: dict[str, object] = {"search": name, "per_page": 25}
        email = setting("OPENALEX_EMAIL").strip()
        if email:
            params["mailto"] = email
        api_key = setting("OPENALEX_API_KEY").strip()
        if api_key:
            params["api_key"] = api_key
        profiles: list[tuple[str, tuple[str, ...]]] = []
        for author in openalex_get_json(
            OPENALEX_AUTHORS_URL,
            params=params,
            timeout=8,
        ).get("results", []):
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
    except (OSError, TypeError, ValueError, RuntimeError, OpenAlexUnavailable):
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


def _attributed_role(name: str, context: str, pattern: re.Pattern) -> re.Match | None:
    """Require the role's subject to be the candidate, not an advisor or neighbor.

    Short profile rows and explicit first-person role statements are supported.
    Historical roles and roles in mentorship/team descriptions are not current
    appointments. This is conservative attribution, not mere keyword proximity.
    """
    text = _ascii_fold(context)
    tokens = _name_tokens(name)
    if not tokens:
        return None
    orders = [tokens]
    if len(tokens) >= 2:
        orders.extend(([tokens[-1], *tokens[:-1]], [*tokens[1:], tokens[0]]))
    if len(tokens) >= 3:
        orders.extend(([tokens[0][0], *tokens[1:]], tokens[1:]))
    names_by_span: dict[tuple[int, int], re.Match] = {}
    for order in orders:
        name_pattern = r'\b' + r'[\W_]*(?:[A-Za-z]\.?[\W_]*)?'.join(
            map(re.escape, order)
        ) + r'\b'
        for found in re.finditer(name_pattern, text, flags=re.I):
            names_by_span[(found.start(), found.end())] = found
    names = sorted(names_by_span.values(), key=lambda value: (value.start(), value.end()))
    for role in pattern.finditer(text):
        before = text[max(0, role.start() - 180):role.start()]
        # "I am an Associate Professor" on an already attributed profile.
        if re.search(r"\bI(?:\s+am|\s*m)(?:\s+(?:currently|now|an?|the))*\s*$", before, re.I):
            return role
        # "I am VP at Company, a professor at University, and ..." shares
        # the first-person subject. Do not cross a sentence, another subject,
        # historical clause, or mentorship relationship.
        shared = re.search(r"\bI(?:\s+am|\s*m)\s+([^.!?;\n]{0,170})[,;]\s*(?:and\s+)?(?:an?\s*)?$", before, re.I)
        if shared and not re.search(r'\b(?:was|former|formerly|previously|worked|with|under|advisor|adviser|mentor|supervis\w*|who|he|she|they|student|candidate)\b', shared.group(1), re.I):
            return role
        for person in reversed(names):
            if person.end() <= role.start() and role.start() - person.end() <= 160:
                bridge = text[person.end():role.start()]
                # A new explicit present-tense clause can follow a historical one.
                bridge = re.split(r'\b(?:currently|now)\b', bridge, flags=re.I)[-1]
                if re.search(r'\b(?:was|were|former|formerly|previously|past|earned|received|'
                             r'completed|served|graduated|alumni|mentorship|mentor|advisor|adviser|'
                             r'advised|supervis\w*|mentored|collaborat\w*|students?|candidates?|'
                             r'under|with|team|directory|faculty\s+list)\b', bridge, re.I):
                    continue
                # Another person's name or another role intervenes in this row.
                name_bridge = re.sub(r"\b(?:Provosts?\s+Chair|Distinguished|Endowed)\b", '', bridge, flags=re.I)
                # Endowed-chair wording may contain a donor's name, not another
                # subject. Keep mentorship and intervening-role exclusions above.
                if re.search(r'\b(?:chair|endowed|family)\b', bridge, re.I):
                    name_bridge = ''
                # Names inside an endowed chair title identify donors, not a
                # second profile subject. Keep this narrow: it must be a short
                # title-case phrase containing ``and`` or ``&``.
                donor_words = re.findall(r"[A-Za-z][A-Za-z'’.-]*|&", bridge)
                donor_title = bool(
                    pattern is FACULTY_TITLE_PATTERN
                    and 3 <= len(donor_words) <= 14
                    and any(word.casefold() in {'and', '&'} for word in donor_words)
                    and all(word.casefold() in {'and', '&'} or word[:1].isupper()
                            for word in donor_words)
                )
                if donor_title:
                    name_bridge = ''
                if re.search(r"\b[A-Z][a-z'’.-]{2,}\s+[A-Z][a-z'’.-]{2,}\b", name_bridge):
                    continue
                if FACULTY_TITLE_PATTERN.search(bridge) or NON_FACULTY_PATTERN.search(bridge):
                    continue
                return role
            # "Professor Jane Smith" / "PhD student Jane Smith".
            if role.end() <= person.start() and person.start() - role.end() <= 12:
                bridge = text[role.end():person.start()]
                prefix = text[max(0, role.start() - 70):role.start()]
                if not bridge.strip(' ,:-') and not re.search(
                    r'\b(?:former|formerly|previously|was|advisor|adviser|mentor)\b', prefix, re.I
                ):
                    return role
    return None


def _clean_faculty_title(value: str | None) -> str | None:
    """Clean presentation artifacts while preserving named professorships."""
    title = ' '.join(str(value or '').split()).strip(' |,:;-–—')
    if not title:
        return None
    title = re.sub(
        r'^(?:is\s+|serves\s+as\s+)?(?:currently\s+|now\s+)?(?:an?\s+|the\s+)?',
        '', title, flags=re.I,
    )
    title = re.sub(r'\bAsst\.?\s+Prof\.?\b', 'Assistant Professor', title, flags=re.I)
    title = re.sub(r'\bAssoc\.?\s+Prof\.?\b', 'Associate Professor', title, flags=re.I)
    title = re.sub(r'\bAdj\.?\s+Prof\.?\b', 'Adjunct Professor', title, flags=re.I)
    title = re.sub(r'\bProf\.?(?=\s|$)', 'Professor', title, flags=re.I)
    title = re.sub(r'\bLect\.?(?=\s|$)', 'Lecturer', title, flags=re.I)
    title = re.sub(r'\bInstr\.?(?=\s|$)', 'Instructor', title, flags=re.I)
    # Department/degree abbreviations occasionally leak in immediately before
    # a standard rank (for example ``MS Assistant Professor``).
    title = re.sub(
        r'^(?:[A-Z]{2,6}\s+)(?=(?:Assistant|Associate|Adjunct|Research|Clinical|'
        r'Teaching|Visiting|Distinguished|Endowed|Professor|Lecturer|Instructor)\b)',
        '', title,
    )
    return title.strip(' |,:;-–—') or None


def _detailed_faculty_title(
    name: str, context: str, role: re.Match | None
) -> str | None:
    """Return the exact public rank or named professorship from the profile."""
    if role is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", context).split())
    folded = _ascii_fold(normalized)
    tokens = _name_tokens(name)
    if tokens:
        own_name = r"\b" + r"[\W_]+".join(map(re.escape, tokens)) + r"\b"
        preceding = list(re.finditer(own_name, folded[:role.start()], re.I))
        if preceding:
            nearest = preceding[-1]
            candidate_title = normalized[nearest.end():role.end()].strip(" ,:;-–—")
            candidate_title = re.sub(r"^(?:Dr\.?\s+)", "", candidate_title, flags=re.I)
            if not re.search(
                r"\b(?:apply|links?|menu|tools?|search|directory|profile|contact|email|phone)\b",
                candidate_title,
                re.I,
            ):
                words = re.findall(r"[A-Za-z][A-Za-z'’.-]*|&", candidate_title)
                if (
                    1 <= len(words) <= 14
                    and re.search(r"\b(?:professor|lecturer|instructor)\b", candidate_title, re.I)
                ):
                    return _clean_faculty_title(candidate_title)
    name_patterns: list[str] = []
    if tokens:
        name_patterns.append(r"\b" + r"[\W_]+".join(map(re.escape, tokens)) + r"\b")
    if len(tokens) >= 3:
        name_patterns.extend([
            r"\b" + re.escape(tokens[0][0]) + r"\.?[\W_]+" +
            r"[\W_]+".join(map(re.escape, tokens[1:])) + r"\b",
            r"\b" + r"[\W_]+".join(map(re.escape, tokens[1:])) + r"\b",
        ])
    for name_pattern in name_patterns:
        for person in re.finditer(name_pattern, _ascii_fold(normalized), re.I):
            tail = normalized[person.end():person.end() + 190].lstrip(" ,:;-–—")
            # Page title + body often repeats the person's name before the
            # actual title. Remove one repeated identity, not arbitrary words.
            for repeated_name in name_patterns:
                repeated = re.match(repeated_name, _ascii_fold(tail), re.I)
                if repeated:
                    tail = tail[repeated.end():].lstrip(" ,:;-–—")
                    break
            tail = re.sub(
                r"^(?:is\s+)?(?:currently\s+|now\s+)?(?:an?\s+|the\s+)?",
                "", tail, flags=re.I,
            )
            endowed = re.match(
                r"(?P<title>(?:(?:[A-Z][A-Za-z'’.-]*|and|&)\s+){2,14}"
                r"Professor(?:\s+of\s+Practice)?)\b",
                tail,
            )
            if endowed:
                return _clean_faculty_title(endowed.group("title"))
            ranked = re.match(
                r"(?P<title>(?:(?:Tenure[- ]Track|Assistant|Associate|Full|Research|"
                r"Clinical|Teaching|Adjunct|Visiting|Distinguished|Endowed)\s+){0,3}"
                r"Professor(?:\s+of\s+Practice)?)\b",
                tail, re.I,
            )
            if ranked:
                return _clean_faculty_title(ranked.group("title"))
    return _clean_faculty_title(role.group(0))


def _current_nonfaculty_role(name: str, context: str) -> re.Match | None:
    """Return a current, candidate-attributed industry/postdoc role.

    A personal profile saying ``I am a Software Engineer at Zoox`` is decisive
    negative faculty evidence.  Historical education and employment clauses do
    not count, and a concurrent faculty title remains ambiguous.
    """
    role = _attributed_role(name, context, CURRENT_NONFACULTY_ROLE_PATTERN)
    if role is None:
        return None
    before = _ascii_fold(context[max(0, role.start() - 180):role.start()])
    after = _ascii_fold(context[role.end():role.end() + 220])
    sentence = f"{before} {role.group(0)} {after}"
    if re.search(
        r"\b(?:formerly|previously|past|was|were|earned|received|completed|"
        r"graduated|before|until)\b[^.!?;\n]{0,100}$",
        before,
        re.IGNORECASE,
    ):
        return None
    if not (
        CURRENT_ROLE_CUE_PATTERN.search(sentence)
        or re.search(r"\b(?:current|present)\s+(?:appointment|position|role)\b", sentence, re.I)
    ):
        return None
    return role


def _role_category(role_text: str | None) -> str:
    value = str(role_text or "").casefold()
    if re.search(
        r"\b(?:professor|faculty|lecturer|instructor)\b|"
        r"\b(?:prof|lect|instr)\.",
        value,
    ):
        return "FACULTY"
    # Check postdoctoral roles before the broader doctoral/student wording:
    # "postdoctoral" contains "doctoral", but it is a distinct career stage.
    if re.search(r"\bpost[- ]?doc", value):
        return "POSTDOC"
    if re.search(r"ph\.?\s*d\.?|doctoral|graduate|grad\.?|undergraduate|undergrad\.?", value):
        return "STUDENT"
    if re.search(
        r"software\s+engineer|data\s+scientist|technical\s+lead|manager|developer|"
        r"analyst|consultant|architect|specialist|officer|technician|teacher|"
        r"educator|administrator|coordinator",
        value,
    ):
        return "INDUSTRY"
    if re.search(r"research\s+scientist|research\s+engineer|researcher|scientist", value):
        return "RESEARCHER"
    if "engineer" in value:
        return "INDUSTRY"
    return "UNKNOWN"


def _employer_after_role(context: str, role: re.Match | None) -> str | None:
    if role is None:
        return None
    tail = context[role.end():role.end() + 220]
    clause = re.split(r"[.!?;\n]", tail, maxsplit=1)[0]
    match = re.match(
        r"(?:\band\s+(?:the\s+)?(?:technical\s+)?lead\s+)?\s*\bat\s+"
        r"([^,;.!?\n]{2,100})",
        clause,
        re.IGNORECASE,
    )
    employer = re.split(
        r"\s+(?:where|working|with|on|and)\s+",
        match.group(1) if match else "",
        maxsplit=1,
        flags=re.I,
    )[0]
    employer = " ".join(employer.split()).strip(" ()-")
    if not employer:
        current = re.search(
            r"\b(?:currently|now)\s+(?:working|employed)?\s*(?:on\s+[^.!?;]{0,80}\s+)?"
            r"(?:at|with)\s+([^,;.!?\n]{2,100})",
            context,
            re.I,
        )
        employer = " ".join((current.group(1) if current else "").split()).strip(" ()-")
    if (
        not employer
        or "@" in employer
        or re.search(r"\b(?:email|e-mail|gmail|outlook|contact|reach\s+me|http|www\.)\b", employer, re.I)
    ):
        return None
    return employer


def _generic_current_nonfaculty_employer(name: str, context: str) -> str | None:
    """Read explicit current employment without listing every possible job."""
    if not _identity_matches(name, context) or FACULTY_TITLE_PATTERN.search(context):
        return None
    patterns = (
        r"\bI\s+am\s+currently\s+(?:working|employed)\b[^.!?;\n]{0,100}?\bat\s+([^,;.!?\n]{2,100})",
        r"\bI\s+currently\s+work\b[^.!?;\n]{0,80}?\bat\s+([^,;.!?\n]{2,100})",
        r"\bcurrently\s+(?:working|employed)\s+at\s+([^,;.!?\n]{2,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, context, re.I)
        if not match:
            continue
        employer = " ".join(match.group(1).split()).strip(" ()-")
        employer = re.split(
            r"\s+(?:where|while|focusing|working)\b", employer, 1, flags=re.I
        )[0]
        if (
            employer
            and "@" not in employer
            and not INSTITUTION_NAME_PATTERN.search(employer)
            and not re.search(r"\b(?:gmail|outlook|email|contact|reach\s+me|http|www\.)\b", employer, re.I)
        ):
            return employer
    return None


def _faculty_employer_after_role(context: str, role: re.Match | None) -> str | None:
    """Keep compound research-institute names attached to a faculty title."""
    if role is None:
        return None
    tail = context[role.end():role.end() + 260]
    match = re.match(r"\s*(?:at|@)\s+([^.;!?\n]{2,180})", tail, re.IGNORECASE)
    if not match:
        return None
    employer = re.split(
        r"\s+(?:where|who|whose|and\s+(?:he|she|they))\s+",
        match.group(1), maxsplit=1, flags=re.IGNORECASE,
    )[0]
    return " ".join(employer.split()).strip(" ,-()") or None


def _explicit_role_currentness(text: str) -> str:
    """Honor structured profile end dates instead of labelling every role current."""
    lowered = str(text or "").casefold()
    if re.search(r"\bis\s+current\s*:\s*false\b", lowered):
        return "HISTORICAL"
    today = datetime.now(timezone.utc).date()
    appointment_date, _precision = _extract_appointment_date(text)
    if appointment_date and appointment_date > today:
        return 'FUTURE'
    for match in re.finditer(r"\bend\s+date\s*:\s*(20\d{2})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", lowered):
        try:
            end_date, _ = _partial_date(
                int(match.group(1)),
                int(match.group(2)) if match.group(2) else None,
                int(match.group(3)) if match.group(3) else None,
                end=True,
            )
            if end_date < today:
                return "HISTORICAL"
        except ValueError:
            continue
    return "CURRENT"


def _non_us_faculty_claim(context: str, url: str, role: re.Match | None) -> bool:
    domain = _edu_domain(url) or _host_domain(url)
    if re.search(r"\.(?:edu|ac)\.(?!us$)[a-z]{2}$", domain, re.I):
        return True
    if role is None:
        return False
    role_context = context[max(0, role.start() - 80):role.end() + 240]
    return bool(NON_US_LOCATION_PATTERN.search(role_context))


def _linkedin_headline(name: str, result: dict[str, Any]) -> str:
    """Extract a useful exact-person LinkedIn headline without naming every job."""
    url = str(result.get("href") or "")
    if not _host_domain(url).endswith("linkedin.com") or "/in/" not in urlparse(url).path:
        return ""
    title = " ".join(str(result.get("title") or "").split())
    body = str(result.get("body") or "")
    if not _identity_matches(name, f"{title} {body}"):
        return ""
    # API-backed LinkedIn results commonly put the useful current role in a
    # structured body line while the title contains only a location.
    structured = re.search(
        r"(?:^|\n)\s*(?:[-*]\s*)?(?:f\s+)?headline\s*:\s*([^\n]{3,240})",
        body,
        re.IGNORECASE,
    )
    if structured:
        headline = " ".join(structured.group(1).split()).strip(" -|·")
        if headline and len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", headline)) >= 4:
            return headline[:240]
    folded_name = fold_name_text(name)
    pieces = re.split(r"\s+(?:[-–—|·])\s+", title, maxsplit=2)
    usable = [piece.strip() for piece in pieces if piece.strip()]
    while usable and fold_name_text(usable[0]) == folded_name:
        usable.pop(0)
    headline = usable[0] if usable else ""
    if fold_name_text(headline) in {"linkedin", "professional profile", "profile"}:
        return ""
    if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", headline)) < 4:
        return ""
    return headline[:240]


def _candidate_identity_clues(candidate: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    clues: list[str] = []
    for key in ("institution_name", "_imported_institution", "_paper_university"):
        value = str(candidate.get(key) or "").strip()
        if value:
            clues.append(value)
    for affiliation in candidate.get("paper_affiliations") or []:
        value = str(affiliation.get("institution_name") or "").strip()
        if value:
            clues.append(value)
    # Another exact-name result can connect a new institution mentioned in a
    # LinkedIn education line to this same publication identity.
    for result in results:
        text = " ".join(str(result.get(key) or "") for key in ("title", "body"))
        if not _identity_matches(str(candidate.get("name") or ""), text):
            continue
        clues.extend(match.group(0) for match in INSTITUTION_HINT_PATTERN.finditer(text))
    return list(dict.fromkeys(clues))


def _linkedin_identity_linked(
    candidate: dict[str, Any], result: dict[str, Any], results: list[dict[str, Any]]
) -> bool:
    summary = fold_name_text(" ".join(str(result.get(key) or "") for key in ("title", "body")))
    for clue in _candidate_identity_clues(candidate, results):
        folded = fold_name_text(clue)
        if folded and folded in summary:
            return True
    field = next((str(paper.get("matched_query") or "") for paper in candidate.get("recent_papers") or []), "")
    field_tokens = {token for token in name_tokens(field) if len(token) >= 5}
    if field_tokens and len(field_tokens.intersection(set(summary.split()))) >= min(2, len(field_tokens)):
        return True
    for paper in candidate.get("recent_papers") or []:
        title_tokens = {token for token in name_tokens(str(paper.get("title") or "")) if len(token) >= 5}
        if len(title_tokens.intersection(set(summary.split()))) >= 3:
            return True
    return False


def _public_snippet_nonfaculty_consensus(
    name: str, results: list[dict[str, Any]], candidate: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Use matching LinkedIn + Google Scholar snippets only as a consensus.

    One snippet can be stale or joined from nearby text. Two independently
    attributable public-profile snippets, with no faculty result accepted in
    the pass, are enough to hide the candidate and schedule a later recheck.
    """
    evidence: list[dict[str, Any]] = []
    providers: set[str] = set()
    for result in results:
        url = str(result.get("href") or "")
        host = _host_domain(url)
        provider = (
            "LINKEDIN_SNIPPET" if host.endswith("linkedin.com")
            else "GOOGLE_SCHOLAR" if host.startswith("scholar.google.")
            else ""
        )
        if not provider:
            continue
        summary = " ".join(str(result.get(key) or "") for key in ("title", "body"))
        if not _identity_matches(name, summary):
            continue
        if _attributed_role(name, summary, FACULTY_TITLE_PATTERN):
            return None
        headline = _linkedin_headline(name, result) if provider == "LINKEDIN_SNIPPET" else ""
        role = (
            _current_nonfaculty_role(name, summary)
            or _attributed_role(name, summary, NON_FACULTY_PATTERN)
        )
        linked_headline = bool(
            headline
            and not LINKEDIN_FACULTY_WORDING_PATTERN.search(headline)
            and candidate is not None
            and _linkedin_identity_linked(candidate, result, results)
        )
        if role is None and not linked_headline:
            continue
        providers.add(provider)
        role_text = headline or " ".join(role.group(0).split())
        evidence.append({
            "status": "UNVERIFIED",
            "source_url": url,
            "source_domain": host,
            "source_type": provider,
            "title": role_text,
            "role_category": _role_category(role_text),
            "observed_employer": _employer_after_role(summary, role),
            "currentness": _explicit_role_currentness(summary),
            "lookup_status": "FOUND",
            "evidence_text": summary[:900],
            "confidence": 0.70,
            "method": "linkedin_meaningful_nonfaculty_headline" if linked_headline else "public_profile_snippet",
        })
    linkedin_rows = [
        row for row in evidence
        if row["source_type"] == "LINKEDIN_SNIPPET"
        and row.get("currentness") == "CURRENT"
    ]
    # Search-result titles often expose a current role even when LinkedIn blocks
    # automated page access. Accept one exact-name identity, but keep multiple
    # same-name LinkedIn profiles ambiguous.
    scholar_rows = [
        row for row in evidence
        if row["source_type"] == "GOOGLE_SCHOLAR"
        and row.get("currentness") == "CURRENT"
    ]
    # A historical LinkedIn position must not participate in a current-role
    # consensus merely because the provider was present in the result set.
    has_public_profile_consensus = bool(linkedin_rows and scholar_rows)
    if (
        not has_public_profile_consensus
        and len(linkedin_rows) == 1
        and (
            linkedin_rows[0]["method"] == "linkedin_meaningful_nonfaculty_headline"
            or linkedin_rows[0]["role_category"] in {"INDUSTRY", "RESEARCHER", "POSTDOC", "STUDENT"}
        )
    ):
        primary = linkedin_rows[0]
        return {
            **primary,
            "status": "NOT_FACULTY",
            "confidence": 0.84,
            "method": "linkedin_no_faculty_headline",
            "evidence_text": (
                "An identity-linked, exact-person LinkedIn profile has a meaningful "
                "current headline with no faculty wording, and the completed searches "
                "found no current faculty appointment. " + primary["evidence_text"]
            )[:900],
        }
    if not has_public_profile_consensus:
        return None
    primary = next(
        row for row in evidence
        if row["source_type"] == "LINKEDIN_SNIPPET"
        and row.get("currentness") == "CURRENT"
    )
    return {
        **primary,
        "status": "NOT_FACULTY",
        "confidence": 0.80,
        "method": "public_profile_snippet_consensus",
        "evidence_text": (
            "Matching LinkedIn and Google Scholar snippets identify a current "
            "student, postdoctoral, company, or other nonfaculty role; no current "
            "faculty profile was found in this pass."
        ),
        "alternative_evidence": [row for row in evidence if row is not primary],
    }


def _is_current_faculty_listing(url: str, page_title: str) -> bool:
    path = urlparse(url).path
    if _is_news_or_event(url) or HISTORICAL_APPOINTMENT_URL_PATTERN.search(page_title):
        return False
    return bool(CURRENT_FACULTY_LISTING_PATTERN.search(f"{path} {page_title}"))


def _document_links_to_institution(
    document: dict[str, Any], institution: str
) -> bool:
    """Confirm that an attributed lab/personal page links to the university.

    This is supporting attribution, never faculty proof by itself. The caller
    must still find the candidate's own current faculty title.
    """
    if not institution:
        return False
    for link in document.get('links') or []:
        href = str(link.get('href') or '')
        host = _host_domain(href)
        record = record_for_host(host) if host else None
        linked_name = record[0] if record else ''
        if linked_name and _institution_similarity(institution, linked_name) >= 0.5:
            return True
        if href.casefold().startswith('mailto:'):
            email_domain = href.rsplit('@', 1)[-1].split('?', 1)[0]
            record = record_for_host(email_domain)
            linked_name = record[0] if record else ''
            if linked_name and _institution_similarity(institution, linked_name) >= 0.5:
                return True
    return False


def _is_news_or_event(url: str) -> bool:
    parsed = urlparse(url)
    return bool(HISTORICAL_APPOINTMENT_URL_PATTERN.search(parsed.path) or
                re.search(r"(?:^|\.)(?:news|events|stories|press)\.", parsed.hostname or "", re.I))


def _snippet_only_url(url: str) -> bool:
    host = _host_domain(url)
    return host != 'sites.google.com' and any(
        host == blocked or host.endswith('.' + blocked)
        for blocked in PAPER_LINKED_PROFILE_BLOCKED_DOMAINS
    )


def _profile_priority(result: dict[str, Any]) -> int:
    """Order returned links, not extra queries: faculty profiles before other pages."""
    url = str(result.get('href') or '')
    path = urlparse(url).path
    if _is_news_or_event(url) or _snippet_only_url(url):
        return 9
    if _edu_domain(url):
        if re.search(r'/(?:faculty-members|faculty|faculty-pages)/[^/]+', path, re.I):
            return 0
        if PROFILE_RESULT_PATTERN.search(path):
            return 1
        return 3
    return 4


def _faculty_source_quality(url: str) -> int:
    """Prefer a current individual profile over events and other locators."""
    value = str(url or '')
    path = urlparse(value).path
    if not value or _is_news_or_event(value):
        return 0
    if _edu_domain(value):
        if re.search(r'/(?:faculty-members|faculty|faculty-pages)/[^/]+', path, re.I):
            return 5
        if PROFILE_RESULT_PATTERN.search(path):
            return 4
        return 2
    if re.search(r'\b(?:lab|group)\b', value, re.I):
        return 3
    return 1


def _explicit_faculty_membership(name: str, url: str, title: str, text: str) -> bool:
    """A substantive individual entry in a faculty-only directory, not just /people/.

    Records membership only; it does not infer assistant/associate/full rank.
    A same-name paper/field ambiguity must still be resolved separately.
    """
    return bool(
        _profile_title_matches(name, title)
        and re.search(r'/faculty-members/[^/]+', urlparse(url).path, re.I)
        and not _is_news_or_event(url)
        and not re.search(r'\b(?:emeritus|emerita|retired|former|in\s+memoriam)\b',
                          f'{title} {_identity_context(name, text)}', re.I)
        and re.search(r'\bresearch\b', text, re.I)
        and re.search(r'\b(?:selected\s+publications|research\s+interests|areas?\s+of\s+interest)\b', text, re.I)
    )


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


def _verified_email_domain_hints(
    name: str, results: list[dict[str, Any]]
) -> list[str]:
    """Use Google Scholar's verified-email label only to locate an official page."""
    domains: list[str] = []
    for result in results:
        if not _host_domain(str(result.get("href") or "")).startswith("scholar.google."):
            continue
        summary = " ".join(
            str(result.get(key) or "") for key in ("title", "body")
        )
        if not _identity_matches(name, summary):
            # Search excerpts sometimes split a surname ("Ta Sooji"). Exact
            # letters may supply a domain clue, never a faculty decision.
            expected_letters = ''.join(_name_tokens(name))
            if not expected_letters or expected_letters not in ''.join(_name_tokens(summary)):
                continue
        summary = re.sub(r'\s*\.\s*', '.', summary)
        for match in VERIFIED_EMAIL_DOMAIN_PATTERN.finditer(summary):
            domain = match.group(1).casefold().rstrip(".")
            if academic_domain_hint(domain) and domain not in domains:
                domains.append(domain)
    return domains


def _profile_root_result(
    name: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Turn an academic PDF below `/~person/` into the person's homepage lead."""
    url = str(result.get("href") or "")
    parsed = urlparse(url)
    match = re.match(r"(?P<root>.*/~[^/]+/)", parsed.path)
    if not match:
        return None
    root_url = f"{parsed.scheme}://{parsed.netloc}{match.group('root')}"
    if root_url.rstrip("/") == url.rstrip("/"):
        return None
    return {
        "title": name,
        "body": str(result.get("body") or ""),
        "href": root_url,
    }


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
        own_positive = _attributed_role(str(candidate.get('name') or ''), identity_quote, FACULTY_TITLE_PATTERN)
        own_negative = _attributed_role(str(candidate.get('name') or ''),
            _identity_context(str(candidate.get('name') or ''), page_text), NON_FACULTY_PATTERN)
        if (
            not title
            or _normalized_text(title) not in normalized_page
            or _normalized_text(title) not in _normalized_text(identity_quote)
            or not FACULTY_TITLE_PATTERN.search(title)
            or NON_FACULTY_PATTERN.search(f"{title} {identity_quote}")
            or not own_positive or own_negative
        ):
            return None
        expected = str(candidate.get("_paper_university") or candidate.get("institution_name") or "")
        if expected and institution and _institution_similarity(expected, institution) < 0.5:
            common["status"] = "CONFLICT"
            common["method"] = "institution_mismatch_review"
            common["evidence_text"] = f"Paper/imported university: {expected}. Observed faculty university: {institution}. Staff review is required; AI cannot automatically approve a move."
            return common
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
        if not _attributed_role(str(candidate.get('name') or ''), identity_quote, NON_FACULTY_PATTERN):
            return None
        return common
    return common


def inspect_faculty_result(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    decision = _inspect_faculty_result(candidate, result)
    return _record_profile_decision(result.get('href'), decision)


def _record_profile_decision(url, decision):
    document = (audit_log.CURRENT.get() or {}).get('_documents', {}).get(url, {})
    decision.setdefault('source_type', _source_type_for_evidence({
        **decision, 'source_url': decision.get('source_url') or url,
        'source_kind': 'university' if 'official' in str(decision.get('method') or '') else None,
    }))
    decision.setdefault('lookup_status', _lookup_status_for_evidence({**document, **decision}))
    decision.setdefault('role_category', _role_category(decision.get('title')))
    if decision.get('title') and decision.get('status') in {
        'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE'
    }:
        decision.setdefault('currentness', 'CURRENT')
    else:
        decision.setdefault('currentness', 'UNKNOWN')
    if decision.get('status') == 'VERIFIED':
        campus = offshore_appointment(document.get('final_url') or decision.get('source_url') or url or '')
        if campus:
            decision.update(institution_name=campus[0], country_code=campus[1],
                            scope_status='OUT_OF_SCOPE',
                            scope_reason='Faculty role established at an overseas campus, not a US appointment.')
    if decision.get('status') == 'CLUE' and (document.get('failure_code') or document.get('http_status', 0) >= 400):
        decision['status'] = 'UNVERIFIED'
    if decision.get('status') == 'UNVERIFIED':
        if document.get('failure_code'):
            decision.update(reason=document['reason'], failure_code=document['failure_code'])
        elif document.get('http_status', 0) >= 400:
            decision.update(failure_code='SOURCE_BLOCKED' if document['http_status'] in (401, 403, 429) else 'SOURCE_UNAVAILABLE')
        elif 'unavailable' in str(decision.get('reason', '')).lower():
            decision['failure_code'] = 'SOURCE_UNAVAILABLE'
        else:
            decision.setdefault('failure_code', 'IDENTITY_EVIDENCE_INCOMPLETE')
    return audit_log.record_page(url, decision)


def _inspect_faculty_result(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Classify an exact-name individual profile on an official university page."""
    url = str(result.get("href") or "").strip()
    summary = " ".join(
        str(result.get(key) or "") for key in ("title", "body", "href")
    )
    domain = _possible_official_domain(
        url, str(candidate.get("institution_name") or ""), summary
    )
    if (
        not domain
        or not is_public_http_url(url)
    ):
        return {"status": "UNVERIFIED", "reason": "Not an attributable official university URL"}
    if not _identity_matches(str(candidate["name"]), summary):
        return {"status": "UNVERIFIED", "reason": "Search result does not identify this candidate"}
    if re.search(r'recent[-_/ ]*(?:doctoral[-_/ ]*)?graduates|commencement|alumni|dissertations?|theses', urlparse(url).path, re.I):
        return {'status': 'UNVERIFIED', 'reason': 'Historical graduation/alumni/publication listing, not a current role profile'}

    try:
        page_text, page_title = _fetch_official_page(url)
    except (OSError, requests.RequestException) as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        return {'status': 'UNVERIFIED', 'reason': f'Page unavailable: {type(error).__name__}' + (f' (HTTP {status})' if status else '')}
    if not page_text:
        return {"status": "UNVERIFIED", "reason": "No readable HTML content (redirect, challenge, or unsupported response)"}
    document = (audit_log.CURRENT.get() or {}).get('_documents', {}).get(url, {})
    # A redirect can change both page type and institution. Attribute the
    # response to its final host, never to the originally requested university.
    url = document.get('final_url') or url
    domain = _possible_official_domain(url, str(candidate.get('institution_name') or ''), page_title)
    if not domain:
        return {'status': 'UNVERIFIED', 'reason': 'University URL redirected outside an attributable official university domain'}
    context = _identity_context(str(candidate["name"]), f"{page_title} {page_text}")
    if not context or not _identity_matches(str(candidate["name"]), context):
        return {"status": "UNVERIFIED", "reason": "Candidate name could not be located in the fetched page"}
    title_match = _attributed_role(str(candidate['name']), context, FACULTY_TITLE_PATTERN)
    profile_identity = _profile_title_matches(str(candidate['name']), page_title) or any(
        _profile_title_matches(str(candidate['name']), heading) for heading in document.get('headings', []))
    generic_directory_title = bool(re.match(r'\s*(?:faculty|staff|people|directory|academic staff)\b', page_title, re.I))
    directory_title_match = (
        title_match
        if _is_current_faculty_listing(url, page_title) and (profile_identity or generic_directory_title)
        else None
    )
    if directory_title_match is not None:
        title_match = directory_title_match
    negative_match = _attributed_role(str(candidate['name']), context, NON_FACULTY_PATTERN)
    membership = not title_match and not negative_match and _explicit_faculty_membership(
        str(candidate['name']), url, page_title, page_text)
    if membership:
        # Directory membership is explicit evidence, but the precise rank is not.
        title_match = FACULTY_TITLE_PATTERN.search('Member of the faculty')
    # Student directories may supply explicit student evidence, but alumni and
    # graduation listings must not become current faculty appointments.
    if NON_PROFILE_URL_PATTERN.search(urlparse(url).path):
        title_match = directory_title_match = None
    institution = _institution_for_domain(domain)
    imported_name = str(candidate.get('institution_name') or '')
    if imported_name and institution.casefold().startswith(imported_name.casefold() + ' - '):
        institution = imported_name
    # Non-standard international domains such as `ualberta.ca` are accepted
    # only when the institution's root page confirms an academic organization
    # whose name matches that host. A name-looking arbitrary domain is not
    # sufficient evidence.
    if not _edu_domain(url) and (
        not institution or not _domain_matches_institution(url, institution)
    ):
        return {"status": "UNVERIFIED"}
    continuity = _institution_continuity(
        str(candidate.get("institution_name") or ""), institution, page_text
    )
    role_for_excerpt = negative_match or title_match
    display_faculty_title = _detailed_faculty_title(
        str(candidate['name']), context, title_match
    )
    evidence_context = " ".join(context.split())[:700]
    if role_for_excerpt and not membership:
        evidence_context = context[max(0, role_for_excerpt.start() - 120):role_for_excerpt.end() + 260].strip()
    page_evidence = {
        "source_url": url,
        "source_domain": domain,
        "institution_name": institution or str(candidate.get("institution_name") or ""),
        "evidence_text": evidence_context,
        "page_title": page_title,
        "_page_text": page_text,
    }
    if _is_news_or_event(url):
        return {**page_evidence, 'status': 'UNVERIFIED', 'reason': 'News/event/archive page, not a current individual faculty profile'}
    if NON_APPOINTMENT_ROLE_PATTERN.search(context):
        return {
            **page_evidence,
            "status": "CONFLICT",
            "title": display_faculty_title
                if title_match else None,
            "confidence": 0.95,
            "method": "official_non_appointment_page",
            "evidence_text": (
                "The university page identifies this person as a guest, event, "
                "or invited speaker. A faculty title elsewhere in that biography "
                "does not establish an appointment at the host university."
            ),
        }
    # A person-specific profile title is required for the rule-only positive
    # decision. The optional AI extractor may still inspect a supplied official
    # directory page, but its output must pass stricter quotation/link checks.
    profile_title_matches = profile_identity
    role_is_person_specific = profile_title_matches or directory_title_match is not None
    source_country = _country_code_for_domain(domain)
    if title_match and role_is_person_specific and source_country and source_country != "US":
        return {
            **page_evidence,
            "status": "OUT_OF_SCOPE",
            "title": display_faculty_title,
            "confidence": 0.96,
            "method": "non_us_official_faculty_profile",
            "country_code": source_country,
            "scope_status": "OUT_OF_SCOPE",
            "scope_reason": "A faculty role was found outside the current US-only index.",
            "evidence_text": (
                "An exact-name official university profile establishes a faculty "
                f"role outside the United States. Evidence: {evidence_context}"
            )[:900],
        }
    expected_institution = str(candidate.get("_paper_university") or candidate.get("institution_name") or "").strip()
    own_role = title_match or negative_match
    same_current_institution = bool(
        not expected_institution
        or not institution
        or _institution_similarity(expected_institution, institution) >= 0.5
    )
    if negative_match and profile_title_matches and continuity:
        if title_match:
            return {**page_evidence, 'status': 'UNVERIFIED', 'confidence': 0.0,
                    'method': 'contradictory_role_evidence',
                    'evidence_text': 'The profile contains conflicting current roles for this person; no automatic faculty decision was made.'}
        return {**page_evidence, 'status': 'NOT_FACULTY',
                'title': negative_match.group(0).strip().title(), 'confidence': 0.90,
                'method': 'official_directory',
                'evidence_text': f'Profile identifies the candidate as {negative_match.group(0).strip()}. Evidence: {evidence_context}'[:700]}
    if re.search(r'visiting\s+scholar\s*/\s*faculty\s*/\s*researcher', context, re.I):
        return {**page_evidence, 'status': 'UNVERIFIED', 'method': 'ambiguous_profile_role',
                'reason': 'Profile uses a combined scholar/faculty/researcher label; a faculty appointment is not explicit'}
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
    paper_corroborated = bool(
        title_match
        and not continuity
        and role_is_person_specific
        and _paper_identity_link(candidate, page_text, "")
    )
    appointment_date, appointment_precision = _extract_appointment_date(context)
    latest_paper_year = max(
        (int(paper.get('publication_year') or 0) for paper in candidate.get('recent_papers') or []),
        default=0,
    )
    dated_move_corroborated = bool(
        title_match
        and role_is_person_specific
        and appointment_date
        and appointment_date <= datetime.now(timezone.utc).date()
        and (not latest_paper_year or appointment_date.year >= latest_paper_year)
    )
    if title_match and role_is_person_specific and (
        same_current_institution or paper_corroborated or move_corroborated or dated_move_corroborated
    ):
        appointment_year = _extract_appointment_year(context)
        return {
            **page_evidence,
            "status": "VERIFIED",
            "title": display_faculty_title,
            "confidence": 0.96 if paper_corroborated else (
                0.94 if move_corroborated else 0.97
            ),
            "method": (
                "official_faculty_membership" if membership else
                "official_directory_publication_link"
                if paper_corroborated
                else (
                    "official_directory_openalex_history"
                    if move_corroborated
                    else (
                        "official_directory_dated_move"
                        if dated_move_corroborated and not same_current_institution
                        else "official_directory"
                    )
                )
            ),
            "evidence_text": (
                "The official faculty-members directory has an individual matching profile with research and publication information. Faculty membership is established; the exact rank is not stated."
                if membership else
                "A current official faculty listing contains a matching paper "
                "title or DOI, linking this appointment to the OpenAlex author."
                if paper_corroborated
                else (
                    "A current official faculty listing is corroborated by rare-name "
                    "OpenAlex affiliation records at both the previous and current "
                    "institutions."
                    if move_corroborated
                    else evidence_context
                )
            ),
            "appointment_year": appointment_year,
            "appointment_start_date": appointment_date.isoformat() if appointment_date else None,
            "appointment_date_precision": appointment_precision,
            "career_stage": "NEW_AP" if (
                "assistant professor" in str(display_faculty_title or '').casefold()
                and (appointment_year or NEW_FACULTY_PATTERN.search(context))
            ) else None,
        }
    if own_role and role_is_person_specific and not same_current_institution:
        return {**page_evidence, "status": "CONFLICT", "title": (
                    display_faculty_title if title_match else own_role.group(0).title()),
                "method": "institution_mismatch_review", "confidence": 0.0,
                "evidence_text": f"Paper/imported university: {expected_institution}. Profile university: {institution}. No sufficiently dated or publication-linked evidence established that this is a career move rather than a different person with the same name; staff review is required."}
    if title_match and not role_is_person_specific:
        return {
            **page_evidence,
            "status": "UNVERIFIED",
            "title": display_faculty_title,
            "confidence": 0.0,
            "method": "automatic_search",
        }
    if title_match and not continuity:
        return {
            **page_evidence,
            "status": "CONFLICT",
            "title": display_faculty_title,
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
        "reason": "No attributable current faculty/student role found on this profile",
    }


def inspect_researcher_profile_result(
    candidate: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    return _record_profile_decision(result.get('href'), _inspect_researcher_profile_result(candidate, result))


def _inspect_researcher_profile_result(candidate, result):
    """Use a discovered researcher page only when it contains a matching paper."""
    url = str(result.get("href") or "").strip()
    host = _host_domain(url)
    if (
        not host
        or any(
              (host == blocked or host.endswith(f".{blocked}")) and host != 'sites.google.com'
            for blocked in PAPER_LINKED_PROFILE_BLOCKED_DOMAINS
        )
        or not is_public_http_url(url)
    ):
        return {"status": "UNVERIFIED"}
    summary = " ".join(str(result.get(key) or "") for key in ("title", "body", "href"))
    if not _identity_matches(str(candidate.get("name") or ""), summary):
        return {"status": "UNVERIFIED"}
    try:
        page_text, page_title = _fetch_official_page(url)
    except (OSError, requests.RequestException) as error:
        return {"status": "UNVERIFIED", "reason": f"Personal/lab page unavailable: {type(error).__name__}"}
    name = str(candidate.get("name") or "")
    document = (audit_log.CURRENT.get() or {}).get('_documents', {}).get(url, {})
    title_identity = _profile_title_matches(name, page_title)
    named_heading = any(_profile_title_matches(name, heading) for heading in document.get('headings', []))
    profile_identity = title_identity or named_heading
    if not page_text or not profile_identity:
        return {"status": "UNVERIFIED", "reason": "Personal/lab page did not identify the candidate in its title or profile heading"}
    own_sections = document.get('profile_sections') or []
    own_text = (' '.join(own_sections) if own_sections and not title_identity
                else f'{page_title} {page_text}')
    own_text = re.sub(r'[“"]\w+[”"]', '', own_text)
    # Prefer the explicit biography sentence over a short heading like
    # "Assistant professor at SPSE". Do not extend into another team member.
    bio_pattern = r'\b' + r'\W+'.join(map(re.escape, _name_tokens(name))) + r'\s+is\s+(?:currently\s+)?(?:an?\s+)?'
    biography = re.search(bio_pattern, own_text, re.I)
    if biography:
        own_text = own_text[biography.start():]
    context = _identity_context(name, own_text)
    document['name_context'] = context[:900]
    title_match = _attributed_role(name, context, FACULTY_TITLE_PATTERN)
    negative_match = _attributed_role(name, context, NON_FACULTY_PATTERN)
    current_nonfaculty = _current_nonfaculty_role(name, context)
    generic_nonfaculty_employer = _generic_current_nonfaculty_employer(name, context)
    institution = str(candidate.get("_paper_university") or candidate.get("institution_name") or "")
    continuity = _institution_continuity(institution, "", page_text)
    official_link = bool(result.get('_official_link_institution') and
                         _institution_similarity(institution, result['_official_link_institution']) >= 0.5)
    page_institution_linked = bool(
        _document_links_to_institution(document, institution)
        and _institution_continuity(institution, '', page_text)
    )
    official_link = official_link or page_institution_linked
    paper_linked = _paper_identity_link(candidate, page_text, "")
    if not paper_linked and not official_link:
        linked_text = _fetch_related_publication_text(url)
        paper_linked = _paper_identity_link(candidate, f"{page_text} {linked_text}", "")
    # Match the current role sentence, never a former employer elsewhere in a CV.
    # When a person explicitly holds a concurrent faculty and industry role,
    # attach the institution check to the faculty clause. The industry role is
    # still retained as separate evidence below.
    role = title_match or current_nonfaculty or negative_match
    observed = []
    if role:
        role_sentence = re.split(r"[.!?;\n]|\b(?:previously|formerly|before|after)\b", context[role.end():role.end() + 240], maxsplit=1, flags=re.I)[0]
        observed = institutions_in_text(role_sentence)
        # Official institution aliases are locators/names, not proof by
        # themselves. Expand only the employer immediately attached to role.
        employer = re.match(r'\s*(?:at|@)\s+([^,;.!?]+)', role_sentence, re.I)
        alias = record_for_name(employer.group(1).strip()) if employer else None
        if alias:
            observed = [alias[0]]
        if not observed and official_link and (
            _institution_continuity(institution, '', context) or page_institution_linked
        ):
            observed = [institution]
    role_text = " ".join(role.group(0).split()) if role else None
    if not role_text and generic_nonfaculty_employer:
        role_text = "Current non-faculty role"
    display_faculty_title = _detailed_faculty_title(name, context, title_match)
    observed_employer = _employer_after_role(context, role) or generic_nonfaculty_employer
    current_faculty_employer = _faculty_employer_after_role(context, title_match)
    if title_match and not observed and current_faculty_employer:
        observed = [current_faculty_employer]
    career_continuity = bool(
        title_match
        and institution
        and _institution_continuity(institution, "", context)
        and re.search(
            r"\b(?:previously|formerly|subsequently|prior\s+to|before|where\s+he\s+worked|where\s+she\s+worked)\b",
            context,
            re.IGNORECASE,
        )
    )
    host_key = re.sub(r"[^a-z0-9]", "", host.split(".")[0].casefold())
    employer_key = re.sub(r"[^a-z0-9]", "", str(current_faculty_employer or "").casefold())
    organization_owned_profile = bool(
        title_match and host_key and len(host_key) >= 5 and host_key in employer_key
    )
    identity_linked = paper_linked or official_link or career_continuity
    common = {
        "source_url": url,
        "source_domain": host,
        "institution_name": institution,
        "page_title": page_title,
        "_page_text": page_text,
        "source_type": "PERSONAL_WEBSITE" if host not in {"linkedin.com", "scholar.google.com"} else "PUBLIC_PROFILE",
        "lookup_status": "FOUND",
        "currentness": "CURRENT" if role or generic_nonfaculty_employer else "UNKNOWN",
        "role_category": (
            _role_category(role_text) if not generic_nonfaculty_employer else "INDUSTRY"
        ),
        "observed_employer": observed_employer,
    }
    if current_nonfaculty and not title_match:
        return {
            **common,
            "status": "NOT_FACULTY",
            "title": role_text,
            "confidence": 0.92,
            "method": (
                "official_directory_profile_link"
                if official_link else "attributed_current_nonfaculty_profile"
            ),
            "evidence_text": (
                "The exact-name personal profile states a current nonfaculty role"
                + (f" at {observed_employer}" if observed_employer else "")
                + f". Evidence: {context[:500]}"
            )[:900],
        }
    if generic_nonfaculty_employer and not title_match:
        return {
            **common,
            "status": "NOT_FACULTY",
            "title": role_text,
            "confidence": 0.92,
            "method": "attributed_current_employment_profile",
            "evidence_text": (
                "The exact-name personal profile explicitly states current employment at "
                f"{generic_nonfaculty_employer} and contains no faculty appointment. "
                f"Evidence: {context[:500]}"
            )[:900],
        }
    if negative_match and not title_match and profile_identity:
        return {
            **common,
            "status": "NOT_FACULTY",
            "title": role_text,
            "confidence": 0.90,
            "method": (
                "official_directory_profile_link"
                if official_link else "attributed_current_nonfaculty_profile"
            ),
            "evidence_text": (
                "The exact-name personal profile identifies the candidate as "
                f"{role_text}. Evidence: {context[:550]}"
            )[:900],
        }
    if title_match and profile_identity and _non_us_faculty_claim(context, url, title_match):
        return {
            **common,
            "status": "OUT_OF_SCOPE",
            "title": display_faculty_title,
            "confidence": 0.88,
            "method": "non_us_faculty_profile",
            "scope_status": "OUT_OF_SCOPE",
            "scope_reason": "A faculty role was found outside the current US-only index.",
            "evidence_text": f"The exact-name profile states a faculty role outside the United States. Evidence: {context[:600]}",
        }
    if title_match and profile_identity and organization_owned_profile and career_continuity:
        current_institution = current_faculty_employer or observed_employer or host
        classification = lookup_institution_classification(current_institution, host)
        outside_us_university_scope = bool(
            classification.get("organization_type") != "HIGHER_EDUCATION"
            and re.search(r"\b(?:research\s+institute|research\s+centre|research\s+center)\b", current_institution, re.I)
        )
        if outside_us_university_scope:
            return {
                **common,
                "status": "OUT_OF_SCOPE",
                "institution_name": current_institution,
                "title": role_text,
                "role_category": "FACULTY",
                "confidence": 0.94,
                "method": "current_research_institute_faculty_profile",
                "scope_status": "OUT_OF_SCOPE",
                "scope_reason": "The current faculty appointment is at a research institute rather than a US College Scorecard university.",
                "previous_institutions": [institution],
                "evidence_text": (
                    f"The current organization profile identifies {name} as {role_text} at "
                    f"{current_institution}; the same biography links the person to the imported "
                    f"prior institution, {institution}."
                ),
            }
    if title_match and profile_identity and len(observed) == 1 and institution and _institution_similarity(institution, observed[0]) < 0.5:
        return {
            **common,
            "status": "UNVERIFIED",
            "institution_name": observed[0],
            "title": display_faculty_title,
            "method": "personal_current_faculty_claim",
            "failure_code": "CURRENT_AFFILIATION_UNCONFIRMED",
            "confidence": 0.0,
            "evidence_text": (
                f"The exact-name personal page states a current faculty role at {observed[0]}, "
                f"while the imported/paper affiliation is {institution}. ScholarRadar must confirm "
                "the current appointment on an official university page."
            ),
        }
    if role and identity_linked and len(observed) == 1:
        if institution and _institution_similarity(institution, observed[0]) < 0.5:
            return {**common, "status": "CONFLICT", "institution_name": observed[0],
                    "title": display_faculty_title or role.group(0).title(), "method": "institution_mismatch_review", "confidence": 0.0,
                    "evidence_text": f"Paper/imported university: {institution}. Researcher page states a faculty role at {observed[0]} and contains a matching paper. Staff review is required; automatic retries stop."}
    # Personal-page positives require a university attached to the role, not
    # merely the old institution appearing somewhere in the page.
    continuity = (len(observed) == 1 and _institution_similarity(institution, observed[0]) >= 0.5) or bool(
        negative_match and not title_match and not observed and
        _institution_continuity(institution, '', context) and identity_linked)
    if title_match and current_nonfaculty and continuity and identity_linked:
        return {
            **common,
            "status": "VERIFIED",
            "title": display_faculty_title,
            "confidence": 0.90,
            "method": "concurrent_faculty_profile_publication_link",
            "role_category": "FACULTY",
            "evidence_text": (
                "The exact-name profile states a current faculty appointment at "
                "the matching university and also states a concurrent nonfaculty "
                "role. A matching publication connects the profile to this author."
            ),
        }
    if title_match and (negative_match or current_nonfaculty):
        return {**common, 'status': 'UNVERIFIED', 'method': 'contradictory_role_evidence',
                'evidence_text': 'The researcher page has conflicting current roles; review is required.'}
    if title_match and continuity and identity_linked:
        return {
            **common,
            "status": "VERIFIED",
            "title": display_faculty_title,
            "confidence": 0.90,
            "method": "official_directory_profile_link" if official_link else "researcher_profile_publication_link",
            "evidence_text": (
                "A researcher page found through the person's name and target university "
                "states a current faculty role. Identity is linked by an official directory "
                "link or a matching supporting publication."
            ),
        }
    if negative_match and not title_match and continuity and identity_linked:
        return {
            **common,
            "status": "NOT_FACULTY",
            "title": " ".join(negative_match.group(0).split()).title(),
            "confidence": 0.85,
            "method": "official_directory_profile_link" if official_link else "researcher_profile_publication_link",
            "evidence_text": (
                "A researcher page found through the person's name and target university "
                "identifies the person as a student or postdoctoral researcher. Identity "
                "is linked by an official directory link or a matching publication."
            ),
        }
    missing = []
    if not title_match and not negative_match:
        missing.append('no current role attributable to this person')
    if not continuity:
        missing.append('university not safely attached to the current role')
    if not identity_linked:
        missing.append('no matching supporting paper or attributable official directory link found')
    return {**common, "status": "UNVERIFIED", "reason": 'Personal/lab page: ' + '; '.join(missing)}


def verify_faculty_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    audit = audit_log.new_audit(candidate)
    audit.update(_result_limit=setting_int('FACULTY_IDENTITY_PASS_RESULTS', 100, 10, 100),
                 _page_limit=setting_int('FACULTY_IDENTITY_PASS_PAGES', 20, 3, 20), _pages_used=0)
    token = audit_log.CURRENT.set(audit)
    audit_log.emit('identity_started', university=candidate.get('institution_name'),
                   max_queries=3,
                   max_results=audit['_result_limit'], max_pages=audit['_page_limit'])
    try:
        decision = _verify_faculty_candidate(dict(candidate))
        if decision.get('status') == 'UNVERIFIED':
            reasons = list(dict.fromkeys(str(page['reason'])[:200] for page in audit['pages'] if page.get('reason')))
            codes = [p.get('failure_code') for p in audit['pages'] if p.get('failure_code')]
            decision.setdefault('failure_code', 'MISSING_AFFILIATION' if decision.get('method') == 'missing_paper_affiliation' else
                                ('PROFILE_REMOVED' if 'PROFILE_REMOVED' in codes and all(p.get('failure_code') for p in audit['pages']) else
                                 'SOURCE_BLOCKED' if 'SOURCE_BLOCKED' in codes and all(p.get('failure_code') for p in audit['pages']) else
                                 'SOURCE_UNAVAILABLE' if 'SOURCE_UNAVAILABLE' in codes else
                                 'IDENTITY_EVIDENCE_INCOMPLETE' if audit['pages'] else 'NO_USEFUL_PROFILE'))
            decision['reason'] = (decision.get('reason') or decision.get('evidence_text') or
                                  ('; '.join(reasons[:3]) if reasons else 'Search returned no usable individual profile. This is not proof that the person is not faculty.'))
            source_problems = list(dict.fromkeys(code for code in codes if code in {
                'SOURCE_BLOCKED', 'SOURCE_UNAVAILABLE', 'PROFILE_REMOVED', 'NO_READABLE_CONTENT', 'PDF_PARSE_FAILED', 'PDF_NO_TEXT'}))
            if source_problems:
                decision['source_problems'] = source_problems
                audit['source_problems'] = source_problems
                decision['reason'] += ' Source problems encountered: ' + ', '.join(source_problems) + '.'
        decision['search_audit'] = audit_log.finish(audit, decision)
        return decision
    except SearchUnavailable as error:
        error.search_audit = audit_log.finish(audit, {'status': 'SOURCE_WAIT', 'failure_code': 'SEARCH_UNAVAILABLE',
                                                     'reason': 'Search provider unavailable or waiting for its next request slot; no identity decision saved'})
        raise
    finally:
        audit_log.CURRENT.reset(token)


def _verify_faculty_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    name = str(candidate["name"])
    institution = str(candidate.get("institution_name") or "").strip()
    candidate['_imported_institution'] = institution
    # Use stored OpenAlex authorship metadata first. This makes no API request
    # and downloads no paper.
    candidate = enrich_candidate_metadata_affiliations(candidate, max_papers=3)
    if not institution and not candidate.get("paper_affiliations") and candidate.get("recent_papers"):
        candidate = enrich_candidate_paper_affiliations(candidate, max_papers=3)
        candidate["_pdf_attempted"] = True
    supported = [item for item in candidate.get("paper_affiliations") or []
                 if item.get("status") == "MATCHED" and item.get("institution_name")]
    if supported:
        institution = str(supported[0]["institution_name"]).strip()
        candidate["_paper_university"] = institution
        candidate["institution_name"] = institution
    audit = audit_log.CURRENT.get()
    if audit is not None:
        audit['search_university'] = institution
        audit['affiliation_evidence'] = [{k: item.get(k) for k in ('status', 'institution_name', 'source_url')} for item in candidate.get('paper_affiliations') or []][:3]
    audit_log.emit('identity_affiliation', university=institution,
                   evidence=(audit or {}).get('affiliation_evidence', []))
    audit_log.remaining_seconds()
    field = next((str(paper.get("matched_query") or "").strip()
                  for paper in candidate.get("recent_papers") or [] if paper.get("matched_query")), "")
    # Known profile URLs and bounded evidence are checked before web search.
    institution_queries: list[str] = []
    institution_domain = str(candidate.get("institution_domain") or "").strip()
    registered_institution = record_for_name(institution)
    if registered_institution:
        institution_domain = registered_institution[1]
    elif institution != str(candidate.get('_imported_institution') or candidate.get('institution_name') or ''):
        institution_domain = ''
    if institution:
        institution_queries.append(f'"{name}" "{institution}"')
    # Do not spend the bounded pass on ambiguous acronyms such as PU or DCAD.
    institution_queries = list(dict.fromkeys(institution_queries))
    seen_urls: set[str] = set()
    seen_queries: set[str] = set()
    negative: dict[str, Any] | None = None
    ambiguous_pages: list[dict[str, Any]] = []
    mismatch_pages: list[dict[str, Any]] = []
    search_results: list[dict[str, Any]] = []
    queries_checked = 0
    pages_checked = 0
    search_outage: SearchUnavailable | None = None
    # Each search may return many links. Limit the paid retrieval stage to the
    # three targeted queries defined below and spend the remaining work on page
    # inspection instead of query variations.
    query_limit = 3
    page_limit = setting_int("FACULTY_IDENTITY_PASS_PAGES", 20, 3, 20)
    result_limit = setting_int('FACULTY_IDENTITY_PASS_RESULTS', 100, 10, 100)
    result_urls: set[str] = set()
    confirming_personal = False
    confirmation_target_institution = ""
    discovered_domains: list[str] = []

    def domain_queries():
        """Refresh locators after every inspected CV/profile; never a Gmail query."""
        domains = [institution_domain, *discovered_domains]
        for evidence in candidate.get('paper_affiliations') or []:
            email = str(evidence.get('email') or '')
            if '@' in email:
                domains.append(email.rsplit('@', 1)[-1])
        for item in search_results:
            domain = _possible_official_domain(str(item.get('href') or ''), institution, '')
            if domain and _domain_matches_institution('https://' + domain, institution):
                domains.append(domain)
        domains.extend(_verified_email_domain_hints(name, search_results))
        return list(dict.fromkeys(academic_domain_hint(d) for d in domains if academic_domain_hint(d)))[:3]

    def confirm_personal_affiliation(proposal):
        """A personal page can retain an old employer after a move. Check once."""
        nonlocal confirming_personal, confirmation_target_institution
        if proposal.get('method') not in {
            'researcher_profile_publication_link', 'personal_current_faculty_claim'
        } or proposal.get('status') not in {'VERIFIED', 'UNVERIFIED'}:
            return proposal
        if not confirming_personal:
            confirming_personal = True
            confirmation_target_institution = str(proposal.get('institution_name') or '').strip()
            audit_log.emit('identity_affiliation_confirmation', reason='Personal/lab faculty evidence needs official current-affiliation corroboration')
            target_record = record_for_name(confirmation_target_institution)
            confirm_queries = []
            if target_record:
                confirm_queries.append(f'"{name}" site:{target_record[1]}')
            confirm_queries.append(
                f'"{name}" "{confirmation_target_institution}" faculty profile'
                if confirmation_target_institution else f'"{name}" faculty profile'
            )
            try:
                for query in confirm_queries:
                    corroborated = inspect_query(query)
                    if corroborated and (corroborated.get('status') in {'CONFLICT', 'NOT_FACULTY'} or
                            (corroborated.get('status') == 'VERIFIED' and corroborated.get('method') != 'researcher_profile_publication_link')):
                        return corroborated
            finally:
                confirming_personal = False
                confirmation_target_institution = ""
        return {**proposal, 'status': 'UNVERIFIED', 'confidence': 0.0,
                'failure_code': 'CURRENT_AFFILIATION_UNCONFIRMED',
                'reason': 'A personal/lab page states a faculty role, but its current university could not be corroborated on an official profile. The page may retain an old appointment.'}

    def classify(result):
        url = str(result.get('href') or '')
        summary = ' '.join(str(result.get(k) or '') for k in ('title', 'body', 'href'))
        official = bool(_possible_official_domain(url, institution, summary))
        matches = _identity_matches(name, summary)
        # A target university's directory can lead to an individual entry even
        # when its search excerpt omits the name. It cannot itself prove a role.
        directory = (official and _domain_matches_institution(url, institution) and
                     bool(re.search(r'/(?:directory|faculty|people|staff|faculty-and-staff|fac-staff)(?:/index\.[a-z]+|\.[a-z]+)?/?$', urlparse(url).path, re.I)))
        kind = source_kind(url, summary, name_matches=matches or directory, official=official,
                           profile_title=_profile_title_matches(name, str(result.get('title') or '')))
        if not kind and matches and not excluded_profile_source(url) and (result.get('discovered_from') or result.get('_known_profile')):
            kind = 'personal'
        result['source_kind'] = kind
        return kind, official, matches

    def inspect_source(result, depth=0):
        nonlocal pages_checked
        url = canonical_source_url(str(result.get('href') or '').strip())
        result['href'] = url
        kind, official, matches = classify(result)
        if not url or url in seen_urls:
            return None
        doc_alias = (audit or {}).get('_documents', {}).get(url, {})
        if doc_alias.get('final_url') in seen_urls and doc_alias.get('final_url') != url:
            return None
        if not kind:
            audit_log.note_result(url, 'Skipped: no candidate-related profile, university, lab, LinkedIn or CV indication')
            return None
        if _snippet_only_url(url):
            audit_log.note_result(url, 'Snippet retained as a clue; source page not inspected')
            return None
        audit_log.remaining_seconds()
        used = max(pages_checked, (audit or {}).get('_pages_used', 0))
        if used >= page_limit:
            audit_log.note_result(url, 'Not inspected: page-check budget reached')
            return None
        seen_urls.add(url)
        pages_checked = used + 1
        if audit is not None:
            audit['_pages_used'] = pages_checked
        # University news and general directories are link locators only.
        lead_only = _is_news_or_event(url) or not matches or kind == 'cv'
        if lead_only:
            try:
                _fetch_official_page(url)
                inspected = {'status': 'CLUE', 'reason': 'Read as a locator for an individual profile, not as faculty proof'}
            except (OSError, requests.RequestException) as error:
                inspected = {'status': 'UNVERIFIED', 'reason': f'Locator page unavailable: {type(error).__name__}'}
            _record_profile_decision(url, inspected)
        else:
            inspection_candidate = candidate
            if official and confirming_personal and confirmation_target_institution:
                inspection_candidate = dict(candidate)
                inspection_candidate['institution_name'] = confirmation_target_institution
                inspection_candidate['_paper_university'] = confirmation_target_institution
            inspected = (inspect_faculty_result(inspection_candidate, result) if official else
                         inspect_researcher_profile_result(candidate, result))
        document = (audit or {}).get('_documents', {}).get(url, {})
        if document.get('final_url'):
            seen_urls.add(document['final_url'])
        if _identity_matches(name, document.get('text', '')):
            for domain in re.findall(r'@[ \t]*([A-Za-z0-9.-]+\.[A-Za-z]{2,})', document.get('text', '')):
                if academic_domain_hint(domain) and domain not in discovered_domains:
                    discovered_domains.append(domain)
        if inspected.get('status') == 'NOT_FACULTY' and not official:
            # A subordinate /authors/admin biography may predate the current
            # homepage. Check that linked homepage before saving a student role.
            roots = [a['href'] for a in document.get('links', []) if
                     _host_domain(a['href']) == _host_domain(url) and
                     urlparse(a['href']).path in {'', '/'} and
                     str(a.get('label') or '').lower() in {'home', 'homepage', 'publications'}]
            if roots and urlparse(url).path not in {'', '/'} and depth < 2:
                root_url = canonical_source_url(roots[0])
                if root_url not in seen_urls:
                    alternate = inspect_source({'href': root_url, 'title': name,
                                                'discovered_from': url}, depth + 1)
                    if alternate:
                        return alternate
                root_doc = (audit or {}).get('_documents', {}).get(root_url, {})
                root_text = root_doc.get('text', '')
                completed = re.search(r'\b(?:earned|received|completed|graduated)\b.{0,60}\bPh\.?D\.?\b|\bPh\.?D\.?\s*[,;:]\s*20\d{2}\b', root_text, re.I)
                if completed and _profile_title_matches(name, root_doc.get('title', '')):
                    unresolved = {**inspected, 'status': 'UNVERIFIED', 'confidence': 0.0,
                        'failure_code': 'CURRENT_ROLE_UNCONFIRMED',
                        'reason': 'A subordinate page says student, but the attributed homepage records a completed doctorate; the student statement may be historical.'}
                    _record_profile_decision(url, unresolved)
                    ambiguous_pages.append(unresolved)
                    return None
        if (inspected.get('status') in {'VERIFIED', 'NOT_FACULTY', 'OUT_OF_SCOPE'} or
                inspected.get('method') in {'institution_mismatch_review', 'personal_current_faculty_claim'}):
            if inspected.get('status') == 'CONFLICT':
                inspected['alternative_evidence'] = [
                    {key: page.get(key) for key in ('source_url', 'institution_name', 'title', 'status', 'evidence_text')}
                    for page in [*mismatch_pages, inspected]
                ]
            return confirm_personal_affiliation(inspected)
        if inspected.get('source_url') and inspected.get('_page_text'):
            ambiguous_pages.append(inspected)
        if inspected.get('status') == 'CONFLICT':
            mismatch_pages.append(inspected)
        if depth >= 2:
            return None
        leads = linked_profile_leads(document.get('final_url') or url, document.get('links', []),
                                      lambda text: _identity_matches(name, text), personal=kind in {'personal', 'lab'})
        root = cv_homepage(url) if kind == 'cv' else None
        if kind == 'cv':
            # A CV and its search excerpt can expose the author's actual site.
            # These links are locators only; the destination must prove the role.
            cv_urls = text_url_leads(str(result.get('body') or ''))
            if _identity_matches(name, document.get('text', '')):
                cv_urls = [a['href'] for a in document.get('links', [])] + cv_urls
            leads = [{'href': u, 'title': name, 'body': '', 'discovered_from': url}
                     for u in dict.fromkeys(cv_urls) if canonical_source_url(u) != url and
                     not urlparse(u).path.lower().endswith('.pdf') and not excluded_profile_source(u)] + leads
        if root:
            leads.insert(0, {'href': root, 'title': name, 'body': '', 'discovered_from': url})
        for lead in leads[:2]:
            # Link discovery selects a candidate; the target must independently
            # pass name, own-role, institution and paper checks.
            lead['title'] = name
            if official and not _is_news_or_event(url) and lead.get('named_anchor') and (
                    _domain_matches_institution(url, institution) or
                    (institution_domain and _edu_domain(url) == institution_domain)):
                lead['_official_link_institution'] = institution
            classify(lead)
            if audit is not None and not any(r['url'] == lead['href'] for r in audit['results']):
                if len(audit['results']) < result_limit:
                    audit_log.record_results('Link followed from ' + url, [lead])
            audit_log.emit('identity_follow_link', source=url, target=lead['href'])
            found = inspect_source(lead, depth + 1)
            if found:
                return found
        return None

    def inspect_query(
        query: str, *, allow_researcher_profile_with_paper: bool = False
    ) -> dict[str, Any] | None:
        nonlocal negative, queries_checked, pages_checked, search_outage
        if (
            search_outage is not None
            or query in seen_queries
            or queries_checked >= query_limit
            or pages_checked >= page_limit
            or len(result_urls) >= result_limit
        ):
            return None
        seen_queries.add(query)
        queries_checked += 1
        if candidate.get("_direct_only"):
            search_outage = SearchUnavailable("Web search waiting: direct evidence checked; search is scheduled later.",
                                              int(candidate.get("_search_retry_seconds") or 60))
            return None
        audit_log.emit('identity_search_started', query=query, number=queries_checked, max_queries=query_limit)
        # The worker process is isolated, so a longer per-query ceiling cannot
        # stall the whole indexer while a provider is slow but responsive.
        query_timeout = setting_int('FACULTY_SEARCH_QUERY_TIMEOUT_SECONDS', 300, 10, 300)
        audit_log.begin_search(query_timeout)
        try:
            results = search_web(
                query,
                max_results=min(20, result_limit - len(result_urls)),
            )
        except SearchUnavailable as error:
            if audit is not None:
                audit['queries'].append({'query': query, 'returned': 0, 'kind': 'search',
                                         'error': 'Search provider unavailable or waiting',
                                         'retry_after_seconds': error.retry_after_seconds})
            audit_log.emit('identity_search_wait', query=query, retry_after_seconds=error.retry_after_seconds)
            search_outage = error
            return None
        except audit_log.IdentityPassLimit as error:
            if audit is not None:
                audit['queries'].append({'query': query, 'returned': 0, 'kind': 'search',
                                         'error': 'SEARCH_TIMEOUT',
                                         'timeout_seconds': query_timeout})
            audit_log.emit('identity_search_timeout', query=query,
                           timeout_seconds=query_timeout, reason=str(error))
            return None
        except Exception as error:
            # An unexpected retrieval error is not a completed negative search.
            if audit is not None:
                audit['queries'].append({'query': query, 'returned': 0, 'kind': 'search', 'error': type(error).__name__})
            audit_log.emit('identity_search_error', query=query, error_type=type(error).__name__)
            search_outage = SearchUnavailable(f'Identity search failed: {type(error).__name__}', 60)
            return None
        finally:
            audit_log.end_search()
        results = results[:min(20, result_limit - len(result_urls))]
        for result in results:
            classify(result)
        search_results.extend(results)
        audit_log.record_results(query, results)
        result_urls.update(canonical_source_url(item.get('href')) for item in results if item.get('href'))
        query_page_ceiling = (max(pages_checked + 1, page_limit // 2)
                              if queries_checked < query_limit and len(results) < 20 and len(result_urls) < result_limit
                              else page_limit)
        query_negative: dict[str, Any] | None = None
        for result in sorted(results, key=lambda item: (
                0 if item.get('source_kind') == 'university' and not _is_news_or_event(str(item.get('href') or '')) else 1,
                _profile_priority(item))):
            url = str(result.get("href") or "").strip()
            if not url or url in seen_urls:
                continue
            if max(pages_checked, (audit or {}).get('_pages_used', 0)) >= query_page_ceiling:
                break
            result_urls.add(url)
            result_title = str(result.get("title") or "")
            result_summary = " ".join(
                str(result.get(key) or "") for key in ("title", "body", "href")
            )
            # Name+paper searches are often dominated by publication records.
            # They help identify the person, but are not current faculty pages
            # and must not consume the entire profile-fetch budget.
            snippet_role = _attributed_role(name, result_summary, NON_FACULTY_PATTERN)
            if snippet_role:
                audit_log.note_result(url, '', f'Snippet suggests {snippet_role.group(0).strip()}; source role not confirmed')
            inspected = inspect_source(result)
            if not inspected:
                continue
            if inspected.get('status') == 'NOT_FACULTY':
                # Old university student/postdoc pages often remain live.
                # Finish the promising results already returned by this query
                # so a newer explicit faculty appointment can supersede one.
                if query_negative is None:
                    query_negative = inspected
                if negative is None:
                    negative = inspected
                continue
            if inspected.get('status') == 'OUT_OF_SCOPE':
                return inspected
            if inspected.get('failure_code') == 'CURRENT_AFFILIATION_UNCONFIRMED':
                if confirming_personal:
                    continue
                ambiguous_pages.append(inspected)
                continue
            if inspected.get("status") == "VERIFIED":
                return inspected
            if inspected.get('status') == 'CONFLICT' and inspected.get('method') == 'institution_mismatch_review':
                # A supported institution mismatch is already a decision under
                # this workflow. Do not spend more page/API budget or let a later
                # old-university page silently replace it with VERIFIED.
                return inspected
            if inspected.get("source_url") and inspected.get("_page_text"):
                ambiguous_pages.append(inspected)
            if inspected.get("status") == "CONFLICT":
                mismatch_pages.append(inspected)
            if inspected.get("status") == "NOT_FACULTY" and negative is None:
                negative = inspected
        # Retain alternatives from this result set, without buying more queries
        # or automatically accepting an apparent move.
        conflicts = [page for page in mismatch_pages if page.get("method") == "institution_mismatch_review"]
        if conflicts:
            conflict = dict(conflicts[0])
            conflict["alternative_evidence"] = [
                {key: page.get(key) for key in ("source_url", "institution_name", "title", "status", "evidence_text")}
                for page in conflicts
            ]
            return conflict
        if query_negative is not None:
            return query_negative
        return None

    def inspect_known_url(url: str) -> dict[str, Any] | None:
        if not url:
            return None
        # The synthetic title only passes the result prefilter. The fetched
        # document must still independently pass name, role and attribution.
        result = {"href": canonical_source_url(url), "title": name, "body": "", '_known_profile': True}
        classify(result)
        audit_log.record_results('Previously saved profile URL', [result])
        return inspect_source(result)

    known_urls = [str(candidate.get(key) or '') for key in ('faculty_source_url', 'homepage_url')]
    previous_audit = candidate.get('identity_search_audit') or {}
    if previous_audit.get('outcome') == 'NOT_FACULTY' and previous_audit.get('source_url'):
        known_urls.insert(0, previous_audit['source_url'])
    # Remember discovered profile URLs even when blocked/unresolved. They are
    # leads only: all name, role and affiliation checks still run on the page.
    known_urls += [str(page.get('final_url') or page.get('url') or '')
                   for page in previous_audit.get('pages', [])
                   if page.get('status') != 'CLUE' and not excluded_profile_source(str(page.get('url') or ''))][:3]
    for url in dict.fromkeys(known_urls):
        verified = inspect_known_url(url)
        if verified:
            return verified

    clues = fetch_orcid_clues(candidate)
    # Only an already linked ORCID with a compatible name can supply locators.
    # Employment claims and dates never directly decide faculty status.
    if clues and _same_normalized_name(name, str(clues.get("name") or "")):
        for url in list(clues.get("links") or [])[:3]:
            verified = inspect_known_url(str(url))
            if verified:
                return verified
        for employment in list(clues.get("employments") or [])[:2]:
            hint = str(employment.get("institution") or "").strip()
            if hint:
                institution_queries.append(f'"{name}" "{hint}"')

    if negative and not ambiguous_pages and not mismatch_pages:
        return negative

    # Known pages were insufficient. Inspect no more than three recent
    # accessible papers to corroborate the stored affiliation, then retry with
    # the name, target university, and exact supporting paper.
    if candidate.get("recent_papers") and not candidate.get("_pdf_attempted") and (not candidate.get("paper_affiliations") or ambiguous_pages or mismatch_pages):
        candidate = enrich_candidate_paper_affiliations(candidate, max_papers=3)
    paper_evidence = list(candidate.get("paper_affiliations") or [])
    paper_identity_confirmed = any(
        str(evidence.get("status") or "") == "MATCHED"
        for evidence in paper_evidence
    )
    paper_institutions = list(dict.fromkeys(
        str(evidence.get("institution_name") or "").strip()
        for evidence in paper_evidence
        if str(evidence.get("status") or "") == "MATCHED"
        and str(evidence.get("institution_name") or "").strip()
    ))
    if not institution and paper_institutions:
        institution = paper_institutions[0]
        candidate["institution_name"] = institution

    if not institution:
        return {"status": "UNVERIFIED", "confidence": 0.0, "method": "missing_paper_affiliation",
                "evidence_text": "No university could be linked safely from stored authorship metadata or accessible papers. No unanchored name search was sent."}

    institution_classification = lookup_institution_classification(
        institution, str(candidate.get("institution_domain") or "")
    )
    candidate["institution_classification"] = institution_classification
    if audit is not None:
        audit["institution_classification"] = institution_classification

    # Run three targeted searches only. Known URLs, ORCID locators and paper
    # metadata above do not consume these search calls.
    primary_queries = [f'"{name}" "{institution}"']
    provisional_stale_faculty: dict[str, Any] | None = None
    verified = inspect_query(
        primary_queries[0], allow_researcher_profile_with_paper=True
    )
    if verified:
        if (
            verified.get("status") == "VERIFIED"
            and STALE_FACULTY_PROFILE_HINT_PATTERN.search(
                f'{verified.get("source_url") or ""} {verified.get("page_title") or ""}'
            )
        ):
            provisional_stale_faculty = verified
        else:
            return verified

    # Query one can reveal a verified-email or official university domain. Build
    # the site query afterwards so that evidence is available here.
    domains = domain_queries()
    if domains:
        primary_queries.append(f'"{name}" site:{domains[0]}')
    else:
        primary_queries.append(f'"{name}" "{institution}" faculty profile')
    stale_affiliation_hint = bool(
        provisional_stale_faculty
        or any(
            STALE_FACULTY_PROFILE_HINT_PATTERN.search(
                f'{item.get("href") or ""} {item.get("title") or ""}'
            )
            for item in search_results
        )
    )
    primary_queries.append(
        f'"{name}" professor current university'
        if stale_affiliation_hint
        else (
            f'"{name}" "{institution}" {field[:100]}'
            if field else f'"{name}" "{institution}" professor'
        )
    )
    primary_queries = list(dict.fromkeys(primary_queries[:3]))
    for query in primary_queries[1:]:
        verified = inspect_query(query, allow_researcher_profile_with_paper=True)
        if verified:
            if (
                provisional_stale_faculty
                and verified.get("status") == "VERIFIED"
                and _institution_similarity(
                    str(verified.get("institution_name") or ""), institution
                ) >= 0.5
            ):
                provisional_stale_faculty = verified
                continue
            return verified
    if provisional_stale_faculty:
        return provisional_stale_faculty

    # An explicit official student/postdoc result is already a strong rule-based
    # decision. Spend AI quota only on unresolved pages or identity conflicts.
    if negative and negative.get("status") == "NOT_FACULTY":
        return negative
    # This bounded pass is rule-based. Do not let AI promote a rejected news,
    # directory or snippet into an individual current faculty appointment.

    # A single different-university page is usually a search miss or a
    # namesake, not a task for staff. Escalate only when multiple official
    # domains contain plausible faculty profiles for the same person name.
    conflict_domains = {
        str(page.get("source_domain") or "")
        for page in mismatch_pages
        if page.get("source_domain")
    }
    if mismatch_pages and paper_identity_confirmed:
        conflict = mismatch_pages[0]
        conflict["status"] = "CONFLICT"
        conflict["confidence"] = 0.0
        conflict["method"] = "paper_affiliation_institution_conflict"
        conflict["evidence_text"] = (
            "A paper confirms the candidate's imported university affiliation, "
            "but an exact-name official faculty page points to a different university. "
            "This may be a recent move or a different person with the same name."
        )
        return conflict
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
    snippet_decision = _public_snippet_nonfaculty_consensus(name, search_results, candidate)
    if snippet_decision:
        return snippet_decision
    if search_outage is not None:
        # Preserve the identity; record the retry separately in the batch runner.
        raise search_outage
    if audit is not None:
        audit['stopping_reason'] = ('Page-check limit reached' if max(pages_checked, audit.get('_pages_used', 0)) >= page_limit else
                                    'Result limit reached' if len(result_urls) >= result_limit else
                                    'Query limit reached' if queries_checked >= query_limit else
                                    'Available query variants completed')
    # A promising personal/lab faculty page with an unconfirmed current
    # affiliation is stronger than a bare search miss. Keep it reviewable
    # instead of replacing it with the low-confidence three-query exclusion.
    unresolved = mismatch_pages or ambiguous_pages
    if unresolved:
        best = next((page for page in unresolved if page.get('failure_code') == 'CURRENT_ROLE_UNCONFIRMED'), unresolved[0])
        return {
            **best,
            "status": "UNVERIFIED",
            "confidence": 0.0,
            "method": "automatic_search",
            "evidence_text": (
                "A possible official or attributable profile was found, but the "
                "current role or institution could not be confirmed safely."
            ),
        }
    source_problem_pages = [
        page for page in (audit or {}).get('pages', [])
        if page.get('failure_code') in {
            'SOURCE_BLOCKED', 'SOURCE_UNAVAILABLE', 'PROFILE_REMOVED',
            'NO_READABLE_CONTENT', 'PDF_PARSE_FAILED', 'PDF_NO_TEXT',
        }
    ]
    if source_problem_pages:
        first_problem = source_problem_pages[0]
        return {
            'status': 'UNVERIFIED',
            'confidence': 0.0,
            'method': 'source_problem',
            'failure_code': first_problem.get('failure_code'),
            'source_url': first_problem.get('url'),
            'evidence_text': (
                'A potentially useful identity source was blocked, unavailable, '
                'or unreadable. Absence of a readable faculty page is not a '
                'completed negative search.'
            ),
        }
    if queries_checked == len(primary_queries):
        if institution_classification.get("organization_type") == "K12_SCHOOL":
            return {
                "status": "NOT_FACULTY",
                "confidence": 0.94,
                "method": "k12_organization_no_faculty_profile",
                "review_recommended": True,
                "evidence_text": (
                    institution_classification.get("evidence")
                    or f"{institution} is identified as a K–12 school."
                ) + " Three completed searches found no current US university faculty appointment.",
                "institution_classification": institution_classification,
            }
        return {
            "status": "NOT_FACULTY",
            "confidence": 0.55,
            "method": "completed_three_query_no_faculty_profile",
            "review_recommended": True,
            "evidence_text": (
                "Three successful targeted searches found no attributable current "
                "US university faculty profile. This is a low-confidence automatic "
                "exclusion; staff confirmation is recommended."
            ),
            "alternative_evidence": [
                {
                    key: page.get(key)
                    for key in ("source_url", "title", "reason", "failure_code")
                }
                for page in (mismatch_pages + ambiguous_pages)[:10]
            ],
        }
    return {
        "status": "UNVERIFIED",
        "confidence": 0.0,
        "method": "automatic_search",
    }


def _source_type_for_evidence(row: dict[str, Any]) -> str:
    explicit = str(row.get("source_type") or "").strip().upper()
    if explicit:
        return explicit
    kind = str(row.get("source_kind") or "").casefold()
    host = _host_domain(str(row.get("source_url") or row.get("url") or ""))
    method = str(row.get("method") or row.get("decision_method") or "")
    if "official" in method or kind == "university":
        return "OFFICIAL_UNIVERSITY_PAGE"
    if kind == "lab":
        return "LAB_WEBSITE"
    if kind == "personal":
        return "PERSONAL_WEBSITE"
    if kind == "cv":
        return "CV"
    if kind == "linkedin" or host.endswith("linkedin.com"):
        return "LINKEDIN_SNIPPET"
    if host.startswith("scholar.google."):
        return "GOOGLE_SCHOLAR"
    return "SEARCH_RESULT"


def _lookup_status_for_evidence(row: dict[str, Any]) -> str:
    failure = str(row.get("failure_code") or "").upper()
    http_status = int(row.get("http_status") or 0)
    if failure == "SOURCE_BLOCKED" or http_status in {401, 403, 429}:
        return "BLOCKED"
    if http_status == 404:
        return "DELETED"
    if failure in {"SOURCE_UNAVAILABLE", "NO_READABLE_CONTENT"} or http_status >= 400:
        return "HTTP_ERROR"
    return str(row.get("lookup_status") or "FOUND").upper()


def _identity_evidence_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist the useful attribution trail, not every raw search response."""
    final_status = str(result.get("status") or "UNVERIFIED")
    allowed_statuses = {
        "UNVERIFIED", "VERIFIED", "NOT_FACULTY", "OUT_OF_SCOPE",
        "CONFLICT", "MANUAL_REVIEW",
    }
    if final_status not in allowed_statuses:
        final_status = "UNVERIFIED"
    by_url: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any], *, supports_decision: bool = False) -> None:
        source_url = canonical_source_url(str(row.get("source_url") or row.get("final_url") or row.get("url") or ""))
        if not source_url:
            return
        normalized = dict(row)
        normalized["source_url"] = source_url
        normalized.setdefault("source_domain", _host_domain(source_url))
        normalized.setdefault("verification_status", normalized.get("status") or "UNVERIFIED")
        evidence_status = str(normalized.get("verification_status") or "UNVERIFIED").upper()
        # CLUE, FOUND, BLOCKED, and similar labels describe retrieval or page
        # handling; they are not final identity decisions accepted by the DB.
        normalized["verification_status"] = (
            evidence_status if evidence_status in allowed_statuses else "UNVERIFIED"
        )
        normalized.setdefault("supports_decision", supports_decision)
        normalized.setdefault("source_type", _source_type_for_evidence(normalized))
        normalized.setdefault("lookup_status", _lookup_status_for_evidence(normalized))
        normalized.setdefault("page_title", normalized.get("page_title") or normalized.get("title"))
        inspection = str(normalized.get("inspection") or "").strip()
        normalized.setdefault(
            "inspection_status",
            "RETURNED_NOT_INSPECTED"
            if inspection == "Not inspected in this pass"
            else "INSPECTED" if inspection or normalized.get("status") else "UNKNOWN",
        )
        normalized.setdefault(
            "evidence_excerpt",
            normalized.get("evidence_text")
            or normalized.get("reason")
            or normalized.get("snippet_hint")
            or normalized.get("snippet"),
        )
        normalized.setdefault("extracted_text", normalized.get("name_context") or normalized.get("text_excerpt") or normalized.get("snippet"))
        if not normalized.get("observed_title"):
            role_source = " ".join(
                str(normalized.get(key) or "")
                for key in ("page_title", "title", "snippet", "evidence_text", "extracted_text")
            )
            role_match = (
                CURRENT_NONFACULTY_ROLE_PATTERN.search(role_source)
                or NON_FACULTY_PATTERN.search(role_source)
                or FACULTY_TITLE_PATTERN.search(role_source)
            )
            if role_match:
                normalized["observed_title"] = " ".join(role_match.group(0).split())
        normalized.setdefault(
            "role_category", _role_category(normalized.get("observed_title"))
        )
        existing = by_url.get(source_url)
        if existing is None:
            by_url[source_url] = normalized
            return
        for key, value in normalized.items():
            if value is not None and value != "" and value != [] and value != {} and not existing.get(key):
                existing[key] = value
        existing["supports_decision"] = bool(existing.get("supports_decision") or supports_decision)

    add(result, supports_decision=True)
    for alternative in list(result.get("alternative_evidence") or []):
        add(alternative)
    audit = result.get("search_audit") or {}
    for page in list(audit.get("pages") or []):
        add(page)
    for search_result in list(audit.get("results") or []):
        add(search_result)
    rows = list(by_url.values())
    for row in rows:
        if row.get("supports_decision"):
            row["verification_status"] = final_status
        elif str(row.get("verification_status") or "").upper() not in allowed_statuses:
            row["verification_status"] = "UNVERIFIED"
        row.setdefault("currentness", "CURRENT" if row.get("role_category") not in {None, "", "UNKNOWN"} else "UNKNOWN")
        row.setdefault("scope_status", result.get("scope_status") or "UNKNOWN")
    return rows


def _save_result(candidate: dict[str, Any], result: dict[str, Any]) -> None:
    result = dict(result)
    professor_id = int(candidate["id"])
    status = str(result.get("status") or "UNVERIFIED")
    existing_source = str(candidate.get('faculty_source_url') or '')
    new_source = str(result.get('source_url') or '')
    same_saved_institution = institutions_equivalent(
        str(candidate.get('institution_name') or ''),
        str(result.get('institution_name') or candidate.get('institution_name') or ''),
    )
    if (
        status in {'VERIFIED', 'OUT_OF_SCOPE'}
        and same_saved_institution
        and _faculty_source_quality(existing_source) > _faculty_source_quality(new_source)
    ):
        result['source_url'] = existing_source
        saved_record = record_for_host(_host_domain(existing_source))
        if saved_record:
            result['source_domain'] = saved_record[1]
    if status == "SEARCH_UNAVAILABLE":
        raise ValueError("A provider outage cannot be saved as an identity decision.")
    method = str(result.get("method") or (
        "official_directory" if result.get("source_url") else "automatic_search"
    ))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE professors SET identity_retry_at = NULL, identity_retry_reason = NULL, identity_search_pending = FALSE, identity_search_audit = %s::jsonb WHERE id = %s",
                (json.dumps(result.get('search_audit') or {}), professor_id),
            )
            institution_name = canonical_institution(
                str(result.get("institution_name") or candidate["institution_name"])
            )
            country_code = (
                str(result.get("country_code") or "").upper()
                or _country_code_for_domain(str(result.get("source_domain") or ""))
            )
            institution_id = candidate.get("institution_id")
            if status in {"VERIFIED", "OUT_OF_SCOPE"} and institution_name:
                cursor.execute(
                    """
                    INSERT INTO institutions (name, country_code)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO UPDATE
                    SET country_code = COALESCE(
                        EXCLUDED.country_code, institutions.country_code
                    )
                    RETURNING id
                    """,
                    (institution_name, country_code),
                )
                institution_id = cursor.fetchone()["id"]
                source_domain = str(result.get("source_domain") or "").strip()
                if source_domain:
                    cursor.execute(
                        """
                        UPDATE institutions
                        SET primary_domain = COALESCE(primary_domain, %s)
                        WHERE id = %s
                        """,
                        (source_domain, institution_id),
                    )
            cursor.execute(
                """
                UPDATE professors
                SET faculty_status = CASE
                        WHEN faculty_status = 'VERIFIED' AND %s = 'UNVERIFIED'
                            THEN 'MANUAL_REVIEW'
                        ELSE %s
                    END,
                    faculty_title = CASE
                        WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE faculty_title
                    END,
                    faculty_source_url = CASE
                        WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE faculty_source_url
                    END,
                    faculty_verification_method = %s,
                    faculty_verification_version = %s,
                    faculty_confidence = %s,
                    faculty_checked_at = NOW(),
                    faculty_verified_at = CASE WHEN %s = 'VERIFIED' THEN NOW() ELSE NULL END,
                    next_identity_check_at = NOW() + INTERVAL '30 days',
                    official_institution_domain = CASE
                        WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s
                        WHEN faculty_verification_method = 'official_directory' THEN NULL
                        ELSE official_institution_domain
                    END,
                    appointment_year = COALESCE(%s, appointment_year),
                    appointment_start_date = COALESCE(%s::date, appointment_start_date),
                    appointment_date_precision = COALESCE(%s, appointment_date_precision),
                    career_stage = COALESCE(%s, career_stage),
                    previous_institutions = CASE
                        WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE')
                             AND institution_name <> %s
                             AND NOT (institution_name = ANY(previous_institutions))
                            THEN array_append(previous_institutions, institution_name)
                        ELSE previous_institutions
                    END,
                    institution_id = CASE WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s ELSE institution_id END,
                    institution_name = CASE WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s ELSE institution_name END,
                    homepage_url = CASE WHEN %s IN ('VERIFIED', 'OUT_OF_SCOPE') THEN %s ELSE homepage_url END,
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
                    status, result.get("source_domain"),
                    result.get("appointment_year"),
                    result.get("appointment_start_date"),
                    result.get("appointment_date_precision"),
                    result.get("career_stage"),
                    status, institution_name,
                    status, institution_id,
                    status, institution_name,
                    status, result.get("source_url"),
                    professor_id,
                ),
            )
            evidence_results = _identity_evidence_rows(result)
            saved_urls: set[str] = set()
            for evidence_result in evidence_results:
                source_url = str(evidence_result.get("source_url") or "").strip()
                if not source_url or source_url in saved_urls:
                    continue
                saved_urls.add(source_url)
                evidence_status = str(
                    evidence_result.get("verification_status")
                    or evidence_result.get("status")
                    or status
                )
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
                        source_type, page_title, role_category, observed_employer,
                        currentness, lookup_status, evidence_excerpt,
                        extracted_text, http_status, supports_decision,
                        scope_status, checked_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
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
                        source_type = EXCLUDED.source_type,
                        page_title = EXCLUDED.page_title,
                        role_category = EXCLUDED.role_category,
                        observed_employer = EXCLUDED.observed_employer,
                        currentness = EXCLUDED.currentness,
                        lookup_status = EXCLUDED.lookup_status,
                        evidence_excerpt = EXCLUDED.evidence_excerpt,
                        extracted_text = EXCLUDED.extracted_text,
                        http_status = EXCLUDED.http_status,
                        supports_decision = EXCLUDED.supports_decision,
                        scope_status = EXCLUDED.scope_status,
                        checked_at = NOW()
                    """,
                    (
                        professor_id,
                        source_url,
                        evidence_result.get("source_domain") or "",
                        evidence_result.get("observed_title") or evidence_result.get("role") or (
                            evidence_result.get("title") if evidence_result.get("supports_decision") else None
                        ),
                        evidence_institution,
                        evidence_result.get("evidence_text") or evidence_result.get("evidence_excerpt"),
                        evidence_status,
                        float(evidence_result.get("confidence") or 0),
                        evidence_method,
                        evidence_result.get("model_name"),
                        evidence_result.get("prompt_version"),
                        evidence_result.get("source_type") or "UNKNOWN",
                        evidence_result.get("page_title"),
                        evidence_result.get("role_category") or _role_category(
                            evidence_result.get("observed_title") or evidence_result.get("role") or (
                                evidence_result.get("title") if evidence_result.get("supports_decision") else None
                            )
                        ),
                        evidence_result.get("observed_employer"),
                        evidence_result.get("currentness") or "UNKNOWN",
                        evidence_result.get("lookup_status") or "FOUND",
                        evidence_result.get("evidence_excerpt"),
                        str(evidence_result.get("extracted_text") or "")[:4000] or None,
                        evidence_result.get("http_status"),
                        bool(evidence_result.get("supports_decision")),
                        evidence_result.get("scope_status") or "UNKNOWN",
                    ),
                )


def verify_faculty_candidates(
    professor_ids: list[int],
    max_candidates: int | None = None,
    *, direct_only: bool = False, retry_after_seconds: int = 60,
    cache_max_age_days: int = 30,
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
                SELECT professor.id, professor.openalex_id, professor.name,
                       professor.institution_id, professor.institution_name,
                       professor.homepage_url, professor.faculty_source_url,
                       professor.identity_search_audit,
                       professor.orcid_id, professor.identity_retry_at,
                       professor.research_domain,
                       professor.faculty_status, professor.faculty_checked_at,
                       professor.next_identity_check_at,
                       professor.faculty_verification_method,
                       professor.faculty_verification_version,
                       institution.primary_domain AS institution_domain
                FROM professors professor
                LEFT JOIN institutions institution
                  ON institution.id = professor.institution_id
                WHERE professor.id = ANY(%s)
                """,
                (ordered_ids,),
            )
            by_id = {int(row["id"]): row for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT ranked.professor_id, ranked.paper_id, ranked.openalex_id,
                       ranked.title, ranked.publication_year, ranked.doi,
                       ranked.pdf_url, ranked.author_position,
                       ranked.raw_affiliation_text, ranked.affiliation_status,
                       ranked.affiliation_text, ranked.affiliation_source_url,
                       ranked.affiliation_institution, ranked.affiliation_email,
                       ranked.affiliation_checked_at, ranked.affiliation_version, ranked.matched_query
                FROM (
                    SELECT pp.professor_id, paper.id AS paper_id,
                           paper.openalex_id, paper.title, paper.publication_year,
                           paper.doi, paper.pdf_url, pp.author_position,
                           pp.raw_affiliation_text, pp.affiliation_status,
                           pp.affiliation_text, pp.affiliation_source_url,
                           pp.affiliation_institution, pp.affiliation_email,
                           pp.affiliation_checked_at, pp.affiliation_version,
                           (SELECT evidence.matched_query FROM radar_topic_professor_papers evidence
                            WHERE evidence.professor_id = pp.professor_id AND evidence.paper_id = paper.id
                              AND evidence.is_current_match = TRUE
                            ORDER BY evidence.relevance_score DESC LIMIT 1) AS matched_query,
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
    deferred_delays: list[int] = []
    now = datetime.now(timezone.utc)
    for professor_id in ordered_ids:
        candidate = by_id.get(professor_id)
        if not candidate:
            continue
        manual_decision = candidate.get("faculty_verification_method") == "manual_review"
        version_current = (
            int(candidate.get("faculty_verification_version") or 0)
            >= FACULTY_VERIFICATION_VERSION
        )
        if manual_decision:
            if candidate.get("faculty_status") == "VERIFIED":
                verified_ids.append(professor_id)
            continue
        if version_current and recently_checked(
            candidate, now, max_age_days=cache_max_age_days
        ):
            if candidate.get("faculty_status") == "VERIFIED":
                verified_ids.append(professor_id)
            continue
        if candidate.get("identity_retry_at") and candidate["identity_retry_at"] > now:
            deferred_delays.append(max(1, int((candidate["identity_retry_at"] - now).total_seconds())))
            continue
        candidate["_direct_only"] = direct_only
        candidate["_search_retry_seconds"] = retry_after_seconds
        pending.append(candidate)

    workers = setting_int("FACULTY_VERIFY_MAX_WORKERS", 3, 1, 6)
    checked = 0
    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as executor:
        futures = {executor.submit(verify_faculty_candidate, candidate): candidate for candidate in pending}
        for future in as_completed(futures):
            candidate = futures[future]
            checked += 1
            try:
                result = future.result()
            except SearchUnavailable as error:
                # This schedules another attempt, without saving an identity
                # verdict or changing faculty_checked_at / verification version.
                with get_db_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE professors SET identity_retry_at = NOW() + (%s * INTERVAL '1 second'),
                                 identity_retry_reason = %s, identity_search_pending = TRUE,
                                 identity_search_audit = %s::jsonb WHERE id = %s""",
                              (error.retry_after_seconds, str(error)[:1000], json.dumps(getattr(error, 'search_audit', {})), candidate["id"]),
                        )
                deferred_delays.append(error.retry_after_seconds)
                continue
            except Exception as error:
                # A network outage or unexpected verifier error is not evidence
                # about this person's identity. Leave the professor unchanged
                # so the durable job can retry later.
                failures.append(error)
                continue
            _save_result(candidate, result)
            if result.get("status") == "VERIFIED":
                verified_ids.append(int(candidate["id"]))

    if failures:
        retry_after = max(
            int(getattr(error, "retry_after_seconds", 60))
            for error in failures
        )
        if any(isinstance(error, SearchUnavailable) for error in failures):
            raise SearchUnavailable(
                f"Search providers were unavailable for {len(failures)} "
                "faculty candidate(s); no identity decisions were saved for them.",
                retry_after_seconds=retry_after,
            )
        raise RuntimeError(
            f"Faculty verification failed for {len(failures)} candidate(s); "
            "their identity decisions were left unchanged."
        )

    verified_set = set(verified_ids)
    verified_ids = [value for value in ordered_ids if value in verified_set]
    return {
        "verified_ids": verified_ids,
        "checked": checked,
        "evaluated": checked,
        "verified": len(verified_ids),
        "deferred": len(deferred_delays),
        "retry_after_seconds": min(deferred_delays) if deferred_delays else 0,
    }


def get_cached_faculty_decisions(
    professor_ids: list[int], *, max_age_days: int = 30
) -> dict[str, list[int]]:
    """Return current positive and negative decisions without launching web checks."""
    ordered_ids = list(dict.fromkeys(int(value) for value in professor_ids))
    if not ordered_ids:
        return {"verified_ids": [], "decided_ids": []}
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, faculty_status, faculty_verification_version, faculty_verification_method FROM professors
                WHERE id = ANY(%s)
                  AND (
                      faculty_verification_method = 'manual_review'
                      OR (
                          faculty_verification_version >= %s
                          AND faculty_checked_at >= NOW() - (%s * INTERVAL '1 day')
                      )
                  )
                """,
                (ordered_ids, FACULTY_VERIFICATION_VERSION, max(1, int(max_age_days))),
            )
            rows = list(cursor.fetchall())
    decided = {int(row["id"]) for row in rows}
    verified = {
        int(row["id"]) for row in rows if row["faculty_status"] == "VERIFIED" and (
            row.get("faculty_verification_method") == "manual_review"
            or int(row.get("faculty_verification_version") or 0) >= FACULTY_VERIFICATION_VERSION
        )
    }
    return {
        "verified_ids": [value for value in ordered_ids if value in verified],
        "decided_ids": [value for value in ordered_ids if value in decided],
    }


def get_cached_verified_faculty_ids(professor_ids: list[int]) -> list[int]:
    """Compatibility wrapper for callers that only need positive decisions."""
    return get_cached_faculty_decisions(professor_ids)["verified_ids"]
