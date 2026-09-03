import unittest
from pathlib import Path

from ingestion.check_grants import _institution_matches, _person_matches


PROJECT_DIR = Path(__file__).resolve().parents[1]


class GrantMatchingTests(unittest.TestCase):
    def test_person_match_requires_family_and_given_name(self) -> None:
        self.assertTrue(_person_matches("Ying Lin", "Lin, Ying"))
        self.assertFalse(_person_matches("Ying Lin", "Brian Lin"))

    def test_institution_match_ignores_generic_words(self) -> None:
        self.assertTrue(
            _institution_matches("University of Southern California", "Southern California University")
        )
        self.assertFalse(_institution_matches("Stanford University", "Harvard University"))

    def test_grant_scan_is_scoped_to_explicit_professor_ids(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "check_grants.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("WHERE id = ANY(%s)", source)
        self.assertNotIn("WHERE research_domain = %s", source)


if __name__ == "__main__":
    unittest.main()
