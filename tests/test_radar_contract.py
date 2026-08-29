import unittest
from pathlib import Path

from ingestion.fetch_prof import (
    _best_work_match,
    _education_institution,
    _probable_pi_authorships,
    _work_matches_query,
    _work_relevance_score,
)
from ingestion.taxonomy import build_search_queries


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RadarContractTests(unittest.TestCase):
    def test_direct_work_filter_rejects_unrelated_concept_scattering(self) -> None:
        work = {
            "title": "Innovative Educational Techniques",
            "keywords": [
                {"display_name": "Artificial Intelligence"},
                {"display_name": "Border Security"},
            ],
        }
        self.assertFalse(_work_matches_query(work, "AI security"))

    def test_direct_work_filter_accepts_coherent_title(self) -> None:
        work = {"title": "Security of Artificial Intelligence Systems"}
        self.assertTrue(_work_matches_query(work, "AI security"))

    def test_ai_security_rejects_food_security_application(self) -> None:
        work = {
            "title": "AI Forecasting Innovations in Food Security and Agricultural Supply Chains"
        }
        self.assertFalse(_work_matches_query(work, "AI security"))

    def test_ai_security_accepts_red_teaming(self) -> None:
        work = {"title": "Red-Teaming for Generative AI"}
        self.assertTrue(_work_matches_query(work, "AI security"))

    def test_broad_openalex_topic_alone_does_not_prove_paper_relevance(self) -> None:
        work = {
            "title": "A survey on large language model security and privacy",
            "topics": [{"display_name": "Law and Political Science"}],
        }
        self.assertFalse(_work_matches_query(work, "Political science"))

    def test_abstract_can_supply_direct_research_evidence(self) -> None:
        work = {
            "title": "Voter participation across regions",
            "abstract_inverted_index": {
                "This": [0],
                "political": [1],
                "science": [2],
                "study": [3],
            },
        }
        self.assertGreaterEqual(
            _work_relevance_score(work, "Political science"), 5.0
        )

    def test_best_work_match_records_the_query_that_explains_the_match(self) -> None:
        work = {"title": "Security of Artificial Intelligence Systems"}
        score, matched_query = _best_work_match(
            work, ["robotics", "AI security"]
        )
        self.assertGreaterEqual(score, 5.0)
        self.assertEqual(matched_query, "AI security")

    def test_international_educational_affiliation_is_a_candidate(self) -> None:
        institution = _education_institution({
            "institutions": [{
                "display_name": "Shandong University",
                "country_code": "CN",
                "type": "education",
            }]
        })
        self.assertIsNotNone(institution)
        self.assertEqual(institution["country_code"], "CN")

    def test_discovered_signal_is_not_turned_into_a_submitted_opportunity(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "parse_hiring_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("INSERT INTO hiring_signals", source)
        self.assertNotIn("INSERT INTO opportunities", source)
        self.assertIn("attribution_status", source)

    def test_discovery_keeps_first_corresponding_and_senior_authors(self) -> None:
        authorships = [
            {"author": {"id": "student"}, "author_position": "first"},
            {"author": {"id": "pi-1"}, "is_corresponding": True},
            {"author": {"id": "pi-2"}, "author_position": "last"},
        ]
        self.assertEqual(
            [item["author"]["id"] for item in _probable_pi_authorships(authorships)],
            ["student", "pi-1", "pi-2"],
        )

    def test_discovery_keeps_repeated_middle_author_for_later_verification(self) -> None:
        authorships = [
            {"author": {"id": "first"}, "author_position": "first"},
            {"author": {"id": "new-pi"}, "author_position": "middle"},
            {"author": {"id": "last"}, "author_position": "last"},
        ]
        result = _probable_pi_authorships(authorships, {"new-pi": 2})
        self.assertEqual([item["author"]["id"] for item in result], ["first", "new-pi", "last"])

    def test_query_expansion_is_reusable_and_covers_ai_security_subtopics(self) -> None:
        queries = build_search_queries("AI security")
        self.assertIn("network intrusion detection", queries)
        self.assertIn("AI security", queries)

    def test_discovery_keeps_a_deeper_pool_for_progressive_verification(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("target_professors * 6", source)
        self.assertIn("candidate_budget = min(600", source)
        self.assertIn('"candidates_ranked": len(ranked_prospects)', source)
        self.assertNotIn("institutions.country_code:us", source)

    def test_topic_and_text_sources_share_the_paper_relevance_gate(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("for query in search_queries", source)
        self.assertIn('work["_scholarradar_relevance"]', source)
        self.assertIn('work["_scholarradar_matched_query"]', source)
        self.assertIn('"supporting_papers": []', source)

    def test_publication_affiliation_cannot_overwrite_safe_current_appointment(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("faculty_verification_version >= 2", source)
        self.assertIn("THEN institution_name", source)

    def test_duplicate_openalex_people_do_not_collide_on_affiliation_update(self) -> None:
        source = (PROJECT_DIR / "ingestion" / "fetch_prof.py").read_text(encoding="utf-8")
        self.assertIn("other.id <> professors.id", source)
        self.assertIn("other.name = professors.name", source)


if __name__ == "__main__":
    unittest.main()
