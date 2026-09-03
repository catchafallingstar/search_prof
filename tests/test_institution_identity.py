import unittest
from unittest.mock import patch

from ingestion.institution_domains import (
    canonical_institution,
    institutions_equivalent,
)
from ingestion.verify_faculty import _institution_similarity


class InstitutionIdentityTests(unittest.TestCase):
    @patch('ingestion.institution_domains._directory_record_for_name')
    def test_scorecard_name_becomes_canonical_display_name(self, lookup):
        lookup.return_value = ('University of California, Berkeley', 'berkeley.edu', ())
        self.assertEqual(
            canonical_institution('University of California Berkeley'),
            'University of California, Berkeley',
        )

    def test_washu_and_university_of_washington_remain_distinct(self):
        self.assertFalse(institutions_equivalent(
            'WashU', 'University of Washington, Seattle'
        ))
        self.assertEqual(
            canonical_institution('Washington University at St. Louis'),
            'Washington University in St. Louis',
        )

    def test_safe_common_aliases_expand_to_full_names(self):
        self.assertEqual(canonical_institution("UMich"), "University of Michigan")
        self.assertEqual(
            canonical_institution("WashU"),
            "Washington University in St. Louis",
        )

    def test_washington_universities_do_not_collapse_together(self):
        self.assertFalse(institutions_equivalent(
            "University of Washington",
            "Washington University in St. Louis",
        ))
        self.assertEqual(_institution_similarity(
            "University of Washington",
            "Washington University in St. Louis",
        ), 0.0)

    def test_ambiguous_uw_is_not_guessed(self):
        self.assertEqual(canonical_institution("UW"), "UW")
        self.assertFalse(institutions_equivalent("UW", "University of Washington"))


if __name__ == "__main__":
    unittest.main()
