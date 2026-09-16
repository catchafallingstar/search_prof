from __future__ import annotations

import argparse

from db import get_db_connection
from ingestion.faculty_roster import crawl_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh registered official faculty directories.")
    parser.add_argument("--directory-id", type=int)
    args = parser.parse_args()
    if args.directory_id:
        ids = [args.directory_id]
    else:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM faculty_directories WHERE active = TRUE ORDER BY id")
                ids = [int(row["id"]) for row in cursor.fetchall()]
    failures = 0
    for directory_id in ids:
        try:
            print(crawl_directory(directory_id))
        except Exception as error:
            failures += 1
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE faculty_directories SET last_error = %s, updated_at = NOW() WHERE id = %s",
                        (f"{type(error).__name__}: {error}"[:1000], directory_id),
                    )
            print({"directory_id": directory_id, "error": f"{type(error).__name__}: {error}"})
    if failures:
        raise SystemExit(f"{failures} directory refresh(es) failed.")


if __name__ == "__main__":
    main()
