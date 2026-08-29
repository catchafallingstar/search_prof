import unittest
from pathlib import Path

from scripts.check_production import check_configuration


PROJECT_DIR = Path(__file__).resolve().parents[1]


class ProductionSafetyTests(unittest.TestCase):
    def test_safe_production_configuration_passes(self) -> None:
        checks = check_configuration(
            {
                "APP_ENV": "production",
                "DEV_AUTH_BYPASS": "false",
                "DATABASE_URL": "postgresql://app:secret@db.provider.net/app?sslmode=require",
                "OPENALEX_API_KEY": "real-openalex-key",
                "BRAVE_SEARCH_API_KEY": "real-brave-key",
                "CONTACT_EMAIL": "support@scholarradar.org",
            }
        )
        self.assertFalse([check for check in checks if check.level == "FAIL"])

    def test_local_development_configuration_fails_launch_check(self) -> None:
        checks = check_configuration(
            {
                "APP_ENV": "development",
                "DEV_AUTH_BYPASS": "true",
                "DATABASE_URL": "postgresql://app:secret@localhost/app",
                "OPENALEX_API_KEY": "replace-with-key",
                "BRAVE_SEARCH_API_KEY": "",
                "CONTACT_EMAIL": "you@example.com",
            }
        )
        failed_names = {check.name for check in checks if check.level == "FAIL"}
        self.assertIn("Production mode", failed_names)
        self.assertIn("Development login", failed_names)
        self.assertIn("Managed PostgreSQL", failed_names)
        self.assertIn("Correction contact", failed_names)

    def test_private_searxng_can_be_the_production_search_provider(self) -> None:
        checks = check_configuration(
            {
                "APP_ENV": "production",
                "DEV_AUTH_BYPASS": "false",
                "DATABASE_URL": "postgresql://app:secret@db.provider.net/app?sslmode=require",
                "OPENALEX_API_KEY": "real-openalex-key",
                "BRAVE_SEARCH_API_KEY": "",
                "SEARXNG_URL": "https://private-search.internal.example",
                "CONTACT_EMAIL": "support@scholarradar.org",
            }
        )
        self.assertFalse([check for check in checks if check.level == "FAIL"])

    def test_submission_limits_are_enforced_in_database_layer(self) -> None:
        source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("ROLE_VERIFICATION_HOURLY_LIMIT", source)
        self.assertIn("OPPORTUNITY_SUBMISSION_HOURLY_LIMIT", source)
        self.assertGreaterEqual(source.count("_enforce_submission_rate_limit("), 4)

    def test_public_policy_page_has_correction_path(self) -> None:
        source = (PROJECT_DIR / "pages" / "6_Data_and_policies.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Corrections and removal requests", source)
        self.assertIn("CONTACT_EMAIL", source)
        self.assertIn("Hiring signal found online", source)


if __name__ == "__main__":
    unittest.main()
