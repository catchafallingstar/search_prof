"""Read-only database checks for the local PostgreSQL instance."""

from db import get_db_connection
from schema_contract import schema_gaps


def main() -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            missing_tables, missing_columns = schema_gaps(cursor)
            if missing_tables or missing_columns:
                details = []
                if missing_tables:
                    details.append(f"missing tables: {missing_tables}")
                if missing_columns:
                    details.append(f"missing columns: {missing_columns}")
                raise SystemExit("Database schema is incomplete; " + "; ".join(details))

            cursor.execute(
                """
                SELECT u.email
                FROM site_admins sa
                JOIN users u ON u.id = sa.user_id
                WHERE sa.admin_role = 'owner' AND sa.revoked_at IS NULL
                """
            )
            owners = cursor.fetchall()
            if len(owners) != 1:
                raise SystemExit(f"Expected exactly one active owner; found {len(owners)}")

    print(f"Database smoke test passed; owner: {owners[0]['email']}")


if __name__ == "__main__":
    main()
