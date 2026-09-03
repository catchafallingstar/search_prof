import unittest
from unittest.mock import MagicMock, patch

from ingestion.hiring_discovery import discover_hiring_first_leads
from ingestion.websearch import SearchUnavailable


class HiringDiscoveryResilienceTests(unittest.TestCase):
    @patch("ingestion.hiring_discovery.search_web")
    @patch("ingestion.hiring_discovery.get_db_connection")
    def test_optional_hiring_search_failure_does_not_abort_radar(
        self, get_connection, search_web
    ):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 7, "name": "Jane Smith"}]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        get_connection.return_value.__enter__.return_value = connection
        search_web.side_effect = SearchUnavailable(
            "provider returned unrelated results", retry_after_seconds=300
        )

        result = discover_hiring_first_leads("Materials science", [7])

        self.assertEqual(result, {})
        self.assertEqual(search_web.call_count, 2)


if __name__ == "__main__":
    unittest.main()
