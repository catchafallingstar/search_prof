import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

import requests

from db import get_db_connection
from ingestion.orcid_evidence import normalize_orcid
from ingestion.taxonomy import phrase_covers_query
from settings import setting, setting_bool

NSF_API_URL = "https://api.nsf.gov/services/v1/awards.json"
NIH_REPORTER_API_URL = "https://api.reporter.nih.gov/v2/projects/search"
ORCID_RECORD_URL = "https://pub.orcid.org/v3.0/{orcid}/record"


def get_funding_hash(professor_id: int, grant_id: str, award_title: str) -> str:
    raw = f"{professor_id}|{grant_id.strip()}|{award_title.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_name(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2}


def _person_matches(expected: str, actual: str) -> bool:
    """Require the expected family name and at least one given-name token."""
    expected_parts = re.findall(r"[a-z0-9]+", expected.casefold())
    actual_parts = set(re.findall(r"[a-z0-9]+", actual.casefold()))
    if not expected_parts or not actual_parts:
        return False
    if len(expected_parts) == 1:
        return expected_parts[0] in actual_parts
    family_name = expected_parts[-1]
    given_names = set(expected_parts[:-1])
    return family_name in actual_parts and bool(given_names & actual_parts)


def _institution_matches(expected: str, actual: str) -> bool:
    expected_tokens = _normalize_name(expected) - {"university", "college", "institute", "the"}
    actual_tokens = _normalize_name(actual) - {"university", "college", "institute", "the"}
    return bool(expected_tokens and len(expected_tokens & actual_tokens) >= min(2, len(expected_tokens)))


def _grant_matches_domain(raw_query: str, award: dict[str, Any], related_queries: list[str] | None = None) -> bool:
    """Require topical grant evidence, not merely any award held by the professor."""
    evidence = " ".join(
        str(award.get(key) or "")
        for key in ("title", "abstractText", "fundProgramName")
    )
    if related_queries:
        return any(_grant_matches_domain(query, award) for query in [raw_query, *related_queries])
    query_tokens = set(re.findall(r"[a-z0-9]+", raw_query.casefold()))
    if "security" in query_tokens and (
        "ai" in query_tokens
        or {"artificial", "intelligence"}.issubset(query_tokens)
    ):
        return bool(
            re.search(
                r"\b(?:adversarial|attack|cybersecurity|privacy|robustness|secure|security|threat)\w*\b",
                evidence,
                re.IGNORECASE,
            )
        )
    return phrase_covers_query(raw_query, evidence)


def _parse_nsf_date(value: Any) -> date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def _nih_awards_for_professor(professor: dict[str, Any]) -> list[dict[str, Any]]:
    """Return NIH RePORTER projects in the same shape used by NSF matching.

    RePORTER supports PI-name searches, unlike broad federal-spending data.
    Results still pass the same PI, institution, active-date, and topical
    checks below before they can be saved.
    """
    name_parts = str(professor.get("name") or "").split()
    if not name_parts:
        return []
    response = requests.post(
        NIH_REPORTER_API_URL,
        json={
            "criteria": {
                "pi_names": [{
                    "any_name": name_parts[-1],
                    "first_name": " ".join(name_parts[:-1]),
                }],
                "include_active_projects": True,
            },
            "offset": 0,
            "limit": 50,
        },
        timeout=8,
    )
    response.raise_for_status()
    projects = list(response.json().get("results") or [])
    awards: list[dict[str, Any]] = []
    for project in projects:
        investigators = project.get("principal_investigators") or project.get("principal_investigator") or []
        if isinstance(investigators, dict):
            investigators = [investigators]
        pi_names = [
            str(item.get("full_name") or item.get("name") or "")
            for item in investigators if isinstance(item, dict)
        ]
        project_number = str(
            project.get("core_project_num") or project.get("project_num") or ""
        )
        organization = project.get("organization") or {}
        if not isinstance(organization, dict):
            organization = {}
        awards.append({
            "id": project_number,
            "title": str(project.get("project_title") or "Untitled NIH project"),
            "abstractText": str(project.get("abstract_text") or ""),
            "fundProgramName": str(project.get("activity_code") or "NIH"),
            "pdPIName": " | ".join(pi_names),
            "awardeeName": str(organization.get("org_name") or project.get("org_name") or ""),
            "fundsObligatedAmt": project.get("award_amount") or project.get("total_cost"),
            "startDate": project.get("project_start_date"),
            "expDate": project.get("project_end_date"),
            "_source": "NIH RePORTER",
            "_source_url": (
                f"https://reporter.nih.gov/project-details/{project_number}"
                if project_number else None
            ),
        })
    return awards


