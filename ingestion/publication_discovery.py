"""Evidence-first publication discovery for already verified roster faculty."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.institution_domains import institutions_equivalent
from ingestion.faculty_roster import eligible_research_group_leader
from ingestion.name_normalization import fold_name_text, name_tokens
from ingestion.ollama_evidence import (
    review_publication_identity,
    review_research_interest_summary,
)
from ingestion.websearch import SearchUnavailable, search_web

PUBLICATION_HEADING = re.compile(r"\b(?:selected )?(?:publications?|papers?|articles?|bibliography|research outputs?)\b", re.I)
RESEARCH_LINK = re.compile(r"\b(?:personal|academic|research|lab(?:oratory)?|group|publications?|website|homepage)\b", re.I)
YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
FACULTY_TITLE = re.compile(
    r"\b(?:assistant|associate|full|distinguished|endowed|research|clinical|"
    r"teaching|practice)?\s*professor\b",
    re.I,
)
NON_PROFILE_PATH = re.compile(
    r"/(?:news|events?|awards?|honors?|alumni|archive|stories?|press|jobs?)(?:/|$)",
    re.I,
)
PUBLICATION_DISCOVERY_VERSION = 10
SCHOLAR_SUFFIXES = frozenset({'com','co.uk','com.tr','de','fr','ca','com.au','co.in',
    'co.jp','com.br','es','it','nl','ch','se','no','dk','fi','at','be','pl','pt',
    'co.nz','co.za','com.mx','com.sg','com.hk','com.tw','co.kr','co.id'})


def _is_google_scholar_host(host: str) -> bool:
    return str(host).casefold() in {'scholar.google.' + suffix for suffix in SCHOLAR_SUFFIXES}


def _is_scholar_profile_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme in {'http','https'} and _is_google_scholar_host(parsed.hostname or '')
            and parsed.path.rstrip('/') == '/citations' and bool(parse_qs(parsed.query).get('user')))
RESEARCH_INTEREST_HEADING = re.compile(
    r"\b(?:research|scholarly|academic)?\s*(?:interests?|areas?|expertise|"
    r"specialt(?:y|ies)|research focus|areas? of interest|areas? of expertise)\b",
    re.I,
)
BIOGRAPHY_HEADING = re.compile(
    r"\b(?:biography|bio|about(?: me)?|profile|professional background)\b", re.I
)
BLOCK_PAGE_TITLE = re.compile(
    r"^(?:access denied|forbidden|request rejected|just a moment|"
    r"attention required|automated requests?)\b", re.I
)


@dataclass(frozen=True)
class Publication:
    title: str
    year: int | None
    doi: str
    source_url: str
    source_type: str
    evidence: str
    authors: str = ""
    venue: str = ""


def _name_parts(value: str) -> list[str]:
    folded = fold_name_text(value).casefold()
    if "," in folded:
        family, given = folded.split(",", 1)
        folded = f"{given} {family}"
    return re.findall(r"[^\W_]+", folded, re.UNICODE)


def same_person_name(left: str, right: str) -> bool:
    """Compare common name variants; this signal can never merge by itself."""
    a, b = _name_parts(left), _name_parts(right)
    if len(a) < 2 or len(b) < 2:
        return False
    def compatible(x: list[str], y: list[str]) -> bool:
        return (x[-1] == y[-1]
                and (x[0] == y[0] or (min(len(x[0]), len(y[0])) == 1 and x[0][0] == y[0][0]))
                and (not x[1:-1] or not y[1:-1]
                     or all(p[0] == q[0] for p, q in zip(x[1:-1], y[1:-1]))))
    return compatible(a, b) or compatible(a, list(reversed(b)))


def _title_key(value: str) -> str:
    return " ".join(word for word in re.findall(r"[a-z0-9]+", value.casefold())
                    if word not in {"a", "an", "the"})


def paper_key(paper: Publication) -> str:
    identity = f"doi:{paper.doi.casefold()}" if paper.doi else f"title:{_title_key(paper.title)}|year:{paper.year or ''}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _main(soup: BeautifulSoup) -> Any:
    root = soup.select_one("main,[role=main],article,#main-content,.main-content") or soup.body or soup
    for node in root.select("script,style,noscript,header,nav,footer,aside,form,.menu,.navigation,.footer,.comments-area,.sharedaddy"):
        node.decompose()
    return root


def _is_block_page(html: str) -> bool:
    """Recognize HTTP-200 access challenges without scanning normal prose."""
    soup = BeautifulSoup(html, "html.parser")
    labels = []
    if soup.title:
        labels.append(" ".join(soup.title.get_text(" ", strip=True).split()))
    heading = soup.find("h1")
    if heading:
        labels.append(" ".join(heading.get_text(" ", strip=True).split()))
    return any(BLOCK_PAGE_TITLE.search(value) for value in labels)


def _host_root(value: str) -> str:
    host = (urlparse(value).hostname or value).casefold().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _clean_person_heading(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:dr|prof(?:essor)?|ph\.?d|m\.?d)\.?\b", " ", value, flags=re.I
    )
    return " ".join(cleaned.replace("–", " ").replace("—", " ").split())


def _heading_contains_person_name(expected: str, observed: str) -> bool:
    """Allow site suffixes while requiring the complete name in one sequence."""
    cleaned = _clean_person_heading(observed)
    if same_person_name(expected, cleaned):
        return True
    expected_tokens = _name_parts(expected)
    observed_tokens = _name_parts(cleaned)
    if len(expected_tokens) < 2:
        return False
    variants = [expected_tokens]
    if "," in fold_name_text(expected):
        variants.append([*expected_tokens[1:], expected_tokens[0]])
    for variant in variants:
        width = len(variant)
        for index in range(len(observed_tokens) - width + 1):
            segment = observed_tokens[index:index + width]
            if all(
                left == right
                or (len(left) == 1 and left[0] == right[0])
                or (len(right) == 1 and right[0] == left[0])
                for left, right in zip(variant, segment)
            ):
                return True
    return False


def alternate_official_profile_matches(
    html: str, url: str, professor: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Verify a same-person official page for an already approved professor.

    The approved roster has already established the faculty role. An alternate
    person page only needs to establish identity on the official domain; it
    must not be rejected merely because its template omits the title.
    """
    official = str(
        professor.get("official_institution_domain")
        or urlparse(str(professor.get("faculty_source_url") or "")).hostname
        or ""
    )
    if not official or _host_root(url) != _host_root(official):
        return False, {"reason": "OUTSIDE_OFFICIAL_DOMAIN"}
    if NON_PROFILE_PATH.search(urlparse(url).path):
        return False, {"reason": "NON_PROFILE_PATH"}
    soup = BeautifulSoup(html, "html.parser")
    root = _main(soup)
    headings = [
        " ".join(node.get_text(" ", strip=True).split())
        for node in root.find_all(["h1", "h2"], limit=8)
    ]
    title = " ".join(
        (soup.title.get_text(" ", strip=True) if soup.title else "").split()
    )
    matched_heading = next(
        (value for value in [*headings, title]
         if _heading_contains_person_name(str(professor["name"]), value)),
        "",
    )
    intro = " ".join(root.get_text(" ", strip=True).split())[:4000]
    role = FACULTY_TITLE.search(intro)
    if not matched_heading:
        return False, {"reason": "IDENTITY_OR_ROLE_NOT_ESTABLISHED",
                       "matched_heading": matched_heading}
    return True, {
        "matched_heading": matched_heading,
        "role": role.group(0) if role else str(professor.get("faculty_title") or ""),
        "role_source": "ALTERNATE_PAGE" if role else "APPROVED_ROSTER",
        "official_domain": _host_root(official),
    }


