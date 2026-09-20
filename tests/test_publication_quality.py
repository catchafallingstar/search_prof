from ingestion.publication_quality import (
    ACCEPT,
    AMBIGUOUS,
    REJECT,
    scholar_publication_quality,
)
from ingestion.research_classification import _metadata_url


def test_obvious_service_rows_are_rejected_without_qwen() -> None:
    rejected = [
        "SBST 2017",
        "SEAMS 2017",
        "Danny Weyns (Chair)",
        "SBST 2019 Committees",
        "Artifact Program Committee",
        "Workshop Organization",
        "Research Track Program Committee",
        "Doctoral Symposium",
        "Message from the SBST 2021 Chairs",
    ]
    for title in rejected:
        result = scholar_publication_quality(title=title, year=2021)
        assert result.decision == REJECT, title


def test_normal_publication_metadata_is_accepted_without_qwen() -> None:
    result = scholar_publication_quality(
        title="Towards run-time testing of dynamic adaptive systems",
        authors="Erik M. Fredericks, Betty H. C. Cheng",
        venue="Software Quality Journal",
        year=2017,
    )
    assert result.decision == ACCEPT


def test_workshop_heading_is_ambiguous_not_auto_rejected() -> None:
    result = scholar_publication_quality(
        title="The 13th International Workshop on Genetic Improvement (GI @ ICSE 2024)",
        authors="Erik Fredericks, Justyna Petke",
        venue="ICSE Companion Proceedings",
        year=2024,
    )
    assert result.decision == AMBIGUOUS


def test_person_affiliation_list_is_ambiguous_not_auto_imported() -> None:
    result = scholar_publication_quality(
        title=(
            "Brad Alexander, Optimatics Nadia Alshahwan, Meta Bobby Bruce, "
            "University of California, Davis James Callan, University College London"
        ),
        authors="",
        venue="",
        year=2023,
    )
    assert result.decision == AMBIGUOUS


def test_metadata_url_treats_profile_pages_as_provenance_only() -> None:
    assert _metadata_url({
        "doi": "",
        "source_type": "PERSONAL_SITE",
        "source_url": "https://example.edu/person/publications/",
    }) == ""
    assert _metadata_url({
        "doi": "",
        "source_type": "GOOGLE_SCHOLAR",
        "source_url": "https://scholar.google.com/citations?user=abc",
    }) == ""


def test_metadata_url_prefers_doi_even_for_profile_discovery() -> None:
    assert _metadata_url({
        "doi": "10.1000/example.123",
        "source_type": "PERSONAL_SITE",
        "source_url": "https://example.edu/person/publications/",
    }) == "https://doi.org/10.1000/example.123"
