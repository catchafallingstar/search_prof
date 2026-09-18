from unittest.mock import patch

from ingestion.ollama_evidence import OllamaReview
from ingestion.publication_discovery import _store_interest_fallback


def run_interest_case(explicit, bio, response):
    steps = []
    with patch('ingestion.publication_discovery.review_research_interest_summary', return_value=response) as model, \
         patch('ingestion.publication_discovery._save_research_interests', return_value=1) as save, \
         patch('radar_store.enqueue_radar_job',return_value={'id':42}):
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
    assert steps[0]['status'] == 'AWAITING_MODEL_REVIEW'
    assert steps[0]['review_job_id']==42
    assert steps[0]['evidence_status']=='NO_DIRECT_INTEREST_EVIDENCE'


def test_empty_valid_response_is_reported_as_no_supported_interests():
    _, save, steps = run_interest_case([], '', OllamaReview('VALID', {'research_interests': []}))
    save.assert_not_called()
    assert steps[0]['status'] == 'NO_SUPPORTED_INTERESTS'


def test_explicit_interests_remain_unreviewed_during_outage():
    _,save,steps=run_interest_case([(['AI'],'https://example.edu/jane','AI')],'',OllamaReview('MODEL_UNAVAILABLE',{}))
    save.assert_not_called()
    assert steps[0]['extracted_interests']==['AI']
    assert steps[0]['interests']==[]
    assert steps[0]['evidence_status']=='EXPLICIT_INTERESTS_FOUND'


def test_unavailable_cache_is_retried_when_model_recovers():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ingestion import ollama_evidence as model
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):pass
        def fetchone(self):return {'validation_status':'MODEL_UNAVAILABLE','parsed_response':{},'validation_errors':[]}
    @contextmanager
    def connection():yield SimpleNamespace(cursor=Cursor)
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'message':{'content':'{"research_interests":["AI"]}'}})
    with patch.object(model,'get_db_connection',connection),patch.object(model,'enabled',return_value=True), \
         patch.object(model.requests,'post',return_value=response) as post:
        result=model._run_cached_review(source_type='RESEARCH_INTEREST_SUMMARY',source_record_key='1:url',
                    institution_id=1,prompt_version='test',source_text='AI',prompt='review AI')
    assert result.status=='VALID' and result.data['research_interests']==['AI']
    post.assert_called_once()


def test_retry_reuses_evidence_without_search_or_duplicate_job():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ingestion.publication_discovery import review_queued_interests
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):pass
        def fetchone(self):return {'id':1,'name':'Jane Smith','institution_id':1}
    @contextmanager
    def connection():yield SimpleNamespace(cursor=Cursor)
    payload={'professor':{'name':'Jane Smith','institution_id':1,'institution_name':'Example','department':'CS'},
             'explicit':[(['AI'],'https://example.edu/jane','AI')],'biography_text':'','biography_url':'https://example.edu/jane'}
    job={'professor_id':1,'result_json':{'interest_input':payload}}
    with patch('ingestion.publication_discovery.get_db_connection',connection), \
         patch('ingestion.publication_discovery.review_research_interest_summary',side_effect=[OllamaReview('MODEL_UNAVAILABLE',{}),OllamaReview('VALID',{'research_interests':['AI']})]), \
         patch('ingestion.publication_discovery._save_research_interests',return_value=1) as save, \
         patch('radar_store.enqueue_radar_job') as enqueue:
        failed=review_queued_interests(job)
        assert failed['status']=='MODEL_UNAVAILABLE' and failed['interest_input']==payload
        success=review_queued_interests({**job,'result_json':failed})
        assert success['status']=='APPROVED'
        enqueue.assert_not_called();save.assert_called_once()