def _section_after_heading(heading: Any, *, max_chars: int = 8000) -> str:
    """Return one local section without navigation or neighboring sections."""
    level = int(heading.name[1]) if str(heading.name).startswith("h") else 6
    parts: list[str] = []
    for node in heading.find_all_next():
        if node is heading:
            continue
        if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            node_level = int(node.name[1])
            if node_level <= level:
                break
        if node.name in {"li", "p", "dd"}:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text and (not parts or text != parts[-1]):
                parts.append(text)
        if sum(len(value) for value in parts) >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _interest_labels(section_text: str) -> list[str]:
    """Normalize explicit labels while refusing long prose as a field name."""
    values: list[str] = []
    for line in section_text.splitlines():
        chunks = re.split(r"\s*[;•|]\s*", line)
        if len(chunks) == 1 and 1 <= line.count(",") <= 7:
            chunks = [part.strip() for part in line.split(",")]
        for chunk in chunks:
            value = " ".join(chunk.strip(" .:;-–—").split())
            words = value.split()
            if 2 <= len(value) <= 80 and 1 <= len(words) <= 8:
                values.append(value)
    return list(dict.fromkeys(values))[:12]


def extract_research_interests(html: str) -> tuple[list[str], str]:
    """Extract subject-local explicit research-interest sections."""
    root = _main(BeautifulSoup(html, "html.parser"))
    candidates = list(root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]))
    candidates.extend(
        node for node in root.find_all(["strong", "b"])
        if len(node.get_text(" ", strip=True)) <= 100
    )
    for heading in candidates:
        label = " ".join(heading.get_text(" ", strip=True).split())
        if not RESEARCH_INTEREST_HEADING.fullmatch(label.strip(" :")):
            continue
        section = _section_after_heading(heading)
        interests = _interest_labels(section)
        if interests:
            return interests, section
    return [], ""


def extract_biography_text(html: str) -> str:
    """Return a profile biography section for constrained Qwen summarization."""
    root = _main(BeautifulSoup(html, "html.parser"))
    for heading in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong"]):
        label = " ".join(heading.get_text(" ", strip=True).split())
        if BIOGRAPHY_HEADING.fullmatch(label.strip(" :")):
            section = _section_after_heading(heading, max_chars=16000)
            if len(section) >= 80:
                return section
    # Some official profiles have no Biography heading. Use only substantial
    # introductory paragraphs from the main profile container, never menus.
    paragraphs: list[str] = []
    for node in root.find_all("p"):
        text = " ".join(node.get_text(" ", strip=True).split())
        if 80 <= len(text) <= 3000 and not PUBLICATION_HEADING.search(text[:100]):
            paragraphs.append(text)
        if sum(len(value) for value in paragraphs) >= 12000:
            break
    return "\n".join(paragraphs)[:16000]


def _normalize_interest(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _save_research_interests(
    professor_id: int, interests: list[str], *, method: str,
    source_url: str, source_excerpt: str, confidence: float,
) -> int:
    clean = [(value.strip(), _normalize_interest(value)) for value in interests]
    clean = [(display, normalized) for display, normalized in clean if normalized]
    if not clean:
        return 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM professor_research_interests WHERE professor_id=%s",
                (professor_id,),
            )
            for display, normalized in clean[:12]:
                cursor.execute(
                    """INSERT INTO professor_research_interests
                       (professor_id,display_interest,normalized_interest,
                        evidence_method,source_url,source_excerpt,confidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (professor_id,normalized_interest,source_url)
                       DO UPDATE SET display_interest=EXCLUDED.display_interest,
                         evidence_method=EXCLUDED.evidence_method,
                         source_excerpt=EXCLUDED.source_excerpt,
                         confidence=EXCLUDED.confidence,checked_at=NOW()""",
                    (professor_id, display, normalized, method, source_url,
                     source_excerpt[:12000], confidence),
                )
            cursor.execute("UPDATE radar_topics SET next_refresh_at=NOW(),updated_at=NOW()")
    return len(clean[:12])


def _publication_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Wait through one normal DDGS slot; treat unrelated results as empty."""
    waited = 0
    while True:
        try:
            return search_web(query, max_results=max_results)
        except SearchUnavailable as error:
            message = str(error)
            delay = int(getattr(error, "retry_after_seconds", 0) or 0)
            if "unrelated to the quoted identity" in message:
                return []
            if 0 < delay <= 90 and waited + delay <= 95:
                time.sleep(delay + 0.1)
                waited += delay
                continue
            raise


def _clean_publication_title(title: str) -> str:
    # Strip only explicitly bracketed award badges, never the raw citation.
    return re.sub(r'^\s*\[[^\]]*\baward\b[^\]]*\]\s*', '', title, flags=re.I).strip()


def _author_year_publication(text: str, url: str, source_type: str,
                             subject_name: str) -> Publication | None:
    """Recognize attributable citations even without a Publications heading.

    A subject surname/initial must occur in the author list, not just in the
    title or surrounding prose. Preserve the full citation for auditing.
    """
    parts = _name_parts(subject_name)
    if len(parts) < 2:
        return None
    match = re.fullmatch(r'(.{3,600}?)\s*\(((?:19|20)\d{2})[a-z]?\)\.\s*(.+)', text)
    if not match:
        return None
    authors, year, body = match.groups()
    if not re.match(r'^[^,;\d]{1,80},\s*[A-Za-z]', authors):
        return None
    folded_authors = fold_name_text(authors).casefold()
    subject_author = (rf'(?<!\w){re.escape(parts[-1])},\s*'
                      rf'(?:{re.escape(parts[0])}\b|{re.escape(parts[0][0])}\.(?=\W|$))')
    if not re.search(subject_author, folded_authors):
        return None
    pieces = re.split(r'\.\s+', body, maxsplit=1)
    if len(pieces) != 2:
        return None
    title, venue = pieces
    title = _clean_publication_title(title)
    if len(_title_key(title).split()) < 3:
        return None
    # Journal volume/pages, a chapter's editor container, or a publisher.
    # A year in an education/award sentence is not sufficient.
    if not re.search(r'\b(?:Journal|Review|Proceedings|Press|Routledge|Springer|Wiley)\b|^In\s|\d+\s*\(\d+\)|\d+\s*[,;:]\s*\d+[-–]\d+', venue, re.I):
        return None
    doi = DOI.search(text)
    return Publication(title.strip(), int(year), doi[0].rstrip('.,;)') if doi else '',
                       url, source_type, text, authors.strip(), venue.strip())


def extract_publications(html: str, url: str, source_type: str, subject_name: str = '') -> list[Publication]:
    """Read publication sections and subject-attributed bibliographic records."""
    root = _main(BeautifulSoup(html, "html.parser"))
    entries: list[tuple[str, str]] = []
    consumed: set[int] = set()
    headings = list(root.find_all(["h1", "h2", "h3", "h4", "h5"]))
    headings.extend(
        node for node in root.find_all("p")
        if node.find(["strong", "b"])
        and len(node.get_text(" ", strip=True)) <= 120
        and node.get_text(' ', strip=True).strip(' :') == node.find(['strong','b']).get_text(' ', strip=True).strip(' :')
        and PUBLICATION_HEADING.search(node.get_text(" ", strip=True))
    )
    for heading in headings:
        if not PUBLICATION_HEADING.search(heading.get_text(" ", strip=True)):
            continue
        for node in heading.find_all_next():
            if root not in node.parents:
                break
            if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                break
            if (
                heading.name == "p"
                and node.name == "p"
                and node.find(["strong", "b"])
                and len(node.get_text(" ", strip=True)) <= 160
            ):
                break
            if node.name in {"li", "tr", "article", "cite", "p"}:
                if id(node) in consumed:
                    continue
                nested = node.find_all(['ul', 'ol'], recursive=False) if node.name == 'li' else []
                title_only = ''
                if nested:
                    # A book's indented editor/publisher lines belong to that
                    # book. Do not flatten them into its title or emit them again.
                    metadata = [li.get_text(' ', strip=True) for lst in nested for li in lst.find_all('li')]
                    is_metadata = lambda value: bool(re.search(
                        r'^(?:edited by|published by|ISBN\b|DOI\b)|\b(?:Press|Publishing|Publisher|Gale Group)\b.*\b(?:19|20)\d{2}\b', value, re.I))
                    own = BeautifulSoup(str(node), 'html.parser').find('li')
                    for lst in own.find_all(['ul','ol']):
                        lst.decompose()
                    candidate_title = own.get_text(' ', strip=True).strip(' .')
                    if metadata and all(is_metadata(value) for value in metadata) and len(candidate_title.split()) >= 3:
                        title_only = candidate_title
                        consumed.update(id(child) for child in node.find_all())
                    else:
                        # Group labels such as "Books" aren't publications;
                        # their child entries must be parsed independently.
                        continue
                text = " ".join(node.get_text(" ", strip=True).split()).strip(" •·-–—")
                if 18 <= len(text) <= 1200:
                    entries.append((text, title_only))
            if len(entries) >= 200:
                break
    found: dict[str, Publication] = {}
    for entry, title_only in entries:
        if re.match(r'^(?:(?:19|20)\d{2}\s+)?(?:Ph\.?D\.?|M\.?Sc\.?|B\.?A\.?|M\.?A\.?|Education|Office|Email)\b', entry, re.I):
            continue
        quoted = re.search(r"[\"“]([^\"”]{12,350})[\"”]", entry)
        # Author-year citations can contain a short quoted phrase *within*
        # their title. Do not discard its unquoted subtitle.
        citation = re.search(r'\((?:19|20)\d{2}[a-z]?\)\.\s*(.+)', entry)
        title = title_only or (re.split(r'\.\s+', citation.group(1), maxsplit=1)[0]
                 if citation else quoted.group(1) if quoted else entry).strip()
        title = _clean_publication_title(title)
        if len(_title_key(title).split()) < 3:
            continue
        year = YEAR.search(entry)
        doi = DOI.search(entry)
        paper = Publication(title[:500], int(year.group(1)) if year else None,
                            doi.group(0).rstrip(".,;)") if doi else "", url,
                            source_type, entry)
        found.setdefault(paper_key(paper), paper)
    # Humanities profiles often put full book citations under Achievements,
    # not Publications. Require the subject's author name and publisher/year.
    family = _name_parts(subject_name)[-1] if subject_name else ''
    for record in root.find_all(['p', 'li', 'cite']):
        if record.find(['p','li','cite']):
            continue  # Only leaf records, never a whole containing list.
        text = ' '.join(record.get_text(' ', strip=True).split())
        citation = _author_year_publication(text, url, source_type, subject_name)
        if citation:
            found[paper_key(citation)] = citation
    for paragraph in root.find_all('p'):
        text = ' '.join(paragraph.get_text(' ', strip=True).split())
        citation = re.match(r'^((?:.+?,\s*eds?|[^.]*))\.\s*(.+?)\s*\(([^()]*\bPress\b[^()]*\b((?:19|20)\d{2}))\)\.?$', text)
        if citation and family and fold_name_text(citation[1]).casefold().startswith(family + ','):
            title = re.sub(r'^(?:eds?\.)\s*', '', citation[2]).strip(' .')
            paper = Publication(title, int(citation[4]), '', url, source_type, text)
            found.setdefault(paper_key(paper), paper)
        # An explicitly authored book in the biography: title is italicized,
        # and its own adjacent publisher clause supplies the date, not prose.
        if not re.search(r'\b(?:author|co-editor) of\b', text, re.I):
            continue
        subject_parts = _name_parts(subject_name)
        paragraph_words = set(_name_parts(text))
        if not subject_parts or not (all(part in paragraph_words for part in (subject_parts[0], subject_parts[-1])) or re.match(r'^(?:Hello!\s*)?I am\b', text)):
            continue
        for emphasis in paragraph.find_all(['em','i']):
            title = emphasis.get_text(' ', strip=True).strip()
            tail = text.split(title, 1)[-1] if title else ''
            publication = re.match(r'\s*\(([^()]*\bPress\b[^()]*?)(?:,\s*((?:19|20)\d{2}))?\)', tail)
            if publication and len(title.split()) >= 3:
                year = YEAR.search(publication[0])
                if not year:
                    continue
                paper = Publication(title, int(year[1]) if year else None, '', url, source_type, text)
                found.setdefault(paper_key(paper), paper)
    return list(found.values())


def linked_directory_person(html: str, url: str, name: str) -> str:
    """Resolve View/Profile links only inside the named person's row or card."""
    root = _main(BeautifulSoup(html, 'html.parser'))
    found = set()
    for row in root.select('tr, article, li, .faculty-card, .person, .directory-person'):
        cells = row.find_all(['td', 'th'], recursive=False)
        labels = ([" ".join(cell.get_text(' ', strip=True) for cell in cells[:2])]
                  if cells else [])
        labels += [n.get_text(' ', strip=True) for n in row.find_all(['h2','h3','h4','a'])]
        if not any(same_person_name(name, _clean_person_heading(label)) for label in labels):
            continue
        for anchor in row.find_all('a', href=True):
            label = anchor.get_text(' ', strip=True)
            if (same_person_name(name, _clean_person_heading(label))
                or re.fullmatch(r'(?:view|profile|details|view profile|view details)', label, re.I)):
                target = urljoin(url, anchor['href'])
                if urlparse(target).scheme in {'http','https'} and _host_root(target)==_host_root(url) and target != url:
                    found.add(target)
    return next(iter(found)) if len(found)==1 else ''


