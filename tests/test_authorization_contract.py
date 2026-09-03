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


if __name__ == "__main__":
    unittest.main()
