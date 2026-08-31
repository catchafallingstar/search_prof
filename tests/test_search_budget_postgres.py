"""Opt-in real PostgreSQL lock/quota tests; no external web requests."""
import os
import unittest
import uuid
from unittest.mock import patch

from db import get_db_connection
from ingestion.search_budget import SearchBudgetWait, search_slot


@unittest.skipUnless(os.getenv("RUN_DB_INTEGRATION") == "1", "explicit database test required")
class SearchBudgetPostgresTests(unittest.TestCase):
    def test_separate_sessions_share_lock_spacing_quota_and_preserve_reservation(self):
        provider = "test-budget-" + uuid.uuid4().hex
        settings = {"SEARCH_MIN_INTERVAL_MS": 60000, "SEARCH_DAILY_LIMIT": 2}
        try:
            with patch("ingestion.search_budget.setting_int", side_effect=lambda name, default, *_: settings.get(name, default)):
                with search_slot(provider):
                    with self.assertRaisesRegex(SearchBudgetWait, "Another worker"):
                        with search_slot(provider):
                            self.fail("concurrent request was admitted")
                with self.assertRaisesRegex(SearchBudgetWait, "next search slot"):
                    with search_slot(provider):
                        self.fail("spacing not enforced after connection closed")
                with get_db_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("UPDATE web_search_provider_health SET next_request_at = NOW() - INTERVAL '1 second' WHERE provider_name = %s", (provider,))
                with search_slot(provider):
                    pass
                with self.assertRaisesRegex(SearchBudgetWait, "Daily search budget"):
                    with search_slot(provider):
                        self.fail("quota ignored")
                with get_db_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT requests_today FROM web_search_provider_health WHERE provider_name = %s", (provider,))
                        self.assertEqual(cursor.fetchone()["requests_today"], 2)
        finally:
            with get_db_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM web_search_provider_health WHERE provider_name = %s", (provider,))