def _is_shared_person_directory(html: str) -> bool:
    """Multiple person-detail rows must never be read as one person's bio."""
    root = _main(BeautifulSoup(html, 'html.parser'))
    rows = root.select('tr, .faculty-card, .person, .directory-person')
    records = [row for row in rows if any(
        re.fullmatch(r'(?:view|profile|details|view profile|view details)',
                     a.get_text(' ', strip=True), re.I)
        for a in row.find_all('a', href=True))]
    return len(records) > 1


def linked_research_pages(html: str, profile_url: str, subject_name: str = '') -> list[str]:
    root = _main(BeautifulSoup(html, "html.parser"))
    urls = []
    for anchor in root.find_all("a", href=True):
        url = urljoin(profile_url, str(anchor["href"]))
        host = (urlparse(url).hostname or '').removeprefix('www.').casefold()
        label = anchor.get_text(' ', strip=True).casefold().strip().rstrip('/')
        name_parts = _name_parts(subject_name)
        named_homepage = (len(name_parts) >= 2 and all(p in host for p in (name_parts[0],name_parts[-1]))
                          and label.removeprefix('https://').removeprefix('http://').removeprefix('www.') == host)
        if not RESEARCH_LINK.search(label) and not named_homepage:
            continue
        if urlparse(url).scheme in {"http", "https"} and "scholar.google." not in url.casefold():
            urls.append(url)
    return list(dict.fromkeys(urls))[:5]


def personal_site_sections(html: str, page_url: str) -> list[str]:
    """One bounded hop within an already official-linked personal site.

    Navigation is useful for discovery, but never used as evidence text.
    """
    soup = BeautifulSoup(html, 'html.parser')
    urls = []
    for a in soup.find_all('a', href=True):
        if not re.fullmatch(r'(?:about(?: me)?|research|publications?|books?|selected publications)', a.get_text(' ',strip=True), re.I):
            continue
        target = urljoin(page_url, a['href'])
        parsed = urlparse(target)
        if parsed.scheme in {'http','https'} and parsed.hostname == urlparse(page_url).hostname and not parsed.query and not parsed.fragment and target.rstrip('/') != page_url.rstrip('/'):
            urls.append(target)
    return list(dict.fromkeys(urls))[:4]


def linked_scholar_profiles(html: str, profile_url: str) -> list[str]:
    """Read person-specific Scholar profiles linked by the official profile.

    Scholar links are identity candidates, not ordinary research pages.  Match
    the destination URL rather than the anchor label because faculty templates
    often render Scholar as an icon with no visible text.
    """
    root = _main(BeautifulSoup(html, "html.parser"))
    urls: list[str] = []
    for anchor in root.find_all("a", href=True):
        url = urljoin(profile_url, str(anchor["href"]))
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if (
            _is_google_scholar_host(host)
            and parsed.path.rstrip("/") == "/citations"
            and parse_qs(parsed.query).get("user")
        ):
            urls.append(url)
    return list(dict.fromkeys(urls))[:5]


def _scholar_profile_key(url: str) -> str:
    """Identify a Scholar profile across harmless query/redirect variations."""
    parsed = urlparse(url)
    return (parse_qs(parsed.query).get("user") or [""])[0]


def _dedupe_scholar_profiles(urls: list[str]) -> list[str]:
    """Keep the first URL for each Scholar user ID (official links go first)."""
    found: dict[str, str] = {}
    for url in urls:
        key = _scholar_profile_key(url) or url
        found.setdefault(key, url)
    return list(found.values())


