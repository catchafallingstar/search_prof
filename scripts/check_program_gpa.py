from __future__ import annotations

import argparse

from db import get_db_connection
from ingestion.program_gpa import check_program_gpa_for_professor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check an official PhD admission GPA page for one canonical professor."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--professor-id", type=int)
    target.add_argument("--name")
    parser.add_argument("--institution", default="")
    args = parser.parse_args()

    if args.professor_id is not None:
        professor_id = args.professor_id
    else:
        filters = [
            "LOWER(BTRIM(name)) = LOWER(BTRIM(%s))",
            "data_origin IN ('OFFICIAL_DIRECTORY', 'OFFICIAL_PROFILE', 'MANUAL_REVIEW')",
        ]
        params: list[object] = [args.name]
        if args.institution.strip():
            filters.append("institution_name ILIKE %s")
            params.append(f"%{args.institution.strip()}%")
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT id, name, institution_name FROM professors WHERE {' AND '.join(filters)} ORDER BY id",
                    params,
                )
                matches = list(cursor.fetchall())
        if not matches:
            raise SystemExit("No canonical professor matched that name and institution.")
        if len(matches) > 1:
            print("More than one professor matched; rerun with --professor-id:")
            for match in matches:
                print(f"  {match['id']}: {match['name']} — {match['institution_name']}")
            raise SystemExit(2)
        professor_id = int(matches[0]["id"])

    print(check_program_gpa_for_professor(professor_id))


if __name__ == "__main__":
    main()
