from __future__ import annotations

import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

from db import get_db_connection
from ingestion.institution_classifier import normalize_domain, normalize_institution_name
from settings import setting


DEFAULT_FILENAME = "Most-Recent-Cohorts-Institution.csv"


def _truth(value: str | None) -> bool | None:
    text = str(value or "").strip()
    if text == "1":
        return True
    if text == "0":
        return False
    return None


def _homepage(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw if "://" in raw else "https://" + raw


def _default_path() -> Path:
    configured = setting("COLLEGE_SCORECARD_CSV_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "college_scorecard" / DEFAULT_FILENAME


def import_scorecard(csv_path: Path) -> tuple[int, int]:
    csv.field_size_limit(10_000_000)
    if not csv_path.is_file():
        raise FileNotFoundError(f"College Scorecard CSV not found: {csv_path}")
    rows: list[tuple] = []
    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        reader = csv.DictReader(source)
        required = {"UNITID", "INSTNM"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("College Scorecard CSV is missing columns: " + ", ".join(sorted(missing)))
        for record in reader:
            name = str(record.get("INSTNM") or "").strip()
            unit_id = str(record.get("UNITID") or "").strip()
            if not name or not unit_id.isdigit():
                continue
            homepage = _homepage(record.get("INSTURL"))
            rows.append((
                int(unit_id), str(record.get("OPEID") or "").strip() or None,
                name, normalize_institution_name(name),
                str(record.get("CITY") or "").strip() or None,
                str(record.get("STABBR") or "").strip() or None,
                str(record.get("ZIP") or "").strip() or None,
                homepage or None, normalize_domain(homepage) or None,
                _truth(record.get("MAIN")), _truth(record.get("CURROPER")), csv_path.name,
            ))
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO college_scorecard_institutions (
                    unit_id, ope_id, institution_name, normalized_name, city,
                    state_code, postal_code, homepage_url, primary_domain,
                    is_main_campus, is_currently_operating, source_file, imported_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (unit_id) DO UPDATE SET
                    ope_id = EXCLUDED.ope_id,
                    institution_name = EXCLUDED.institution_name,
                    normalized_name = EXCLUDED.normalized_name,
                    city = EXCLUDED.city,
                    state_code = EXCLUDED.state_code,
                    postal_code = EXCLUDED.postal_code,
                    homepage_url = EXCLUDED.homepage_url,
                    primary_domain = EXCLUDED.primary_domain,
                    is_main_campus = EXCLUDED.is_main_campus,
                    is_currently_operating = EXCLUDED.is_currently_operating,
                    source_file = EXCLUDED.source_file,
                    imported_at = NOW()
                """,
                rows,
            )
            cursor.execute(
                """
                UPDATE institutions i
                SET organization_type = 'HIGHER_EDUCATION',
                    organization_type_method = 'COLLEGE_SCORECARD_DOMAIN',
                    organization_type_checked_at = NOW(),
                    scorecard_unit_id = s.unit_id
                FROM college_scorecard_institutions s
                WHERE i.organization_type IN ('UNKNOWN', 'HIGHER_EDUCATION')
                  AND s.primary_domain IS NOT NULL
                  AND s.primary_domain = LOWER(NULLIF(i.primary_domain, ''))
                """
            )
            linked = cursor.rowcount
            cursor.execute(
                """
                UPDATE institutions i
                SET organization_type = 'HIGHER_EDUCATION',
                    organization_type_method = 'COLLEGE_SCORECARD_NAME',
                    organization_type_checked_at = NOW(),
                    scorecard_unit_id = s.unit_id,
                    primary_domain = COALESCE(NULLIF(i.primary_domain, ''), s.primary_domain)
                FROM college_scorecard_institutions s
                WHERE i.organization_type = 'UNKNOWN'
                  AND s.normalized_name = BTRIM(regexp_replace(
                      LOWER(translate(i.name, '&', ' ')), '[^a-z0-9]+', ' ', 'g'
                  ))
                """
            )
            linked += cursor.rowcount
    return len(rows), linked


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the local College Scorecard institution directory.")
    parser.add_argument("--csv", type=Path, default=_default_path())
    args = parser.parse_args()
    imported, linked = import_scorecard(args.csv.resolve())
    print(f"Imported {imported:,} College Scorecard institutions; linked {linked:,} ScholarRadar institution records.")


if __name__ == "__main__":
    main()
