"""Add staff-only identity diagnostics without reclassifying any existing person."""
from db import get_db_connection


def main():
    with get_db_connection() as connection:
        connection.execute("ALTER TABLE professors ADD COLUMN IF NOT EXISTS identity_search_audit JSONB NOT NULL DEFAULT '{}'::jsonb")
    print('Identity search audit column ready; no existing identities changed.')


if __name__ == '__main__':
    main()
