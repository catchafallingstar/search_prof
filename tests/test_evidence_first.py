import unittest
from unittest.mock import patch, MagicMock

from ingestion.verify_faculty import verify_faculty_candidate, verify_faculty_candidates
from ingestion.index_worker import _verify
from ingestion.websearch import SearchUnavailable
from ingestion.orcid_evidence import normalize_orcid, extract_orcid_clues


class EvidenceFirstTests(unittest.TestCase):
    def setUp(self):
        capacity = patch("ingestion.index_worker.search_provider_runtime_state", return_value={"available": ["ddgs"], "retry_after_seconds": 0})
        capacity.start()
        self.addCleanup(capacity.stop)
        self.candidate = {"id": 1, "name": "Jane Smith", "institution_name": "Example University",
                          "homepage_url": "https://example.edu/faculty/jane", "recent_papers": []}

    @patch("ingestion.verify_faculty.search_web", side_effect=AssertionError("must not search"))
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations", side_effect=AssertionError("must not download"))
    @patch("ingestion.verify_faculty.fetch_orcid_clues", side_effect=AssertionError("must stop early"))
    @patch("ingestion.verify_faculty.inspect_faculty_result", return_value={"status": "VERIFIED"})
    def test_known_faculty_page_short_circuits_all_fallbacks(self, *_mocks):
        self.assertEqual(verify_faculty_candidate(self.candidate)["status"], "VERIFIED")

    @patch("ingestion.verify_faculty.fetch_orcid_clues", return_value={})
    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.validate_ai_identity_assessment", return_value=None)
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations")
    @patch("ingestion.verify_faculty.search_web")
    def test_paper_fallback_runs_before_unavailable_search(self, search, papers, *_mocks):
        self.candidate["homepage_url"] = ""
        self.candidate["recent_papers"] = [{"title": "Example paper", "pdf_url": "https://example.edu/paper.pdf"}]
        events = []
        papers.side_effect = lambda candidate, **kw: events.append("paper") or candidate
        def unavailable(*args, **kwargs):
            events.append("search")
            raise SearchUnavailable("cooldown", 3600)
        search.side_effect = unavailable
        with self.assertRaises(SearchUnavailable):
            verify_faculty_candidate(self.candidate)
        self.assertEqual(events, ["paper", "search"])

    def test_orcid_checksum_and_untrusted_input(self):
        self.assertEqual(normalize_orcid("https://orcid.org/0000-0002-1825-0097"), "0000-0002-1825-0097")
        self.assertEqual(normalize_orcid("0000-0002-1825-0098"), "")
        self.assertEqual(normalize_orcid("http://localhost:8080/admin"), "")

    @patch("ingestion.verify_faculty.fetch_orcid_clues", return_value={"name": "Another Person", "links": ["https://example.edu/other"]})
    @patch("ingestion.verify_faculty.inspect_faculty_result", return_value={"status": "UNVERIFIED"})
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations", side_effect=lambda candidate, **kw: candidate)
    @patch("ingestion.verify_faculty.search_web", side_effect=SearchUnavailable("pause", 3600))
    def test_mismatched_orcid_does_not_supply_another_persons_page(self, search, pdf, inspect, clues):
        with self.assertRaises(SearchUnavailable):
            verify_faculty_candidate(self.candidate)
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[1]["href"], self.candidate["homepage_url"])

    def test_orcid_employment_is_only_a_clue(self):
        clues = extract_orcid_clues({"person": {"name": {"given-names": {"value": "Jane"}, "family-name": {"value": "Smith"}}},
            "activities-summary": {"employments": {"affiliation-group": [{"summaries": [{"employment-summary": {
                "organization": {"name": "Example University"}, "role-title": "Professor", "end-date": None,
                "source": {"source-name": {"value": "Jane Smith"}}}}]}]}}})
        self.assertEqual(clues["employments"][0]["role"], "Professor")
        self.assertNotIn("status", clues)

    @patch("ingestion.index_worker.enqueue_radar_job")
    @patch("ingestion.index_worker.fetch_topic_identity_retry_delay", return_value=3600)
    @patch("ingestion.index_worker.refresh_topic_coverage", return_value={"verified_count": 1, "desired_results": 100})
    @patch("ingestion.index_worker.fetch_topic_candidate_ids", side_effect=[[], [], [22]])
    @patch("ingestion.index_worker._topic_for_job", return_value={"id": 7})
    def test_deferred_candidates_are_not_mistaken_for_exhaustion(self, topic, fetch, coverage, delay, enqueue):
        result, more = _verify({"job_type": "VERIFY_FACULTY"})
        self.assertTrue(more)
        self.assertEqual(result["retry_after_seconds"], 3600)
        enqueue.assert_not_called()

    @patch("ingestion.verify_faculty._save_result")
    @patch("ingestion.verify_faculty.verify_faculty_candidate", side_effect=SearchUnavailable("waiting", 3600))
    @patch("ingestion.verify_faculty.get_db_connection")
    def test_outage_persists_retry_without_identity_decision(self, connection, verify, save):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        candidate = dict(self.candidate, faculty_status="UNVERIFIED")
        cursor.fetchall.side_effect = [[candidate], []]
        result = verify_faculty_candidates([1])
        self.assertEqual(result["deferred"], 1)
        save.assert_not_called()
        self.assertTrue(any("identity_retry_at = NOW()" in call.args[0] for call in cursor.execute.call_args_list))
