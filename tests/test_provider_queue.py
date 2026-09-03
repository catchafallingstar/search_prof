import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from identity_schedule import recently_checked
from ingestion.index_worker import _verify
from ingestion.search_budget import reservation_delay, provider_limits
from ingestion.verify_faculty import verify_faculty_candidate, verify_faculty_candidates
from ingestion.websearch import (_tavily_search, _provider_names, search_provider_runtime_state,
                                search_web, SearchUnavailable, SearchProviderUnavailable)


class ProviderQueueTests(unittest.TestCase):
    def test_completed_check_reused_for_rolling_month(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(recently_checked({"faculty_checked_at": now - timedelta(days=29)}, now))
        self.assertFalse(recently_checked({"faculty_checked_at": now - timedelta(days=30)}, now))
        self.assertTrue(recently_checked(
            {"faculty_checked_at": now - timedelta(days=6)}, now, max_age_days=7
        ))
        self.assertFalse(recently_checked(
            {"faculty_checked_at": now - timedelta(days=8)}, now, max_age_days=7
        ))
        self.assertFalse(recently_checked({"identity_retry_at": now}, now))

    @patch("ingestion.verify_faculty.verify_faculty_candidate")
    @patch("ingestion.verify_faculty.get_db_connection")
    def test_old_algorithm_is_rechecked_even_inside_month(self, connection, verify):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[{"id": 1, "faculty_status": "VERIFIED", "faculty_verification_version": 1,
            "faculty_checked_at": datetime.now(timezone.utc) - timedelta(days=3)}], []]
        verify.return_value = {"status": "VERIFIED", "search_audit": {}}
        with patch("ingestion.verify_faculty._save_result") as save_result:
            result = verify_faculty_candidates([1])
        verify.assert_called_once()
        save_result.assert_called_once()
        self.assertEqual(result["verified_ids"], [1])
        self.assertEqual(result["checked"], 1)

    @patch("ingestion.verify_faculty.fetch_orcid_clues", return_value={})
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations", side_effect=lambda c, **kw: c)
    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.search_web", side_effect=AssertionError("must not request a search slot"))
    def test_direct_mode_never_hits_search_gate(self, *_):
        with self.assertRaises(SearchUnavailable):
            verify_faculty_candidate({"id": 1, "name": "Jane Smith", "institution_name": "Example University",
                "_direct_only": True, "_search_retry_seconds": 3600, "recent_papers": []})

    @patch("ingestion.index_worker.search_provider_runtime_state", return_value={"available": ["ddgs"], "retry_after_seconds": 0})
    @patch("ingestion.index_worker._topic_for_job", return_value={"id": 7})
    @patch("ingestion.index_worker.fetch_topic_candidate_ids", side_effect=[[1], [2]])
    @patch("ingestion.index_worker.refresh_topic_coverage", return_value={"verified_count": 1, "desired_results": 100})
    @patch("ingestion.index_worker.verify_faculty_candidates", return_value={"deferred": 1, "retry_after_seconds": 60})
    def test_next_candidate_does_not_cause_two_second_retry(self, *_):
        result, more = _verify({"job_type": "VERIFY_FACULTY"})
        self.assertTrue(more)
        self.assertEqual(result["retry_after_seconds"], 60)
        self.assertEqual(result["waiting_for"], "web_search")

    @patch("ingestion.index_worker.search_provider_runtime_state", return_value={"available": [], "retry_after_seconds": 3600})
    @patch("ingestion.index_worker._topic_for_job", return_value={"id": 7})
    @patch("ingestion.index_worker.fetch_topic_candidate_ids", side_effect=[[1], [2]])
    @patch("ingestion.index_worker.refresh_topic_coverage", return_value={"verified_count": 1, "desired_results": 100})
    @patch("ingestion.index_worker.verify_faculty_candidates", return_value={"deferred": 1, "retry_after_seconds": 3600})
    def test_paused_provider_only_allows_direct_mode(self, verify, coverage, fetch, *_):
        result, more = _verify({"job_type": "VERIFY_FACULTY"})
        self.assertTrue(fetch.call_args_list[0].kwargs["direct_only"])
        verify.assert_called_once_with([1], direct_only=True, retry_after_seconds=3600)
        self.assertEqual(result["retry_after_seconds"], 3600)

    def test_monthly_and_total_limits_are_separate(self):
        now = datetime(2026, 8, 30, tzinfo=timezone.utc)
        row = {"now": now, "utc_month": now.date().replace(day=1), "usage_month": now.date().replace(day=1),
               "utc_day": now.date(), "requests_today": 0,
               "requests_this_month": 1000, "requests_total": 1000, "next_utc_month": datetime(2026, 9, 1, tzinfo=timezone.utc)}
        self.assertIn("Monthly", reservation_delay(row, 2, 200, 1000, 2000)[1])
        self.assertIn("Operator", reservation_delay(row, 2, 200, 1000, 1000)[1])
        row["usage_month"] = now.date().replace(month=7, day=1)
        self.assertEqual(reservation_delay(row, 2, 200, 1000, 2000)[0], 0)
        self.assertIn("Operator", reservation_delay(row, 2, 200, 1000, 1000)[1])

    @patch("ingestion.websearch._persistent_block_remaining", return_value=0)
    @patch("ingestion.websearch.provider_capacity", side_effect=[{"retry_after_seconds": 86400}, {"retry_after_seconds": 0}])
    @patch("ingestion.websearch._provider_names", return_value=["tavily", "ddgs"])
    def test_exhausted_api_does_not_block_other_provider(self, *_):
        state = search_provider_runtime_state()
        self.assertEqual(state["available"], ["ddgs"])

    @patch("ingestion.websearch.requests.post")
    def test_tavily_uses_basic_no_generated_answer(self, post):
        post.return_value.json.return_value = {"results": [{"url": "https://example.edu/jane", "title": "Jane Smith", "content": "Professor"}]}
        result = _tavily_search("Jane Smith University", 5, "test-secret")
        self.assertEqual(result[0]["body"], "Professor")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["auto_parameters"])
        self.assertFalse(payload["include_answer"])
        self.assertNotIn("test-secret", str(payload))
        post.return_value.json.return_value = {"error": "exhausted"}
        with self.assertRaises(RuntimeError):
            _tavily_search("Jane Smith", 5, "test-secret")

    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._provider_names", return_value=["tavily", "ddgs"])
    @patch("ingestion.websearch._provider_strategy", return_value="fallback")
    @patch("ingestion.websearch._run_provider", side_effect=[SearchProviderUnavailable("quota exhausted", 86400), [{"href": "https://example.edu", "title": "Jane Smith", "body": "Professor"}]])
    def test_quota_failure_falls_through_to_other_engine(self, run, *_):
        result = search_web('"Jane Smith" University')
        self.assertEqual(result[0]["href"], "https://example.edu")
        self.assertEqual([call.args[0] for call in run.call_args_list], ["tavily", "ddgs"])

    @patch("ingestion.websearch._read_cache", return_value=None)
    @patch("ingestion.websearch._write_cache")
    @patch("ingestion.websearch._provider_names", return_value=["tavily", "ddgs"])
    @patch("ingestion.websearch._provider_strategy", return_value="fallback")
    @patch("ingestion.websearch._run_provider", side_effect=[SearchProviderUnavailable("quota exhausted", 86400), []])
    def test_empty_backup_does_not_hide_primary_outage(self, run, strategy, names, write, read):
        with self.assertRaises(SearchUnavailable):
            search_web('"Jane Smith" University')
        write.assert_not_called()

    @patch("ingestion.websearch.setting_bool", return_value=False)
    @patch("ingestion.websearch.setting", side_effect=lambda name: {"SEARCH_PROVIDERS": "brave,tavily,ddgs", "BRAVE_SEARCH_API_KEY": "x", "TAVILY_API_KEY": ""}.get(name, ""))
    def test_missing_key_or_storage_rights_not_activated(self, *_):
        self.assertEqual(_provider_names(), ["ddgs"])

    @patch("ingestion.websearch._cache_enabled", return_value=True)
    @patch("ingestion.websearch._provider_names", return_value=["tavily", "ddgs"])
    @patch("ingestion.websearch.get_db_connection")
    def test_old_positive_cache_reused_but_old_empty_not_hiding_new_provider(self, connection, *_):
        from ingestion.websearch import _read_cache
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"results_json": [], "provider_names": ["ddgs"]}]
        self.assertIsNone(_read_cache('"Jane Smith"', 3))
        cursor.fetchall.return_value = [{"results_json": [{"href": "https://example.edu/jane", "title": "Jane Smith"}], "provider_names": ["ddgs"]}]
        self.assertEqual(_read_cache('"Jane Smith"', 3)[0]["title"], "Jane Smith")

    def test_two_candidates_use_different_providers_concurrently(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import nullcontext, ExitStack
        from threading import Event
        from ingestion import websearch
        started, release = Event(), Event()
        def primary(query, count, key):
            started.set()
            if not release.wait(3):
                raise RuntimeError("backup request never started in parallel")
            return [{"href": "https://example.edu/a", "title": query, "body": "Professor"}]
        def backup(query, count):
            release.set()
            return [{"href": "https://example.edu/b", "title": query, "body": "Professor"}]
        with ExitStack() as stack:
            for name, options in {
                "_read_cache": {"return_value": None}, "_write_cache": {},
                "_provider_names": {"return_value": ["tavily", "ddgs"]},
                "_provider_strategy": {"return_value": "fallback"},
                "_assert_provider_available": {}, "_record_provider_success": {},
                "check_tavily_quota": {},
                "_provider_limit": {"return_value": 1},
                "search_slot": {"side_effect": lambda p: nullcontext()},
                "_tavily_search": {"side_effect": primary},
                "_fallback_search": {"side_effect": backup},
            }.items():
                stack.enter_context(patch.object(websearch, name, **options))
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(search_web, '"Jane Smith" University')
                self.assertTrue(started.wait(2))
                second = pool.submit(search_web, '"John Doe" University')
                self.assertEqual(second.result(timeout=4)[0]["href"], "https://example.edu/b")
                self.assertEqual(first.result(timeout=4)[0]["href"], "https://example.edu/a")
