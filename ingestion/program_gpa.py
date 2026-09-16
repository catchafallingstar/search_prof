from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.parse_hiring_signals import _fetch_with_safe_redirects, _host_matches_domain
from ingestion.websearch import SearchUnavailable, search_web


_HARD_MINIMUM = re.compile(
    r"(?:minimum|required|must have|at least).{0,80}\bGPA\b(?:\s+of)?\s*[:=]?\s*([234](?:\.\d{1,2})?)|"
    r"\bGPA\b.{0,80}(?:minimum|required|must be|at least)\s*[:=]?\s*([234](?:\.\d{1,2})?)",
    re.I,
)
_RECOMMENDED = re.compile(
    r"(?:recommended|prefer(?:red)?|normally|typically).{0,80}\bGPA\b.{0,50}([234](?:\.\d{1,2})?)|"
    r"\bGPA\b.{0,80}(?:recommended|prefer(?:red)?|normally|typically).{0,30}([234](?:\.\d{1,2})?)|"
    r"\bGPA\b(?:\s+of)?\s*([234](?:\.\d{1,2})?).{0,80}(?:recommended|prefer(?:red)?|normally|typically)",
    re.I,
)
_NO_MINIMUM = re.compile(r"(?:no|without)\s+(?:formal\s+)?(?:minimum|required)\s+GPA|GPA\s+(?:is\s+)?not\s+required", re.I)
_HOLISTIC = re.compile(r"holistic.{0,100}\bGPA\b|\bGPA\b.{0,100}holistic", re.I)


def extract_program_gpa(text: str) -> dict[str, Any] | None:
    """Extract one attributable admission requirement without treating silence as no cutoff."""
    chunks = [" ".join(value.split()) for value in re.split(r"(?<=[.!?])\s+|[\n\r]+", text)]
    chunks = [value for value in chunks if 15 < len(value) < 700 and re.search(r"\bGPA\b", value, re.I)]
    for chunk in chunks:
        match = _HARD_MINIMUM.search(chunk)
        if match:
            return {"policy": "HARD_MINIMUM", "minimum": float(next(v for v in match.groups() if v)), "evidence": chunk}
    for chunk in chunks:
        match = _RECOMMENDED.search(chunk)
        if match:
            return {"policy": "RECOMMENDED", "minimum": float(next(v for v in match.groups() if v)), "evidence": chunk}
    for chunk in chunks:
        if _NO_MINIMUM.search(chunk):
            return {"policy": "NO_FORMAL_MINIMUM", "minimum": None, "evidence": chunk}
        if _HOLISTIC.search(chunk):
            return {"policy": "HOLISTIC_REVIEW", "minimum": None, "evidence": chunk}
    return None


def _official_page(url: str, domain: str) -> tuple[str, bool]:
    host = (urlparse(url).hostname or "").casefold()
    if not _host_matches_domain(host, domain):
        return "", False
    try:
        response = _fetch_with_safe_redirects(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "noscript"]):
            element.decompose()
        return "\n".join(soup.stripped_strings)[:200_000], True
    except Exception:
        return "", False


def check_program_gpa_for_professor(professor_id: int) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT p.institution_id, p.institution_name,
                          COALESCE(p.department, '') AS department,
                          COALESCE(i.primary_domain, p.official_institution_domain, '') AS domain
                   FROM professors p JOIN institutions i ON i.id = p.institution_id
                   WHERE p.id = %s AND p.data_origin IN
                       ('OFFICIAL_DIRECTORY', 'OFFICIAL_PROFILE', 'MANUAL_REVIEW')""",
                (professor_id,),
            )
            professor = cursor.fetchone()
            if not professor:
                raise ValueError("Canonical professor was not found.")
            department_key = " ".join(str(professor["department"]).casefold().split())
            cursor.execute(
                """SELECT * FROM program_admission_requirements
                   WHERE institution_id = %s AND department_key = %s AND degree_type = 'PhD'""",
                (professor["institution_id"], department_key),
            )
            existing = cursor.fetchone()

    domain = str(professor["domain"] or "").strip()
    candidates: list[str] = []
    if existing and existing.get("source_url"):
        candidates.append(str(existing["source_url"]))
    search_error = ""
    if not candidates and domain:
        department = str(professor["department"] or "graduate").strip()
        query = f'site:{domain} "{department}" PhD admissions ("minimum GPA" OR "required GPA")'
        try:
            candidates.extend(
                str(item.get("href") or "") for item in search_web(query, max_results=10)
            )
        except SearchUnavailable as error:
            # Provider pacing is not evidence that an admission source is
            # unavailable. Let the durable worker reschedule for the next slot.
            raise error

    accessible = False
    evidence: dict[str, Any] | None = None
    evidence_url = ""
    for url in dict.fromkeys(candidates):
        text, fetched = _official_page(url, domain)
        accessible = accessible or fetched
        if not fetched:
            continue
        evidence = extract_program_gpa(text)
        if evidence:
            evidence_url = url
            break

    if evidence:
        policy, status = str(evidence["policy"]), "FOUND"
        next_interval = "180 days"
    elif accessible:
        policy, status = "NOT_STATED", "NOT_STATED"
        next_interval = "90 days"
    else:
        policy, status = "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE"
        next_interval = "7 days"
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO program_admission_requirements (
                        institution_id, department_key, department, degree_type,
                        policy, minimum_gpa, evidence_text, source_url,
                        check_status, checked_at, next_check_at, last_error
                    ) VALUES (%s, %s, %s, 'PhD', %s, %s, %s, %s, %s,
                              NOW(), NOW() + INTERVAL '{next_interval}', %s)
                    ON CONFLICT (institution_id, department_key, degree_type) DO UPDATE SET
                        department = EXCLUDED.department, policy = EXCLUDED.policy,
                        minimum_gpa = EXCLUDED.minimum_gpa,
                        evidence_text = EXCLUDED.evidence_text,
                        source_url = COALESCE(EXCLUDED.source_url, program_admission_requirements.source_url),
                        check_status = EXCLUDED.check_status, checked_at = NOW(),
                        next_check_at = EXCLUDED.next_check_at, last_error = EXCLUDED.last_error""",
                (professor["institution_id"], department_key, professor["department"] or None,
                 policy, (evidence or {}).get("minimum"), (evidence or {}).get("evidence"),
                 evidence_url or (candidates[0] if candidates else None), status,
                 search_error or None),
            )
    return {"institution": professor["institution_name"], "department": professor["department"],
            "policy": policy, "minimum_gpa": (evidence or {}).get("minimum"),
            "source_url": evidence_url or None}
