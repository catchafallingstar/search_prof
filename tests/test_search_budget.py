import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ingestion.search_budget import reservation_delay, SearchBudgetWait, search_slot


class SearchBudgetTests(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        self.row = {"now": now, "utc_day": now.date(), "usage_day": now.date(),
                    "requests_today": 0, "next_utc_day": now.replace(hour=0) + timedelta(days=1)}

    def test_persistent_slot_delays_new_process(self):
        self.row["next_request_at"] = self.row["now"] + timedelta(seconds=60)
        self.assertEqual(reservation_delay(self.row, 60, 200)[0], 60)

    def test_quota_resets_at_utc_midnight(self):
        self.row["requests_today"] = 200
        self.assertEqual(reservation_delay(self.row, 60, 200)[0], 43200)
        self.row["usage_day"] -= timedelta(days=1)
        self.assertEqual(reservation_delay(self.row, 60, 200)[0], 0)

    def test_provider_cooldown_beats_spacing(self):
        self.row["blocked_until"] = self.row["now"] + timedelta(hours=2)
        self.assertEqual(reservation_delay(self.row, 60, 200)[0], 7200)

    @patch("ingestion.search_budget.get_db_connection")
    def test_no_network_when_another_process_holds_lock(self, connection):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"acquired": False}
        with self.assertRaises(SearchBudgetWait):
            with search_slot("ddgs"):
                self.fail("must not make a request")
