import unittest
from unittest.mock import patch

from ingestion.taxonomy import (
    _exact_hierarchy_match,
    _topic_relevance,
    build_search_queries,
    normalize_taxonomy,
    phrase_covers_query,
)


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

    def test_biomed_shorthand_expands_to_paper_vocabulary(self) -> None:
        for query in ("biomed", "bio med", "biomedical"):
            searches = build_search_queries(query)
            self.assertIn("biomedical engineering", searches)
            self.assertIn("biomedical sciences", searches)
            self.assertIn("biomedicine", searches)

    def test_political_science_expands_to_real_paper_vocabulary(self) -> None:
        searches = build_search_queries("Political science")
        self.assertIn("political behavior", searches)
        self.assertIn("comparative politics", searches)
        self.assertIn("public policy", searches)

    def test_broad_ai_matches_subfield_not_one_narrow_topic(self) -> None:
        topics = [{
            "id": "https://openalex.org/T1",
            "display_name": "Artificial Intelligence in Healthcare and Education",
            "subfield": {
                "id": "https://openalex.org/subfields/1702",
                "display_name": "Artificial Intelligence",
            },
            "field": {"id": "17", "display_name": "Computer Science"},
            "domain": {"id": "3", "display_name": "Physical Sciences"},
        }]
        match = _exact_hierarchy_match("AI", topics)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match[0], "subfield")
        self.assertEqual(match[1]["display_name"], "Artificial Intelligence")

    @patch("ingestion.taxonomy._child_topic_mappings", return_value=[])
    @patch("ingestion.taxonomy._topic_lookup")
    def test_normalized_ai_uses_openalex_subfield_mapping(
        self, lookup, _children
    ) -> None:
        lookup.return_value = [{
            "id": "T1",
            "display_name": "Artificial Intelligence in Healthcare and Education",
            "keywords": [],
            "subfield": {"id": "S1702", "display_name": "Artificial Intelligence"},
            "field": {"id": "F17", "display_name": "Computer Science"},
            "domain": {"id": "D3", "display_name": "Physical Sciences"},
        }]
        result = normalize_taxonomy("Artificial intelligence")
        self.assertEqual(result["query_level"], "subfield")
        self.assertEqual(result["topic_name"], "Artificial Intelligence")
        self.assertEqual(result["openalex_mappings"][0]["openalex_id"], "S1702")
        self.assertEqual(len(result["openalex_mappings"]), 1)
        self.assertIn("natural language processing", result["search_queries"])
        self.assertIn("computer vision", result["search_queries"])
        self.assertIn("AI security and robustness", result["search_queries"])
        self.assertNotIn(
            "Artificial Intelligence in Healthcare and Education",
            result["search_queries"],
        )

    @patch("ingestion.taxonomy._topic_lookup")
    def test_cross_topic_query_keeps_relevant_expansion_topics(self, lookup) -> None:
        base = {
            "id": "T-security",
            "display_name": "Security of Artificial Intelligence Systems",
            "keywords": [],
            "subfield": {"id": "S1702", "display_name": "Artificial Intelligence"},
            "field": {"id": "F17", "display_name": "Computer Science"},
            "domain": {"id": "D3", "display_name": "Physical Sciences"},
        }
        adversarial = {
            **base,
            "id": "T-adversarial",
            "display_name": "Adversarial Machine Learning",
        }
        privacy = {
            **base,
            "id": "T-privacy",
            "display_name": "Privacy Preserving Machine Learning",
        }
        lookup.side_effect = [[base], [], [adversarial], [privacy]]
        result = normalize_taxonomy("AI security")
        self.assertEqual(result["query_level"], "cross_topic")
        self.assertEqual(
            {value["openalex_id"] for value in result["openalex_mappings"]},
            {"T-security", "T-adversarial", "T-privacy"},
        )


if __name__ == "__main__":
    unittest.main()
