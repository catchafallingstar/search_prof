from __future__ import annotations

import argparse
from urllib.parse import urlparse

import requests

from db import get_db_connection
from ingestion.faculty_roster import validate_faculty_directory
from ingestion.institution_classifier import normalize_domain


def main() -> None:
    parser = argparse.ArgumentParser(description="Register one official faculty directory.")
    parser.add_argument("institution")
    parser.add_argument("department")
    parser.add_argument("url")
    parser.add_argument("--country", default="US")
    args = parser.parse_args()
    domain = normalize_domain(args.url)
    if not domain or urlparse(args.url).scheme not in {"http", "https"}:
        raise SystemExit("A full http(s) official-directory URL is required.")
    response = requests.get(args.url, timeout=30)
    response.raise_for_status()
    members, reason = validate_faculty_directory(response.text, args.url)
    if not members:
        raise SystemExit(f"This URL is not an attributable faculty roster: {reason}")
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO institutions (name, country_code, primary_domain, organization_type)
                   VALUES (%s, %s, %s, 'HIGHER_EDUCATION')
                   ON CONFLICT (name) DO UPDATE SET
                       country_code = COALESCE(institutions.country_code, EXCLUDED.country_code),
                       primary_domain = COALESCE(institutions.primary_domain, EXCLUDED.primary_domain)
                   RETURNING id""",
                (args.institution.strip(), args.country.upper(), domain),
            )
            institution_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """INSERT INTO institution_domains (institution_id, domain, reviewed)
                   VALUES (%s, %s, TRUE)
                   ON CONFLICT (institution_id, domain) DO UPDATE SET reviewed = TRUE""",
                (institution_id, domain),
            )
            cursor.execute(
                """INSERT INTO faculty_directories (
                       institution_id, department, directory_url,
                       validation_status, validation_reason, discovered_by,
                       expected_profile_count
                   ) VALUES (%s, %s, %s, 'APPROVED', NULL, 'MANUAL', %s)
                   ON CONFLICT (directory_url) DO UPDATE SET
                       institution_id = EXCLUDED.institution_id,
                       department = EXCLUDED.department, active = TRUE,
                       validation_status = 'APPROVED', validation_reason = NULL,
                       expected_profile_count = EXCLUDED.expected_profile_count,
                       updated_at = NOW()
                   RETURNING id""",
                (institution_id, args.department.strip(), args.url, len(members)),
            )
            directory_id = int(cursor.fetchone()["id"])
    print(f"Registered directory {directory_id} for {args.institution}.")


if __name__ == "__main__":
    main()
