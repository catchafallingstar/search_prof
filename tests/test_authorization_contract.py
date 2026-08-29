import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import _with_effective_verification_status


PROJECT_DIR = Path(__file__).resolve().parents[1]


class AuthorizationContractTests(unittest.TestCase):
    def test_expired_verification_is_not_treated_as_verified(self) -> None:
        row = {
            "verification_status": "verified",
            "verification_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        self.assertEqual(_with_effective_verification_status(row)["verification_status"], "expired")

    def test_current_verification_stays_verified(self) -> None:
        row = {
            "verification_status": "verified",
            "verification_expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        }
        self.assertEqual(_with_effective_verification_status(row)["verification_status"], "verified")

    def test_schema_separates_site_authority_from_academic_role(self) -> None:
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS site_admins", schema)
        self.assertIn("admin_role IN ('owner', 'moderator')", schema)
        self.assertIn("account_role IN ('applicant', 'professor', 'institution_admin')", schema)

    def test_owner_can_resolve_hidden_faculty_identities(self) -> None:
        store = (PROJECT_DIR / "radar_store.py").read_text(encoding="utf-8")
        page = (PROJECT_DIR / "pages" / "5_Radar_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def review_faculty_identity(", store)
        self.assertIn("owner_only=True", store)
        self.assertIn("Confirm faculty identity", page)
        self.assertIn("Retry automatic check", page)
        self.assertIn("Mark as not faculty", page)


if __name__ == "__main__":
    unittest.main()
