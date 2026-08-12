import hashlib
import re
from datetime import date, datetime
from typing import Any

import requests

from db import get_db_connection

NSF_API_URL = "https://api.nsf.gov/services/v1/awards.json"


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


def _parse_nsf_date(value: Any) -> date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def check_and_save_grants(tax_meta: dict[str, Any]) -> dict[str, int]:
    """Check NSF only. Other agencies require separate provider modules."""
    router = tax_meta.get("router_config") or {}
    if router.get("primary_agency") != "NSF":
        print(f"Skipping grants: {router.get('primary_agency', 'unknown')} needs its own API connector.")
        return {"professors_checked": 0, "grants_added": 0}

    research_domain = str(tax_meta.get("topic_name") or "").strip()
    if not research_domain:
        return {"professors_checked": 0, "grants_added": 0}

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            # A radar run must not recheck every professor accumulated by every
            # previous research-area scan.
            cursor.execute(
                """
                SELECT id, name, institution_name
                FROM professors
                WHERE research_domain = %s
                ORDER BY id
                """,
                (research_domain,),
            )
            professors = list(cursor.fetchall())
            grants_added = 0
            for professor in professors:
                try:
                    response = requests.get(
                        NSF_API_URL,
                        params={
                            "pdPIName": professor["name"],
                            "ActiveAwards": "true",
                            "rpp": 25,
                        },
                        timeout=15,
                    )
                    response.raise_for_status()
                except requests.RequestException as error:
                    print(f"NSF request failed for {professor['name']}: {error}")
                    continue

                score_boost = 0
                for award in response.json().get("response", {}).get("award", []):
                    pi_name = str(award.get("pdPIName") or " ".join(award.get("pi") or []))
                    if not _person_matches(professor["name"], pi_name):
                        continue
                    if not _institution_matches(professor["institution_name"], str(award.get("awardeeName") or "")):
                        continue
                    expiration_date = _parse_nsf_date(award.get("expDate"))
                    if expiration_date and expiration_date < date.today():
                        continue
                    title = str(award.get("title") or "Untitled NSF award")
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
                    cursor.execute(
                        """
                        INSERT INTO fundings (
                            professor_id, funding_hash, grant_title, grant_id, funder,
                            amount, award_date, expiration_date, source_url
                        ) VALUES (%s, %s, %s, %s, 'NSF', %s, %s, %s, %s)
                        ON CONFLICT (funding_hash) DO NOTHING
                        """,
                        (
                            professor["id"], funding_hash, title, grant_id, amount,
                            _parse_nsf_date(award.get("startDate")), expiration_date,
                            f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={grant_id}" if grant_id else None,
                        ),
                    )
                    if cursor.rowcount:
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
    return {"professors_checked": len(professors), "grants_added": grants_added}
