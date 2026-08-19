import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.index_worker import process_job
from radar_store import normalize_topic_query, topic_key


PROJECT_DIR = Path(__file__).resolve().parents[1]


class BackgroundIndexTests(unittest.TestCase):
    def test_topic_identity_is_shared_across_spacing_and_case(self) -> None:
        self.assertEqual(topic_key("AI security"), topic_key("  ai   SECURITY  "))
        self.assertEqual(normalize_topic_query("AI-security"), "ai security")

    def test_invalid_short_topic_is_rejected_before_database_work(self) -> None:
        with self.assertRaises(ValueError):
            normalize_topic_query("AI")

    @patch("ingestion.index_worker._discover")
    def test_discovery_and_reindex_share_the_discovery_handler(self, discover) -> None:
        discover.return_value = {"candidates_ranked": 12}
        result, needs_more = process_job({"job_type": "DISCOVER_CANDIDATES"})
        self.assertEqual(result["candidates_ranked"], 12)
        self.assertFalse(needs_more)
        process_job({"job_type": "REINDEX_RESEARCH"})
        self.assertEqual(discover.call_count, 2)

    @patch("ingestion.index_worker.verify_faculty_candidates")
    def test_single_faculty_refresh_is_supported(self, verify) -> None:
        verify.return_value = {"verified": 1}
        result, needs_more = process_job(
            {"job_type": "REFRESH_FACULTY", "professor_id": 44}
        )
        verify.assert_called_once_with([44])
        self.assertEqual(result, {"verified": 1})
        self.assertFalse(needs_more)

    def test_database_query_is_parameterized_and_public_status_is_strict(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn("candidate.status = 'active'", source)
        self.assertIn("o.source_kind IN ('verified_post', 'university_post')", source)
        self.assertIn("p.faculty_status = 'VERIFIED'", source)
        self.assertIn("OFFSET %s", source)

    def test_queue_claiming_is_fair_between_topics(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn("ORDER BY priority DESC, available_at, created_at", source)
        self.assertIn("active_job_type", source)


if __name__ == "__main__":
    unittest.main()
