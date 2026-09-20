from types import SimpleNamespace

from ingestion import publication_discovery as pub
from ingestion.ollama_evidence import OllamaReview


def test_research_profile_version_is_current():
    assert pub.RESEARCH_PROFILE_VERSION == 2


def professor():
    return {
        "name": "Jane Smith",
        "institution_id": 1,
        "institution_name": "Example University",
        "department": "Computer Science",
    }


def test_representative_paper_payload_keeps_recent_and_older_work():
    papers = [
        pub.Publication(
            f"Paper {year}", year, "", "https://example.test/pubs",
            "GOOGLE_SCHOLAR", f"Paper {year}", venue="Journal",
        )
        for year in range(1990, 2030)
    ]
    payload = pub._paper_summary_payload(papers)
    assert len(payload) == 30
    years = [item["year"] for item in payload]
    assert max(years) == 2029
    assert 2010 in years  # newest 20 are preserved
    assert min(years) < 2010  # older career evidence is represented


def test_verified_papers_create_profile_and_replace_weaker_fallback(monkeypatch):
    saved = {}
    state = {}
    monkeypatch.setattr(
        pub, "review_paper_research_summary",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "primary_field": "Computer Science",
                "research_interests": ["Software Engineering"],
                "supporting_evidence": {
                    "Software Engineering": ["Paper A", "Paper B"]
                },
                "basis_summary": "Two papers support the area.",
            },
        ),
    )
    monkeypatch.setattr(
        pub, "_save_research_interests",
        lambda professor_id, interests, **kwargs: (
            saved.update({"interests": interests, **kwargs}) or len(interests)
        ),
    )
    monkeypatch.setattr(
        pub, "_set_research_profile_state",
        lambda professor_id, status, **kwargs: state.update({"status": status, **kwargs}),
    )
    monkeypatch.setattr(pub, "_research_profile_has_any_interests", lambda _pid: True)

    steps = []
    pub._store_paper_research_summary(
        1, professor(),
        [
            pub.Publication("Paper A", 2026, "", "https://example.test", "GOOGLE_SCHOLAR", "Paper A"),
            pub.Publication("Paper B", 2025, "", "https://example.test", "GOOGLE_SCHOLAR", "Paper B"),
        ],
        "https://example.test", steps, queue_on_failure=False,
    )
    assert saved["method"] == "QWEN_PAPER_SUMMARY"
    assert saved["replace_all"] is True
    assert saved["confidence"] == 0.70
    assert state["status"] == "PAPER_DERIVED"
    assert state["primary_field"] == "Computer Science"
    assert steps[-1]["status"] == "QWEN_REVIEWED"


def test_no_papers_and_no_biography_goes_to_manual_review(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pub, "review_research_interest_summary",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Qwen must not run")),
    )
    monkeypatch.setattr(
        pub, "_mark_research_profile_manual_review",
        lambda professor_id, steps, **kwargs: calls.append((professor_id, kwargs)),
    )
    steps = []
    pub._store_interest_fallback(1, professor(), [], "", "https://example.test", steps)
    assert calls and calls[0][0] == 1


def test_biography_is_used_only_as_grounded_fallback(monkeypatch):
    saved = {}
    state = {}
    monkeypatch.setattr(
        pub, "review_research_interest_summary",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "primary_field": "Communication",
                "research_interests": ["Media Literacy"],
                "basis_summary": "Biography directly describes the work.",
            },
        ),
    )
    monkeypatch.setattr(
        pub, "_save_research_interests",
        lambda professor_id, interests, **kwargs: (
            saved.update({"interests": interests, **kwargs}) or len(interests)
        ),
    )
    monkeypatch.setattr(
        pub, "_set_research_profile_state",
        lambda professor_id, status, **kwargs: state.update({"status": status, **kwargs}),
    )
    monkeypatch.setattr(pub, "_research_profile_has_any_interests", lambda _pid: True)
    steps = []
    pub._store_interest_fallback(
        1, professor(), [],
        "Jane studies how young people interpret advertising and media messages.",
        "https://example.test/jane", steps, queue_on_failure=False,
    )
    assert saved["method"] == "QWEN_BIO_SUMMARY"
    assert saved["replace_all"] is True
    assert state["status"] == "BIOGRAPHY_DERIVED"
    assert state["primary_field"] == "Communication"