def parse_scholar_profile(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    details = [" ".join(n.get_text(" ", strip=True).split()) for n in soup.select(".gsc_prf_il")]
    homepage = ""
    for node in soup.select(".gsc_prf_il"):
        link = node.find("a", href=True)
        if link:
            homepage = urljoin(url, str(link["href"]))
    papers = []
    for row in soup.select("tr.gsc_a_tr"):
        title_node, year_node = row.select_one(".gsc_a_at"), row.select_one(".gsc_a_y")
        if not title_node:
            continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        gray = [" ".join(n.get_text(" ", strip=True).split()) for n in row.select(".gs_gray")]
        year = YEAR.search(year_node.get_text(" ", strip=True) if year_node else "")
        papers.append(Publication(title, int(year.group(1)) if year else None, "", url,
                                  "GOOGLE_SCHOLAR", " | ".join([title, *gray]),
                                  gray[0] if gray else "", gray[1] if len(gray) > 1 else ""))
    name_node = soup.select_one("#gsc_prf_in")
    return {"name": name_node.get_text(" ", strip=True) if name_node else "",
            "affiliation": details[0] if details else "",
            "verified_email": next((v for v in details if "verified email" in v.casefold()), ""),
            "homepage": homepage,
            "research_interests": [n.get_text(" ", strip=True) for n in soup.select(".gsc_prf_inta")],
            "papers": papers}


def scholar_identity_decision(
    professor: dict[str, Any], scholar: dict[str, Any], known_urls: list[str],
    *, scholar_url: str = "", official_scholar_urls: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Require name plus one independent identity signal."""
    if not same_person_name(str(professor["name"]), str(scholar.get("name") or "")):
        return "PROFILE_NAME_CONFLICT", []
    reasons = ["compatible_name"]
    if institutions_equivalent(str(professor["institution_name"]), str(scholar.get("affiliation") or "")):
        reasons.append("compatible_affiliation")
    domain = str(professor.get("official_institution_domain") or "").casefold().removeprefix("www.")
    email_match = re.search(r'Verified email at\s+([a-z0-9.-]+)', str(scholar.get('verified_email') or ''), re.I)
    if domain and email_match and email_match[1].casefold().rstrip('.') == domain:
        reasons.append("verified_email_domain")
    homepage_host = (urlparse(str(scholar.get("homepage") or "")).hostname or "").casefold().removeprefix("www.")
    known_hosts = {(urlparse(value).hostname or "").casefold().removeprefix("www.") for value in known_urls}
    homepage_path = urlparse(str(scholar.get('homepage') or '')).path.rstrip('/')
    if homepage_host and homepage_path and any(
        urlparse(value).hostname == urlparse(str(scholar.get('homepage') or '')).hostname
        and urlparse(value).path.rstrip('/') == homepage_path for value in known_urls):
        reasons.append("known_homepage")
    official_keys = {
        _scholar_profile_key(url) for url in (official_scholar_urls or set())
    }
    if scholar_url and _scholar_profile_key(scholar_url) in official_keys:
        reasons.append("linked_from_official_profile")
    return ("VERIFIED", reasons) if len(reasons) >= 2 else ("REVIEW_REQUIRED", reasons)


def _record_source(professor_id: int, source_type: str, url: str, status: str, evidence: dict[str, Any]) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO professor_publication_sources
                (professor_id,source_type,source_url,identity_status,evidence,checked_at)
                VALUES (%s,%s,%s,%s,%s::jsonb,NOW())
                ON CONFLICT (professor_id,source_url) DO UPDATE SET
                source_type=EXCLUDED.source_type,identity_status=EXCLUDED.identity_status,
                evidence=professor_publication_sources.evidence || EXCLUDED.evidence,checked_at=NOW()""",
                (professor_id, source_type, url, status, json.dumps(evidence)))


def _save(professor_id: int, papers: list[Publication], progress: Callable[[str, int, int], None] | None) -> int:
    imported = 0
    paper_ids: list[int] = []
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for index, paper in enumerate(papers, 1):
                if progress:
                    progress(paper.title, index, len(papers))
                cursor.execute("""INSERT INTO papers
                    (source_key,title,publication_year,venue,citation_count,doi,source_type,
                     source_url,source_evidence,raw_citation,metadata_status)
                    VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_key) WHERE source_key IS NOT NULL
                    DO UPDATE SET title=EXCLUDED.title,
                    publication_year=COALESCE(EXCLUDED.publication_year,papers.publication_year),
                    venue=COALESCE(NULLIF(EXCLUDED.venue,''),papers.venue),
                    doi=COALESCE(EXCLUDED.doi,papers.doi),
                    source_type=CASE WHEN EXCLUDED.source_type='GOOGLE_SCHOLAR' AND papers.source_type<>'GOOGLE_SCHOLAR' THEN papers.source_type ELSE EXCLUDED.source_type END,
                    source_url=CASE WHEN EXCLUDED.source_type='GOOGLE_SCHOLAR' AND papers.source_type<>'GOOGLE_SCHOLAR' THEN papers.source_url ELSE EXCLUDED.source_url END,
                    source_evidence=CASE WHEN EXCLUDED.source_type='GOOGLE_SCHOLAR' AND papers.source_type<>'GOOGLE_SCHOLAR' THEN papers.source_evidence ELSE EXCLUDED.source_evidence END,
                    raw_citation=CASE WHEN EXCLUDED.source_type='GOOGLE_SCHOLAR' AND papers.source_type<>'GOOGLE_SCHOLAR' THEN papers.raw_citation ELSE COALESCE(NULLIF(EXCLUDED.raw_citation,''),papers.raw_citation) END,
                    metadata_status=CASE WHEN EXCLUDED.doi IS NOT NULL THEN 'DOI_IDENTIFIED'
                                         ELSE papers.metadata_status END RETURNING id""",
                    (paper_key(paper), paper.title, paper.year, paper.venue, paper.doi or None,
                     paper.source_type, paper.source_url, paper.evidence[:2000],
                     paper.evidence[:4000], "DOI_IDENTIFIED" if paper.doi else "NEEDS_RESOLUTION"))
                paper_id = int(cursor.fetchone()["id"])
                paper_ids.append(paper_id)
                cursor.execute("""INSERT INTO professor_papers (professor_id,paper_id,affiliation_status,affiliation_version)
                    VALUES (%s,%s,'NOT_CHECKED',2) ON CONFLICT DO NOTHING""", (professor_id, paper_id))
                imported += int(cursor.rowcount > 0)
            if papers:
                cursor.execute("UPDATE radar_topics SET next_refresh_at=NOW(),updated_at=NOW()")
    if paper_ids:
        # Queue metadata/category work after the import transaction commits.
        from radar_store import enqueue_radar_job
        for paper_id in dict.fromkeys(paper_ids):
            enqueue_radar_job(
                "ENRICH_CLASSIFY_PAPER", paper_id=paper_id,
                priority=45, max_attempts=3,
            )
    return imported


def _status(professor_id: int, value: str) -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE professors SET publication_status=CASE
                     WHEN %s IN ('NO_PUBLICATIONS_FOUND','REVIEW_REQUIRED','SCHOLAR_REVIEW_QUEUED','SOURCE_UNAVAILABLE')
                       AND EXISTS (SELECT 1 FROM professor_papers WHERE professor_id=professors.id)
                     THEN CASE WHEN publication_status='SCHOLAR_VERIFIED'
                               THEN 'SCHOLAR_VERIFIED' ELSE 'OFFICIAL_PUBLICATIONS_FOUND' END
                     ELSE %s END,
                     publication_checked_at=NOW(),publication_discovery_version=%s,
                     updated_at=NOW() WHERE id=%s""",
                (value, value, PUBLICATION_DISCOVERY_VERSION, professor_id),
            )


def _queue_linked_scholar_review(professor_id: int, urls: list[str],
                                 steps: list[dict[str, Any]], *,
                                 discovered_by: str = 'OFFICIAL_PROFILE_LINK') -> None:
    """Supplement existing papers with new official-linked Scholar evidence.

    Completed decisions are not silently retried on every publication crawl.
    Active work is deduplicated by the durable worker queue.
    """
    if not urls:
        return
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_url,identity_status,checked_at FROM professor_publication_sources WHERE professor_id=%s AND source_type='GOOGLE_SCHOLAR' ORDER BY checked_at DESC", (professor_id,))
            previous = {}
            for row in cursor.fetchall():
                key = _scholar_profile_key(row['source_url'])
                # A failed regional URL must not erase a verified decision
                # for the same Scholar user ID.
                if key not in previous or (row['identity_status']=='VERIFIED' and previous[key]['identity_status']!='VERIFIED'):
                    previous[key] = row
    pending = []
    for url in _dedupe_scholar_profiles(urls):
        saved = previous.get(_scholar_profile_key(url), {})
        status = saved.get("identity_status")
        checked_at = saved.get("checked_at")
        now = datetime.now(timezone.utc)

        refresh_due = (
            status == "VERIFIED"
            and checked_at
            and checked_at < now - timedelta(days=30)
        )

        retry_due = (
            status == "SOURCE_UNAVAILABLE"
            and (
                checked_at is None
                or checked_at < now - timedelta(hours=6)
            )
        )

        if (
            status
            and status not in {"QWEN_QUEUED", "STAFF_CANDIDATE"}
            and not refresh_due
            and not retry_due
        ):
            steps.append({
                "step": "QWEN_SCHOLAR_REVIEW",
                "status": status,
                "source_url": url,
                "reason": (
                    "Saved decision retained; retry is not due yet."
                    if status == "SOURCE_UNAVAILABLE"
                    else
                    "Saved decision retained; explicit retry or scheduled refresh required."
                ),
            })
            continue
        _record_source(professor_id,'GOOGLE_SCHOLAR',url,'QWEN_QUEUED',
                       {'discovered_by':discovered_by,'verified':False})
        pending.append(url)
    if pending:
        from radar_store import enqueue_radar_job
        job = enqueue_radar_job(
            "QWEN_REVIEW_PUBLICATION",
            professor_id=professor_id,
            priority=70,
            max_attempts=3,
        )

        steps.append({
            "step": "QWEN_SCHOLAR_REVIEW",
            "status": "QUEUED",
            "job_id": job["id"],
            "reason": "Supplemental Scholar review; existing publications retained.",
            "candidate_count": len(pending),
        })

def _store_interest_fallback(
    professor_id: int, professor: dict[str, Any],
    explicit: list[tuple[list[str], str, str]],
    biography_text: str, biography_url: str,
    steps: list[dict[str, Any]],
    *, queue_on_failure: bool = True,
) -> None:
    """Review explicit evidence or generate clearly labeled AI suggestions."""
    supplied, source_url, source_text = (
        explicit[0] if explicit else ([], biography_url, biography_text)
    )
    speculative = not source_text.strip()
    review = review_research_interest_summary(
        source_record_key=f"{professor_id}:{source_url}",
        institution_id=int(professor["institution_id"]),
        professor_name=str(professor["name"]),
        institution=str(professor["institution_name"]),
        department=str(professor.get("department") or ""),
        biography_text=source_text,
        explicit_interests=supplied,
        speculative=speculative,
    )
    labels = review.data.get("research_interests") if review.status == "VALID" else []
    labels = [str(value).strip() for value in labels or [] if str(value).strip()]
    count = 0
    if labels:
        # Confidence is a product policy, never the model's self-assessment.
        confidence = 0.15 if speculative else 0.45
        count = _save_research_interests(
            professor_id, labels,
            method=("AI_SUGGESTION" if speculative else
                    "QWEN_VALIDATED_SECTION" if supplied else "QWEN_BIO_SUMMARY"),
            source_url=source_url,
            source_excerpt=(source_text if not speculative else
                            "No direct evidence; supplied department: "
                            + str(professor.get("department") or "Not stated")),
            confidence=confidence,
        )
    pending = review.status in {'MODEL_UNAVAILABLE','DISABLED'}
    job_id = None
    if pending and queue_on_failure:
        from radar_store import enqueue_radar_job
        payload = {'professor': {k:professor.get(k) for k in ('name','institution_id','institution_name','department')},
                   'explicit':explicit,'biography_text':biography_text,'biography_url':biography_url}
        job = enqueue_radar_job('QWEN_REVIEW_INTERESTS',professor_id=professor_id,priority=60,max_attempts=3,
                                initial_result={'interest_input':payload},delay_seconds=300)
        job_id = job['id']
    steps.append({
        "step": "RESEARCH_INTERESTS",
        "status": (("AI_SUGGESTED" if speculative else "QWEN_REVIEWED") if count
                   else 'AWAITING_MODEL_REVIEW' if pending else "NO_SUPPORTED_INTERESTS" if review.status == "VALID" else review.status),
        'model_status':review.status,
        'evidence_status':'EXPLICIT_INTERESTS_FOUND' if supplied else 'BIOGRAPHY_FOUND' if source_text.strip() else 'NO_DIRECT_INTEREST_EVIDENCE',
        'extracted_interests':supplied,
        'review_job_id':job_id,
        "source_url": source_url,
        "evidence_method": "AI_SUGGESTION" if speculative else "WEBSITE_INTERESTS",
        "confidence": "Very low" if speculative else "Low",
        "interests": labels[:12] if count else [],
        "interests_saved": count,
        "reason": (str(review.data.get("basis_summary") or "") if count
                   else 'Awaiting Qwen; saved evidence will be retried without repeating publication searches.' if pending
                   else "Qwen did not return validated research interests."),
    })


def review_queued_interests(job: dict[str, Any]) -> dict[str, Any]:
    payload = (job.get('result_json') or {}).get('interest_input')
    if not payload:
        raise ValueError('Research-interest review is missing its saved input')
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,name,institution_id FROM professors WHERE id=%s AND faculty_status='VERIFIED'",(job['professor_id'],))
            current = cursor.fetchone()
            if not current:
                return {'status':'NOT_APPLICABLE','steps':[]}
            if (current['institution_id']!=payload['professor']['institution_id']
                or not same_person_name(current['name'],payload['professor']['name'])):
                return {'status':'REVIEW_REQUIRED','steps':[{'step':'RESEARCH_INTERESTS',
                    'status':'STALE_EVIDENCE','reason':'Faculty identity or institution changed since extraction; fresh evidence is required.'}]}
    steps = []
    _store_interest_fallback(job['professor_id'],payload['professor'],payload['explicit'],
                             payload['biography_text'],payload['biography_url'],steps,queue_on_failure=False)
    pending = steps[-1]['status']=='AWAITING_MODEL_REVIEW'
    retries = int((job.get('result_json') or {}).get('model_wait_count') or 0)+1
    return {'status':'MODEL_UNAVAILABLE' if pending else 'APPROVED' if steps[-1]['interests_saved'] else 'REVIEW_REQUIRED',
            'steps':steps,'interest_input':payload,'model_wait_count':retries,
            'retry_after_seconds':min(3600,300*2**min(retries-1,4))}


def _dismiss_resolved_scholar_reviews(professor_id: int) -> None:
    """Remove obsolete staff alerts after one Scholar identity is verified."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE professor_identity_review_queue
                   SET status='DISMISSED', reviewed_at=NOW()
                   WHERE status='PENDING'
                     AND reason='SCHOLAR_PROFILE_UNRESOLVED'
                     AND %s = ANY(professor_ids)""",
                (professor_id,),
            )


def _saved_scholar_candidates(professor_id: int) -> list[str]:
    """Return durable staff/search candidates that still need a decision."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT source_url
                   FROM professor_publication_sources
                   WHERE professor_id=%s AND source_type='GOOGLE_SCHOLAR'
                     AND identity_status IN (
                       'STAFF_CANDIDATE','QWEN_QUEUED','REVIEW_REQUIRED',
                       'SOURCE_UNAVAILABLE','PROFILE_NAME_CONFLICT'
                     )
                   ORDER BY checked_at DESC""",
                (professor_id,),
            )
            return [str(row["source_url"]) for row in cursor.fetchall()]


def _official_profile_scholar_candidates(professor_id: int) -> set[str]:
    """Return candidates whose provenance is a verified official profile."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT source_url
                   FROM professor_publication_sources
                   WHERE professor_id=%s AND source_type='GOOGLE_SCHOLAR'
                     AND evidence->>'discovered_by'='OFFICIAL_PROFILE_LINK'""",
                (professor_id,),
            )
            return {str(row["source_url"]) for row in cursor.fetchall()}


def save_staff_scholar_candidate(
    professor_id: int, source_url: str, *, submitted_by: int | None = None
) -> None:
    """Persist an unverified staff lead; saving it never attaches papers."""
    url = str(source_url or "").strip()
    parsed = urlparse(url)
    if not _is_scholar_profile_url(url):
        raise ValueError("Enter a Google Scholar citations profile URL.")
    _record_source(
        professor_id,
        "GOOGLE_SCHOLAR",
        url,
        "STAFF_CANDIDATE",
        {"submitted_by": submitted_by, "verified": False},
    )
    _status(professor_id, "SCHOLAR_REVIEW_QUEUED")


def discover_faculty_publications(professor_id: int, *, progress_callback: Callable[[str, int, int], None] | None = None,
                                  activity_callback: Callable[[str, dict[str, Any]], None] | None = None,
                                  max_works: int = 100) -> dict[str, object]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT p.id,p.name,p.institution_id,p.institution_name,p.faculty_source_url,
                (SELECT candidate.profile_url FROM roster_member_candidates candidate
                 WHERE candidate.professor_id=p.id AND candidate.validation_status IN ('PROFILE_VERIFIED','ROSTER_VERIFIED')
                 ORDER BY candidate.checked_at DESC NULLS LAST LIMIT 1) AS verified_profile_url,
                p.department,p.official_institution_domain,p.faculty_title,p.canonical_rank,
                p.employment_status FROM professors p WHERE p.id=%s
                AND p.faculty_status='VERIFIED' AND p.data_origin='OFFICIAL_DIRECTORY'
                AND EXISTS (SELECT 1 FROM faculty_directory_memberships m JOIN faculty_directories d ON d.id=m.directory_id
                    WHERE m.professor_id=p.id AND m.currently_listed AND d.active AND d.validation_status='APPROVED')""", (professor_id,))
            professor = cursor.fetchone()
    if not professor:
        raise RuntimeError("Publication discovery requires an approved current roster member.")
    if not eligible_research_group_leader(
        str(professor.get("faculty_title") or ""),
        "RESEARCH" if str(professor.get("employment_status") or "") == "RESEARCH" else "PRIMARY",
    ):
        _status(professor_id, "NOT_APPLICABLE")
        return {
            "status": "NOT_APPLICABLE",
            "papers_found": 0,
            "papers_imported": 0,
            "steps": [{
                "step": "ROLE_ELIGIBILITY",
                "status": "NOT_GROUP_LEADING_FACULTY",
                "reason": str(professor.get("faculty_title") or "Role is not eligible"),
            }],
        }
    headers = {"User-Agent": "ScholarRadar/2.0 evidence-first publication indexer"}
    steps: list[dict[str, Any]] = []
    profile_url = str(professor.get("verified_profile_url") or professor.get("faculty_source_url") or "")
    known_urls, papers = ([profile_url] if profile_url else []), []
    official_scholar_urls: list[str] = []
    linked_site_scholar_urls: list[str] = []
    explicit_interests: list[tuple[list[str], str, str]] = []
    biography_text = ""
    biography_url = profile_url
    if profile_url:
        try:
            if activity_callback:
                activity_callback("OFFICIAL_PROFILE", {"source_url": profile_url})
            response = requests.get(profile_url, timeout=30, headers=headers); response.raise_for_status()
            if _is_block_page(response.text):
                raise requests.HTTPError("Official profile returned an access challenge")
            detail_url = linked_directory_person(response.text, str(response.url), str(professor['name']))
            if detail_url:
                steps.append({'step':'DIRECTORY_PERSON_LINK','status':'FOUND','source_url':detail_url})
                response = requests.get(detail_url, timeout=30, headers=headers)
                response.raise_for_status()
                if _is_block_page(response.text):
                    raise requests.HTTPError('Person detail returned an access challenge')
                if linked_directory_person(response.text, str(response.url), str(professor['name'])):
                    raise requests.HTTPError('Directory did not resolve to an individual record')
            if _is_shared_person_directory(response.text):
                steps.append({'step': 'DIRECTORY_PERSON_LINK', 'status': 'REVIEW_REQUIRED',
                              'reason': 'No unique individual record resolved'})
                raise requests.HTTPError('Shared directory is not an individual publication source')
            papers.extend(extract_publications(response.text, str(response.url), "OFFICIAL_PROFILE", str(professor['name'])))
            interests, interest_excerpt = extract_research_interests(response.text)
            if interests:
                explicit_interests.append((interests, str(response.url), interest_excerpt))
            biography_text = extract_biography_text(response.text)
            biography_url = str(response.url)
            links = linked_research_pages(response.text, str(response.url), str(professor['name'])); known_urls.extend(links)
            official_scholar_urls = linked_scholar_profiles(
                response.text, str(response.url)
            )
            steps.append({"step": "OFFICIAL_PROFILE", "status": "CHECKED",
                          "source_url": str(response.url), "papers_found": len(papers),
                          "linked_research_pages": len(links),
                          "linked_scholar_profiles": len(official_scholar_urls)})
            for scholar_url in official_scholar_urls:
                steps.append({"step": "OFFICIAL_SCHOLAR_LINK", "status": "FOUND",
                              "source_url": scholar_url})
            _record_source(professor_id, "OFFICIAL_PROFILE", str(response.url), "VERIFIED", {"papers_found": len(papers)})
            pending_links = [(url, 0) for url in links]
            visited_links = set()
            for url, depth in pending_links:
                if url in visited_links or len(visited_links) >= 9:
                    continue
                visited_links.add(url)
                try:
                    linked = requests.get(url, timeout=30, headers=headers); linked.raise_for_status()
                    if _is_block_page(linked.text):
                        raise requests.HTTPError("Linked page returned an access challenge")
                    kind = "LAB_SITE" if re.search(r"\blab", url, re.I) else "PERSONAL_SITE"
                    if depth and urlparse(linked.url).hostname != urlparse(url).hostname:
                        raise requests.HTTPError('Personal-site section redirected off site')
                    if not depth:
                        pending_links.extend((child, 1) for child in personal_site_sections(linked.text, str(linked.url)))
                    found = extract_publications(linked.text, str(linked.url), kind, str(professor['name'])); papers.extend(found)
                    linked_site_scholar_urls.extend(linked_scholar_profiles(linked.text, str(linked.url)))
                    interests, interest_excerpt = extract_research_interests(linked.text)
                    if interests:
                        explicit_interests.append(
                            (interests, str(linked.url), interest_excerpt)
                        )
                    steps.append({"step": kind, "status": "CHECKED",
                                  "source_url": str(linked.url),
                                  "papers_found": len(found)})
                    _record_source(professor_id, kind, str(linked.url), "LINKED_FROM_OFFICIAL_PROFILE", {"papers_found": len(found)})
                except requests.RequestException as error:
                    steps.append({"step": "LINKED_RESEARCH_PAGE", "status": "SOURCE_UNAVAILABLE",
                                  "source_url": url, "error": type(error).__name__})
                    _record_source(professor_id, "LINKED_SITE", url, "SOURCE_UNAVAILABLE", {"error": type(error).__name__})
        except requests.RequestException as error:
            steps.append({"step": "OFFICIAL_PROFILE", "status": "SOURCE_UNAVAILABLE",
                          "source_url": profile_url, "error": type(error).__name__})
            _record_source(professor_id, "OFFICIAL_PROFILE", profile_url, "SOURCE_UNAVAILABLE", {"error": type(error).__name__})
    papers = list({paper_key(p): p for p in papers}.values())[:max_works]
    if papers:
        imported = _save(professor_id, papers, progress_callback); _status(professor_id, "OFFICIAL_PUBLICATIONS_FOUND")
        _queue_linked_scholar_review(professor_id, official_scholar_urls, steps)
        _queue_linked_scholar_review(professor_id,
            [u for u in linked_site_scholar_urls if _scholar_profile_key(u) not in {_scholar_profile_key(v) for v in official_scholar_urls}],
            steps, discovered_by='LINKED_RESEARCH_PAGE')
        if explicit_interests:
            _store_interest_fallback(professor_id, professor, explicit_interests,
                                     biography_text, biography_url, steps)
        steps.append({"step": "PAPER_IMPORT", "status": "COMPLETED",
                      "papers_found": len(papers), "papers_imported": imported,
                      "papers_already_linked": len(papers)-imported})
        return {"status": "OFFICIAL_PUBLICATIONS_FOUND", "papers_found": len(papers),
                "papers_imported": imported, "steps": steps}
    official_root = _host_root(
        str(professor.get("official_institution_domain") or profile_url)
    )
    recovery_query = f'site:{official_root} "{professor["name"]}"'
    recovery_results = _publication_search(recovery_query, max_results=8)
    alternate_urls = []
    for result in recovery_results:
        url = str(result.get("href") or result.get("url") or "").strip()
        if (
            url
            and url != profile_url
            and _host_root(url) == official_root
            and not NON_PROFILE_PATH.search(urlparse(url).path)
        ):
            alternate_urls.append(url)
    alternate_urls = list(dict.fromkeys(alternate_urls))[:5]
    steps.append({
        "step": "OFFICIAL_SITE_RECOVERY_SEARCH", "status": "CHECKED",
        "query": recovery_query, "results_returned": len(recovery_results),
        "official_candidates": len(alternate_urls),
    })
    for url in alternate_urls:
        try:
            response = requests.get(url, timeout=30, headers=headers)
            response.raise_for_status()
            if _is_block_page(response.text):
                raise requests.HTTPError("Alternate page returned an access challenge")
            matches, identity = alternate_official_profile_matches(
                response.text, str(response.url), dict(professor)
            )
            if not matches:
                steps.append({"step": "ALTERNATE_OFFICIAL_PROFILE",
                              "status": "REJECTED", "source_url": str(response.url),
                              "reason": identity.get("reason")})
                continue
            found = extract_publications(
                response.text, str(response.url), "OFFICIAL_ALTERNATE_PROFILE", str(professor['name'])
            )
            interests, interest_excerpt = extract_research_interests(response.text)
            if interests:
                explicit_interests.append(
                    (interests, str(response.url), interest_excerpt)
                )
            steps.append({"step": "ALTERNATE_OFFICIAL_PROFILE",
                          "status": "VERIFIED", "source_url": str(response.url),
                          "papers_found": len(found)})
            _record_source(
                professor_id, "OFFICIAL_ALTERNATE_PROFILE", str(response.url),
                "VERIFIED", {**identity, "papers_found": len(found)},
            )
            papers.extend(found)
        except requests.RequestException as error:
            steps.append({"step": "ALTERNATE_OFFICIAL_PROFILE",
                          "status": "SOURCE_UNAVAILABLE", "source_url": url,
                          "error": type(error).__name__})
    papers = list({paper_key(p): p for p in papers}.values())[:max_works]
    if papers:
        imported = _save(professor_id, papers, progress_callback)
        _status(professor_id, "OFFICIAL_PUBLICATIONS_FOUND")
        _queue_linked_scholar_review(professor_id, official_scholar_urls, steps)
        _queue_linked_scholar_review(professor_id,
            [u for u in linked_site_scholar_urls if _scholar_profile_key(u) not in {_scholar_profile_key(v) for v in official_scholar_urls}],
            steps, discovered_by='LINKED_RESEARCH_PAGE')
        steps.append({"step": "PAPER_IMPORT", "status": "COMPLETED",
                      "papers_found": len(papers), "papers_imported": imported,
                      "papers_already_linked": len(papers)-imported})
        return {"status": "OFFICIAL_PUBLICATIONS_FOUND",
                "papers_found": len(papers), "papers_imported": imported,
                "steps": steps}
    _store_interest_fallback(
        professor_id, dict(professor), explicit_interests,
        biography_text, biography_url, steps,
    )
    for url in official_scholar_urls:
        _record_source(professor_id, "GOOGLE_SCHOLAR", url, "QWEN_QUEUED", {
            "discovered_by": "OFFICIAL_PROFILE_LINK",
            "official_profile_url": profile_url,
            "verified": False,
        })
    saved_urls = _saved_scholar_candidates(professor_id)
    linked_site_keys = {_scholar_profile_key(u) for u in linked_site_scholar_urls}
    scholar_query = f'"{professor["name"]}" "{professor["institution_name"]}" Google Scholar'
    # An explicit link from the verified university profile is stronger than a
    # search result and makes another DDGS request unnecessary.
    results = [] if saved_urls or linked_site_scholar_urls else _publication_search(scholar_query, max_results=5)
    searched_urls = [str(r.get("href") or r.get("url") or "") for r in results]
    searched_urls = [u for u in searched_urls if _is_scholar_profile_url(u)]
    urls = _dedupe_scholar_profiles(
        [*official_scholar_urls, *linked_site_scholar_urls, *saved_urls, *searched_urls]
    )[:5]
    steps.append({"step": "GOOGLE_SCHOLAR_SEARCH",
                  "status": "NOT_NEEDED" if saved_urls else "CHECKED",
                  "query": scholar_query, "results_returned": len(results),
                  "saved_candidates": len(saved_urls),
                  "scholar_candidates": len(urls),
                  "reason": ("Using a durable Scholar candidate from the official profile or staff."
                             if saved_urls else "")})
    if not urls:
        steps.append({"step": "QWEN_SCHOLAR_REVIEW", "status": "NOT_RUN",
                      "reason": "No Google Scholar profile candidate was returned"})
        _status(professor_id, "NO_PUBLICATIONS_FOUND")
        return {"status": "NO_PUBLICATIONS_FOUND", "papers_found": 0,
                "papers_imported": 0, "steps": steps}
    for url in urls:
        _record_source(professor_id, "GOOGLE_SCHOLAR", url, "QWEN_QUEUED", {
            "discovered_by": ("OFFICIAL_PROFILE_LINK" if url in official_scholar_urls
                              else 'LINKED_RESEARCH_PAGE' if _scholar_profile_key(url) in linked_site_keys
                              else "SAVED_CANDIDATE" if url in saved_urls else "SEARCH"),
            "verified": False,
        })
    _status(professor_id, "SCHOLAR_REVIEW_QUEUED")
    steps.append({"step": "QWEN_SCHOLAR_REVIEW", "status": "QUEUED",
                  "reason": "Candidate identity review is handled by the serialized Qwen queue."})
    return {"status": "SCHOLAR_REVIEW_QUEUED", "papers_found": 0,
            "papers_imported": 0, "candidate_count": len(urls), "steps": steps}


