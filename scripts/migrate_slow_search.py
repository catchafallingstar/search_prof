"""Additive migration only; does not rebuild topics or change identity verdicts."""
from db import get_db_connection


def main():
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE professors ADD COLUMN IF NOT EXISTS orcid_id TEXT;
                ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_retry_at TIMESTAMPTZ;
                ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_retry_reason TEXT;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS usage_day DATE;
                ALTER TABLE web_search_provider_health ADD COLUMN IF NOT EXISTS requests_today INTEGER NOT NULL DEFAULT 0;
                CREATE TABLE IF NOT EXISTS identity_orcid_cache (
                    orcid_id TEXT PRIMARY KEY,
                    result_json JSONB NOT NULL DEFAULT '{}',
                    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                );
            """)
    print("Slow-search migration applied; existing identity decisions and topic evidence preserved.")


if __name__ == "__main__":
    main()
