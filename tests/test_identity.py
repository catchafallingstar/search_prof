import unittest

from ingestion.homepagefinder import _result_matches_professor, result_matches_institution
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

    def test_search_result_must_match_professor_institution(self) -> None:
        self.assertTrue(
            result_matches_institution(
                "University of Southern California",
                "Yue Wang | USC Computer Science",
            )
        )
        self.assertFalse(
            result_matches_institution(
                "Illinois Institute of Technology",
                "Yan Lab, Shanghai Institute of Immunity and Infection",
            )
        )


if __name__ == "__main__":
    unittest.main()
