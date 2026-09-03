import unittest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from ingestion.index_worker import (
    _enrich_professors,
    _verify,
    process_job,
    run_job_isolated,
)
from radar_store import normalize_topic_query, save_topic_candidates, topic_key


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _slow_job(_job):
    time.sleep(10)


class BackgroundIndexTests(unittest.TestCase):
    def setUp(self):
        capacity = patch("ingestion.index_worker.search_provider_runtime_state", return_value={"available": ["ddgs"], "retry_after_seconds": 0})
        capacity.start()
        self.addCleanup(capacity.stop)

    def test_topic_identity_is_shared_across_spacing_and_case(self) -> None:
        self.assertEqual(topic_key("AI security"), topic_key("  ai   SECURITY  "))
        self.assertEqual(normalize_topic_query("AI-security"), "ai security")

    def test_known_singular_and_plural_topic_aliases_share_one_index(self) -> None:
        self.assertEqual(normalize_topic_query("Asian Study"), "asian studies")
        self.assertEqual(topic_key("Asian Study"), topic_key("Asian studies"))

    def test_successful_discovery_delays_maintenance_and_active_jobs_block_it(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        discovery_update = source[
            source.index("def update_topic_after_discovery") :
            source.index("def fetch_topic_candidate_ids")
        ]
        maintenance = source[
            source.index("def enqueue_due_maintenance") :
            source.index("def _attach_identity_review_context")
        ]
        self.assertIn("next_refresh_at = NOW() + INTERVAL '30 days'", discovery_update)
        self.assertIn("NOT EXISTS", maintenance)
        self.assertIn("active_job.status IN ('queued', 'running')", maintenance)

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

    def test_worker_keeps_direct_checks_running_during_search_cooldown(self) -> None:
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        store = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn('"VERIFY_FACULTY",', worker)
        self.assertIn('"CHECK_HIRING",', worker)
        self.assertIn("job = claim_next_radar_job(worker_id, search_ready=search_ready)", worker)
        self.assertIn("NOT (job_type = ANY(%s::TEXT[]))", store)
        self.assertNotIn("search_jobs_paused_until", worker)
        self.assertIn("identity_retry_at", store)
        self.assertIn("include_deferred=True", worker)

    def test_hiring_refresh_is_per_professor_freshness_and_batched(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn("INTERVAL '24 hours'", source)
        self.assertIn("request_visible_hiring_refreshes", source)
        self.assertIn("public_hiring_check_status", source)
        self.assertIn("hiring_refresh_needed", source)
        self.assertIn("hiring_check_pending", source)

    def test_hiring_jobs_support_direct_professor_refresh(self) -> None:
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('job.get("professor_id") is not None', worker)

    @patch("ingestion.index_worker.enqueue_radar_job")
    @patch("ingestion.index_worker.refresh_topic_coverage")
    @patch("ingestion.index_worker.fetch_topic_candidate_ids")
    @patch("ingestion.index_worker.verify_faculty_candidates")
    @patch("ingestion.index_worker.setting_int", return_value=1)
    @patch("ingestion.index_worker._topic_for_job")
    def test_verification_uses_one_candidate_when_one_provider_is_available(
        self, topic_for_job, batch_size, verify, fetch_ids, refresh, enqueue
    ) -> None:
        topic_for_job.return_value = {"id": 7}
        verify.return_value = {"evaluated": 1, "verified": 0}
        fetch_ids.side_effect = [[11], [12]]
        refresh.return_value = {
            "verified_count": 0,
            "desired_results": 100,
            "candidates_seen": 20,
        }

        _, needs_more = _verify({"job_type": "VERIFY_FACULTY"})

        self.assertTrue(needs_more)
        self.assertEqual(fetch_ids.call_args_list[0].args, (7, 1))
        verify.assert_called_once_with([11])
        enqueue.assert_not_called()

    @patch(
        "ingestion.index_worker.setting_int", return_value=3,
    )
    @patch("ingestion.index_worker.enqueue_radar_job")
    @patch("ingestion.index_worker.refresh_topic_coverage")
    @patch("ingestion.index_worker.fetch_topic_candidate_ids")
    @patch("ingestion.index_worker.verify_faculty_candidates")
    @patch("ingestion.index_worker._topic_for_job")
    def test_newly_verified_faculty_enrich_before_the_whole_topic_finishes(
        self, topic_for_job, verify, fetch_ids, refresh, enqueue, _provider_state
    ) -> None:
        topic_for_job.return_value = {"id": 7}
        verify.return_value = {"evaluated": 3, "verified": 1}
        fetch_ids.side_effect = [[11, 12, 13], [14]]
        refresh.return_value = {
            "verified_count": 10,
            "desired_results": 100,
            "candidates_seen": 50,
        }
        _, needs_more = _verify({"job_type": "VERIFY_FACULTY"})
        self.assertTrue(needs_more)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0], "ENRICH_PROFESSORS")

    @patch(
        "ingestion.index_worker.setting_int", return_value=3,
    )
    @patch("ingestion.index_worker.enqueue_radar_job")
    @patch("ingestion.index_worker.refresh_topic_coverage")
    @patch("ingestion.index_worker.fetch_topic_candidate_ids")
    @patch("ingestion.index_worker.verify_faculty_candidates")
    @patch("ingestion.index_worker._topic_for_job")
    def test_completed_verification_enqueues_one_combined_enrichment_job(
        self, topic_for_job, verify, fetch_ids, refresh, enqueue, _provider_state
    ) -> None:
        topic_for_job.return_value = {"id": 7}
        verify.return_value = {"evaluated": 2, "verified": 1}
        fetch_ids.side_effect = [[11, 12], [], []]
        refresh.return_value = {
            "verified_count": 8,
            "desired_results": 100,
            "candidates_seen": 20,
        }
        _, needs_more = _verify({"job_type": "VERIFY_FACULTY"})
        self.assertFalse(needs_more)
        enqueue.assert_called_once_with(
            "ENRICH_PROFESSORS",
            radar_topic_id=7,
            requested_by=None,
            priority=86,
            max_attempts=20,
        )

    @patch("ingestion.index_worker._check_hiring")
    @patch("ingestion.index_worker._check_grants")
    def test_grants_and_hiring_share_one_parallel_enrichment_stage(
        self, grants, hiring
    ) -> None:
        grants.return_value = ({"professors_checked": 2}, False)
        hiring.return_value = ({"professors_checked": 2}, True)
        result, needs_more = _enrich_professors({"radar_topic_id": 7})
        self.assertEqual(result["grants"]["professors_checked"], 2)
        self.assertEqual(result["hiring"]["professors_checked"], 2)
        self.assertTrue(needs_more)

    def test_successful_continuation_does_not_consume_failure_attempts(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        continuation = source[source.index("def reschedule_radar_job"):]
        self.assertIn("SET status = 'queued', attempts = 0", continuation)
        self.assertIn("last_error = NULL", continuation)

    def test_worker_isolates_jobs_without_an_aggregate_deadline(self) -> None:
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def run_job_isolated", worker)
        self.assertIn('multiprocessing.get_context("fork")', worker)
        self.assertNotIn("Job exceeded its", worker)
        self.assertNotIn('setting_int("INDEX_JOB_TIMEOUT_SECONDS"', worker)
        self.assertIn("update_worker_heartbeat(worker_id", worker)

    def test_identity_jobs_are_small_and_resume_from_saved_decisions(self) -> None:
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        verifier = (PROJECT_DIR / "ingestion" / "verify_faculty.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('setting_int("INDEX_VERIFY_BATCH_SIZE", 1, 1, 6)', worker)
        self.assertIn("_save_result(candidate, result)", verifier)
        self.assertIn("query_limit = 3", verifier, "Expected the agreed three-query maximum")
        self.assertIn("FACULTY_SEARCH_QUERY_TIMEOUT_SECONDS", verifier)
        self.assertTrue('setting_int("FACULTY_IDENTITY_PASS_PAGES", 20, 3, 20)' in verifier, 'Expected a bounded twenty-page maximum')

    @patch("ingestion.index_worker.update_worker_heartbeat")
    @patch("ingestion.index_worker.process_job", return_value=({"ok": True}, False))
    def test_worker_waits_for_isolated_job_completion(
        self, _process_job, heartbeat
    ) -> None:
        result, needs_more = run_job_isolated({"id": 999}, "test-worker")
        self.assertEqual(result, {"ok": True})
        self.assertFalse(needs_more)
        heartbeat.assert_called()

    def test_retry_uses_a_fresh_runtime_window(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn("started_at = NOW(), completed_at = NULL", source)
        self.assertIn("started_at = NULL", source)

    def test_search_outages_do_not_become_identity_decisions(self) -> None:
        verifier = (PROJECT_DIR / "ingestion" / "verify_faculty.py").read_text(
            encoding="utf-8"
        )
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        store = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        websearch = (PROJECT_DIR / "ingestion" / "websearch.py").read_text(
            encoding="utf-8"
        )
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("except SearchUnavailable", verifier)
        self.assertIn("failures.append(error)", verifier)
        self.assertIn("their identity decisions were left unchanged", verifier)
        self.assertIn('"retry_after_seconds"', worker)
        self.assertIn('getattr(error, "retry_after_seconds"', store)
        self.assertIn("dependency_outage = requested_delay > 0", store)
        self.assertIn("GREATEST(attempts - 1, 0)", store)
        self.assertIn('"job_deferred"', worker)
        self.assertIn("before releasing the provider semaphore", websearch)
        self.assertIn("CREATE TABLE IF NOT EXISTS web_search_provider_health", schema)
        self.assertIn("next_request_at TIMESTAMPTZ", schema)
        self.assertIn("ENRICH_PROFESSORS", schema)
        staff_page = (PROJECT_DIR / "pages" / "5_Radar_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Web-search fallback is paused", staff_page)
        self.assertIn('"search_providers": search_providers', store)

    def test_staff_dashboard_distinguishes_stalled_jobs(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        page = (PROJECT_DIR / "pages" / "5_Radar_control.py")
        if not page.exists():
            page = PROJECT_DIR / "5_Radar_control.py"
        self.assertIn("THEN 'stalled'", source)
        page_source = page.read_text(encoding="utf-8")
        self.assertIn('counts.get("stalled", 0)', page_source)
        self.assertIn('"Recover overdue tasks"', page_source)
        self.assertIn("recover_stalled_radar_jobs", source)
        self.assertIn("worker.worker_id = job.locked_by", source)
        self.assertIn("worker.last_seen_at > NOW() - INTERVAL '2 minutes'", source)

    def test_staff_dashboard_has_live_identity_progress(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        page = (PROJECT_DIR / "pages" / "5_Radar_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def fetch_live_indexing_status", source)
        self.assertIn("pending_identities", source)
        self.assertIn("checked_since_worker_start", source)
        self.assertIn('@st.fragment(run_every="5s")', page)
        self.assertIn("Live indexing activity", page)
        self.assertIn("Most recent activity (20)", page)
        self.assertIn("Current professor batch", page)
        self.assertIn("Page identified:", page)
        self.assertIn("activity_logs", source)
        self.assertIn("faculty_verification_evidence", source)

    def test_worker_persists_current_professor_batch_for_staff(self) -> None:
        store = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        worker = (PROJECT_DIR / "ingestion" / "index_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def update_radar_job_progress", store)
        self.assertIn('"live_professors": professors', store)
        self.assertIn("update_radar_job_progress(", worker)
        self.assertIn('"VERIFY_FACULTY"', worker)
        self.assertIn('"CHECK_GRANTS"', worker)
        self.assertIn('"CHECK_HIRING"', worker)

    def test_staff_topic_table_explains_real_coverage(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        page = (PROJECT_DIR / "pages" / "5_Radar_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("AS exact_evidence_professors", source)
        self.assertIn("AS fresh_hiring_checked", source)
        self.assertIn('topic["coverage_stage"]', source)
        self.assertIn('"Candidates": row["candidates_seen"]', page)
        self.assertIn('"Verified faculty": row["verified_count"]', page)
        self.assertIn('"Exact evidence": row["exact_evidence_professors"]', page)
        self.assertIn('"Hiring checked": row["fresh_hiring_checked"]', page)
        self.assertIn('"Problems": row["problem_count"]', page)

    def test_staff_identity_review_includes_research_and_source_context(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        page = (PROJECT_DIR / "pages" / "5_Radar_control.py")
        page_source = page.read_text(encoding="utf-8")
        self.assertIn("def _attach_identity_review_context", source)
        self.assertIn('identity["identity_papers"]', source)
        self.assertIn('identity["matching_topics"]', source)
        self.assertIn('identity["topic_paper_evidence"]', source)
        self.assertIn('identity["historical_topics"]', source)
        self.assertIn('identity["identity_evidence"]', source)
        self.assertIn("Papers supporting current search matches", page_source)
        self.assertIn("Other recent papers for identity checking", page_source)
        self.assertIn("Saved identity sources", page_source)
        self.assertIn('row.get("evidence_excerpt")', page_source)
        self.assertIn("Imported institution (may be historical)", page_source)

    def test_reindex_marks_only_the_latest_candidate_set_as_current(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("is_current_match BOOLEAN NOT NULL DEFAULT TRUE", schema)
        self.assertIn("SET is_current_match = FALSE", source)
        self.assertIn("is_current_match = TRUE", source)

    def test_topic_professor_match_keeps_exact_supporting_papers(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_topic_professor_papers", schema)
        self.assertIn("relevance_score NUMERIC(5, 2)", schema)
        self.assertIn("matched_query TEXT NOT NULL", schema)
        self.assertIn("INSERT INTO radar_topic_professor_papers", source)
        self.assertIn("evidence.is_current_match = TRUE", source)

    @patch("radar_store.get_db_connection")
    def test_saving_candidates_writes_their_exact_paper_evidence(
        self, get_connection
    ) -> None:
        connection = MagicMock()
        cursor = MagicMock()
        get_connection.return_value.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        save_topic_candidates(
            7,
            [{
                "professor_id": 11,
                "research_score": 28,
                "matching_papers": 1,
                "supporting_papers": [{
                    "paper_id": 13,
                    "relevance_score": 8,
                    "matched_query": "AI security",
                }],
            }],
        )

        evidence_calls = [
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO radar_topic_professor_papers" in call.args[0]
        ]
        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(
            evidence_calls[0].args[1],
            (7, 11, 13, 8.0, "AI security", None),
        )

    def test_old_discovery_versions_upgrade_only_when_requested(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("RADAR_DISCOVERY_VERSION = 4", source)
        self.assertIn('topic.get("discovery_version")', source)
        self.assertIn("discovery_version INTEGER NOT NULL DEFAULT 0", schema)
        self.assertIn("return topic, []", source)
        self.assertIn("current_match.is_current_match = TRUE", source)

    def test_admin_can_queue_real_versioned_topic_rebuilds(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        script = (PROJECT_DIR / "scripts" / "rebuild_topics.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def fetch_topic_rebuild_status", source)
        self.assertIn("def queue_outdated_topic_rebuilds", source)
        self.assertIn("professors_with_evidence < current_professors", source)
        self.assertIn('"REINDEX_RESEARCH"', source)
        self.assertIn("queue_outdated_topic_rebuilds", script)
        self.assertIn("--status", script)

    def test_reused_queued_job_can_be_promoted(self) -> None:
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        enqueue = source[source.index("def enqueue_radar_job"):]
        self.assertIn("WHEN status = 'queued' THEN GREATEST(priority, %s)", enqueue)

    def test_controlled_seed_catalog_is_broad_unique_and_low_priority(self) -> None:
        from ingestion.research_seeds import RESEARCH_SEED_GROUPS, seed_topic_names

        topics = seed_topic_names()
        normalized = [normalize_topic_query(topic) for topic in topics]
        self.assertGreaterEqual(len(RESEARCH_SEED_GROUPS), 8)
        self.assertGreaterEqual(len(topics), 80)
        self.assertEqual(len(normalized), len(set(normalized)))
        self.assertIn("Political science", topics)
        self.assertIn("Molecular biology", topics)
        source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        script = (PROJECT_DIR / "scripts" / "seed_topics.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def queue_seed_topics", source)
        self.assertIn("enforce_hourly_limit=False", source)
        self.assertIn("priority=20", script)


if __name__ == "__main__":
    unittest.main()
