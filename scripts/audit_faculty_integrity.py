from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from db import get_db_connection


CHECKS: tuple[tuple[str, str], ...] = (
    (
        "VERIFIED_WITHOUT_CURRENT_SUPPORT",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               'No active roster membership or current decision evidence' AS detail
        FROM professors p
        WHERE p.faculty_status = 'VERIFIED'
          AND NOT EXISTS (
              SELECT 1 FROM faculty_directory_memberships membership
              JOIN faculty_directories directory ON directory.id = membership.directory_id
              WHERE membership.professor_id = p.id
                AND membership.currently_listed = TRUE AND directory.active = TRUE
          )
          AND NOT EXISTS (
              SELECT 1 FROM faculty_verification_evidence evidence
              WHERE evidence.professor_id = p.id
                AND evidence.supports_decision = TRUE
                AND evidence.currentness = 'CURRENT'
                AND evidence.verification_status = 'VERIFIED'
          )
        """,
    ),
    (
        "HISTORICAL_SOURCE_SUPPORTS_CURRENT_ROLE",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               evidence.source_url AS detail
        FROM professors p
        JOIN faculty_verification_evidence evidence ON evidence.professor_id = p.id
        WHERE evidence.supports_decision = TRUE
          AND evidence.currentness = 'CURRENT'
          AND evidence.source_url ~* '/(news|stories?|events?|awards?|honors?|alumni|archive|grantsandawards)/'
        """,
    ),
    (
        "ROSTER_INSTITUTION_CONFLICT",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               CONCAT('professor institution=', p.institution_id,
                      '; directory institution=', directory.institution_id) AS detail
        FROM professors p
        JOIN faculty_directory_memberships membership ON membership.professor_id = p.id
        JOIN faculty_directories directory ON directory.id = membership.directory_id
        WHERE membership.currently_listed = TRUE
          AND p.institution_id IS DISTINCT FROM directory.institution_id
        """,
    ),
    (
        "MULTIPLE_CURRENT_PRIMARY_INSTITUTIONS",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               STRING_AGG(DISTINCT appointment.institution_id::text, ',') AS detail
        FROM professors p
        JOIN faculty_appointments appointment ON appointment.professor_id = p.id
        WHERE appointment.current = TRUE AND appointment.primary_appointment = TRUE
        GROUP BY p.id, p.name, p.institution_name
        HAVING COUNT(DISTINCT appointment.institution_id) > 1
        """,
    ),
    (
        "OFFICIAL_DIRECTORY_WITHOUT_MEMBERSHIP",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               COALESCE(p.faculty_source_url, '') AS detail
        FROM professors p
        WHERE p.data_origin = 'OFFICIAL_DIRECTORY'
          AND NOT EXISTS (
              SELECT 1 FROM faculty_directory_memberships membership
              JOIN faculty_directories directory ON directory.id = membership.directory_id
              WHERE membership.professor_id = p.id
                AND membership.currently_listed = TRUE AND directory.active = TRUE
          )
        """,
    ),
    (
        "CURRENT_TOPIC_WITHOUT_PAPER_EVIDENCE",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               CONCAT('topic_id=', topic_match.radar_topic_id) AS detail
        FROM radar_topic_professors topic_match
        JOIN professors p ON p.id = topic_match.professor_id
        WHERE topic_match.is_current_match = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM radar_topic_professor_papers paper_evidence
              WHERE paper_evidence.radar_topic_id = topic_match.radar_topic_id
                AND paper_evidence.professor_id = topic_match.professor_id
                AND paper_evidence.is_current_match = TRUE
                AND paper_evidence.evidence_method = 'DIRECT_PAPER_TEXT'
          )
        """,
    ),
    (
        "CANONICAL_FACULTY_WITHOUT_PAPERS",
        """
        SELECT p.id AS professor_id, p.name, p.institution_name,
               'Current identity is supported but no publication identity is linked' AS detail
        FROM professors p
        WHERE p.faculty_status = 'VERIFIED'
          AND (EXISTS (
              SELECT 1 FROM faculty_directory_memberships membership
              JOIN faculty_directories directory ON directory.id = membership.directory_id
              WHERE membership.professor_id = p.id
                AND membership.currently_listed = TRUE AND directory.active = TRUE
          ) OR EXISTS (
              SELECT 1 FROM faculty_verification_evidence evidence
              WHERE evidence.professor_id = p.id
                AND evidence.supports_decision = TRUE
                AND evidence.currentness = 'CURRENT'
                AND evidence.verification_status = 'VERIFIED'
          ))
          AND NOT EXISTS (SELECT 1 FROM professor_papers link WHERE link.professor_id = p.id)
        """,
    ),
)


def audit(output_path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM professors")
            professors_scanned = int(cursor.fetchone()["total"])
            for issue, query in CHECKS:
                cursor.execute(query)
                rows = list(cursor.fetchall())
                counts[issue] = len(rows)
                findings.extend({"issue": issue, **row} for row in rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as output:
        columns = ["issue", "professor_id", "name", "institution_name", "detail"]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(findings)
    return {
        "professors_scanned": professors_scanned,
        "findings": len(findings),
        "counts": counts,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit every canonical professor and topic evidence chain.")
    parser.add_argument("--output", type=Path, default=Path("backups/faculty-integrity-audit.csv"))
    args = parser.parse_args()
    print(json.dumps(audit(args.output), indent=2))


if __name__ == "__main__":
    main()
