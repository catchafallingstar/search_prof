import unittest
from pathlib import Path

from ingestion.fetch_prof import _probable_pi_authorships, _work_matches_query


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

    def test_discovery_keeps_corresponding_and_senior_authors(self) -> None:
        authorships = [
            {"author": {"id": "student"}, "author_position": "first"},
            {"author": {"id": "pi-1"}, "is_corresponding": True},
            {"author": {"id": "pi-2"}, "author_position": "last"},
        ]
        self.assertEqual(
            [item["author"]["id"] for item in _probable_pi_authorships(authorships)],
            ["pi-1", "pi-2"],
        )


if __name__ == "__main__":
    unittest.main()
