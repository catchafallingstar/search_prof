from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from typing import Callable
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.name_normalization import canonical_name_key, name_tokens
from ingestion.ollama_evidence import NONFACULTY_TYPES, OllamaReview, review as review_with_ollama


_FACULTY_ROLE = re.compile(
    r"\b(?:assistant|associate|full|distinguished|endowed|research|clinical|adjunct|"
    r"visiting|teaching|practice|affiliate)?\s*professor(?:\s+emerit(?:us|a))?\b|"
    r"\bprofessor\s+emerit(?:us|a)\b|\b(?:senior\s+)?lecturer\b|\binstructor\b|"
    r"\b(?:senior\s+)?(?:affiliate|affiliated|visiting|adjunct|research|clinical|teaching|part[- ]time)\s+faculty(?:\s+member)?\b",
    re.I,
)
_NONFACULTY = re.compile(
    r"\b(?:students?|ph\.?d\.? candidates?|doctoral candidates?|postdoc(?:toral)?|staff|"
    r"coordinators?|administrators?|academic advisors?|student success advisors?|"
    r"career consultants?|graphic designers?|management analysts?|program managers?|"
    r"administrative assistants?|recruitment managers?|graduate assistants?|"
    r"research assistants?|teaching assistants?|visitors?)\b", re.I
)
_HISTORICAL_PROFILE_PATH = re.compile(
    r"/(?:news|stories?|events?|awards?|honors?|alumni|archive|grantsandawards)(?:/|$)", re.I
)
_NON_DIRECTORY_PATH = re.compile(
    r"/(?:news|stories?|events?|awards?|honors?|alumni|archive|grantsandawards|"
    r"media-experts?|core-facilities|research-projects?)(?:/|$)", re.I
)
_NON_PERSON_LABEL = re.compile(
    r"\b(?:about|academics|admissions|africa|alumni|america|asia|australia|centers?|"
    r"college|community|corporate|departments?|directory|east|europe|experts?|"
    r"facilities|faculty|home|laboratories|labs?|middle|north|partners?|people|"
    r"programs?|research|resources?|services?|south|students?|staff|union|university|"
    r"view|visitors?|west|default|placeholder|circle|logo|image|photo)\b", re.I
)
_HISTORICAL_TEXT = re.compile(
    r"\b(?:in memoriam|obituary|remembering|mourns? the loss|passed away|"
    r"former faculty|past faculty)\b", re.I
)
_CURATED_GROUP = re.compile(
    r"\b(?:faculty fellows?|faculty mentors?|mentor profiles?|scholars? cohort|"
    r"faculty cohort|program participants?|research mentors?|fellow profiles?)\b", re.I
)
_NON_ROSTER_PURPOSE = re.compile(
    r"\b(?:faculty[- ]led programs?|faculty\s*(?:and|&|/)\s*staff disability support|"
    r"faculty[- ]staff associations?|"
    r"united way campaign|employee resources?|faculty resources?|"
    r"resources for faculty and staff|division of people,? equity,? and culture)\b", re.I
)
_PERSON_TOKEN = re.compile(r"^(?:[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*|\([A-Za-zÀ-ÖØ-öø-ÿ'’.-]+\))$")
_TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def _canonical_profile_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    if _HISTORICAL_PROFILE_PATH.search(path):
        return ""
    query = urlencode(sorted(
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_PARAMETERS
    ))
    suffix = f"?{query}" if query else ""
    return f"https://{parsed.hostname.casefold().removeprefix('www.')}{path}{suffix}"


@dataclass(frozen=True)
class RosterMember:
    name: str
    title: str
    profile_url: str
    appointment_type: str
    excerpt: str
    email: str = ""
    phone: str = ""
    office_address: str = ""
    section_heading: str = ""
    raw_card_html: str = ""


@dataclass(frozen=True)
class DirectoryClassification:
    status: str
    page_type: str
    reason: str
    scope_label: str
    members: tuple[RosterMember, ...]


def _appointment_type(text: str) -> str:
    lowered = text.casefold()
    if "emeritus" in lowered or "emerita" in lowered:
        return "EMERITUS"
    if "adjunct" in lowered:
        return "ADJUNCT"
    if "visiting" in lowered:
        return "VISITING"
    if "research professor" in lowered:
        return "RESEARCH"
    if "affiliate faculty" in lowered or "affiliated faculty" in lowered:
        return "AFFILIATE"
    if re.search(r"\bpart[- ]time\s+faculty\b", lowered):
        return "PART_TIME"
    return "PRIMARY"


def _canonical_rank(text: str) -> str | None:
    lowered = text.casefold()
    if "assistant professor" in lowered:
        return "ASSISTANT_PROFESSOR"
    if "associate professor" in lowered:
        return "ASSOCIATE_PROFESSOR"
    if "professor" in lowered:
        return "PROFESSOR"
    if re.search(r"\b(?:part[- ]time\s+faculty|lecturer|instructor)\b", lowered):
        return "FACULTY_OTHER"
    return None


def eligible_research_group_leader(
    title: str, appointment_type: str, role_context: str = ""
) -> bool:
    """Return whether a roster role belongs in the public professor index.

    University people directories frequently label instructors, lecturers,
    adjuncts, affiliates, visitors, and part-time teachers as faculty.  They
    remain valid roster observations, but this product indexes professors who
    can plausibly supervise a research group.  A faculty-page listing alone
    must therefore never promote those teaching/secondary roles.
    """
    rank = _canonical_rank(title)
    combined = f"{title} | {role_context}"
    if re.search(
        r"\b(?:adjunct|affiliate|affiliated|visiting|emerit(?:us|a)|"
        r"part[- ]time|lecturer|instructor)\b",
        combined,
        re.I,
    ):
        return False
    return bool(
        rank in {"ASSISTANT_PROFESSOR", "ASSOCIATE_PROFESSOR", "PROFESSOR"}
        and appointment_type in {"PRIMARY", "RESEARCH"}
    )


def _same_directory_document(left: str, right: str) -> bool:
    """Return whether a URL selects a record from the same directory document."""
    a, b = urlparse(str(left or "")), urlparse(str(right or ""))
    return bool(
        a.hostname and b.hostname
        and a.hostname.casefold().removeprefix("www.")
            == b.hostname.casefold().removeprefix("www.")
        and (a.path.rstrip("/") or "/") == (b.path.rstrip("/") or "/")
        and (a.query != b.query or a.fragment != b.fragment)
    )


def _looks_like_person_name(value: str) -> bool:
    """Reject navigation/organization labels before they become identities."""
    name = " ".join(str(value or "").split()).strip()
    if not name or any(symbol in name for symbol in ("»", "›", "→", "▶")):
        return False
    if _NON_PERSON_LABEL.search(name) or re.search(r"\d|@|/|\\|\b(?:and|of|for|the)\b", name, re.I):
        return False
    tokens = name.split()
    if not 2 <= len(tokens) <= 7:
        return False
    return all(_PERSON_TOKEN.fullmatch(token) for token in tokens)


