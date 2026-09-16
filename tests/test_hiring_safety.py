from datetime import date

from ingestion.parse_hiring_signals import (
    _eligible_hiring_search_result,
    _hiring_signal_freshness,
    _page_is_person_specific,
    _quote_names_conflicting_program,
    _saved_page_is_attributed,
)


def test_rejects_ad_click_redirects():
    assert not _eligible_hiring_search_result("https://www.bing.com/aclick?ld=tracking")


def test_two_digit_cycle_year_is_historical():
    result = _hiring_signal_freshness(
        "I am recruiting PhD students for Fall'25.",
        {},
        today=date(2026, 9, 13),
    )
    assert result["freshness_status"] == "HISTORICAL"
    assert result["source_date"].year == 2025


def test_spaced_year_is_historical():
    result = _hiring_signal_freshness(
        "Accepting PhD students for 202 3.",
        {},
        today=date(2026, 9, 13),
    )
    assert result["freshness_status"] == "HISTORICAL"


def test_publication_does_not_replace_current_institution_evidence():
    professor = {
        "name": "Jionghao Lin",
        "institution_name": "Carnegie Mellon University",
        "supporting_paper_titles": ["A paper visible on the page"],
    }
    snapshot = {
        "title": "Jionghao Lin",
        "text": "Jionghao Lin, University of Hong Kong. A paper visible on the page",
    }
    assert not _saved_page_is_attributed(professor, snapshot)


def test_generic_openings_page_is_not_person_specific():
    professor = {"name": "Rainer Hebert"}
    snapshot = {"title": "Engineering Graduate Openings"}
    assert not _page_is_person_specific(
        professor,
        "https://example.edu/prospective-students/graduate-openings/",
        snapshot,
    )


def test_rejects_hiring_for_a_different_named_program():
    professor = {"institution_name": "Carnegie Mellon University"}
    quote = "For the HKU PhD application, I am seeking PhD applicants."
    assert _quote_names_conflicting_program(professor, quote)
