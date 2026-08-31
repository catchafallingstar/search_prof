"""Optional, cached ORCID identity clues, never a faculty-role decision."""
from datetime import datetime, timedelta, timezone
import re
import requests
from psycopg.types.json import Jsonb

from db import get_db_connection
from settings import setting, setting_bool


def normalize_orcid(value: str) -> str:
    value = re.sub(r"^https?://orcid\.org/", "", str(value or "").strip()).upper()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        return ""
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    return value if digits[-1] == ("X" if check == 10 else str(check)) else ""


def extract_orcid_clues(payload: dict) -> dict:
    person = payload.get("person") or {}
    name = person.get("name") or {}
    full_name = " ".join(str((name.get(key) or {}).get("value") or "") for key in ("given-names", "family-name")).strip()
    links = [str((item.get("url") or {}).get("value") or "")
             for item in (person.get("researcher-urls") or {}).get("researcher-url", [])]
    employments = []
    activity = payload.get("activities-summary") or {}
    for group in (activity.get("employments") or {}).get("affiliation-group", []):
        for item in group.get("summaries", []):
            entry = item.get("employment-summary") or {}
            source = entry.get("source") or {}
            employments.append({
                "institution": (entry.get("organization") or {}).get("name"),
                "role": entry.get("role-title"),
                "start_date": entry.get("start-date"),
                "end_date": entry.get("end-date"),
                "source": (source.get("source-name") or {}).get("value"),
                "organization_asserted": bool(source.get("source-client-id")),
            })
            url = (entry.get("url") or {}).get("value")
            if url:
                links.append(str(url))
    return {"name": full_name, "links": list(dict.fromkeys(links))[:5], "employments": employments}


def fetch_orcid_clues(candidate: dict) -> dict:
    if not setting_bool("ORCID_ENABLED", False):
        return {}
    orcid = normalize_orcid(candidate.get("orcid_id") or candidate.get("orcid") or "")
    if not orcid:
        return {}  # Do not guess identities or issue name searches.
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT result_json FROM identity_orcid_cache WHERE orcid_id = %s AND expires_at > NOW()", (orcid,))
                row = cursor.fetchone()
                if row:
                    return dict(row["result_json"])
    except Exception:
        return {}  # No unbounded requests during a DB/schema outage.
    headers = {"Accept": "application/json", "User-Agent": "ScholarRadar/1.0 (identity clues)"}
    token = setting("ORCID_ACCESS_TOKEN").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    result = {}
    days = 1
    try:
        response = requests.get(f"https://pub.orcid.org/v3.0/{orcid}/record", headers=headers,
                                timeout=8, allow_redirects=False)
        response.raise_for_status()
        result = extract_orcid_clues(response.json())
        result["orcid_id"] = orcid
        days = 30
    except (requests.RequestException, ValueError, TypeError):
        pass  # Optional evidence unavailable; never a negative identity decision.
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO identity_orcid_cache(orcid_id, result_json, expires_at)
                   VALUES (%s, %s, %s) ON CONFLICT (orcid_id) DO UPDATE
                   SET result_json = EXCLUDED.result_json, checked_at = NOW(), expires_at = EXCLUDED.expires_at""",
                (orcid, Jsonb(result), datetime.now(timezone.utc) + timedelta(days=days)),
            )
    return result