def _profile_name_compatible(expected: str, observed: str) -> bool:
    """Allow omitted middle names while preserving given/family-name identity."""
    def tokens(value: str) -> list[str]:
        cleaned = re.sub(r"\b(?:dr|prof(?:essor)?|ph\.?d|m\.?d)\.?\b", " ", value, flags=re.I)
        if "," in cleaned:
            family, given = cleaned.split(",", 1)
            cleaned = f"{given} {family}"
        return re.findall(r"[^\W_]+", cleaned.casefold(), re.UNICODE)

    left, right = tokens(expected), tokens(observed)
    if len(left) < 2 or len(right) < 2 or left[-1] != right[-1]:
        return False
    left_given, right_given = left[:-1], right[:-1]
    if left_given[0] == right_given[0]:
        return True
    return bool(set(left_given) & set(right_given))


def _container_signature(container: object) -> str:
    classes = sorted(str(value) for value in (container.get("class") or []))[:4]
    return f"{container.name}.{' '.join(classes)}"


def _smallest_person_container(anchor: object) -> object | None:
    """Return the person's own card, never a broader section containing peers."""
    for parent in anchor.parents:
        if getattr(parent, "name", None) not in {"article", "li", "tr", "section", "div"}:
            continue
        local_text = " ".join(parent.get_text(" ", strip=True).split())
        if len(local_text) > 600:
            return None
        if len(parent.find_all("a", href=True)) <= 6:
            return parent
    return None


def _table_roster_members(root: object, directory_url: str) -> list[RosterMember]:
    """Parse server-rendered directories whose names and links occupy different cells."""
    members: list[RosterMember] = []
    for table in root.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 4:
            continue
        headers = [" ".join(cell.get_text(" ", strip=True).split()).casefold()
                   for cell in rows[0].find_all(["th", "td"])]
        first_index = next((i for i, value in enumerate(headers) if value == "first name"), None)
        last_index = next((i for i, value in enumerate(headers) if value == "last name"), None)
        name_index = next((i for i, value in enumerate(headers) if value == "name"), None)
        if name_index is None and (first_index is None or last_index is None):
            continue
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells or max(i for i in (first_index, last_index, name_index) if i is not None) >= len(cells):
                continue
            if name_index is not None:
                name = " ".join(cells[name_index].get_text(" ", strip=True).split())
            else:
                name = " ".join((cells[first_index].get_text(" ", strip=True),
                                 cells[last_index].get_text(" ", strip=True))).strip()
            name = re.sub(r"^(?:Dr|Professor)\.?\s+", "", name, flags=re.I)
            if not _looks_like_person_name(name):
                continue
            links = [anchor for anchor in row.find_all("a", href=True)
                     if not str(anchor.get("href") or "").casefold().startswith(("mailto:", "tel:", "/cdn-cgi/"))
                     and str(anchor.get("href") or "") != "#"]
            if not links:
                continue
            profile_link = next((anchor for anchor in links if re.search(
                r"\b(?:view|meet|profile|bio)\b", anchor.get_text(" ", strip=True), re.I
            )), links[-1])
            profile_url = urljoin(directory_url, str(profile_link.get("href") or ""))
            if not _canonical_profile_url(profile_url) or _canonical_profile_url(profile_url) == _canonical_profile_url(directory_url):
                continue
            context = " | ".join(" ".join(cell.get_text(" ", strip=True).split()) for cell in cells)
            if _NONFACULTY.search(context):
                continue
            role = _FACULTY_ROLE.search(context)
            email = next((re.sub(r"^mailto:", "", str(a.get("href") or ""), flags=re.I).split("?", 1)[0]
                          for a in row.find_all("a", href=True)
                          if str(a.get("href") or "").casefold().startswith("mailto:")), "")
            office_index = next((i for i, value in enumerate(headers) if "office" in value and "phone" not in value), None)
            department_index = next((i for i, value in enumerate(headers) if "department" in value), None)
            section = cells[department_index].get_text(" ", strip=True) if department_index is not None and department_index < len(cells) else ""
            members.append(RosterMember(
                name=name, title=role.group(0).strip() if role else "", profile_url=profile_url,
                appointment_type=_appointment_type(context), excerpt=context[:500], email=email,
                office_address=cells[office_index].get_text(" ", strip=True) if office_index is not None and office_index < len(cells) else "",
                section_heading=section,
                raw_card_html=str(row)[:24000],
            ))
    return members


def _linked_profile_card_members(root: object, directory_url: str) -> list[RosterMember]:
    """Read cards whose clickable image is separate from the displayed name."""
    members: list[RosterMember] = []
    for anchor in root.find_all("a", href=True):
        profile_url = urljoin(directory_url, str(anchor.get("href") or ""))
        path = urlparse(profile_url).path
        if not re.search(r"/(?:faculty-profile(?:-|/)|faculty/profiles?/|profiles?/)[^/]+", path, re.I):
            continue
        container = None
        name = ""
        for parent in list(anchor.parents)[:10]:
            if getattr(parent, "name", None) not in {"article", "li", "div"}:
                continue
            context = " ".join(parent.get_text(" ", strip=True).split())
            if not context or len(context) > 500:
                continue
            cleaned = re.sub(r"^(?:Dr|Professor)\.?\s+", "", context.strip(), flags=re.I)
            if _looks_like_person_name(cleaned):
                container, name = parent, cleaned
                break
        if not name:
            image = anchor.find("img", alt=True)
            image_name = re.sub(
                r"^(?:Dr|Professor)\.?\s+", "",
                str(image.get("alt") or "").strip() if image else "", flags=re.I,
            )
            if _looks_like_person_name(image_name):
                container, name = anchor.parent, image_name
        if not name:
            slug_match = re.search(r"/faculty-profile-(.+?)(?:-\d+)?/?$", path, re.I)
            slug_name = " ".join(
                part.capitalize() for part in (slug_match.group(1).split("-") if slug_match else [])
            )
            if _looks_like_person_name(slug_name):
                container, name = anchor.parent, slug_name
        if container is None or not name:
            continue
        context = " ".join(container.get_text(" ", strip=True).split())
        if _NONFACULTY.search(context):
            continue
        heading = container.find_previous(["h1", "h2", "h3", "h4"])
        section = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
        role = _FACULTY_ROLE.search(f"{context} {section}")
        members.append(RosterMember(
            name=name,
            title=role.group(0).strip() if role else "",
            profile_url=profile_url,
            appointment_type=_appointment_type(f"{context} {section}"),
            excerpt=context[:500],
            section_heading=section,
            raw_card_html=str(container)[:24000],
        ))
    return members


