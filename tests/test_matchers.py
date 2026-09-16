import unittest

from ingestion.matchers import clean_and_extract_hiring_quote, extract_roles_and_funding, is_valid_signal_text


class HiringMatcherTests(unittest.TestCase):
    def test_accepts_explicit_recruiting_sentence(self) -> None:
        text = "I am looking for two PhD students to join my lab."
        self.assertTrue(is_valid_signal_text(text))

    def test_rejects_negative_sentence(self) -> None:
        text = "I am not accepting PhD students this year."
        self.assertFalse(is_valid_signal_text(text))

    def test_rejects_not_taking_students(self) -> None:
        text = "My group is full and I am not taking new PhD students."
        self.assertFalse(is_valid_signal_text(text))

    def test_rejects_welcome_for_existing_student(self) -> None:
        text = "We are thrilled to welcome Jane Doe as a graduate student to our lab."
        self.assertFalse(is_valid_signal_text(text))

    def test_rejects_administrative_recruit_and_retain_language(self) -> None:
        text = "Advise, mentor, and help recruit and retain undergraduate and graduate students."
        self.assertFalse(is_valid_signal_text(text))

    def test_rejects_conditional_future_recruiting(self) -> None:
        text = "Once funding stabilizes, I will recruit more PhD students."
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
