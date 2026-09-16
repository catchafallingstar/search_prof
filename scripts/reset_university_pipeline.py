"""Reset derived ScholarRadar data for a clean university-first initialization."""
from __future__ import annotations

import argparse
import json

from db import get_db_connection


CONFIRMATION = "RESET_UNIVERSITY_PIPELINE"
DERIVED_TABLES = (
    "radar_worker_heartbeats", "radar_jobs", "radar_run_results",
    "radar_run_professors", "radar_runs", "radar_topic_professor_papers",
    "radar_topic_professors", "professor_topic_grant_checks", "radar_topics",
    "reports", "sponsorships", "opportunity_sources", "opportunities",
    "hiring_signals", "fundings", "professor_papers", "papers",
    "role_verifications", "institution_memberships", "professor_profiles",
    "faculty_verification_evidence", "faculty_appointments", "professor_publication_sources",
    "faculty_directory_memberships", "roster_member_candidates",
    "faculty_directories", "faculty_page_candidates", "institution_units",
    "professor_name_aliases",
    "professor_identity_review_queue", "program_admission_requirements",
    "professors", "web_search_cache", "web_search_provider_health",
)


def reset(*, execute: bool, confirmation: str) -> dict[str, object]:
    if not execute:
        return {
            "mode": "dry-run",
            "tables_to_clear": list(DERIVED_TABLES),
            "preserved": [
                "users", "site_admins", "admin_audit_log", "institutions",
                "institution_aliases", "institution_domains",
                "college_scorecard_institutions", "ai_usage_daily",
            ],
        }
    if confirmation != CONFIRMATION:
        raise SystemExit(f"Refusing reset: pass --confirm {CONFIRMATION}")
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE " + ", ".join(DERIVED_TABLES) + " RESTART IDENTITY CASCADE"
            )
            cursor.execute(
                """ALTER TABLE institutions
                   ADD COLUMN IF NOT EXISTS faculty_discovery_status TEXT NOT NULL DEFAULT 'NOT_CHECKED';
                   ALTER TABLE institutions
                   ADD COLUMN IF NOT EXISTS faculty_discovery_checked_at TIMESTAMPTZ;
                   ALTER TABLE institutions
                   ADD COLUMN IF NOT EXISTS faculty_discovery_next_at TIMESTAMPTZ;
                   ALTER TABLE institutions
                   ADD COLUMN IF NOT EXISTS faculty_discovery_error TEXT"""
            )
            cursor.execute(
                """UPDATE institutions SET faculty_discovery_status = 'NOT_CHECKED',
                   faculty_discovery_checked_at = NULL,
                   faculty_discovery_next_at = NULL,
                   faculty_discovery_error = NULL"""
            )
    return {
        "mode": "executed",
        "tables_cleared": len(DERIVED_TABLES),
        "institution_registry_preserved": True,
        "accounts_preserved": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    print(json.dumps(reset(execute=args.execute, confirmation=args.confirm), indent=2))


if __name__ == "__main__":
    main()
