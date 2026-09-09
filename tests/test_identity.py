import unittest

from ingestion.homepagefinder import _result_matches_professor
from ingestion.institution_domains import institutions_equivalent
from ingestion.socialradar import _identity_matches
from ingestion.verify_faculty import (
    FACULTY_TITLE_PATTERN,
    _explicit_transition_corroborates,
    _non_us_faculty_claim,
    _role_institution_continuity,
    verify_faculty_candidates,
)


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

    def test_worker_can_supply_research_area_to_verifier(self) -> None:
        result = verify_faculty_candidates([], research_area="AI security")
        self.assertEqual(result["verified_ids"], [])
        self.assertEqual(result["checked"], 0)

    def test_texas_am_college_station_is_same_institution(self) -> None:
        self.assertTrue(institutions_equivalent(
            "Texas A&M University",
            "Texas A&M University-College Station",
        ))

    def test_overseas_adjunct_does_not_override_us_primary_role(self) -> None:
        text = (
            "Bill Cope is a Professor in the Department of Education Policy, "
            "Organization & Leadership, University of Illinois Urbana-Champaign, "
            "USA and an Adjunct Professor at Charles Darwin University, Australia."
        )
        role = FACULTY_TITLE_PATTERN.search(text)
        self.assertIsNotNone(role)
        self.assertFalse(_non_us_faculty_claim(
            text, "https://newlearningonline.com/bio", role
        ))
        self.assertTrue(_role_institution_continuity(
            "University of Illinois Urbana-Champaign", text, role
        ))

    def test_explicit_incoming_appointment_connects_prior_institution(self) -> None:
        text = (
            "Jovan Stojkovic is an incoming Assistant Professor at The University "
            "of Texas at Austin. He earned his PhD from the University of Illinois "
            "Urbana-Champaign."
        )
        role = FACULTY_TITLE_PATTERN.search(text)
        self.assertIsNotNone(role)
        self.assertTrue(_explicit_transition_corroborates(
            "University of Illinois Urbana-Champaign", text, role
        ))


if __name__ == "__main__":
    unittest.main()
