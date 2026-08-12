import unittest

from ingestion.taxonomy import _topic_relevance, phrase_covers_query


class TaxonomyRelevanceTests(unittest.TestCase):
    def test_rejects_unrelated_openalex_fuzzy_result(self) -> None:
        topic = {
            "display_name": "Innovative Educational Techniques",
            "keywords": ["Artificial Intelligence", "Border Security"],
        }
        self.assertEqual(_topic_relevance("AI security", topic), 0.0)

    def test_accepts_relevant_artificial_intelligence_security_topic(self) -> None:
        topic = {
            "display_name": "Security of Artificial Intelligence Systems",
            "keywords": ["machine learning", "AI safety"],
        }
        self.assertGreater(_topic_relevance("AI security", topic), 0.0)

    def test_accepts_exact_single_word_topic(self) -> None:
        topic = {"display_name": "Robotics", "keywords": []}
        self.assertGreater(_topic_relevance("robotics", topic), 0.0)

    def test_query_concepts_must_occur_in_one_phrase(self) -> None:
        self.assertFalse(phrase_covers_query("AI security", "Artificial Intelligence"))
        self.assertFalse(phrase_covers_query("AI security", "Border Security"))
        self.assertTrue(
            phrase_covers_query("AI security", "Security of Artificial Intelligence Systems")
        )


if __name__ == "__main__":
    unittest.main()
