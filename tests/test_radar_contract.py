import unittest
from pathlib import Path

from ingestion.fetch_prof import _work_matches_query


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

    def test_discovered_signal_requires_moderation(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "parse_hiring_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CASE WHEN %s THEN NOW() + INTERVAL '120 days' ELSE NOW() END", source)


if __name__ == "__main__":
    unittest.main()
