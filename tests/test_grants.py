import unittest
from pathlib import Path

from ingestion.check_grants import (
    _grant_matches_domain,
    _institution_matches,
    _person_matches,
)


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

    def test_grant_scan_is_scoped_to_current_research_domain(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "check_grants.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("WHERE research_domain = %s", source)
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        database_source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("research_domains TEXT[]", schema)
        self.assertIn("= ANY(research_domains)", database_source)

    def test_unknown_taxonomy_does_not_disable_nsf_checks(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "check_grants.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("primary_agency') != \"NSF\"", source)
        self.assertIn("ThreadPoolExecutor", source)

    def test_ai_security_rejects_unrelated_ai_agriculture_grant(self) -> None:
        self.assertFalse(
            _grant_matches_domain(
                "AI security",
                {
                    "title": "AI-driven agricultural intelligence for controlled-environment agriculture"
                },
            )
        )

    def test_ai_security_accepts_cybersecurity_grant(self) -> None:
        self.assertTrue(
            _grant_matches_domain(
                "AI security",
                {"title": "Cybersecurity education, scholarship and service"},
            )
        )


if __name__ == "__main__":
    unittest.main()
