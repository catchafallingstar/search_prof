"""Discover official faculty rosters from a registered US university domain."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.faculty_roster import classify_faculty_page
from ingestion.websearch import search_web


DIRECTORY_HINT = re.compile(
    r"(?:^|[/_-])(?:faculty|people|directory|faculty-and-staff|our-faculty)(?:[/_.-]|$)",
    re.IGNORECASE,
)
ACADEMIC_UNIT_HINT = re.compile(
    r"\b(?:college|school|department|program)\b", re.IGNORECASE
)
REJECT_PATH = re.compile(
    r"/(?:news|stories?|events?|awards?|honors?|alumni|archive|media-experts?|"
    r"research|publications?|jobs?|core-facilities)(?:/|$)|"
    r"(?:faculty[-_]?led[-_]?programs?|disability[-_]?support|"
    r"faculty[-_]?staff[-_]?associations?|united[-_]?way[-_]?campaign|"
    r"faculty[-_]?resources?)",
    re.IGNORECASE,
)


def _official_host(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    domain = domain.casefold().removeprefix("www.")
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def _candidate_url(url: str, domain: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not _official_host(url, domain):
        return ""
    if REJECT_PATH.search(parsed.path) or not DIRECTORY_HINT.search(parsed.path):
        return ""
    return parsed._replace(fragment="", query="").geturl().rstrip("/")


def _links_from_html(html: str, base_url: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    values: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        url = _candidate_url(urljoin(base_url, str(anchor.get("href") or "")), domain)
        if url and re.search(
            r"\b(?:faculty (?:and|&) staff directory|faculty directory|"
            r"staff directory|people directory|our faculty|faculty and staff|directory)\b",
            label, re.I,
        ):
            values.append(url)
    return list(dict.fromkeys(values))


def _sitemap_candidates(domain: str, timeout: int = 20) -> list[str]:
    candidates: list[str] = []
    headers = {"User-Agent": "ScholarRadar/2.0 official-faculty-roster-indexer"}
    pending = [f"https://{domain}/sitemap.xml", f"https://{domain}/sitemap_index.xml"]
    visited: set[str] = set()
    while pending and len(visited) < 30 and len(candidates) < 150:
        sitemap_url = pending.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        try:
            response = requests.get(sitemap_url, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        soup = BeautifulSoup(response.text, "xml")
        locations = [item.get_text(strip=True) for item in soup.find_all("loc")]
        for location in locations[:10_000]:
            if location.casefold().endswith((".xml", ".xml.gz")) and _official_host(location, domain):
                pending.append(location)
                continue
            candidate = _candidate_url(location, domain)
            if candidate:
                candidates.append(candidate)
    return list(dict.fromkeys(candidates))[:150]


def _homepage_candidates(domain: str, timeout: int = 20) -> list[str]:
    url = f"https://{domain}/"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ScholarRadar/2.0 official-faculty-roster-indexer"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException:
        return []
    return _links_from_html(response.text, url, domain)[:30]


def _discover_academic_units(institution_id: int, domain: str, timeout: int = 20) -> list[str]:
    """Enumerate official schools/departments before looking for their rosters."""
    home = f"https://{domain}/"
    headers = {"User-Agent": "ScholarRadar/2.0 academic-unit-indexer"}
    pending = [home]
    units: dict[str, tuple[str, str]] = {}
    visited: set[str] = set()
    while pending and len(visited) < 12 and len(units) < 80:
        page_url = pending.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            response = requests.get(page_url, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            label = " ".join(anchor.get_text(" ", strip=True).split())
            url = urljoin(str(response.url), str(anchor.get("href") or ""))
            if not _official_host(url, domain):
                continue
            if re.search(r"\b(?:academics|colleges and schools|schools and colleges)\b", label, re.I):
                pending.append(url)
            match = ACADEMIC_UNIT_HINT.search(label)
            if not match or len(label) > 180:
                continue
            kind = match.group(0).upper()
            units[url.rstrip("/")] = (label, kind)
    if units:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                for url, (label, kind) in units.items():
                    cursor.execute(
                        """INSERT INTO institution_units
                           (institution_id,unit_name,unit_type,official_url)
                           VALUES (%s,%s,%s,%s)
                           ON CONFLICT (institution_id,official_url) DO UPDATE SET
                             unit_name=EXCLUDED.unit_name,unit_type=EXCLUDED.unit_type,
                             active=TRUE,updated_at=NOW()""",
                        (institution_id, label, kind, url),
                    )
    return list(units)[:80]


def _academic_unit_roster_candidates(unit_urls: list[str], domain: str) -> list[str]:
    candidates: list[str] = []
    headers = {"User-Agent": "ScholarRadar/2.0 academic-unit-roster-indexer"}
    for unit_url in unit_urls[:40]:
        try:
            response = requests.get(unit_url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            continue
        candidates.extend(_links_from_html(response.text, str(response.url), domain))
    return list(dict.fromkeys(candidates))[:100]


def _search_candidates(name: str, domain: str) -> list[str]:
    query = f'site:{domain} "faculty" (directory OR people OR "faculty and staff")'
    candidates: list[str] = []
    for result in search_web(query, max_results=10):
        candidate = _candidate_url(str(result.get("href") or ""), domain)
        if candidate:
            candidates.append(candidate)
    return list(dict.fromkeys(candidates))


def _department_name(url: str, title: str) -> str:
    """Infer an academic unit, never a generic page/navigation title."""
    cleaned = re.sub(r"\s+", " ", title).strip(" |-–—")
    # CMS titles commonly look like
    # "Directory | Department of Physics | UC Santa Barbara" or
    # "Faculty Directory - Cornell Law School". Prefer the academic-unit
    # segment and discard generic navigation labels.
    segments = [part.strip(" |-–—") for part in re.split(r"\s*[|–—]\s*|\s+-\s+", cleaned) if part.strip()]
    generic = re.compile(r"^(?:faculty(?:\s*&\s*staff)?\s+)?directory$|^contact us$|^people$", re.I)
    unit = re.compile(r"\b(?:department|school|college|program|division|faculty)\b", re.I)
    candidates = [segment for segment in segments if not generic.fullmatch(segment) and unit.search(segment)]
    if candidates:
        # University branding is usually the last segment; the most specific
        # academic unit tends to appear earlier.
        return min(candidates, key=lambda value: ("university" in value.casefold(), len(value)))[:160]

    parts = [part.replace("-", " ").replace("_", " ") for part in urlparse(url).path.split("/") if part]
    for part in reversed(parts[:-1] if len(parts) > 1 else parts):
        normalized = " ".join(part.split()).strip()
        if normalized and not re.fullmatch(r"(?:faculty|staff|people|directory|contact)", normalized, re.I):
            return normalized.title()[:160]
    return "University faculty"


def _official_directory_alias(html: str, page_url: str, domain: str) -> str:
    """Follow an explicit same-university shortlink used by legacy CMS pages."""
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.find("a", id=re.compile(r"^shortlink$", re.I))
    if not anchor or not anchor.get("href"):
        return ""
    target = urljoin(page_url, str(anchor.get("href")))
    path = urlparse(target).path
    if not _official_host(target, domain) or not re.search(
        r"/(?:faculty|staff|people|directory)(?:[/_.-]|$)", path, re.I
    ):
        return ""
    return target.rstrip("/")


def discover_faculty_directories(
    institution_id: int,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, object]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, name, primary_domain FROM institutions
                   WHERE id = %s AND country_code = 'US'
                     AND organization_type = 'HIGHER_EDUCATION'""",
                (institution_id,),
            )
            institution = cursor.fetchone()
    if not institution or not institution.get("primary_domain"):
        raise RuntimeError("Institution does not have a usable official US university domain.")

    name = str(institution["name"])
    domain = str(institution["primary_domain"])
    unit_urls = _discover_academic_units(institution_id, domain)
    direct = (
        _academic_unit_roster_candidates(unit_urls, domain)
        + _sitemap_candidates(domain)
        + _homepage_candidates(domain)
    )
    # Search is a recovery path, not the foundation. A university with usable
    # official sitemap/navigation candidates does not consume a DDGS request.
    direct_candidates = list(dict.fromkeys(direct))[:150]
    candidate_groups = [direct_candidates] if direct_candidates else [_search_candidates(name, domain)]
    approved: list[dict[str, object]] = []
    rejected = 0
    checked = 0
    headers = {"User-Agent": "ScholarRadar/2.0 official-faculty-roster-indexer"}
    for group_index, candidates in enumerate(candidate_groups):
        for index, url in enumerate(candidates, start=1):
            checked += 1
            if progress_callback:
                progress_callback(url, index, len(candidates))
            try:
                response = requests.get(url, headers=headers, timeout=25)
                response.raise_for_status()
                final_url = str(response.url)
                alias_url = _official_directory_alias(response.text, final_url, domain)
                if alias_url and alias_url.rstrip("/") != final_url.rstrip("/"):
                    alias_response = requests.get(alias_url, headers=headers, timeout=25)
                    alias_response.raise_for_status()
                    response = alias_response
                    final_url = str(response.url)
                classification = classify_faculty_page(
                    response.text, final_url, fetch_embedded=True
                )
            except requests.RequestException as error:
                status_code = getattr(getattr(error, "response", None), "status_code", None)
                terminal = status_code in {404, 410}
                with get_db_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """INSERT INTO faculty_page_candidates
                               (institution_id,candidate_url,final_url,discovery_method,
                                page_type,classification_status,classification_reason,checked_at)
                               VALUES (%s,%s,%s,%s,'UNKNOWN',%s,%s,NOW())
                               ON CONFLICT (institution_id,candidate_url) DO UPDATE SET
                                 classification_status=EXCLUDED.classification_status,
                                 classification_reason=EXCLUDED.classification_reason,
                                 checked_at=NOW(),updated_at=NOW()""",
                            (institution_id, url, url,
                             "OFFICIAL_NAVIGATION" if url in direct_candidates else "DDGS_RECOVERY",
                             "NOT_A_ROSTER" if terminal else "FETCH_FAILED",
                             f"HTTP_{status_code}" if status_code else type(error).__name__),
                        )
                rejected += 1
                continue
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO faculty_page_candidates
                           (institution_id,candidate_url,final_url,discovery_method,page_type,
                            scope_label,classification_status,classification_reason,evidence,checked_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())
                           ON CONFLICT (institution_id,candidate_url) DO UPDATE SET
                             final_url=EXCLUDED.final_url,page_type=EXCLUDED.page_type,
                             scope_label=EXCLUDED.scope_label,
                             classification_status=EXCLUDED.classification_status,
                             classification_reason=EXCLUDED.classification_reason,
                             evidence=EXCLUDED.evidence,checked_at=NOW(),updated_at=NOW()
                           RETURNING id""",
                        (institution_id, url, final_url,
                         "OFFICIAL_NAVIGATION" if url in direct_candidates else "DDGS_RECOVERY",
                         classification.page_type, classification.scope_label,
                         classification.status, classification.reason,
                         json.dumps({"sample_names":[member.name for member in classification.members[:10]],
                                     "member_count":len(classification.members)})),
                    )
                    page_candidate_id = int(cursor.fetchone()["id"])
            if classification.status != "APPROVED_ROSTER":
                rejected += 1
                continue
            members = list(classification.members)
            soup = BeautifulSoup(response.text, "html.parser")
            title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO faculty_directories
                       (institution_id, department, directory_url, directory_type,
                        parser_type, validation_status, validation_reason,
                        discovered_by, expected_profile_count, active,
                        page_candidate_id, page_type)
                       VALUES (%s, %s, %s, 'DEPARTMENT_FACULTY', 'GENERIC_HTML',
                               'APPROVED', NULL, 'UNIVERSITY_DISCOVERY', %s, TRUE,
                               %s, %s)
                       ON CONFLICT (directory_url) DO UPDATE SET
                           institution_id = EXCLUDED.institution_id,
                           department = EXCLUDED.department,
                           validation_status = 'APPROVED', validation_reason = NULL,
                           expected_profile_count = EXCLUDED.expected_profile_count,
                           active = TRUE, page_candidate_id=EXCLUDED.page_candidate_id,
                           page_type=EXCLUDED.page_type, updated_at = NOW()
                       RETURNING id""",
                        (institution_id, _department_name(final_url, title), final_url,
                         len(members), page_candidate_id, classification.page_type),
                    )
                    directory_id = int(cursor.fetchone()["id"])
            approved.append({"directory_id": directory_id, "url": url, "members": len(members)})
        if approved:
            break
        if group_index == 0 and direct_candidates:
            # Sitemap/navigation candidates existed but none was a roster.
            # Spend one DDGS request only now, as a recovery path.
            candidate_groups.append(_search_candidates(name, domain))

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE institutions SET faculty_discovery_status = %s,
                   faculty_discovery_checked_at = NOW(),
                   faculty_discovery_next_at = NOW() + INTERVAL '90 days',
                   faculty_discovery_error = NULL
                   WHERE id = %s""",
                ("DIRECTORIES_FOUND" if approved else "NO_DIRECTORY_FOUND", institution_id),
            )
    return {
        "institution_id": institution_id,
        "institution": name,
        "candidates_checked": checked,
        "directories": approved,
        "rejected_candidates": rejected,
    }
