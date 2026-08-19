import unittest

from ingestion.hiring_discovery import _result_names_candidate


class HiringFirstDiscoveryTests(unittest.TestCase):
    def test_exact_name_in_result_connects_hiring_lead(self) -> None:
        self.assertTrue(
            _result_names_candidate(
                "Jingdi Chen",
                {
                    "title": "Jingdi Chen | Electrical and Computer Engineering",
                    "body": "Her lab is actively recruiting PhD students.",
                    "href": "https://ece.engineering.arizona.edu/faculty/jingdi-chen",
                },
            )
        )

    def test_compact_personal_site_url_can_connect_hiring_lead(self) -> None:
        self.assertTrue(
            _result_names_candidate(
                "Jingdi Chen",
                {
                    "title": "ANNIE Research Group",
                    "body": "Multiple fully funded PhD openings.",
                    "href": "https://jingdichen.com/annie",
                },
            )
        )

    def test_partial_same_name_is_not_connected(self) -> None:
        self.assertFalse(
            _result_names_candidate(
                "Syed Wali",
                {
                    "title": "Syed Wali Kamal",
                    "body": "Assistant Professor",
                    "href": "https://example.edu/syed-kamal",
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
