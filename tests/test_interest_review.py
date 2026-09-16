from unittest.mock import patch

from ingestion.ollama_evidence import OllamaReview
from ingestion.publication_discovery import _store_interest_fallback


def run_interest_case(explicit, bio, response):
    steps = []
    with patch('ingestion.publication_discovery.review_research_interest_summary', return_value=response) as model, \
         patch('ingestion.publication_discovery._save_research_interests', return_value=1) as save:
        _store_interest_fallback(1, {'id': 1, 'name': 'Jane Smith', 'institution_id': 1,
                                    'institution_name': 'Example', 'department': 'Physics'},
                                 explicit, bio, 'https://example.edu/jane', steps)
    return model, save, steps


def test_explicit_interests_are_reviewed_and_capped_low_confidence():
    model, save, steps = run_interest_case(
        [(['Optics'], 'https://example.edu/jane', 'Research interests: Optics')], '',
        OllamaReview('VALID', {'research_interests': ['Optics'], 'confidence': 1}))
    assert model.call_args.kwargs['explicit_interests'] == ['Optics']
    assert not model.call_args.kwargs['speculative']
    assert save.call_args.kwargs['method'] == 'QWEN_VALIDATED_SECTION'
    assert save.call_args.kwargs['confidence'] == 0.45


def test_missing_evidence_is_speculative_not_website_evidence():
    model, save, steps = run_interest_case([], '', OllamaReview('VALID', {'research_interests': ['Physics']}))
    assert model.call_args.kwargs['speculative']
    assert save.call_args.kwargs['method'] == 'AI_SUGGESTION'
    assert save.call_args.kwargs['confidence'] == 0.15
    assert steps[0]['evidence_method'] == 'AI_SUGGESTION'


def test_model_failure_never_saves_generated_interests():
    _, save, steps = run_interest_case([], '', OllamaReview('MODEL_UNAVAILABLE', {}))
    save.assert_not_called()
    assert steps[0]['status'] == 'MODEL_UNAVAILABLE'


def test_empty_valid_response_is_reported_as_no_supported_interests():
    _, save, steps = run_interest_case([], '', OllamaReview('VALID', {'research_interests': []}))
    save.assert_not_called()
    assert steps[0]['status'] == 'NO_SUPPORTED_INTERESTS'
