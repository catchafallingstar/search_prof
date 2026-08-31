from db import get_db_connection
from scripts.migrate_search_queue import main as migrate_previous


def main():
    migrate_previous()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE professor_papers ADD COLUMN IF NOT EXISTS affiliation_version INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_remaining INTEGER;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_checked_at TIMESTAMPTZ;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS remote_reset_at TIMESTAMPTZ;
                UPDATE professors SET next_identity_check_at = NULL, identity_retry_at = NULL,
                    identity_retry_reason = NULL, identity_search_pending = FALSE WHERE faculty_status = 'CONFLICT';
            """)
    print("Affiliation/quota migration applied. Conflicts retained for staff; no identities or papers deleted.")


if __name__ == '__main__':
    main()
