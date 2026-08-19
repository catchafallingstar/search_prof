import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.verify_faculty import (
    _institution_name_from_title,
    inspect_faculty_result,
    verify_faculty_candidate,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]


class FacultyVerificationTests(unittest.TestCase):
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="University of Arizona")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_official_faculty_page_verifies_new_assistant_professor(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Jingdi Chen Assistant Professor of Electrical and Computer Engineering. "
            "After a postdoc at Carnegie Mellon University, she started the ANNIE Research Group.",
            "Jingdi Chen | Electrical and Computer Engineering",
        )
        result = inspect_faculty_result(
            {"name": "Jingdi Chen", "institution_name": "Carnegie Mellon University"},
            {
                "title": "Jingdi Chen | Electrical and Computer Engineering",
                "body": "Faculty at the University of Arizona",
                "href": "https://ece.engineering.arizona.edu/faculty-staff/faculty/jingdi-chen",
            },
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["institution_name"], "University of Arizona")
        self.assertIn("Assistant Professor", result["title"])

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="UNC Charlotte")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_official_student_page_does_not_become_professor(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Jinal Bhanubhai Butani graduate student and data scientist at UNC Charlotte.",
            "Jinal Bhanubhai Butani",
        )
        result = inspect_faculty_result(
            {"name": "Jinal Bhanubhai Butani", "institution_name": "UNC Charlotte"},
            {
                "title": "Jinal Bhanubhai Butani",
                "body": "UNC Charlotte researcher",
                "href": "https://example.charlotte.edu/jinal-butani",
            },
        )
        self.assertEqual(result["status"], "NOT_FACULTY")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Southern Illinois University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_directory_text_cannot_assign_another_persons_title(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Ning Weng Faculty list. Mehdi Ghasemirahaghi Assistant Professor.",
            "Mehdi Ghasemirahaghi Faculty | SIU",
        )
        result = inspect_faculty_result(
            {"name": "Ning Weng", "institution_name": "Southern Illinois University"},
            {
                "title": "Mehdi Ghasemirahaghi Faculty | SIU",
                "body": "Ning Weng appears in the faculty navigation",
                "href": "https://academics.siu.edu/engineering/faculty/mehdi.php",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Stanford University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_extra_name_token_does_not_verify_a_different_person(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Syed Wali Kamal Clinical Assistant Professor at Stanford University.",
            "Syed Wali Kamal | Stanford Medicine",
        )
        result = inspect_faculty_result(
            {"name": "Syed Wali", "institution_name": "Texas A&M University"},
            {
                "title": "Syed Wali Kamal | Stanford Medicine",
                "body": "Syed Wali Kamal, Assistant Professor",
                "href": "https://med.stanford.edu/profiles/syed-kamal",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Rowan University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_same_name_professor_in_unrelated_field_is_a_conflict(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Bokyung Kim Professor of Public Relations and Advertising. "
            "Her scholarship studies strategic communication and media.",
            "Bokyung Kim | Rowan University",
        )
        result = inspect_faculty_result(
            {
                "name": "Bokyung Kim",
                "institution_name": "Rowan University",
                "research_domain": "AI security",
            },
            {
                "title": "Bokyung Kim | Rowan University",
                "body": "Professor of Public Relations and Advertising",
                "href": "https://www.rowan.edu/communication/faculty/bokyung-kim.html",
            },
        )
        self.assertEqual(result["status"], "CONFLICT")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Rowan University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_candidate_verifier_preserves_research_conflict(
        self, search_web, fetch_page, _institution
    ) -> None:
        search_web.return_value = [
            {
                "title": "Bokyung Kim | Rowan University",
                "body": "Professor of Public Relations and Advertising",
                "href": "https://www.rowan.edu/communication/faculty/bokyung-kim.html",
            }
        ]
        fetch_page.return_value = (
            "Bokyung Kim Professor of Public Relations and Advertising.",
            "Bokyung Kim | Rowan University",
        )
        result = verify_faculty_candidate(
            {
                "name": "Bokyung Kim",
                "institution_name": "Rowan University",
                "research_domain": "AI security",
            }
        )
        self.assertEqual(result["status"], "CONFLICT")

    def test_doctoral_graduate_page_cannot_verify_faculty(self) -> None:
        result = inspect_faculty_result(
            {"name": "Jalal Ghadermazi", "institution_name": "University of South Florida"},
            {
                "title": "Jalal Ghadermazi",
                "body": "Major Professor and doctoral graduate",
                "href": "https://www.usf.edu/people/recent-doctoral-graduates.aspx",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_non_university_page_cannot_verify_faculty(self) -> None:
        result = inspect_faculty_result(
            {"name": "Jingdi Chen", "institution_name": "University of Arizona"},
            {
                "title": "Jingdi Chen",
                "body": "Assistant Professor",
                "href": "https://social.example.com/jingdi",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_official_root_title_can_name_current_institution(self) -> None:
        self.assertEqual(
            _institution_name_from_title(
                "University of Arizona | The University of Arizona"
            ),
            "University of Arizona",
        )

    def test_public_query_requires_verified_faculty(self) -> None:
        source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("AND p.faculty_status = 'VERIFIED'", source)
        self.assertIn("p.faculty_verification_version >= 3", source)

    def test_schema_retains_verification_evidence(self) -> None:
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS faculty_verification_evidence", schema)
        self.assertIn("faculty_status TEXT NOT NULL DEFAULT 'UNVERIFIED'", schema)
        self.assertIn("faculty_verification_version INTEGER NOT NULL DEFAULT 0", schema)


if __name__ == "__main__":
    unittest.main()
