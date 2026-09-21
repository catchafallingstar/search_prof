from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from ingestion.ollama_evidence import OllamaReview
from ingestion.publication_discovery import _store_interest_fallback


def _professor():
    return {
        "id": 1,
        "name": "Jane Smith",
        "institution_id": 1,
        "institution_name": "Example",
        "department": "Physics",
    }


def test_explicit_interests_are_saved_directly_without_model_review():
    steps = []
    explicit = [(["Optics"], "https://example.edu/jane", "Research interests: Optics")]
    with patch(
        "ingestion.publication_discovery._save_explicit_research_interests",
        return_value=1,
    ) as save, patch(
        "ingestion.publication_discovery.review_research_interest_summary"
    ) as model:
        _store_interest_fallback(
            1, _professor(), explicit, "", "https://example.edu/jane", steps
        )
    save.assert_called_once()
    model.assert_not_called()


def test_missing_all_research_evidence_requires_manual_review_without_model_guess():
    steps = []
    with patch(
        "ingestion.publication_discovery._research_profile_is_authoritative", return_value=False
    ), patch(
        "ingestion.publication_discovery._mark_research_profile_manual_review"
    ) as manual, patch(
        "ingestion.publication_discovery.review_research_interest_summary"
    ) as model:
        _store_interest_fallback(
            1, _professor(), [], "", "https://example.edu/jane", steps
        )
    manual.assert_called_once()
    model.assert_not_called()


def test_biography_summary_is_saved_with_biography_provenance():
    steps = []
    response = OllamaReview(
        "VALID",
        {
            "research_interests": ["Optics"],
            "primary_field": "Physics",
            "basis_summary": "Biography explicitly discusses optics research.",
        },
    )
    with patch(
        "ingestion.publication_discovery._research_profile_is_authoritative", return_value=False
    ), patch(
        "ingestion.publication_discovery.review_research_interest_summary",
        return_value=response,
    ) as model, patch(
        "ingestion.publication_discovery._save_research_interests", return_value=1
    ) as save, patch(
        "ingestion.publication_discovery._set_research_profile_state"
    ) as state:
        _store_interest_fallback(
            1,
            _professor(),
            [],
            "Jane studies optical systems and photonics.",
            "https://example.edu/jane",
            steps,
        )
    model.assert_called_once()
    assert save.call_args.kwargs["method"] == "QWEN_BIO_SUMMARY"
    assert save.call_args.kwargs["confidence"] == 0.55
    state.assert_called()
    assert steps[-1]["status"] == "QWEN_REVIEWED"


def test_biography_model_outage_queues_saved_evidence_for_retry():
    steps = []
    with patch(
        "ingestion.publication_discovery._research_profile_is_authoritative", return_value=False
    ), patch(
        "ingestion.publication_discovery.review_research_interest_summary",
        return_value=OllamaReview("MODEL_UNAVAILABLE", {}),
    ), patch(
        "ingestion.publication_discovery._research_profile_has_any_interests",
        return_value=False,
    ), patch(
        "ingestion.publication_discovery._set_research_profile_state"
    ), patch(
        "radar_store.enqueue_radar_job", return_value={"id": 42}
    ) as enqueue:
        _store_interest_fallback(
            1,
            _professor(),
            [],
            "Jane studies optical systems and photonics.",
            "https://example.edu/jane",
            steps,
        )
    enqueue.assert_called_once()
    assert steps[-1]["status"] == "AWAITING_MODEL_REVIEW"
    assert steps[-1]["review_job_id"] == 42
    assert steps[-1]["evidence_status"] == "BIOGRAPHY_FOUND"


def test_empty_valid_biography_review_is_no_supported_interests():
    steps = []
    with patch(
        "ingestion.publication_discovery._research_profile_is_authoritative", return_value=False
    ), patch(
        "ingestion.publication_discovery.review_research_interest_summary",
        return_value=OllamaReview("VALID", {"research_interests": []}),
    ), patch(
        "ingestion.publication_discovery._research_profile_has_any_interests",
        return_value=False,
    ), patch(
        "ingestion.publication_discovery._set_research_profile_state"
    ):
        _store_interest_fallback(
            1,
            _professor(),
            [],
            "Jane is a professor in the physics department.",
            "https://example.edu/jane",
            steps,
        )
    assert steps[-1]["status"] == "NO_SUPPORTED_INTERESTS"


def test_unavailable_cache_is_retried_when_model_recovers():
    from ingestion import ollama_evidence as model

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

        def fetchone(self):
            return {
                "validation_status": "MODEL_UNAVAILABLE",
                "parsed_response": {},
                "validation_errors": [],
            }

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor)

    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"message": {"content": '{"research_interests":["AI"]}'}},
    )
    with patch.object(model, "get_db_connection", connection), patch.object(
        model, "enabled", return_value=True
    ), patch.object(model.requests, "post", return_value=response) as post:
        result = model._run_cached_review(
            source_type="RESEARCH_INTEREST_SUMMARY",
            source_record_key="1:url",
            institution_id=1,
            prompt_version="test",
            source_text="AI",
            prompt="review AI",
        )
    assert result.status == "VALID" and result.data["research_interests"] == ["AI"]
    post.assert_called_once()


def test_retry_reuses_saved_biography_without_search_or_duplicate_job():
    from ingestion.publication_discovery import review_queued_interests

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

        def fetchone(self):
            return {"id": 1, "name": "Jane Smith", "institution_id": 1}

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor)

    payload = {
        "mode": "BIOGRAPHY_SUMMARY",
        "professor": {
            "name": "Jane Smith",
            "institution_id": 1,
            "institution_name": "Example",
            "department": "CS",
        },
        "explicit": [],
        "biography_text": "Jane researches artificial intelligence systems.",
        "biography_url": "https://example.edu/jane",
    }
    job = {"professor_id": 1, "result_json": {"interest_input": payload}}
    with patch(
        "ingestion.publication_discovery.get_db_connection", connection
    ), patch(
        "ingestion.publication_discovery._research_profile_is_authoritative", return_value=False
    ), patch(
        "ingestion.publication_discovery.review_research_interest_summary",
        side_effect=[
            OllamaReview("MODEL_UNAVAILABLE", {}),
            OllamaReview("VALID", {"research_interests": ["AI"]}),
        ],
    ), patch(
        "ingestion.publication_discovery._research_profile_has_any_interests",
        return_value=False,
    ), patch(
        "ingestion.publication_discovery._set_research_profile_state"
    ), patch(
        "ingestion.publication_discovery._save_research_interests", return_value=1
    ) as save, patch("radar_store.enqueue_radar_job") as enqueue:
        failed = review_queued_interests(job)
        assert failed["status"] == "MODEL_UNAVAILABLE"
        assert failed["interest_input"] == payload
        success = review_queued_interests({**job, "result_json": failed})
        assert success["status"] == "APPROVED"
        enqueue.assert_not_called()
        save.assert_called_once()
