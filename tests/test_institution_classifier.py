import unittest
from unittest.mock import patch

from ingestion.institution_classifier import (
    lookup_institution_classification,
    normalize_domain,
    normalize_institution_name,
    obvious_k12_name,
)
from ingestion.verify_faculty import (
    _linkedin_headline,
    _public_snippet_nonfaculty_consensus,
)


class InstitutionClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        lookup_institution_classification.cache_clear()

    def test_scorecard_normalization(self) -> None:
        self.assertEqual(normalize_institution_name("St. John's University"), "st john s university")
        self.assertEqual(normalize_domain("https://www.example.edu/path"), "example.edu")

    def test_university_school_is_k12(self) -> None:
        self.assertTrue(obvious_k12_name("University School of Nashville"))
        result = lookup_institution_classification("University School of Nashville")
        self.assertEqual(result["organization_type"], "K12_SCHOOL")

    def test_scorecard_absence_is_not_not_faculty(self) -> None:
        with patch("ingestion.institution_classifier.get_db_connection", side_effect=RuntimeError("not loaded")):
            result = lookup_institution_classification("Unknown Institute")
        self.assertEqual(result["organization_type"], "UNKNOWN")

    def test_linkedin_headline_requires_person_profile(self) -> None:
        row = {
            "title": "Olubusola Odeyemi - Assistant Director of Finance | LinkedIn",
            "href": "https://www.linkedin.com/in/olubusola-odeyemi-ba7753bb",
        }
        self.assertEqual(_linkedin_headline("Olubusola Odeyemi", row), "Assistant Director of Finance")
        row["href"] = "https://www.linkedin.com/posts/example"
        self.assertEqual(_linkedin_headline("Olubusola Odeyemi", row), "")

    def test_identity_linked_nonfaculty_headline_is_not_faculty(self) -> None:
        results = [{
            "title": "Olubusola Odeyemi - Assistant Director of Finance | LinkedIn",
            "body": "Trevecca Nazarene University Master of Business Administration",
            "href": "https://www.linkedin.com/in/olubusola-odeyemi-ba7753bb",
        }]
        candidate = {
            "name": "Olubusola Odeyemi",
            "institution_name": "Trevecca Nazarene University",
            "recent_papers": [],
        }
        result = _public_snippet_nonfaculty_consensus(
            candidate["name"], results, candidate
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "NOT_FACULTY")
        self.assertEqual(result["method"], "linkedin_no_faculty_headline")

    def test_linkedin_professor_headline_is_not_negative(self) -> None:
        results = [{
            "title": "Alex Smith - Assistant Professor | LinkedIn",
            "body": "Alex Smith at Example University",
            "href": "https://www.linkedin.com/in/alex-smith",
        }]
        candidate = {"name": "Alex Smith", "institution_name": "Example University"}
        self.assertIsNone(_public_snippet_nonfaculty_consensus("Alex Smith", results, candidate))

    def test_unlinked_linkedin_headline_does_not_decide(self) -> None:
        results = [{
            "title": "Alex Smith - Software Engineer | LinkedIn",
            "body": "Works at Example Company",
            "href": "https://www.linkedin.com/in/alex-smith-123",
        }]
        candidate = {"name": "Alex Smith", "institution_name": "Different University"}
        self.assertIsNone(_public_snippet_nonfaculty_consensus("Alex Smith", results, candidate))


if __name__ == "__main__":
    unittest.main()