def _orcid_awards_for_professor(professor: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize public, self-reported ORCID funding for a known ORCID iD."""
    if not setting_bool("ORCID_ENABLED", False) or not setting("ORCID_ACCESS_TOKEN").strip():
        return []
    orcid = normalize_orcid(professor.get("orcid_id") or "")
    if not orcid:
        return []
    headers = {"Accept": "application/json", "User-Agent": "ScholarRadar/1.0 (grant indexer)"}
    token = setting("ORCID_ACCESS_TOKEN").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(ORCID_RECORD_URL.format(orcid=orcid), headers=headers, timeout=8)
    response.raise_for_status()
    activities = response.json().get("activities-summary") or {}
    groups = (activities.get("fundings") or {}).get("group") or []
    awards: list[dict[str, Any]] = []
    for group in groups:
        summaries = group.get("funding-summary") or []
        for summary in summaries:
            title = str((summary.get("title") or {}).get("title") or "").strip()
            organization = summary.get("organization") or {}
            external_ids = (summary.get("external-ids") or {}).get("external-id") or []
            grant_id = next((str(item.get("external-id-value") or "") for item in external_ids if item.get("external-id-value")), "")
            put_code = str(summary.get("put-code") or "")
            awards.append({
                "id": grant_id or f"ORCID:{orcid}:{put_code}",
                "title": title or "Untitled ORCID funding",
                "abstractText": title,
                "fundProgramName": str((summary.get("type") or "").replace("_", " ")),
                "pdPIName": professor["name"],
                "awardeeName": str(organization.get("name") or professor["institution_name"]),
                "startDate": (summary.get("start-date") or {}).get("year", {}).get("value"),
                "expDate": (summary.get("end-date") or {}).get("year", {}).get("value"),
                "_source": "ORCID public funding",
                "_source_url": f"https://orcid.org/{orcid}",
            })
    return awards


def check_and_save_grants(
    tax_meta: dict[str, Any],
    professor_ids: list[int],
) -> dict[str, Any]:
    """Check active NSF awards for an explicit, topic-scoped professor set."""
    research_domain = str(tax_meta.get("topic_name") or "").strip()
    raw_query = str(tax_meta.get("raw_query") or research_domain).strip()
    related_queries = [str(q).strip() for q in (tax_meta.get("search_queries") or []) if str(q).strip() and str(q).strip().casefold() != raw_query.casefold()]
    if not research_domain:
        return {"professors_checked": 0, "grants_added": 0, "results": [], "source_checks": []}

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if not professor_ids:
                return {"professors_checked": 0, "grants_added": 0, "results": [], "source_checks": []}
            cursor.execute(
                """
                SELECT id, name, institution_name, orcid_id
                FROM professors
                WHERE id = ANY(%s)
                ORDER BY id
                """,
                (professor_ids,),
            )
            professors = list(cursor.fetchall())

    def fetch_awards(
        professor: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, list[dict[str, Any]]]:
        source_checks: list[dict[str, Any]] = []
        try:
            response = requests.get(
                NSF_API_URL,
                params={
                    "pdPIName": professor["name"],
                    "ActiveAwards": "true",
                    "rpp": 25,
                },
                timeout=8,
            )
            response.raise_for_status()
            awards = list(response.json().get("response", {}).get("award", []))
            for award in awards:
                award["_source"] = "NSF"
            source_checks.append({"professor_id": professor["id"], "source": "NSF", "status": "CHECKED"})
            try:
                awards.extend(_nih_awards_for_professor(professor))
                source_checks.append({"professor_id": professor["id"], "source": "NIH_REP", "status": "CHECKED"})
            except requests.RequestException as error:
                print(f"NIH RePORTER request failed for {professor['name']}: {error}")
                source_checks.append({"professor_id": professor["id"], "source": "NIH_REP", "status": "SOURCE_UNAVAILABLE", "error": str(error)[:1000]})
            try:
                if normalize_orcid(professor.get("orcid_id") or ""):
                    awards.extend(_orcid_awards_for_professor(professor))
                    configured = setting_bool("ORCID_ENABLED", False) and bool(setting("ORCID_ACCESS_TOKEN").strip())
                    source_checks.append({"professor_id": professor["id"], "source": "ORCID", "status": "CHECKED" if configured else "DISABLED"})
                else:
                    source_checks.append({"professor_id": professor["id"], "source": "ORCID", "status": "NOT_APPLICABLE"})
            except requests.RequestException as error:
                print(f"ORCID funding request failed for {professor['name']}: {error}")
                source_checks.append({"professor_id": professor["id"], "source": "ORCID", "status": "SOURCE_UNAVAILABLE", "error": str(error)[:1000]})
            return professor, awards, None, source_checks
        except requests.RequestException as error:
            print(f"NSF request failed for {professor['name']}: {error}")
            source_checks.append({"professor_id": professor["id"], "source": "NSF", "status": "SOURCE_UNAVAILABLE", "error": str(error)[:1000]})
            return professor, [], str(error), source_checks

    fetched: list[tuple[dict[str, Any], list[dict[str, Any]], str | None, list[dict[str, Any]]]] = []
    if professors:
        with ThreadPoolExecutor(max_workers=min(4, len(professors))) as executor:
            futures = [executor.submit(fetch_awards, professor) for professor in professors]
            for future in as_completed(futures):
                fetched.append(future.result())

    grants_added = 0
    checks: list[dict[str, Any]] = []
    source_checks: list[dict[str, Any]] = []
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for professor, awards, source_error, professor_source_checks in fetched:
                source_checks.extend(professor_source_checks)
                score_boost = 0
                check = {
                    "professor_id": int(professor["id"]),
                    "professor_name": professor["name"],
                    "status": "SOURCE_UNAVAILABLE" if source_error else "NO_MATCH",
                    "error": source_error,
                    "grants": [],
                }
                for award in awards:
                    pi_name = str(award.get("pdPIName") or " ".join(award.get("pi") or []))
                    if not _person_matches(professor["name"], pi_name):
                        continue
                    if not _institution_matches(professor["institution_name"], str(award.get("awardeeName") or "")):
                        continue
                    expiration_date = _parse_nsf_date(award.get("expDate"))
                    if expiration_date and expiration_date < date.today():
                        continue
                    source = str(award.get("_source") or "NSF")
                    title = str(award.get("title") or f"Untitled {source} award")
                    if not _grant_matches_domain(raw_query, award, related_queries):
                        continue
                    grant_id = str(award.get("id") or "")
                    try:
                        amount = float(
                            award.get("fundsObligatedAmt")
                            or award.get("estimatedTotalAmt")
                            or 0
                        )
                    except (TypeError, ValueError):
                        amount = None
                    funding_hash = get_funding_hash(professor["id"], grant_id, title)
                    check["grants"].append(
                        {
                            "title": title,
                            "funder": source,
                            "grant_id": grant_id,
                            "amount": amount,
                            "expiration_date": expiration_date,
                            "source_url": award.get("_source_url") or (
                                f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={grant_id}"
                                if grant_id else None
                            ),
                        }
                    )
                    check["status"] = "MATCH_FOUND"
                    cursor.execute(
                        """
                        INSERT INTO fundings (
                            professor_id, funding_hash, grant_title, grant_id, funder,
                            amount, award_date, expiration_date, source_url,
                            research_domains
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, ARRAY[%s])
                        ON CONFLICT (funding_hash) DO UPDATE
                        SET research_domains = ARRAY(
                            SELECT DISTINCT unnest(
                                fundings.research_domains || EXCLUDED.research_domains
                            )
                        )
                        RETURNING (xmax = 0) AS inserted
                        """,
                        (
                            professor["id"], funding_hash, title, grant_id, source, amount,
                            _parse_nsf_date(award.get("startDate")), expiration_date,
                            award.get("_source_url") or (f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={grant_id}" if grant_id else None),
                            research_domain,
                        ),
                    )
                    saved_funding = cursor.fetchone()
                    if saved_funding and saved_funding["inserted"]:
                        grants_added += 1
                        upper_title = title.upper()
                        score_boost += 40 if "CRII" in upper_title else 30 if "CAREER" in upper_title else 15
                if score_boost:
                    cursor.execute(
                        """
                        UPDATE professors
                        SET radar_score = radar_score + %s,
                            score_breakdown = score_breakdown || %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (score_boost, f" +{score_boost} (active NSF awards)", professor["id"]),
                    )
                checks.append(check)
    return {
        "professors_checked": len(professors),
        "grants_added": grants_added,
        "results": checks,
        "source_checks": source_checks,
    }
