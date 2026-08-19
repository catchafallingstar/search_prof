import unittest
from pathlib import Path

from ingestion.fetch_prof import _probable_pi_authorships, _work_matches_query
from ingestion.taxonomy import build_search_queries


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RadarContractTests(unittest.TestCase):
    def test_direct_work_filter_rejects_unrelated_concept_scattering(self) -> None:
        work = {
            "title": "Innovative Educational Techniques",
            "keywords": [
                {"display_name": "Artificial Intelligence"},
                {"display_name": "Border Security"},
            ],
        }
        self.assertFalse(_work_matches_query(work, "AI security"))

    def test_direct_work_filter_accepts_coherent_title(self) -> None:
        work = {"title": "Security of Artificial Intelligence Systems"}
        self.assertTrue(_work_matches_query(work, "AI security"))

    def test_ai_security_rejects_food_security_application(self) -> None:
        work = {
            "title": "AI Forecasting Innovations in Food Security and Agricultural Supply Chains"
        }
        self.assertFalse(_work_matches_query(work, "AI security"))

    def test_ai_security_accepts_red_teaming(self) -> None:
        work = {"title": "Red-Teaming for Generative AI"}
        self.assertTrue(_work_matches_query(work, "AI security"))

    def test_discovered_signal_requires_moderation(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "parse_hiring_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("'pending', NULL, NOW() + INTERVAL '120 days'", source)
        self.assertNotIn("'active', NOW(), NOW() + INTERVAL '120 days'", source)

    def test_discovery_keeps_first_corresponding_and_senior_authors(self) -> None:
        authorships = [
            {"author": {"id": "student"}, "author_position": "first"},
            {"author": {"id": "pi-1"}, "is_corresponding": True},
            {"author": {"id": "pi-2"}, "author_position": "last"},
        ]
        self.assertEqual(
            [item["author"]["id"] for item in _probable_pi_authorships(authorships)],
            ["student", "pi-1", "pi-2"],
        )

    def test_discovery_keeps_repeated_middle_author_for_later_verification(self) -> None:
        authorships = [
            {"author": {"id": "first"}, "author_position": "first"},
            {"author": {"id": "new-pi"}, "author_position": "middle"},
            {"author": {"id": "last"}, "author_position": "last"},
        ]
        result = _probable_pi_authorships(authorships, {"new-pi": 2})
        self.assertEqual([item["author"]["id"] for item in result], ["first", "new-pi", "last"])

    def test_query_expansion_is_reusable_and_covers_ai_security_subtopics(self) -> None:
        queries = build_search_queries("AI security")
        self.assertIn("network intrusion detection", queries)
        self.assertIn("AI security", queries)

    def test_discovery_keeps_a_deeper_pool_for_progressive_verification(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("target_professors * 6", source)
        self.assertIn("candidate_budget = min(600", source)
        self.assertIn('"candidates_ranked": len(ranked_prospects)', source)

    def test_publication_affiliation_cannot_overwrite_safe_current_appointment(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("faculty_verification_version >= 2", source)
        self.assertIn("THEN institution_name", source)

    def test_duplicate_openalex_people_do_not_collide_on_affiliation_update(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("other.id <> professors.id", source)
        self.assertIn("other.name = professors.name", source)


if __name__ == "__main__":
    unittest.main()
