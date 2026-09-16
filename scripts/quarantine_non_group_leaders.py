"""Quarantine rostered university personnel who are not group-leading professors."""
from __future__ import annotations

import argparse
import json

from db import get_db_connection


INELIGIBLE_TITLE_SQL = (
    r"\m(adjunct|affiliate|affiliated|visiting|emeritus|emerita|"
    r"part[- ]time|lecturer|instructor)\M"
)


def run(*, execute: bool = False) -> dict[str, object]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id,name,institution_name,faculty_title,canonical_rank
                   FROM professors
                   WHERE faculty_status='VERIFIED'
                     AND data_origin='OFFICIAL_DIRECTORY'
                     AND (
                       canonical_rank IS NULL
                       OR canonical_rank NOT IN (
                         'ASSISTANT_PROFESSOR','ASSOCIATE_PROFESSOR','PROFESSOR'
                       )
                       OR faculty_title ~* %s
                     )
                   ORDER BY institution_name,name""",
                (INELIGIBLE_TITLE_SQL,),
            )
            rows = list(cursor.fetchall())
            ids = [int(row["id"]) for row in rows]
            if execute and ids:
                cursor.execute(
                    """UPDATE professors
                       SET faculty_status='NOT_FACULTY',
                           employment_status='NONFACULTY_CONFIRMED',
                           publication_status='NOT_APPLICABLE',
                           updated_at=NOW()
                       WHERE id=ANY(%s::BIGINT[])""",
                    (ids,),
                )
                cursor.execute(
                    """UPDATE roster_member_candidates
                       SET validation_status='NOT_GROUP_LEADING_FACULTY',
                           validation_reason=(
                             'Official roster role is valid, but it is not an eligible '
                             'research-group-leading professor appointment.'
                           ), checked_at=NOW()
                       WHERE professor_id=ANY(%s::BIGINT[])""",
                    (ids,),
                )
                cursor.execute(
                    """UPDATE radar_jobs
                       SET status='cancelled', completed_at=NOW(),
                           locked_at=NULL, locked_by=NULL, updated_at=NOW(),
                           last_error='Cancelled: roster role is not an eligible group-leading professor.'
                       WHERE professor_id=ANY(%s::BIGINT[])
                         AND status IN ('queued','running')""",
                    (ids,),
                )
                cursor.execute(
                    """UPDATE radar_topic_professors
                       SET is_current_match=FALSE
                       WHERE professor_id=ANY(%s::BIGINT[])""",
                    (ids,),
                )
    return {
        "mode": "execute" if execute else "dry-run",
        "records": len(rows),
        "examples": [dict(row) for row in rows[:25]],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.execute and args.confirm != "QUARANTINE_NON_GROUP_LEADERS":
        raise SystemExit("Use --confirm QUARANTINE_NON_GROUP_LEADERS with --execute.")
    print(json.dumps(run(execute=args.execute), indent=2, default=str))


if __name__ == "__main__":
    main()
