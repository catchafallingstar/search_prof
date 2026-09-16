"""Reject a false faculty-page candidate and return its university to discovery."""
from __future__ import annotations

import argparse
import json

from db import get_db_connection


def reject_directory(directory_id: int, reason: str) -> dict[str, object]:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT institution_id, directory_url FROM faculty_directories WHERE id = %s FOR UPDATE",
                (directory_id,),
            )
            directory = cursor.fetchone()
            if not directory:
                raise SystemExit(f"Faculty directory {directory_id} does not exist.")
            cursor.execute(
                """UPDATE faculty_directories SET active = FALSE,
                   validation_status = 'REJECTED', validation_reason = %s,
                   updated_at = NOW() WHERE id = %s""",
                (reason[:500], directory_id),
            )
            cursor.execute(
                """UPDATE institutions SET faculty_discovery_status = 'NOT_CHECKED',
                   faculty_discovery_checked_at = NULL, faculty_discovery_next_at = NULL,
                   faculty_discovery_error = %s WHERE id = %s""",
                (f"Rejected candidate: {reason[:300]}", int(directory["institution_id"])),
            )
            cursor.execute(
                """UPDATE radar_jobs SET status = 'cancelled', completed_at = NOW(),
                   locked_at = NULL, locked_by = NULL, last_error = %s, updated_at = NOW()
                   WHERE faculty_directory_id = %s AND status IN ('queued', 'running')""",
                (f"Faculty page rejected: {reason[:300]}", directory_id),
            )
    return {"directory_id": directory_id, "url": directory["directory_url"], "status": "REJECTED"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory_id", type=int)
    parser.add_argument("reason")
    args = parser.parse_args()
    print(json.dumps(reject_directory(args.directory_id, args.reason), indent=2))


if __name__ == "__main__":
    main()
