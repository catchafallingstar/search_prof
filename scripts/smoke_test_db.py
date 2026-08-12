"""Read-only database checks for the local PostgreSQL instance."""

from db import get_db_connection


REQUIRED_TABLES = {
    "admin_audit_log",
    "hiring_signals",
    "institution_memberships",
    "institutions",
    "opportunities",
    "professor_profiles",
    "professors",
    "role_verifications",
    "site_admins",
    "users",
}


def main() -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = {row["tablename"] for row in cursor.fetchall()}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise SystemExit(f"Database schema is incomplete; missing: {sorted(missing)}")

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
