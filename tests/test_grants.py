import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.check_grants import (
    _nih_awards_for_professor,
    _grant_matches_domain,
    _institution_matches,
    _person_matches,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]


class GrantMatchingTests(unittest.TestCase):
    @patch("ingestion.check_grants.requests.post")
    def test_nih_reporter_projects_are_normalized_for_shared_matching(self, post) -> None:
        response = post.return_value
        response.json.return_value = {"results": [{
            "core_project_num": "R01-123",
            "project_title": "Materials chemistry for batteries",
            "abstract_text": "Chemistry of new battery materials.",
            "activity_code": "R01",
            "principal_investigators": [{"full_name": "Ada Lovelace"}],
            "organization": {"org_name": "Example University"},
            "award_amount": 125000,
            "project_start_date": "2026-01-01",
            "project_end_date": "2030-12-31",
        }]}
        awards = _nih_awards_for_professor({"name": "Ada Lovelace"})
        self.assertEqual(awards[0]["_source"], "NIH RePORTER")
        self.assertEqual(awards[0]["pdPIName"], "Ada Lovelace")
        self.assertEqual(awards[0]["awardeeName"], "Example University")
        self.assertEqual(awards[0]["id"], "R01-123")
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
        self.assertIn("professor_ids: list[int]", source)
        self.assertIn("WHERE id = ANY(%s)", source)
        self.assertNotIn("WHERE research_domain = %s", source)
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        database_source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("research_domains TEXT[]", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS professor_topic_grant_checks", schema)
        self.assertIn("radar_topic_id", schema)
        self.assertIn("next_check_at", schema)
        self.assertIn("= ANY(research_domains)", database_source)
        store_source = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        self.assertIn("def save_topic_grant_checks", store_source)

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
