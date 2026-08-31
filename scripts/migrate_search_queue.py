"""Add queue/budget metadata; never rewrite identity verdicts or refresh dates."""
from db import get_db_connection
from scripts.migrate_slow_search import main as migrate_previous


def main():
    migrate_previous()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_search_pending BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS usage_month DATE;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_this_month INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_total BIGINT NOT NULL DEFAULT 0;
                UPDATE professors SET identity_search_pending = TRUE
                WHERE identity_retry_reason LIKE 'Web search waiting:%'
                   OR identity_retry_reason LIKE 'Search providers were unavailable%';
            """)
    print("Search queue migration applied. Identity decisions and next review dates preserved.")


if __name__ == "__main__":
    main()
