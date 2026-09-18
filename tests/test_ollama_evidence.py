from ingestion.ollama_evidence import validate_review


def test_ollama_quotes_must_exist_in_supplied_record() -> None:
    source = "Ruiting Wang | PhD Student | ruiting.wang@wsu.edu"
    valid = {
        "record_type": "STUDENT",
        "conflicts": [],
        "evidence": {"name": "Ruiting Wang", "role": "PhD Student"},
    }
    assert validate_review(valid, source) == ()
    invented = {**valid, "evidence": {"role": "Assistant Professor"}}
    assert "evidence.role is not in source" in validate_review(invented, source)


def test_ollama_schema_rejects_uncontrolled_record_types() -> None:
    errors = validate_review(
        {"record_type": "MAYBE_PROFESSOR", "conflicts": [], "evidence": {}},
        "Ada Lovelace",
    )
    assert "invalid record_type" in errors


from types import SimpleNamespace

from ingestion import ollama_evidence
from ingestion.ollama_evidence import OllamaReview


def test_publication_identity_yes_requires_grounded_two_sided_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_evidence,
        "_run_cached_review",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "same_person": "YES",
                "official_evidence": ["machine learning for biosignals"],
                "scholar_evidence": ["Machine learning analysis of heart sounds"],
                "matching_signals": ["research topic overlap"],
                "conflicts": [],
                "confidence": 0.94,
            },
        ),
    )
    review = ollama_evidence.review_publication_identity(
        source_record_key="scholar:test",
        institution_id=1,
        professor_name="Jane Smith",
        institution="Example University",
        department="Biomedical Engineering",
        official_email_domain="example.edu",
        known_pages=["https://example.edu/jane"],
        official_biography="Jane studies machine learning for biosignals.",
        official_interests=["Biosignals"],
        scholar_profile={
            "name": "Jane Smith",
            "affiliation": "",
            "verified_email": "",
            "homepage": "",
            "research_interests": [],
            "papers": [SimpleNamespace(
                title="Machine learning analysis of heart sounds",
                authors="Jane Smith",
                venue="Biomedical Signal Processing",
                year=2025,
            )],
        },
    )
    assert review.status == "VALID"
    assert review.data["same_person"] == "YES"


def test_publication_identity_rejects_name_only_yes(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_evidence,
        "_run_cached_review",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "same_person": "YES",
                "official_evidence": ["Jane Smith"],
                "scholar_evidence": ["Jane Smith"],
                "matching_signals": ["same name"],
                "conflicts": [],
                "confidence": 0.99,
            },
        ),
    )
    review = ollama_evidence.review_publication_identity(
        source_record_key="scholar:test",
        institution_id=1,
        professor_name="Jane Smith",
        institution="Example University",
        department="",
        official_email_domain="example.edu",
        known_pages=["https://example.edu/jane"],
        scholar_profile={
            "name": "Jane Smith",
            "affiliation": "",
            "verified_email": "",
            "homepage": "",
            "research_interests": [],
            "papers": [],
        },
    )
    assert review.status == "INVALID_EVIDENCE"


def test_paper_research_summary_requires_exact_supporting_titles(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_evidence,
        "_run_cached_review",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "research_areas": [
                    {
                        "label": "Biomedical signal processing",
                        "supporting_titles": [
                            "Machine learning analysis of heart sounds",
                            "Deep models for cardiac auscultation",
                        ],
                    }
                ],
                "basis_summary": "Two papers directly support the area.",
            },
        ),
    )
    review = ollama_evidence.review_paper_research_summary(
        source_record_key="paper-set:test",
        institution_id=1,
        professor_name="Jane Smith",
        institution="Example University",
        papers=[
            {"title": "Machine learning analysis of heart sounds", "year": 2025, "venue": "Journal A"},
            {"title": "Deep models for cardiac auscultation", "year": 2024, "venue": "Journal B"},
        ],
    )
    assert review.status == "VALID"
    assert review.data["research_interests"] == ["Biomedical signal processing"]