def fetch_scholar_profile(url: str, *, headers: dict[str, str], max_works: int = 300) -> dict[str, Any]:
    """Bounded, paced pagination; never continue through a block or identity change."""
    if not _is_scholar_profile_url(url):
        raise ValueError('Not a supported Scholar profile URL')
    limit = max(1, min(300, max_works))
    found: dict[str, Publication] = {}
    profile = None
    reason = 'LIMIT_REACHED'
    pages = 0
    for offset in range(0, limit, 100):
        if offset:
            time.sleep(2)
        # Remove caller-supplied pagination so it cannot conflict with ours.
        base = url.split('?', 1)[0]
        params = {'user':_scholar_profile_key(url),'pagesize':min(100,limit-offset),'cstart':offset}
        response = requests.get(base,params=params,timeout=30,headers=headers)
        response.raise_for_status()
        if not _is_scholar_profile_url(str(response.url)) or _scholar_profile_key(str(response.url)) != _scholar_profile_key(url):
            raise requests.HTTPError('Scholar redirect changed profile identity')
        if _is_block_page(response.text):
            raise requests.HTTPError('Scholar returned an access challenge')
        page = parse_scholar_profile(response.text,str(response.url))
        if not page.get('name'):
            raise requests.HTTPError('Scholar response has no readable profile identity')
        if profile is not None and not same_person_name(profile['name'],page['name']):
            raise requests.HTTPError('Scholar identity changed during pagination')
        profile = profile or page
        batch = page['papers']; before = len(found); pages += 1
        for paper in batch:
            found.setdefault(paper_key(paper),paper)
        if batch and len(found)==before:
            reason='REPEATED_PAGE'; break
        if len(batch)<params['pagesize']:
            reason='END_OF_LIST'; break
    assert profile is not None
    return {**profile,'papers':list(found.values())[:limit],
            'pagination_status':reason,'pages_fetched':pages,'works_limit':limit}


