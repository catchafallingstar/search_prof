import unittest

from ingestion.homepagefinder import _result_matches_professor
from ingestion.socialradar import _identity_matches


class IdentityMatchingTests(unittest.TestCase):
    def test_social_identity_requires_given_and_family_name(self) -> None:
        self.assertTrue(_identity_matches("Ying Lin", "Ying Lin Lab"))
        self.assertFalse(_identity_matches("Ying Lin", "Brian Lin Lab"))

    def test_homepage_result_requires_given_and_family_name(self) -> None:
        result = {
            "title": "Ying Lin Laboratory",
            "body": "Faculty research page",
            "href": "https://example.edu/ying-lin",
        }
        self.assertTrue(_result_matches_professor("Ying Lin", result))
        self.assertFalse(_result_matches_professor("Brian Lin", result))


if __name__ == "__main__":
    unittest.main()