def _embedded_people_members(html: str, directory_url: str) -> list[RosterMember]:
    """Load an official WSU-style people component from its declared university API."""
    soup = BeautifulSoup(html, "html.parser")
    page_host = (urlparse(directory_url).hostname or "").casefold()
    members: list[RosterMember] = []
    for component in soup.find_all(attrs={"data-base-url": True, "data-directory": True}):
        base_url = str(component.get("data-base-url") or "").strip().rstrip("/")
        base_host = (urlparse(base_url).hostname or "").casefold()
        directory_id = str(component.get("data-directory") or "").strip()
        if not directory_id.isdigit() or not base_host or not page_host:
            continue
        # Both hosts must remain inside the same US university domain.
        if ".".join(base_host.split(".")[-2:]) != ".".join(page_host.split(".")[-2:]):
            continue
        endpoint = f"{base_url}/wp-json/peopleapi/v1/people"
        try:
            response = requests.get(
                endpoint,
                params={"count": 500, "page": 1, "directory": directory_id},
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 ScholarRadar/2.0", "Referer": directory_url,
                         "Accept": "application/json"},
            )
            response.raise_for_status()
            rows = response.json()
        except (requests.RequestException, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows[:1000]:
            if not isinstance(row, dict):
                continue
            name = " ".join(str(row.get("name") or "").split())
            titles = [str(value) for value in (row.get("title") or [])]
            title_text = " | ".join(titles)
            role = _FACULTY_ROLE.search(title_text)
            profile_url = str(row.get("profile_url") or "").strip()
            profile_host = (urlparse(profile_url).hostname or "").casefold()
            if (not role or not _looks_like_person_name(name) or not profile_host
                    or ".".join(profile_host.split(".")[-2:]) != ".".join(page_host.split(".")[-2:])):
                continue
            members.append(RosterMember(
                name=name, title=role.group(0).strip(), profile_url=profile_url,
                appointment_type=_appointment_type(title_text), excerpt=title_text[:500],
                email=str(row.get("email") or ""), phone=str(row.get("phone") or ""),
                office_address=str(row.get("office") or ""),
                section_heading="Faculty",
                raw_card_html=json.dumps(row, ensure_ascii=False)[:24000],
            ))
    return members


def parse_faculty_directory(
    html: str, directory_url: str, *, fetch_embedded: bool = False
) -> list[RosterMember]:
    """Extract repeated, card-local profile records from a roster candidate."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup
    directory_host = urlparse(directory_url).hostname or ""
    found: list[tuple[str, RosterMember]] = []
    structure_counts: Counter[str] = Counter()
    for possible_anchor in root.find_all("a", href=True):
        possible_container = _smallest_person_container(possible_anchor)
        if possible_container is not None:
            structure_counts[_container_signature(possible_container)] += 1
    for anchor in root.find_all("a", href=True):
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if len(name) > 100 or not _looks_like_person_name(name):
            continue
        if re.search(r"\b(?:contact|directory|department|faculty|home|learn|more|news|menu)\b", name, re.I):
            continue
        if _FACULTY_ROLE.search(name) or name.casefold() in {"view profile", "read more"}:
            continue
        profile_url = urljoin(directory_url, str(anchor.get("href") or ""))
        if _canonical_profile_url(profile_url) == _canonical_profile_url(directory_url):
            continue
        profile_host = urlparse(profile_url).hostname or ""
        if not profile_host or not (
            profile_host == directory_host or profile_host.endswith("." + directory_host)
            or directory_host.endswith("." + profile_host)
        ):
            continue
        container = _smallest_person_container(anchor)
        if container is None:
            continue
        context = " ".join((container or anchor).get_text(" ", strip=True).split())[:1000]
        heading = anchor.find_previous(["h1", "h2", "h3", "h4"])
        section = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
        role_match = _FACULTY_ROLE.search(context)
        if _NONFACULTY.search(section) or _NONFACULTY.search(context):
            continue
        # A page-level "Faculty" heading is not evidence about every link on
        # the page. Each saved person needs a title in that person's own card,
        # row, or list item.
        if not role_match:
            continue
        title = role_match.group(0).strip()
        member = RosterMember(
            name=name, title=title, profile_url=profile_url,
            appointment_type=_appointment_type(context + " " + section),
            excerpt=context[:500],
            email=((container.find("a", href=re.compile(r"^mailto:", re.I)) or {}).get("href", "")[7:].split("?", 1)[0]),
            phone=" ".join((container.find("a", href=re.compile(r"^tel:", re.I)) or anchor).get_text(" ", strip=True).split()) if container.find("a", href=re.compile(r"^tel:", re.I)) else "",
            office_address=" ".join((container.find(attrs={"class": re.compile(r"office|location|address", re.I)}) or anchor).get_text(" ", strip=True).split()) if container.find(attrs={"class": re.compile(r"office|location|address", re.I)}) else "",
            section_heading=section,
            raw_card_html=str(container)[:24000],
        )
        found.append((_container_signature(container), member))
    repeated = {signature for signature, count in structure_counts.items() if count >= 3}
    members: dict[str, RosterMember] = {}
    for signature, member in found:
        if signature in repeated:
            members.setdefault(_canonical_profile_url(member.profile_url), member)
    for member in _table_roster_members(root, directory_url):
        members.setdefault(_canonical_profile_url(member.profile_url), member)
    for member in _linked_profile_card_members(root, directory_url):
        members.setdefault(_canonical_profile_url(member.profile_url), member)
    if fetch_embedded:
        for member in _embedded_people_members(html, directory_url):
            members.setdefault(_canonical_profile_url(member.profile_url), member)
    return list(members.values())


def classify_faculty_page(
    html: str, directory_url: str, *, minimum_members: int = 3,
    fetch_embedded: bool = False,
) -> DirectoryClassification:
    """Classify scope before a same-domain page is allowed to become a roster."""
    parsed = urlparse(directory_url)
    if _NON_DIRECTORY_PATH.search(parsed.path):
        return DirectoryClassification("NOT_A_ROSTER", "EDITORIAL_OR_HISTORICAL", "NON_DIRECTORY_PATH", "", ())
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    h1_node = soup.find("h1")
    h1 = " ".join(h1_node.get_text(" ", strip=True).split()) if h1_node else ""
    breadcrumbs = " ".join(node.get_text(" ", strip=True) for node in soup.find_all(
        attrs={"class": re.compile(r"breadcrumb", re.I)}
    ))[:500]
    description_node = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    description = str(description_node.get("content") or "") if description_node else ""
    identity = f"{parsed.hostname} {parsed.path} {title} {h1} {breadcrumbs} {description}"
    content_root = soup.find("main") or soup.body or soup
    main_text = " ".join(content_root.get_text(" ", strip=True).split())
    host = (parsed.hostname or "").casefold()
    if host.startswith("labs.") or re.search(r"/(?:labs?|laboratories)/", parsed.path, re.I):
        return DirectoryClassification("NOT_A_ROSTER", "LAB_MEMBERS", "LAB_OR_LAB_HOST", h1, ())
    if _CURATED_GROUP.search(identity):
        return DirectoryClassification(
            "NOT_A_ROSTER", "CURATED_FACULTY_GROUP",
            "ENRICHMENT_ONLY_MENTOR_FELLOW_SCHOLAR_OR_COHORT_PAGE", h1, (),
        )
    if _NON_ROSTER_PURPOSE.search(f"{identity} {main_text}"):
        return DirectoryClassification(
            "NOT_A_ROSTER", "NON_ROSTER_FACULTY_PAGE", "PAGE_PURPOSE_IS_NOT_A_FACULTY_ROSTER", h1, ()
        )
    negative_types = (
        ("EMPLOYEE_RESOURCES", r"\b(?:employee resources|workday|payroll|benefits|human resources)\b"),
        ("FACULTY_EXPERTISE_DIRECTORY", r"\b(?:faculty expertise|experts directory|research expertise)\b"),
        ("AWARD_OR_HONOR", r"\b(?:awards?|honou?rs?|recipient)\b"),
        ("NEWS_OR_EVENT", r"\b(?:news|events?|press release)\b"),
    )
    for page_type, pattern in negative_types:
        if re.search(pattern, identity, re.I):
            return DirectoryClassification("NOT_A_ROSTER", page_type, f"PAGE_CLASSIFIED_{page_type}", h1, ())
    members = parse_faculty_directory(html, directory_url, fetch_embedded=fetch_embedded)
    organizational_scope = bool(re.search(r"\b(?:department|school|college|university|program)\b", identity, re.I))
    faculty_scope = bool(re.search(r"\bfacult(?:y|ies)\b", f"{title} {h1} {breadcrumbs}", re.I))
    if not members:
        return DirectoryClassification("NOT_A_ROSTER", "NO_PERSON_ROSTER", "NO_EXTRACTABLE_FACULTY_RECORDS", h1, ())
    if not faculty_scope and not (organizational_scope and len(members) >= max(3, minimum_members)):
        return DirectoryClassification("UNCERTAIN_REQUIRES_REVIEW", "UNKNOWN", "NO_FACULTY_SCOPE_HEADING", h1, tuple(members))
    if len(members) < max(2, minimum_members):
        return DirectoryClassification("UNCERTAIN_REQUIRES_REVIEW", "UNKNOWN", "NO_REPEATED_ATTRIBUTABLE_FACULTY_CARDS", h1, tuple(members))
    hosts = {(urlparse(member.profile_url).hostname or "").casefold() for member in members}
    if not hosts:
        return DirectoryClassification("UNCERTAIN_REQUIRES_REVIEW", "UNKNOWN", "NO_PROFILE_HOSTS", h1, ())
    if not organizational_scope:
        return DirectoryClassification("UNCERTAIN_REQUIRES_REVIEW", "UNKNOWN", "NO_CANONICAL_ORGANIZATIONAL_SCOPE", h1, tuple(members))
    if re.search(r"\b(?:students?|staff|postdocs?|administration)\b", f"{title} {h1}", re.I) or any(not member.title for member in members):
        return DirectoryClassification("APPROVED_ROSTER", "MIXED_PEOPLE_DIRECTORY", "MIXED_DIRECTORY_REQUIRES_PROFILE_VALIDATION", h1, tuple(members))
    page_type = "DEPARTMENT_FACULTY_ROSTER" if re.search(
        r"\b(?:department|school|college|program)\b", identity, re.I
    ) else "UNIVERSITY_FACULTY_DIRECTORY"
    return DirectoryClassification("APPROVED_ROSTER", page_type, "REPEATED_CARD_LOCAL_FACULTY_ROLES", h1 or title, tuple(members))


def validate_faculty_directory(
    html: str, directory_url: str, *, minimum_members: int = 3,
    fetch_embedded: bool = False,
) -> tuple[list[RosterMember], str]:
    result = classify_faculty_page(
        html, directory_url, minimum_members=minimum_members, fetch_embedded=fetch_embedded
    )
    return (list(result.members), "APPROVED") if result.status == "APPROVED_ROSTER" else ([], result.reason)


def _main_profile_content(soup: BeautifulSoup) -> object:
    root = (
        soup.select_one("main, [role='main'], #main-content, #main, article.profile, "
                        ".person-profile, .profile-content, .main-content")
        or soup.body or soup
    )
    for node in root.select(
        "script, style, noscript, header, nav, footer, aside, form, "
        ".navigation, .nav, .menu, .footer, .breadcrumbs, .breadcrumb, "
        ".related-content, .related-news"
    ):
        node.decompose()
    return root


def _historical_about_subject(text: str, name: str) -> bool:
    """Match explicit subject/phrase grammar, not nearby unrelated wording."""
    subject = r"\b" + r"\s+".join(re.escape(part) for part in str(name).split()) + r"\b"
    patterns = (
        rf"(?:in memoriam|obituary|remembering)\s*[:\-–—]?\s*(?:dr\.?\s+)?{subject}",
        rf"{subject}[^.!?]{{0,100}}(?:passed away|is deceased|was a former faculty|was a past faculty)",
        rf"(?:mourns? the loss of|passed away[^.!?]{{0,40}}){subject}",
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _bounded_evidence(text: str, patterns: list[re.Pattern[str]], limit: int = 900) -> str:
    pieces: list[str] = []
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            pieces.append(text[max(0, match.start() - 180):match.end() + 320].strip())
    return " … ".join(dict.fromkeys(pieces))[:limit]


def _profile_signals(root: object, member: RosterMember, text: str) -> dict[str, object]:
    aliases = [member.name]
    for heading in root.find_all(["h1", "h2"])[0:5]:
        candidate = " ".join(heading.get_text(" ", strip=True).split()).strip()
        if 1 < len(candidate.split()) <= 7 and not _FACULTY_ROLE.search(candidate):
            aliases.append(candidate)
    paper_titles: list[str] = []
    for heading in root.find_all(["h2", "h3", "h4"]):
        if not re.search(r"\b(?:publications?|selected works?|research papers?)\b", heading.get_text(" ", strip=True), re.I):
            continue
        for node in heading.find_all_next(["li", "a"], limit=60):
            if node.name in {"h2", "h3", "h4"}:
                break
            value = " ".join(node.get_text(" ", strip=True).split())
            if 20 <= len(value) <= 350:
                paper_titles.append(value)
            if len(paper_titles) >= 25:
                break
    publications = list(dict.fromkeys(paper_titles))[:25]
    return {"name_aliases": list(dict.fromkeys(aliases))[:8],
            "paper_titles": publications, "publication_entries": publications}


def validate_directory_detail(
    html: str,
    member: RosterMember,
    institution_domain: str,
) -> tuple[str, str, RosterMember, dict[str, object]]:
    """Validate one record selected inside a shared official directory page."""
    soup = BeautifulSoup(html, "html.parser")
    root = _main_profile_content(soup)
    fields: dict[str, str] = {}
    for label_node in root.find_all(["strong", "dt", "th"]):
        label = re.sub(
            r"[^a-z0-9]+", " ", label_node.get_text(" ", strip=True).casefold()
        ).strip()
        if label not in {
            "name", "first name", "last name", "title", "role", "role s", "roles",
            "position", "email", "office", "office address", "office phone",
            "department", "program",
        }:
            continue
        container = label_node.find_parent(["p", "dd", "tr", "li"])
        if container is None:
            continue
        value = " ".join(container.get_text(" ", strip=True).split())
        label_text = " ".join(label_node.get_text(" ", strip=True).split())
        value = re.sub(rf"^{re.escape(label_text)}\s*:?\s*", "", value, flags=re.I)
        if value:
            fields[label] = value

    observed_name = fields.get("name", "")
    if not observed_name and (fields.get("first name") or fields.get("last name")):
        observed_name = " ".join(
            value for value in (fields.get("first name"), fields.get("last name")) if value
        )
    if observed_name and not _profile_name_compatible(member.name, observed_name):
        return "NAME_MISMATCH", "DIRECTORY_DETAIL_NAME_CONFLICT", member, {"fields": fields}

    role_text = next(
        (fields[key] for key in ("title", "role", "role s", "roles", "position") if fields.get(key)),
        member.title,
    )
    role = _FACULTY_ROLE.search(role_text or "")
    if not role:
        if role_text:
            return (
                "NOT_A_PERSON", "DIRECTORY_DETAIL_IS_NOT_FACULTY", member,
                {"fields": fields, "observed_role": role_text[:300]},
            )
        return "ROLE_UNCLEAR", "DIRECTORY_DETAIL_ROLE_NOT_ESTABLISHED", member, {"fields": fields}

    checked = replace(
        member,
        name=observed_name or member.name,
        title=role.group(0).strip(),
        appointment_type=_appointment_type(role.group(0)),
        email=fields.get("email", member.email),
        phone=fields.get("office phone", member.phone),
        office_address=fields.get("office address", fields.get("office", member.office_address)),
        section_heading=fields.get("department", fields.get("program", member.section_heading)),
    )
    evidence_text = " | ".join(f"{key}: {value}" for key, value in fields.items())
    return (
        "ROSTER_VERIFIED", "OFFICIAL_DIRECTORY_DETAIL_RECORD", checked,
        {
            "name": checked.name,
            "role": checked.title,
            "role_source": "official_roster_detail",
            "official_domain": institution_domain,
            "directory_detail_record": True,
            "evidence_excerpt": evidence_text[:900] or checked.excerpt[:900],
            "name_aliases": [checked.name],
            "paper_titles": [],
            "publication_entries": [],
            "fields": fields,
        },
    )


def validate_faculty_profile(
    html: str, profile_url: str, member: RosterMember,
    institution_name: str, institution_domain: str,
) -> tuple[str, str, RosterMember, dict[str, object]]:
    """Require attributable name, current role, and official institution support."""
    host = (urlparse(profile_url).hostname or "").casefold().removeprefix("www.")
    domain = institution_domain.casefold().removeprefix("www.")
    if not host or not domain or not (host == domain or host.endswith("." + domain)):
        return "INSTITUTION_UNRESOLVED", "PROFILE_OUTSIDE_REGISTERED_DOMAIN", member, {}
    soup = BeautifulSoup(html, "html.parser")
    title_text = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    root = _main_profile_content(soup)
    text = " ".join(root.get_text(" ", strip=True).split())
    # Only the page title and primary H1 identify the profile subject. H2s often
    # contain related stories or publication titles about somebody else.
    heading_values = [" ".join(node.get_text(" ", strip=True).split())
                      for node in root.find_all("h1")[:4]]
    identity_headings = " ".join(heading_values)
    historical_context = f"{title_text} {identity_headings} {text}"
    if _historical_about_subject(historical_context, member.name) or _HISTORICAL_PROFILE_PATH.search(urlparse(profile_url).path):
        return "HISTORICAL_PROFILE", "HISTORICAL_OR_MEMORIAL_PAGE", member, {}
    title_subject = re.split(r"\s+[|–—-]\s+", title_text, maxsplit=1)[0]
    identity_names = [title_subject, *heading_values]
    identity_text = f"{title_text} {identity_headings}"
    matched_profile_name = next(
        (candidate for candidate in identity_names
         if _looks_like_person_name(re.sub(r",?\s+(?:Ph\.?D\.?|M\.?D\.?)$", "", candidate, flags=re.I))
         and _profile_name_compatible(member.name, candidate)),
        "",
    )
    if (not matched_profile_name
            and canonical_name_key(member.name) not in canonical_name_key(identity_text + " " + text)):
        return "NAME_MISMATCH", "PROFILE_NAME_DOES_NOT_MATCH_ROSTER", member, {}
    profile_role = _FACULTY_ROLE.search(text)
    roster_role = _FACULTY_ROLE.search(member.title or "")
    if not profile_role and not roster_role:
        return "ROLE_UNCLEAR", "NO_EXPLICIT_CURRENT_FACULTY_ROLE", member, {}
    email_node = root.find("a", href=re.compile(r"^mailto:", re.I))
    email = str(email_node.get("href") or "")[7:].split("?", 1)[0] if email_node else member.email
    verified_name = re.sub(
        r",?\s+(?:Ph\.?D\.?|M\.?D\.?)$", "", matched_profile_name, flags=re.I
    ).strip() or member.name
    role_text = (profile_role or roster_role).group(0).strip()
    role_source = "official_profile" if profile_role else "official_roster"
    verified = replace(
        member, name=verified_name, title=role_text, email=email, profile_url=profile_url,
        appointment_type=_appointment_type(role_text),
    )
    signals = _profile_signals(root, verified, text)
    role_pattern = re.compile(re.escape(verified.title), re.I)
    name_pattern = re.compile(re.escape(member.name), re.I)
    evidence = {
        "name": member.name, "role": verified.title, "official_domain": domain,
        "role_source": role_source,
        "institution_named": institution_name.casefold() in text.casefold(),
        "email": email, "office_address": member.office_address,
        "evidence_excerpt": _bounded_evidence(text, [name_pattern, role_pattern]),
        **signals,
    }
    reason = (
        "OFFICIAL_PROFILE_NAME_ROLE_INSTITUTION"
        if profile_role else "OFFICIAL_ROSTER_ROLE_PROFILE_IDENTITY"
    )
    return "PROFILE_VERIFIED", reason, verified, evidence


def _ollama_card_source(member: RosterMember) -> str:
    """Keep one person's original fragment, never the complete directory page."""
    parts = [
        f"VISIBLE_CARD_TEXT: {member.excerpt}",
        f"EXTRACTED_NAME: {member.name}",
        f"EXTRACTED_ROLE: {member.title}",
        f"EXTRACTED_EMAIL: {member.email}",
        f"EXTRACTED_PROFILE_URL: {member.profile_url}",
        f"SECTION_HEADING: {member.section_heading}",
    ]
    if member.raw_card_html:
        parts.append(f"ORIGINAL_CARD_HTML:\n{member.raw_card_html}")
    return "\n".join(parts)[:24000]


def _card_requires_model_review(member: RosterMember) -> bool:
    """Reserve slow model work for missing or internally inconsistent cards."""
    if not member.title:
        return True
    tokens = name_tokens(member.name)
    identity_text = f"{member.email} {urlparse(member.profile_url).path}".casefold()
    if len(tokens) >= 2 and tokens[-1] not in canonical_name_key(identity_text):
        return True
    if member.raw_card_html:
        card = BeautifulSoup(member.raw_card_html, "html.parser")
        emails = {
            str(node.get("href") or "")[7:].split("?", 1)[0].casefold()
            for node in card.find_all("a", href=re.compile(r"^mailto:", re.I))
        }
        profile_links = {
            _canonical_profile_url(urljoin(member.profile_url, str(node.get("href") or "")))
            for node in card.find_all("a", href=True)
            if not str(node.get("href") or "").casefold().startswith(("mailto:", "tel:"))
        }
        if len(emails) > 1 or len({link for link in profile_links if link}) > 1:
            return True
    return False


def _apply_valid_card_review(
    member: RosterMember, model_review: OllamaReview,
) -> tuple[RosterMember, str | None, str | None]:
    """Use only quote-validated model output; conflicts can never be promoted."""
    if model_review.status != "VALID":
        return member, None, None
    data = model_review.data
    if data.get("card_boundary_valid") is False or data.get("identity_coherent") is False:
        return member, "NEEDS_REVIEW", "OLLAMA_CARD_IDENTITY_CONFLICT"
    record_type = str(data.get("record_type") or "").upper()
    if record_type in NONFACULTY_TYPES:
        return member, "NOT_A_PERSON", f"OLLAMA_EXPLICIT_{record_type}"
    role_text = str(data.get("role_exact") or "").strip()
    role = _FACULTY_ROLE.search(role_text)
    if not member.title and role:
        member = replace(
            member, title=role.group(0).strip(),
            appointment_type=_appointment_type(role.group(0)),
        )
    return member, None, None


def _profile_review_source(html: str, member: RosterMember) -> str:
    soup = BeautifulSoup(html, "html.parser")
    root = _main_profile_content(soup)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    main_text = " ".join(root.get_text(" ", strip=True).split())
    return (
        f"EXPECTED_ROSTER_NAME: {member.name}\n"
        f"EXPECTED_ROSTER_EMAIL: {member.email}\n"
        f"PROFILE_PAGE_TITLE: {title}\nPROFILE_MAIN_CONTENT:\n{main_text}"
    )[:24000]


def crawl_directory(
    directory_id: int,
    timeout: int = 30,
    progress_callback: Callable[[RosterMember, int, int], None] | None = None,
) -> dict[str, int]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT d.*, i.name AS institution_name, i.country_code, i.primary_domain
                   FROM faculty_directories d JOIN institutions i ON i.id = d.institution_id
                   WHERE d.id = %s AND d.active = TRUE
                     AND d.validation_status = 'APPROVED'
                     AND d.page_type IN ('DEPARTMENT_FACULTY_ROSTER',
                         'COLLEGE_FACULTY_ROSTER', 'UNIVERSITY_FACULTY_DIRECTORY',
                         'MIXED_PEOPLE_DIRECTORY')""",
                (directory_id,),
            )
            directory = cursor.fetchone()
    if not directory:
        raise ValueError(f"Active faculty directory {directory_id} was not found.")
    response = requests.get(
        str(directory["directory_url"]), timeout=timeout,
        headers={"User-Agent": "ScholarRadar/1.0 faculty-directory refresh"},
    )
    response.raise_for_status()
    members, validation_reason = validate_faculty_directory(
        response.text, str(directory["directory_url"]), fetch_embedded=True
    )
    if not members:
        raise RuntimeError(
            f"Directory validation failed ({validation_reason}); existing roster was left unchanged."
        )
    digest = hashlib.sha256(response.content).hexdigest()
    validated: list[tuple[RosterMember, str, str, dict[str, object]]] = []
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''SELECT id,canonical_profile_url,staff_overrides
                FROM roster_member_candidates WHERE directory_id=%s
                AND staff_overrides <> '{}'::jsonb''', (directory_id,))
            overrides = {row['staff_overrides'].get('_source_profile_key', row['canonical_profile_url']):
                         {**row['staff_overrides'], '_candidate_id': row['id']} for row in cursor.fetchall()}
    for member_index, member in enumerate(members, start=1):
        correction = overrides.get(_canonical_profile_url(member.profile_url))
        # A staff-retired observation is historical evidence, not permission
        # to overwrite a manually corrected current appointment on recrawl.
        if correction and correction.get('retired_observation') is True:
            continue
        if correction:
            member = replace(member, **{key: value for key, value in correction.items()
                                       if key in {'name','title','email','office_address','profile_url'}})
        if progress_callback:
            progress_callback(member, member_index, len(members))
        card_review = (
            review_with_ollama(
                source_type="ROSTER_CARD",
                source_record_key=f"{directory_id}:{_canonical_profile_url(member.profile_url)}",
                institution_id=int(directory["institution_id"]),
                institution=str(directory["institution_name"]),
                directory_url=str(directory["directory_url"]),
                source_text=_ollama_card_source(member),
            )
            if _card_requires_model_review(member)
            else OllamaReview("NOT_NEEDED", {})
        )
        member, early_status, early_reason = _apply_valid_card_review(member, card_review)
        if early_status:
            result = (
                early_status, str(early_reason), member,
                {"ollama_review": card_review.data, "ollama_status": card_review.status},
            )
        elif _same_directory_document(member.profile_url, str(directory["directory_url"])):
            try:
                detail_response = requests.get(
                    member.profile_url, timeout=timeout,
                    headers={"User-Agent": "ScholarRadar/2.0 official-directory-detail-validator"},
                )
                detail_response.raise_for_status()
                result = validate_directory_detail(
                    detail_response.text, member, str(directory["primary_domain"] or "")
                )
            except requests.RequestException as error:
                result = (
                    "ROSTER_CONFIRMED_PROFILE_UNAVAILABLE", type(error).__name__, member, {}
                )
        else:
            try:
                profile_response = requests.get(
                    member.profile_url, timeout=timeout,
                    headers={"User-Agent": "ScholarRadar/2.0 official-faculty-profile-validator"},
                )
                profile_response.raise_for_status()
                final_url = str(profile_response.url)
                result = validate_faculty_profile(
                    profile_response.text, final_url, replace(member, profile_url=final_url),
                    str(directory["institution_name"]), str(directory["primary_domain"] or ""),
                )
                if result[0] in {"NAME_MISMATCH", "ROLE_UNCLEAR"}:
                    profile_review = review_with_ollama(
                        source_type="FACULTY_PROFILE",
                        source_record_key=_canonical_profile_url(final_url),
                        institution_id=int(directory["institution_id"]),
                        institution=str(directory["institution_name"]),
                        directory_url=str(directory["directory_url"]),
                        source_text=_profile_review_source(profile_response.text, member),
                    )
                    evidence = dict(result[3])
                    evidence.update({
                        "ollama_review": profile_review.data,
                        "ollama_status": profile_review.status,
                        "ollama_errors": list(profile_review.errors),
                    })
                    result = (result[0], result[1], result[2], evidence)
            except requests.RequestException as error:
                result = (
                    "ROSTER_CONFIRMED_PROFILE_UNAVAILABLE", type(error).__name__, member, {}
                )
        status_code, reason, checked_member, profile_evidence = result
        profile_evidence = {
            **profile_evidence,
            "staff_candidate_id": correction.get('_candidate_id') if correction else None,
            "card_ollama_review": card_review.data,
            "card_ollama_status": card_review.status,
            "card_ollama_errors": list(card_review.errors),
        }
        validated.append((checked_member, status_code, reason, profile_evidence))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE faculty_directory_memberships
                   SET missing_checks = missing_checks + 1
                   WHERE directory_id = %s""",
                (directory_id,),
            )
            promoted = 0
            for member, profile_status, profile_reason, profile_evidence in validated:
                name_key = canonical_name_key(member.name)
                profile_key = _canonical_profile_url(member.profile_url)
                if profile_evidence.get('staff_candidate_id'):
                    cursor.execute('''UPDATE roster_member_candidates
                        SET canonical_profile_url=%s,profile_url=%s WHERE id=%s''',
                        (profile_key, member.profile_url, profile_evidence['staff_candidate_id']))
                cursor.execute(
                    """INSERT INTO roster_member_candidates (
                           directory_id, displayed_name, canonical_name_key,
                           displayed_title, department, profile_url,
                           canonical_profile_url, email, phone, office_address,
                           section_heading, appointment_type, source_excerpt,
                           validation_status, validation_reason, profile_evidence,
                           validation_version, checked_at, last_seen_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,2,NOW(),NOW())
                       ON CONFLICT (directory_id, canonical_profile_url) DO UPDATE SET
                           displayed_name=EXCLUDED.displayed_name,
                           displayed_title=EXCLUDED.displayed_title,
                           email=EXCLUDED.email, phone=EXCLUDED.phone,
                           office_address=EXCLUDED.office_address,
                           validation_status=EXCLUDED.validation_status,
                           validation_reason=EXCLUDED.validation_reason,
                           profile_evidence=EXCLUDED.profile_evidence,
                           validation_version=2,
                           checked_at=NOW(), last_seen_at=NOW()
                       RETURNING id""",
                    (directory_id, member.name, name_key, member.title,
                     directory["department"], member.profile_url, profile_key,
                     member.email or None, member.phone or None,
                     member.office_address or None, member.section_heading or None,
                     member.appointment_type, member.excerpt, profile_status,
                     profile_reason, json.dumps(profile_evidence)),
                )
                candidate_id = int(cursor.fetchone()["id"])
                if profile_status not in {"PROFILE_VERIFIED", "ROSTER_VERIFIED"}:
                    continue
                if not eligible_research_group_leader(
                    member.title,
                    member.appointment_type,
                    " | ".join(filter(None, (
                        member.excerpt,
                        str(profile_evidence.get("role") or ""),
                    ))),
                ):
                    cursor.execute(
                        """UPDATE roster_member_candidates
                           SET validation_status='NOT_GROUP_LEADING_FACULTY',
                               validation_reason=%s, professor_id=NULL,
                               checked_at=NOW()
                           WHERE id=%s""",
                        (
                            "Official roster role is valid, but it is not an eligible "
                            "research-group-leading professor appointment.",
                            candidate_id,
                        ),
                    )
                    if profile_key:
                        cursor.execute(
                            """UPDATE professors
                               SET faculty_status='NOT_FACULTY',
                                   employment_status='NONFACULTY_CONFIRMED',
                                   publication_status='NOT_APPLICABLE',
                                   updated_at=NOW()
                               WHERE canonical_profile_url=%s
                                 AND data_origin='OFFICIAL_DIRECTORY'
                               RETURNING id""",
                            (profile_key,),
                        )
                        ineligible = cursor.fetchone()
                        if ineligible:
                            cursor.execute(
                                """UPDATE radar_jobs
                                   SET status='cancelled', completed_at=NOW(),
                                       locked_at=NULL, locked_by=NULL, updated_at=NOW(),
                                       last_error='Cancelled: role is not an eligible group-leading professor.'
                                   WHERE professor_id=%s
                                     AND status IN ('queued','running')""",
                                (int(ineligible["id"]),),
                            )
                    continue
                status = "EMERITUS_CONFIRMED" if member.appointment_type == "EMERITUS" else "ACTIVE_CONFIRMED"
                existing = None
                if profile_key:
                    cursor.execute(
                        """SELECT id, name FROM professors
                           WHERE canonical_profile_url = %s
                           ORDER BY id LIMIT 1""",
                        (profile_key,),
                    )
                    existing = cursor.fetchone()
                if not existing and name_key:
                    cursor.execute(
                        """SELECT p.id, p.name
                           FROM professors p
                           JOIN faculty_directory_memberships membership
                             ON membership.professor_id = p.id
                           WHERE membership.directory_id = %s
                             AND p.canonical_name_key = %s
                           ORDER BY p.id""",
                        (directory_id, name_key),
                    )
                    same_roster = list(cursor.fetchall())
                    if len(same_roster) == 1:
                        existing = same_roster[0]
                stored_name = str(existing["name"]) if existing else member.name
                verification_method = (
                    "official_profile" if profile_status == "PROFILE_VERIFIED"
                    else "official_roster"
                )
                evidence_source_url = (
                    member.profile_url if profile_status == "PROFILE_VERIFIED" or profile_evidence.get('directory_detail_record')
                    else str(directory["directory_url"])
                )
                values = (
                    stored_name, directory["institution_id"], directory["institution_name"],
                    member.profile_url, member.title, evidence_source_url,
                    verification_method, status,
                    _canonical_rank(member.title), member.title, directory["department"],
                    name_key, profile_key,
                )
                if existing:
                    cursor.execute(
                        """UPDATE professors SET
                            institution_id = %s, institution_name = %s,
                            homepage_url = %s, faculty_status = 'VERIFIED',
                            faculty_title = %s, faculty_source_url = %s,
                            faculty_verification_method = %s,
                            faculty_verification_version = 21, faculty_confidence = 1.0,
                            faculty_checked_at = NOW(), faculty_verified_at = NOW(),
                            next_identity_check_at = NOW() + INTERVAL '30 days',
                            data_origin = 'OFFICIAL_DIRECTORY', employment_status = %s,
                            canonical_rank = %s, display_title = %s, department = %s,
                            roster_last_seen_at = NOW(), canonical_name_key = %s,
                            canonical_profile_url = COALESCE(canonical_profile_url, NULLIF(%s, '')),
                            updated_at = NOW()
                            WHERE id = %s RETURNING id""",
                        (
                            directory["institution_id"], directory["institution_name"],
                            member.profile_url, member.title, evidence_source_url,
                            verification_method, status,
                            _canonical_rank(member.title), member.title, directory["department"],
                            name_key, profile_key, int(existing["id"]),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                    INSERT INTO professors (
                        name, institution_id, institution_name, homepage_url,
                        faculty_status, faculty_title, faculty_source_url,
                        faculty_verification_method, faculty_verification_version,
                        faculty_confidence, faculty_checked_at, faculty_verified_at,
                        next_identity_check_at, data_origin, employment_status,
                        canonical_rank, display_title, department,
                        roster_verified_at, roster_last_seen_at,
                        canonical_name_key, canonical_profile_url
                    ) VALUES (%s, %s, %s, %s, 'VERIFIED', %s, %s,
                              %s, 21, 1.0, NOW(), NOW(),
                              NOW() + INTERVAL '30 days', 'OFFICIAL_DIRECTORY', %s,
                              %s, %s, %s, NOW(), NOW(), %s, NULLIF(%s, ''))
                    ON CONFLICT (name, institution_name) DO UPDATE SET
                        institution_id = EXCLUDED.institution_id,
                        homepage_url = EXCLUDED.homepage_url,
                        faculty_status = 'VERIFIED', faculty_title = EXCLUDED.faculty_title,
                        faculty_source_url = EXCLUDED.faculty_source_url,
                        faculty_verification_method = EXCLUDED.faculty_verification_method,
                        faculty_verification_version = 21, faculty_confidence = 1.0,
                        faculty_checked_at = NOW(), faculty_verified_at = NOW(),
                        next_identity_check_at = NOW() + INTERVAL '30 days',
                        data_origin = 'OFFICIAL_DIRECTORY', employment_status = EXCLUDED.employment_status,
                        canonical_rank = EXCLUDED.canonical_rank, display_title = EXCLUDED.display_title,
                        department = EXCLUDED.department, roster_last_seen_at = NOW(),
                        canonical_name_key = EXCLUDED.canonical_name_key,
                        canonical_profile_url = COALESCE(EXCLUDED.canonical_profile_url, professors.canonical_profile_url),
                        updated_at = NOW()
                    RETURNING id
                    """,
                        values,
                    )
                professor_id = int(cursor.fetchone()["id"])
                promoted += 1
                cursor.execute(
                    "UPDATE roster_member_candidates SET professor_id=%s WHERE id=%s",
                    (professor_id, candidate_id),
                )
                cursor.execute(
                    """INSERT INTO professor_name_aliases
                       (professor_id, alias, canonical_name_key, source_url, is_primary)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (professor_id, alias) DO UPDATE SET
                           canonical_name_key = EXCLUDED.canonical_name_key,
                           source_url = EXCLUDED.source_url, last_seen_at = NOW()""",
                    (professor_id, member.name, name_key, evidence_source_url,
                     member.name == stored_name),
                )
                for alias in profile_evidence.get("name_aliases") or []:
                    alias_key = canonical_name_key(str(alias))
                    if not alias_key:
                        continue
                    cursor.execute(
                        """INSERT INTO professor_name_aliases
                           (professor_id,alias,canonical_name_key,source_url,is_primary)
                           VALUES (%s,%s,%s,%s,FALSE)
                           ON CONFLICT (professor_id,alias) DO UPDATE SET
                             canonical_name_key=EXCLUDED.canonical_name_key,
                             source_url=EXCLUDED.source_url,last_seen_at=NOW()""",
                        (professor_id, str(alias), alias_key, evidence_source_url),
                    )
                cursor.execute(
                    """
                    INSERT INTO faculty_directory_memberships (
                        professor_id, directory_id, listed_name, listed_title,
                        listed_department, profile_url, appointment_type, source_excerpt
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (directory_id, profile_url) DO UPDATE SET
                        professor_id = EXCLUDED.professor_id, listed_name = EXCLUDED.listed_name,
                        listed_title = EXCLUDED.listed_title,
                        listed_department = EXCLUDED.listed_department,
                        appointment_type = EXCLUDED.appointment_type,
                        source_excerpt = EXCLUDED.source_excerpt,
                        currently_listed = TRUE, missing_checks = 0, last_seen_at = NOW()
                    """,
                    (professor_id, directory_id, member.name, member.title,
                     directory["department"], member.profile_url,
                     member.appointment_type, member.excerpt),
                )
                cursor.execute(
                    """
                    INSERT INTO faculty_appointments (
                        professor_id, institution_id, title, canonical_rank,
                        appointment_type, country_code, current, primary_appointment, source_url
                    ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT (professor_id, institution_id, title, source_url) DO UPDATE SET
                        current = TRUE, last_seen_at = NOW(), appointment_type = EXCLUDED.appointment_type
                    """,
                    (professor_id, directory["institution_id"], member.title,
                     _canonical_rank(member.title), member.appointment_type,
                     directory["country_code"], member.appointment_type == "PRIMARY",
                     evidence_source_url),
                )
                cursor.execute(
                    """
                    INSERT INTO faculty_verification_evidence (
                        professor_id, source_url, source_domain, observed_title,
                        observed_institution, evidence_text, verification_status,
                        confidence, decision_method, source_type, role_category,
                        observed_employer, currentness, lookup_status,
                        evidence_excerpt, supports_decision
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'VERIFIED', 1.0,
                              %s, 'OFFICIAL_UNIVERSITY_PAGE',
                              'FACULTY', %s, 'CURRENT', 'FOUND', %s, TRUE)
                    ON CONFLICT (professor_id, source_url) DO UPDATE SET
                        observed_title = EXCLUDED.observed_title,
                        observed_institution = EXCLUDED.observed_institution,
                        evidence_text = EXCLUDED.evidence_text,
                        confidence = 1.0, decision_method = EXCLUDED.decision_method,
                        source_type = 'OFFICIAL_UNIVERSITY_PAGE',
                        role_category = 'FACULTY', currentness = 'CURRENT',
                        lookup_status = 'FOUND', evidence_excerpt = EXCLUDED.evidence_excerpt,
                        supports_decision = TRUE, checked_at = NOW()
                    """,
                    (professor_id, evidence_source_url,
                     urlparse(evidence_source_url).hostname or "",
                     member.title, directory["institution_name"],
                     str(profile_evidence.get("evidence_excerpt") or member.excerpt),
                     verification_method,
                     directory["institution_name"],
                     str(profile_evidence.get("evidence_excerpt") or member.excerpt)),
                )
            cursor.execute(
                """UPDATE faculty_directory_memberships
                   SET currently_listed = FALSE
                   WHERE directory_id = %s AND missing_checks >= 2""",
                (directory_id,),
            )
            cursor.execute(
                """UPDATE faculty_directories SET content_hash = %s,
                   last_success_at = NOW(), last_error = NULL,
                   validation_status = 'APPROVED', validation_reason = NULL,
                   page_type = COALESCE(NULLIF(page_type, 'UNKNOWN'), 'UNIVERSITY_FACULTY_DIRECTORY'),
                   updated_at = NOW()
                   WHERE id = %s""",
                (digest, directory_id),
            )
    return {"directory_id": directory_id, "members_found": len(members),
            "profiles_verified": promoted, "profiles_pending": len(members) - promoted}