def review_queued_scholar_candidates(
    professor_id: int, *, progress_callback: Callable[[str, int, int], None] | None = None,
    activity_callback: Callable[[str, dict[str, Any]], None] | None = None,
    max_works: int = 300,
) -> dict[str, object]:
    """Review durable Scholar candidates. One worker processes this queue serially."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT p.id,p.name,p.institution_id,p.institution_name,
                          p.faculty_source_url,p.department,
                          p.official_institution_domain
                   FROM professors p
                   WHERE p.id=%s AND p.faculty_status='VERIFIED'
                     AND p.data_origin='OFFICIAL_DIRECTORY'""",
                (professor_id,),
            )
            professor = cursor.fetchone()
    if not professor:
        raise RuntimeError("Qwen review requires a verified roster professor.")
    known_urls = [str(professor.get("faculty_source_url") or "")]
    # Re-establish direct provenance from today's official page. Older rows
    # may have incorrectly labelled personal-site links as official links.
    official_scholar_urls: set[str] = set()
    saved_official_urls = _official_profile_scholar_candidates(professor_id)
    headers = {"User-Agent": "Mozilla/5.0 ScholarRadar/2.0 evidence-review"}
    # Re-read the authoritative profile so provenance survives retries even if
    # an older decision row was written before provenance was preserved.
    if known_urls[0]:
        try:
            official_response = requests.get(known_urls[0], timeout=30, headers=headers)
            official_response.raise_for_status()
            attributable, _ = alternate_official_profile_matches(
                official_response.text, str(official_response.url), dict(professor))
            if attributable and not _is_block_page(official_response.text):
                official_scholar_urls.update(linked_scholar_profiles(
                    official_response.text, str(official_response.url)
                ))
        except requests.RequestException:
            pass
    urls = _dedupe_scholar_profiles([
        *official_scholar_urls, *saved_official_urls, *_saved_scholar_candidates(professor_id)
    ])
    if not urls:
        _status(professor_id, "NO_PUBLICATIONS_FOUND")
        return {"status": "NO_PUBLICATIONS_FOUND", "papers_found": 0,
                "papers_imported": 0, "steps": [{"step": "QWEN_SCHOLAR_REVIEW",
                "status": "NOT_RUN", "reason": "No saved Scholar candidate."}]}
    reviewed: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    retry_delay = 21600
    for index, url in enumerate(urls, 1):
        if activity_callback:
            activity_callback("QWEN_SCHOLAR_REVIEW", {"source_url": url,
                "candidate_number": index, "candidate_total": len(urls)})
        try:
            scholar = fetch_scholar_profile(url,headers=headers,max_works=max_works)
            steps.append({'step':'SCHOLAR_PROFILE','status':'CHECKED','source_url':url,
                          'papers_found':len(scholar['papers']),
                          'reason':f"{scholar['pagination_status']}; {scholar['pages_fetched']} page(s), limit {scholar['works_limit']} works."})
            decision, reasons = scholar_identity_decision(
                dict(professor),
                scholar,
                known_urls,
                scholar_url=url,
                official_scholar_urls=official_scholar_urls,
            )

            model = None

            # Bypass the model only for the freshly fetched direct official
            # link plus compatible name. Same university alone is too weak.
            direct_verified = decision == 'VERIFIED' and 'linked_from_official_profile' in reasons
            if not direct_verified and decision != 'PROFILE_NAME_CONFLICT':
                model = review_publication_identity(
                    source_record_key=url,
                    institution_id=int(professor["institution_id"]),
                    professor_name=str(professor["name"]),
                    institution=str(professor["institution_name"]),
                    department=str(professor.get("department") or ""),
                    official_email_domain=str(
                        professor.get("official_institution_domain") or ""
                    ),
                    known_pages=known_urls,
                    scholar_profile=scholar,
                )

                if model.status != "VALID":
                    decision = (
                        "REVIEW_REQUIRED"
                        if model.status != "MODEL_UNAVAILABLE"
                        else "SOURCE_UNAVAILABLE"
                    )

                elif str(model.data.get("same_person") or "").upper() != "YES":
                    decision = (
                        "PROFILE_NAME_CONFLICT"
                        if str(model.data.get("same_person") or "").upper() == "NO"
                        else "REVIEW_REQUIRED"
                    )

            qwen_status = model.status if model is not None else "NOT_NEEDED"
            qwen_data = model.data if model is not None else {}
            qwen_errors = list(model.errors) if model is not None else []
            evidence = {
                "reasons": reasons,
                "qwen_status": qwen_status,
                "qwen": qwen_data,
                "qwen_errors": qwen_errors,
                "name": scholar.get("name"),
                "affiliation": scholar.get("affiliation"),
                "verified_email": scholar.get("verified_email"),
                "discovered_by": (
                    "OFFICIAL_PROFILE_LINK"
                    if _scholar_profile_key(url) in {
                        _scholar_profile_key(value)
                        for value in official_scholar_urls
                    }
                    else "SAVED_CANDIDATE"
                ),
            }
            _record_source(professor_id, "GOOGLE_SCHOLAR", url, decision, evidence)
            reviewed.append({"url": url, "decision": decision, "reasons": reasons})
            steps.append({"step": "QWEN_SCHOLAR_REVIEW", "status": qwen_status,
                          "source_url": url,
                          "deterministic_decision": decision,
                          "decision_signals": reasons})
            if decision == "VERIFIED":
                scholar_papers = list(scholar["papers"])[:max_works]
                imported = _save(professor_id, scholar_papers, progress_callback)
                value = "SCHOLAR_VERIFIED" if scholar_papers else "NO_PUBLICATIONS_FOUND"
                _status(professor_id, value)
                _dismiss_resolved_scholar_reviews(professor_id)
                steps.append({"step": "PAPER_IMPORT", "status": "COMPLETED",
                              "papers_found": len(scholar_papers),
                              "papers_imported": imported,
                              "papers_already_linked": len(scholar_papers)-imported})
                return {"status": value, "papers_found": len(scholar_papers),
                        "papers_imported": imported, "steps": steps}
        except requests.RequestException as error:
            http_status = error.response.status_code if error.response is not None else None
            retry_after = error.response.headers.get('Retry-After','') if error.response is not None else ''
            try:
                delay = int(retry_after) if retry_after.isdigit() else int((parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds())
                retry_delay = max(retry_delay,delay)
            except (ValueError,TypeError,OverflowError):
                pass
            _record_source(professor_id, "GOOGLE_SCHOLAR", url, "SOURCE_UNAVAILABLE",
                           {"error": type(error).__name__, 'http_status':http_status,
                            'retry_after_seconds':retry_delay})
            steps.append({"step": "SCHOLAR_PROFILE", "status": "SOURCE_UNAVAILABLE",
                          "source_url": url, "error": type(error).__name__,
                          'http_status':http_status,'retry_after_seconds':retry_delay})
            reviewed.append({"url": url, "decision": "SOURCE_UNAVAILABLE"})
            if http_status in {403,429}:
                break  # Do not hammer other profiles through the same block.
    if reviewed and all(item.get('decision')=='SOURCE_UNAVAILABLE' for item in reviewed):
        _status(professor_id,'SOURCE_UNAVAILABLE')
        return {'status':'SOURCE_UNAVAILABLE','papers_found':0,'papers_imported':0,
                'candidates':reviewed,'steps':steps,'retry_after_seconds':retry_delay}
    _status(professor_id, "REVIEW_REQUIRED")
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO professor_identity_review_queue
                (professor_ids,canonical_name_key,institution_id,reason,evidence)
                VALUES (ARRAY[%s]::BIGINT[],%s,%s,'SCHOLAR_PROFILE_UNRESOLVED',%s::jsonb) ON CONFLICT DO NOTHING""",
                (professor_id, " ".join(name_tokens(str(professor["name"]))), professor["institution_id"], json.dumps({"candidates": reviewed})))
    return {"status": "REVIEW_REQUIRED", "papers_found": 0, "papers_imported": 0,
            "candidates": reviewed, "steps": steps}
