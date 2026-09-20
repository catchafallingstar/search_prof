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


def test_json_request_retries_once_after_truncated_response(monkeypatch) -> None:
    calls = []
    responses = [
        {
            "message": {"content": '{"research_areas": ['},
            "done_reason": "length",
        },
        {
            "message": {
                "content": '{"research_areas": [], "basis_summary": "No areas."}'
            },
            "done_reason": "stop",
        },
    ]

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        payload = responses.pop(0)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: payload,
        )

    monkeypatch.setattr(ollama_evidence.requests, "post", fake_post)
    raw, data, errors = ollama_evidence._request_json_object(
        model="qwen2.5-coder:7b",
        prompt="Return JSON only.",
    )

    assert errors == ()
    assert data == {"research_areas": [], "basis_summary": "No areas."}
    assert len(calls) == 2
    assert calls[0]["options"]["num_predict"] == 1900
    assert calls[0]["options"]["num_ctx"] == 8192
    assert "JSON REPAIR RETRY" in calls[1]["messages"][0]["content"]
    assert raw.endswith('"No areas."}')


def test_json_request_stops_after_one_repair_retry(monkeypatch) -> None:
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "message": {"content": '{"research_areas": ['},
                "done_reason": "stop",
            },
        )

    monkeypatch.setattr(ollama_evidence.requests, "post", fake_post)
    _raw, data, errors = ollama_evidence._request_json_object(
        model="qwen2.5-coder:7b",
        prompt="Return JSON only.",
    )

    assert data == {}
    assert len(calls) == 2
    assert errors[-1] == "JSON_RETRY_EXHAUSTED"


def test_paper_research_invalid_json_routes_to_staff_review(monkeypatch) -> None:
    from ingestion import publication_discovery as publications

    monkeypatch.setattr(
        publications,
        "review_paper_research_summary",
        lambda **kwargs: OllamaReview(
            "INVALID_RESPONSE", {}, ("JSON_RETRY_EXHAUSTED",)
        ),
    )
    steps = []
    publications._store_paper_research_summary(
        1,
        {
            "name": "Jane Smith",
            "institution_id": 1,
            "institution_name": "Example University",
            "department": "Computer Science",
        },
        [
            publications.Publication(
                "A verified research paper",
                2025,
                "",
                "https://scholar.google.com/citations?user=test",
                "GOOGLE_SCHOLAR",
                "A verified research paper",
                venue="Journal A",
            )
        ],
        "https://scholar.google.com/citations?user=test",
        steps,
    )

    assert steps[-1]["step"] == "PAPER_RESEARCH_AREAS"
    assert steps[-1]["status"] == "REVIEW_REQUIRED"
    assert steps[-1]["model_status"] == "INVALID_RESPONSE"
    assert "staff review" in steps[-1]["reason"]


def test_scholar_publication_filter_accepts_grounded_batch(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_evidence,
        "_run_cached_review",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "items": [
                    {
                        "candidate_id": "1",
                        "decision": "PUBLICATION",
                        "confidence": 0.96,
                        "reason": "Authored conference paper metadata.",
                    },
                    {
                        "candidate_id": "2",
                        "decision": "NOT_PUBLICATION",
                        "confidence": 0.98,
                        "reason": "Committee service listing.",
                    },
                ]
            },
        ),
    )
    review = ollama_evidence.review_scholar_publication_candidates(
        source_record_key="scholar:test:filter",
        institution_id=1,
        candidates=[
            {
                "candidate_id": "1",
                "title": "A real software testing paper",
                "authors": "Jane Smith, John Doe",
                "venue": "ICSE",
                "year": 2025,
            },
            {
                "candidate_id": "2",
                "title": "Program Committee SEAMS 2025",
                "authors": "",
                "venue": "",
                "year": 2025,
            },
        ],
    )
    assert review.status == "VALID"
    assert [item["decision"] for item in review.data["items"]] == [
        "PUBLICATION", "NOT_PUBLICATION"
    ]


def test_scholar_publication_filter_requires_one_decision_per_row(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_evidence,
        "_run_cached_review",
        lambda **kwargs: OllamaReview(
            "VALID",
            {
                "items": [
                    {
                        "candidate_id": "1",
                        "decision": "PUBLICATION",
                        "confidence": 0.9,
                        "reason": "Looks like a paper.",
                    }
                ]
            },
        ),
    )
    review = ollama_evidence.review_scholar_publication_candidates(
        source_record_key="scholar:test:missing",
        institution_id=1,
        candidates=[
            {"candidate_id": "1", "title": "Paper one"},
            {"candidate_id": "2", "title": "Paper two"},
        ],
    )
    assert review.status == "INVALID_EVIDENCE"
    assert any("exactly one decision" in error for error in review.errors)
