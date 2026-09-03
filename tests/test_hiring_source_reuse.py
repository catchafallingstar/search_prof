from datetime import date
import unittest
from unittest.mock import patch

from ingestion.identity_sources import source_kind
from ingestion.parse_hiring_signals import (
    _eligible_hiring_search_result,
    _hiring_signal_freshness,
    process_single_professor,
)
from ingestion.verify_faculty import _identity_evidence_rows


def snapshot(*, text="", sentences=None, links=None, accessible=True, title=""):
    return {
        "text": text,
        "sentences": list(sentences or []),
        "links": list(links or []),
        "accessible": accessible,
        "title": title,
    }


class HiringSourceReuseTests(unittest.TestCase):
    def test_hiring_dates_use_available_precision_against_today(self):
        self.assertEqual(
            _hiring_signal_freshness(
                "We are recruiting PhD students for September 2026.", {}, date(2026, 9, 2)
            )["freshness_status"],
            "CURRENT",
        )
        self.assertEqual(
            _hiring_signal_freshness(
                "We were recruiting PhD students in 2022.", {}, date(2026, 9, 2)
            )["freshness_status"],
            "HISTORICAL",
        )
        self.assertEqual(
            _hiring_signal_freshness(
                "Applications are due August 1, 2026.", {}, date(2026, 9, 2)
            )["freshness_status"],
            "EXPIRED",
        )

    def test_undated_signal_is_reported_as_undated(self):
        result = _hiring_signal_freshness(
            "I am recruiting PhD students to join my lab.", {}, date(2026, 9, 2)
        )
        self.assertEqual(result["freshness_status"], "UNDATED")
        self.assertIsNone(result["source_date"])

    def setUp(self):
        self.professor = {
            "id": 10,
            "name": "Jane Smith",
            "institution_name": "Example University",
            "faculty_source_url": "https://example.edu/faculty/jane-smith",
            "official_faculty_source_url": "https://example.edu/faculty/jane-smith",
            "saved_profile_sources": [],
            "prior_hiring_sources": [],
            "supporting_paper_titles": ["Secure Learning Systems"],
        }

    @patch("ingestion.parse_hiring_signals.search_web")
    @patch("ingestion.parse_hiring_signals._fetch_hiring_page_snapshot")
    def test_previous_verified_source_is_checked_first(self, fetch, search):
        self.professor["prior_hiring_sources"] = ["https://janesmith.org/openings"]
        fetch.return_value = snapshot(sentences=["I am looking for PhD students to join my lab."], text="Jane Smith Example University")
        result = process_single_professor(self.professor)
        self.assertEqual(result["signal"][3], "https://janesmith.org/openings")
        search.assert_not_called()

    @patch("ingestion.parse_hiring_signals.search_web")
    @patch("ingestion.parse_hiring_signals._fetch_hiring_page_snapshot")
    def test_official_linked_lab_precedes_saved_source_and_search(self, fetch, search):
        self.professor["saved_profile_sources"] = [{"source_type": "PERSONAL_WEBSITE", "source_url": "https://janesmith.org"}]
        fetch.side_effect = [
            snapshot(text="Jane Smith Professor Example University", links=[{"label": "Research lab", "href": "https://example.edu/smith-lab"}]),
            snapshot(sentences=["I am looking for PhD students to join my lab."], text="Jane Smith Example University"),
        ]
        result = process_single_professor(self.professor)
        self.assertEqual(result["signal"][3], "https://example.edu/smith-lab")
        search.assert_not_called()

    @patch("ingestion.parse_hiring_signals.search_web")
    @patch("ingestion.parse_hiring_signals._fetch_hiring_page_snapshot")
    def test_saved_personal_source_requires_and_uses_attribution(self, fetch, search):
        self.professor["faculty_source_url"] = None
        self.professor["official_faculty_source_url"] = None
        self.professor["saved_profile_sources"] = [{"source_type": "PERSONAL_WEBSITE", "source_url": "https://janesmith.org"}]
        fetch.return_value = snapshot(
            sentences=["I am looking for PhD students to join my lab."],
            text="Jane Smith, Example University. Secure Learning Systems.",
        )
        result = process_single_professor(self.professor)
        self.assertEqual(result["signal"][3], "https://janesmith.org")
        search.assert_not_called()

    @patch("ingestion.parse_hiring_signals.search_web")
    @patch("ingestion.parse_hiring_signals._fetch_hiring_page_snapshot")
    def test_only_one_targeted_search_is_used(self, fetch, search):
        self.professor["faculty_source_url"] = None
        self.professor["official_faculty_source_url"] = None
        search.return_value = [{"href": "https://janesmith.org/join", "title": "Jane Smith lab openings", "body": ""}]
        fetch.return_value = snapshot(
            sentences=["I am looking for PhD students to join my lab."],
            text="Jane Smith Example University",
        )
        result = process_single_professor(self.professor)
        self.assertEqual(result["signal"][3], "https://janesmith.org/join")
        search.assert_called_once()
        query = search.call_args.args[0]
        self.assertIn('"Jane Smith"', query)
        self.assertIn('"Example University"', query)

    def test_identity_evidence_retains_all_useful_url_types(self):
        result = {
            "name": "Jane Smith",
            "status": "VERIFIED",
            "source_url": "https://example.edu/faculty/jane-smith",
            "source_label": "Example University",
            "source_kind": "university",
            "evidence": "Professor",
            "institution": "Example University",
            "title": "Professor",
            "method": "automatic_search",
            "alternative_evidence": [],
            "source_attributions": [],
            "search_audit": {
                "results": [
                    {"url": "https://janesmith.org", "title": "Jane Smith", "snippet": "Professor at Example University", "source_kind": "personal"},
                    {"url": "https://smithlab.org", "title": "Smith Lab", "snippet": "Jane Smith Example University", "source_kind": "lab"},
                    {"url": "https://janesmith.org/cv.pdf", "title": "Jane Smith CV", "snippet": "Curriculum vitae", "source_kind": "cv"},
                    {"url": "https://www.linkedin.com/in/jane-smith", "title": "Jane Smith - Professor", "snippet": "Example University", "source_kind": "linkedin"},
                ],
                "pages": [],
            },
        }
        rows = _identity_evidence_rows(result)
        types = {row["source_type"] for row in rows}
        self.assertTrue({
            "OFFICIAL_UNIVERSITY_PAGE",
            "PERSONAL_WEBSITE",
            "LAB_WEBSITE",
            "CV",
            "LINKEDIN_SNIPPET",
        }.issubset(types))

    def test_exact_name_root_page_is_a_personal_lead(self):
        kind = source_kind(
            "https://janesmith.org/",
            "Jane Smith — Professor at Example University",
            name_matches=True,
            profile_title=True,
        )
        self.assertEqual(kind, "personal")

    def test_internal_clue_status_is_saved_as_unverified_evidence(self):
        rows = _identity_evidence_rows({
            "status": "UNVERIFIED",
            "search_audit": {
                "pages": [{
                    "url": "https://example.edu/people",
                    "status": "CLUE",
                    "reason": "Directory used only to locate a profile",
                }],
                "results": [],
            },
        })
        self.assertEqual(rows[0]["verification_status"], "UNVERIFIED")

    def test_aggregators_and_news_are_not_hiring_sources(self):
        self.assertFalse(_eligible_hiring_search_result(
            "https://www.phdportal.com/universities/example.html"
        ))
        self.assertFalse(_eligible_hiring_search_result(
            "https://example.edu/news/professor-wins-award"
        ))
        self.assertTrue(_eligible_hiring_search_result(
            "https://example.edu/faculty/jane-smith"
        ))


if __name__ == "__main__":
    unittest.main()
