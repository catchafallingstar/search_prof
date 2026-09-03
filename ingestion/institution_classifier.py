from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from db import get_db_connection


K12_NAME_PATTERN = re.compile(
    r"\b(?:university\s+(?:school|high\s+school)|college\s+preparatory|"
    r"elementary\s+school|middle\s+school|high\s+school|school\s+district|"
    r"public\s+schools?|independent\s+(?:day\s+)?school|charter\s+school|"
    r"grades?\s+k\s*[-–]\s*12|k\s*[-–]\s*12)\b",
    re.IGNORECASE,
)


def normalize_institution_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def normalize_domain(value: str | None) -> str:
    raw = str(value or "").strip().casefold()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").removeprefix("www.")
    return host.rstrip(".")


def obvious_k12_name(name: str | None) -> bool:
    return bool(K12_NAME_PATTERN.search(str(name or "")))


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


@lru_cache(maxsize=4096)
def lookup_institution_classification(name: str, domain: str = "") -> dict[str, Any]:
    """Return cached organization evidence without making an external request.

    A missing College Scorecard row is deliberately UNKNOWN. Some legitimate
    higher-education organizations are absent or use a different legal name.
    """
    clean_name = str(name or "").strip()
    clean_domain = normalize_domain(domain)
    if obvious_k12_name(clean_name):
        return {
            "organization_type": "K12_SCHOOL",
            "method": "K12_NAME_PATTERN",
            "confidence": 0.97,
            "evidence": f"The organization name contains a K–12 school phrase: {clean_name}.",
        }
    normalized_name = normalize_institution_name(clean_name)
    if not normalized_name and not clean_domain:
        return {"organization_type": "UNKNOWN", "method": "NO_INSTITUTION", "confidence": 0.0}
    try:
        with get_db_connection() as connection:
            row = connection.execute(
                """
                SELECT unit_id, institution_name, primary_domain
                FROM college_scorecard_institutions
                WHERE (primary_domain IS NOT NULL AND primary_domain = %s)
                   OR normalized_name = %s
                ORDER BY
                    CASE WHEN primary_domain = %s THEN 0 ELSE 1 END,
                    CASE WHEN is_currently_operating IS TRUE THEN 0 ELSE 1 END,
                    CASE WHEN is_main_campus IS TRUE THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (clean_domain or "__none__", normalized_name, clean_domain or "__none__"),
            ).fetchone()
    except Exception:
        # The verifier can run before the optional local directory is imported.
        return {"organization_type": "UNKNOWN", "method": "SCORECARD_UNAVAILABLE", "confidence": 0.0}
    if not row:
        return {"organization_type": "UNKNOWN", "method": "SCORECARD_NO_MATCH", "confidence": 0.0}
    unit_id = _row_value(row, "unit_id", 0)
    matched_name = _row_value(row, "institution_name", 1)
    matched_domain = _row_value(row, "primary_domain", 2)
    return {
        "organization_type": "HIGHER_EDUCATION",
        "method": "COLLEGE_SCORECARD_DOMAIN" if clean_domain and clean_domain == matched_domain else "COLLEGE_SCORECARD_NAME",
        "confidence": 0.99,
        "scorecard_unit_id": unit_id,
        "matched_name": matched_name,
        "matched_domain": matched_domain,
        "evidence": f"Matched US Department of Education College Scorecard institution {matched_name} (UNITID {unit_id}).",
    }
