import unittest
from pathlib import Path
from unittest.mock import ANY, call, patch

from db import _radar_query_key
from ingestion.radar_pipeline import execute_radar


PROJECT_DIR = Path(__file__).resolve().parents[1]


class PublicRadarContractTests(unittest.TestCase):
    def test_schema_tracks_targeted_runs_and_rank(self) -> None:
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_runs", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_run_results", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_run_professors", schema)
        self.assertIn("target_professors INTEGER", schema)
        self.assertIn("candidates_ranked INTEGER", schema)
        self.assertIn("faculty_identities_checked INTEGER", schema)
        self.assertIn("organic_score INTEGER", schema)

    def test_verified_submissions_have_highest_organic_scores(self) -> None:
        source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("100 if verified_profile else 95", source)
        self.assertIn("ORDER BY o.organic_score DESC", source)
        self.assertIn("AND o.status <> 'rejected'", source)

    def test_public_signals_never_claim_gpa_flexibility(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "parse_hiring_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("'unknown', 'not_stated'", source)

    def test_public_app_has_visible_live_radar_progress(self) -> None:
        source = (PROJECT_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn("Include a live public-web radar", source)
        self.assertIn("st.status", source)
        self.assertIn("st.progress", source)
        self.assertIn("[10, 25, 50, 100]", source)
        self.assertIn("Verified faculty goal", source)
        self.assertIn("100 — deep index", source)
        self.assertIn("press Search", source)
        self.assertIn("Continue checking an incomplete cached search", source)

    def test_professor_categories_follow_evidence_strength(self) -> None:
        database_source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        app_source = (PROJECT_DIR / "app.py").read_text(encoding="utf-8")
        expected = [
            "confirmed_opening",
            "public_hiring_signal",
            "early_career_funded",
            "early_career",
            "funded_lab",
            "research_match",
        ]
        for category in expected:
            self.assertIn(category, database_source)
            self.assertIn(category, app_source)
        self.assertLess(
            app_source.index("public_hiring_signal"),
            app_source.index("early_career_funded"),
        )
        self.assertIn("shared_latest_paper_count", database_source)
        self.assertNotIn("LIKE '%assistant professor%'", database_source)
        self.assertIn("LIKE '%%assistant professor%%'", database_source)
        self.assertIn("Hiring now — current evidence", app_source)
        self.assertIn("Likely opportunities — hiring not confirmed", app_source)
        self.assertIn("Other verified faculty matches — hiring unknown", app_source)

    def test_continuation_rotates_public_checks_to_unchecked_professors(self) -> None:
        database_source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        pipeline_source = (
            PROJECT_DIR / "ingestion" / "radar_pipeline.py"
        ).read_text(encoding="utf-8")
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("public_hiring_checked_at", schema)
        self.assertIn("grant_checked_at", schema)
        self.assertIn("prioritize_professors_for_enrichment", database_source)
        self.assertIn("prioritize_professors_for_enrichment", pipeline_source)

    def test_cli_can_build_a_deep_cache_in_multiple_bounded_passes(self) -> None:
        source = (PROJECT_DIR / "scripts" / "run_radar.py").read_text(encoding="utf-8")
        self.assertIn('"--passes"', source)
        self.assertIn("continue_partial=True", source)
        self.assertIn("verified_goal", source)

    def test_cache_identity_includes_requested_search_size(self) -> None:
        self.assertNotEqual(
            _radar_query_key("AI security", 10, 10),
            _radar_query_key("AI security", 100, 20),
        )
        pipeline_source = (
            PROJECT_DIR / "ingestion" / "radar_pipeline.py"
        ).read_text(encoding="utf-8")
        self.assertIn("force_new=True", pipeline_source)
        self.assertIn("get_cached_faculty_decisions", pipeline_source)

    @patch("ingestion.radar_pipeline.fetch_radar_prospects")
    @patch("ingestion.radar_pipeline.fetch_radar_results")
    @patch("ingestion.radar_pipeline.fetch_radar_run")
    @patch("ingestion.radar_pipeline.finish_radar_run")
    @patch("ingestion.radar_pipeline.scan_hiring_signals")
    @patch("ingestion.radar_pipeline.check_and_save_grants")
    @patch("ingestion.radar_pipeline.verify_faculty_candidates")
    @patch("ingestion.radar_pipeline.fetch_professors_by_keywords")
    @patch("ingestion.radar_pipeline.normalize_taxonomy")
    @patch("ingestion.radar_pipeline.save_radar_prospects")
    @patch("ingestion.radar_pipeline.mark_radar_prospects_checked")
    @patch("ingestion.radar_pipeline.update_radar_run")
    @patch("ingestion.radar_pipeline.start_or_reuse_radar_run")
    def test_pipeline_tracks_progress_and_returns_exact_run_results(
        self,
        start_run,
        update_run,
        mark_checked,
        save_prospects,
        normalize,
        discover,
        verify,
        grants,
        signals,
        finish_run,
        fetch_run,
        fetch_results,
        fetch_prospects,
    ) -> None:
        start_run.return_value = ({"id": 42, "status": "running", "progress": 0}, False)
        normalize.return_value = {"topic_name": "AI safety"}
        prospect_rows = [
            {"professor_id": 2, "research_score": 30, "matching_papers": 2},
            {"professor_id": 4, "research_score": 28, "matching_papers": 1},
            {"professor_id": 8, "research_score": 25, "matching_papers": 1},
        ]
        discover.return_value = {
            "professors": 3,
            "papers": 4,
            "professor_ids": [2, 4, 8],
            "prospects": prospect_rows,
        }
        verify.return_value = {
            "verified_ids": [2, 4, 8], "checked": 3, "evaluated": 3, "verified": 3
        }
        grants.return_value = {"grants_added": 1}
        signals.return_value = {
            "professors_checked": 3,
            "signals_added": 1,
            "timed_out": False,
            "checked_professor_ids": [2, 4, 8],
        }
        fetch_run.return_value = {"id": 42, "status": "completed", "progress": 100}
        fetch_results.return_value = [{"opportunity_id": 7}]
        fetch_prospects.return_value = [{"professor_id": 2, "result_category": "early_career"}]

        stages: list[str] = []
        with (
            patch("ingestion.radar_pipeline.setting", return_value="test-key"),
            patch("ingestion.radar_pipeline.setting_int", side_effect=lambda _n, default, _min, _max: default),
            patch("ingestion.radar_pipeline.discover_hiring_first_leads", return_value={}),
            patch(
                "ingestion.radar_pipeline.prioritize_professors_for_enrichment",
                side_effect=lambda professor_ids: professor_ids,
            ),
            patch(
                "ingestion.radar_pipeline.get_cached_faculty_decisions",
                return_value={"verified_ids": [], "decided_ids": []},
            ),
        ):
            result = execute_radar(
                "AI safety",
                target_professors=25,
                progress_callback=lambda stage, _percent, _counts: stages.append(stage),
            )

        self.assertFalse(result["cached"])
        self.assertEqual(result["results"], [{"opportunity_id": 7}])
        self.assertEqual(result["professors"][0]["professor_id"], 2)
        self.assertIn("Understanding the research topic", stages)
        self.assertTrue(any("Verifying faculty identities" in stage for stage in stages))
        self.assertIn("Complete", stages)
        signals.assert_called_once_with(
            domain_name="AI safety",
            professor_ids=[2, 4, 8],
            progress_callback=ANY,
            radar_run_id=42,
        )
        save_prospects.assert_called_once_with(42, prospect_rows)
        discover.assert_called_once_with(normalize.return_value, target_professors=25)
        verify.assert_called_once_with([2, 4, 8])
        start_run.assert_called_once_with("AI safety", 25, 12, None)
        self.assertEqual(
            mark_checked.call_args_list,
            [
                call(42, [2, 4, 8], "grants"),
                call(42, [2, 4, 8], "public"),
            ],
        )
        finish_run.assert_called_once()
        self.assertGreaterEqual(update_run.call_count, 6)

    @patch("ingestion.radar_pipeline.fetch_radar_prospects")
    @patch("ingestion.radar_pipeline.fetch_radar_results")
    @patch("ingestion.radar_pipeline.start_or_reuse_radar_run")
    def test_pipeline_reuses_recent_completed_search(
        self, start_run, fetch_results, fetch_prospects
    ) -> None:
        start_run.return_value = (
            {"id": 9, "status": "completed", "progress": 100},
            True,
        )
        fetch_results.return_value = [{"opportunity_id": 11}]
        fetch_prospects.return_value = [{"professor_id": 12}]
        result = execute_radar("robotics")
        self.assertTrue(result["cached"])
        self.assertEqual(result["results"], [{"opportunity_id": 11}])
        self.assertEqual(result["professors"], [{"professor_id": 12}])


if __name__ == "__main__":
    unittest.main()
