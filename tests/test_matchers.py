import unittest

from ingestion.matchers import clean_and_extract_hiring_quote, extract_roles_and_funding, is_valid_signal_text


class HiringMatcherTests(unittest.TestCase):
    def test_accepts_explicit_recruiting_sentence(self) -> None:
        text = "I am looking for two PhD students to join my lab."
        self.assertTrue(is_valid_signal_text(text))

    def test_rejects_negative_sentence(self) -> None:
        text = "I am not accepting PhD students this year."
        self.assertFalse(is_valid_signal_text(text))

    def test_extracts_roles_and_funding(self) -> None:
        roles, funded = extract_roles_and_funding("Seeking a PhD and postdoc, funded by NSF CAREER.")
        self.assertEqual(roles, ["Postdoc", "PhD"])
        self.assertTrue(funded)

    def test_rejects_stale_prefixed_result(self) -> None:
        text = "Jan 2, 2020 ... I am looking for PhD students to join my lab."
        self.assertEqual(clean_and_extract_hiring_quote(text), "")


if __name__ == "__main__":
    unittest.main()

