from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import requests

from ingestion import publication_discovery as pub
from ingestion.index_worker import _scholar_retry_delay

URL = 'https://scholar.google.com/citations?user=testid'


@pytest.mark.parametrize('host', ['scholar.google.com','scholar.google.co.uk','scholar.google.com.tr','scholar.google.de'])
def test_supported_regional_profiles(host):
    assert pub._is_scholar_profile_url(f'https://{host}/citations?user=abc')


@pytest.mark.parametrize('url', [
    'https://scholar.google.com.evil.test/citations?user=abc',
    'https://scholar.google.xyz/citations?user=abc',
    'https://scholar.google.com/citations',
    'https://scholar.google.com/other?user=abc',
])
def test_lookalikes_and_nonprofiles_rejected(url):
    assert not pub._is_scholar_profile_url(url)


def install_pages(monkeypatch, batches, names=None):
    calls=[]
    def get(url, params, **kwargs):
        index=len(calls);calls.append(params.copy())
        if isinstance(batches[index],Exception): raise batches[index]
        return SimpleNamespace(text=str(index),url=url+'?'+urlencode(params),raise_for_status=lambda:None)
    def parse(html,url):
        index=int(html)
        return dict(name=names[index] if names else 'Jane Smith', papers=[
            pub.Publication(f'Paper number {n}',2024,'',url,'GOOGLE_SCHOLAR','citation')
            for n in batches[index]])
    monkeypatch.setattr(pub.requests,'get',get)
    monkeypatch.setattr(pub,'parse_scholar_profile',parse)
    monkeypatch.setattr(pub.time,'sleep',lambda _:None)
    return calls


def test_pagination_beyond_one_hundred(monkeypatch):
    calls=install_pages(monkeypatch,[range(100),range(100,140)])
    result=pub.fetch_scholar_profile(URL,headers={})
    assert len(result['papers'])==140
    assert result['pagination_status']=='END_OF_LIST'
    assert [c['cstart'] for c in calls]==[0,100]


def test_repeated_page_stops_without_duplicates(monkeypatch):
    calls=install_pages(monkeypatch,[range(100),range(100)])
    result=pub.fetch_scholar_profile(URL,headers={})
    assert len(result['papers'])==100 and len(calls)==2
    assert result['pagination_status']=='REPEATED_PAGE'


def test_changed_identity_aborts(monkeypatch):
    install_pages(monkeypatch,[range(100),range(100,110)],['Jane Smith','John Jones'])
    with pytest.raises(requests.HTTPError,match='identity changed'):
        pub.fetch_scholar_profile(URL,headers={})


def test_pagination_stops_on_block(monkeypatch):
    calls=install_pages(monkeypatch,[range(100),requests.HTTPError('blocked')])
    with pytest.raises(requests.HTTPError):pub.fetch_scholar_profile(URL,headers={})
    assert len(calls)==2


def test_retry_is_delayed_and_bounded():
    job=dict(job_type='QWEN_REVIEW_PUBLICATION',attempts=1,max_attempts=3)
    assert _scholar_retry_delay(job,{'status':'SOURCE_UNAVAILABLE'})==21600
    assert _scholar_retry_delay(job,{'status':'SOURCE_UNAVAILABLE','retry_after_seconds':86400})==86400
    assert not _scholar_retry_delay({**job,'attempts':3},{'status':'SOURCE_UNAVAILABLE'})
    assert not _scholar_retry_delay(job,{'status':'REVIEW_REQUIRED'})


def setup_review(monkeypatch, *, official=True, affiliation="Example University", suppress_paper_summary=True):
    professor=dict(id=1,name='Jane Smith',institution_id=1,institution_name='Example University',
                   faculty_source_url='https://example.edu/jane',official_institution_domain='example.edu',department='CS')
    queries=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,*args):queries.append(sql)
        def fetchone(self):return professor
    @contextmanager
    def connection():yield SimpleNamespace(cursor=Cursor)
    monkeypatch.setattr(pub,'get_db_connection',connection)
    monkeypatch.setattr(pub,'_saved_scholar_candidates',lambda _: [URL])
    monkeypatch.setattr(pub,'_official_profile_scholar_candidates',lambda _: set())
    html='<main><h1>Jane Smith</h1>'+(f'<a href="{URL}">Scholar</a>' if official else '')+'</main>'
    monkeypatch.setattr(pub.requests,'get',lambda *args,**kwargs:SimpleNamespace(text=html,url=professor['faculty_source_url'],raise_for_status=lambda:None))
    sources=[];statuses=[];saved=[]
    monkeypatch.setattr(pub,'_record_source',lambda *args:sources.append(args))
    monkeypatch.setattr(pub,'_status',lambda pid,value:statuses.append(value))
    monkeypatch.setattr(pub,'_save',lambda pid,papers,callback:saved.extend(papers) or len(papers))
    monkeypatch.setattr(pub,'_dismiss_resolved_scholar_reviews',lambda _:None)
    monkeypatch.setattr(pub,'_scholar_manual_decisions',lambda *args,**kwargs:{})
    monkeypatch.setattr(pub,'_sync_scholar_publication_review_queue',lambda *args,**kwargs:None)
    if suppress_paper_summary:
        monkeypatch.setattr(pub,'_store_paper_research_summary',lambda *args,**kwargs:None)
    scholar=dict(name='Jane Smith',affiliation=affiliation,verified_email='',homepage='',
                 papers=[pub.Publication('A real research paper',2024,'',URL,'GOOGLE_SCHOLAR','evidence', authors='Jane Smith, John Doe', venue='Journal A')],
                 pagination_status='END_OF_LIST',pages_fetched=1,works_limit=300)
    monkeypatch.setattr(pub,'fetch_scholar_profile',lambda *args,**kwargs:scholar)
    return sources,statuses,saved,queries


def test_direct_official_link_does_not_require_qwen(monkeypatch):
    sources,statuses,saved,queries=setup_review(monkeypatch)
    monkeypatch.setattr(pub,'review_publication_identity',lambda **kwargs:pytest.fail('Qwen not needed'))
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SCHOLAR_VERIFIED' and len(saved)==1
    assert any(call[-1].get('qwen_status')=='NOT_NEEDED' for call in sources)


def test_affiliation_alone_cannot_bypass_unavailable_qwen(monkeypatch):
    sources,statuses,saved,queries=setup_review(monkeypatch,official=False)
    monkeypatch.setattr(pub,'review_publication_identity',lambda **kwargs:SimpleNamespace(status='MODEL_UNAVAILABLE',data={},errors=[]))
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SOURCE_UNAVAILABLE' and not saved
    assert not any('INSERT INTO professor_identity_review_queue' in q for q in queries)



def test_grounded_qwen_yes_can_promote_name_only_candidate(monkeypatch):
    sources,statuses,saved,queries=setup_review(
        monkeypatch, official=False, affiliation='Other University'
    )
    monkeypatch.setattr(
        pub, 'review_publication_identity',
        lambda **kwargs: SimpleNamespace(
            status='VALID',
            data={
                'same_person':'YES',
                'official_evidence':['machine learning'],
                'scholar_evidence':['A real research paper'],
                'matching_signals':['official research overlaps representative papers'],
                'conflicts':[],
                'confidence':0.93,
            },
            errors=[],
        ),
    )
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SCHOLAR_VERIFIED'
    assert len(saved)==1
    qwen_step=next(step for step in result['steps'] if step['step']=='QWEN_SCHOLAR_REVIEW')
    assert qwen_step['deterministic_decision']=='VERIFIED'
    assert 'qwen_correlated_identity' in qwen_step['decision_signals']
    assert qwen_step['qwen_same_person']=='YES'


def test_qwen_yes_below_confidence_threshold_stays_review(monkeypatch):
    sources,statuses,saved,queries=setup_review(
        monkeypatch, official=False, affiliation='Other University'
    )
    monkeypatch.setattr(
        pub, 'review_publication_identity',
        lambda **kwargs: SimpleNamespace(
            status='VALID',
            data={
                'same_person':'YES',
                'official_evidence':['machine learning'],
                'scholar_evidence':['A real research paper'],
                'matching_signals':['possible topical overlap'],
                'conflicts':[],
                'confidence':0.65,
            },
            errors=[],
        ),
    )
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='REVIEW_REQUIRED'
    assert not saved

def test_http_outage_is_not_an_identity_review(monkeypatch):
    sources,statuses,saved,queries=setup_review(monkeypatch)
    response=requests.Response();response.status_code=429;response.headers['Retry-After']='86400'
    def fail(*args,**kwargs):raise requests.HTTPError('429',response=response)
    monkeypatch.setattr(pub,'fetch_scholar_profile',fail)
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SOURCE_UNAVAILABLE'
    assert result['retry_after_seconds']==86400
    assert not saved and not any('INSERT INTO professor_identity_review_queue' in q for q in queries)

def test_verified_scholar_runs_paper_research_summary(monkeypatch):
    sources,statuses,saved,queries=setup_review(
        monkeypatch, suppress_paper_summary=False
    )
    calls=[]
    def summarize(professor_id, professor, papers, source_url, steps, **kwargs):
        calls.append((professor_id, [paper.title for paper in papers], source_url))
        steps.append({
            'step':'PAPER_RESEARCH_AREAS',
            'status':'QWEN_REVIEWED',
            'interests':['Machine learning'],
            'interests_saved':1,
        })
    monkeypatch.setattr(pub,'_store_paper_research_summary',summarize)
    monkeypatch.setattr(pub,'review_publication_identity',lambda **kwargs:pytest.fail('Qwen identity not needed'))
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SCHOLAR_VERIFIED'
    assert calls == [(1, ['A real research paper'], URL)]
    assert any(step.get('step')=='PAPER_RESEARCH_AREAS' for step in result['steps'])



def test_verified_scholar_rejects_obvious_service_rows_before_save(monkeypatch):
    sources,statuses,saved,queries=setup_review(monkeypatch)
    scholar={
        'name':'Jane Smith','affiliation':'Example University','verified_email':'','homepage':'',
        'research_interests':[],
        'papers':[
            pub.Publication('A real research paper',2024,'',URL,'GOOGLE_SCHOLAR','A real research paper | Jane Smith, John Doe | Journal A', authors='Jane Smith, John Doe', venue='Journal A'),
            pub.Publication('Artifact Program Committee',2024,'',URL,'GOOGLE_SCHOLAR','Artifact Program Committee'),
        ],
        'pagination_status':'END_OF_LIST','pages_fetched':1,'works_limit':300,
    }
    monkeypatch.setattr(pub,'fetch_scholar_profile',lambda *args,**kwargs:scholar)
    monkeypatch.setattr(pub,'review_publication_identity',lambda **kwargs:pytest.fail('Qwen identity not needed'))
    detached=[]
    monkeypatch.setattr(pub,'_detach_rejected_scholar_links',lambda pid,papers: detached.extend(p.title for p in papers) or len(papers))
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='SCHOLAR_VERIFIED'
    assert [paper.title for paper in saved] == ['A real research paper']
    assert detached == ['Artifact Program Committee']
    filter_step=next(step for step in result['steps'] if step['step']=='SCHOLAR_PUBLICATION_FILTER')
    assert filter_step['accepted_rows']==1
    assert filter_step['rejected_rows']==1
    assert filter_step['review_required_rows']==0


def test_ambiguous_scholar_row_goes_to_staff_review_when_qwen_uncertain(monkeypatch):
    sources,statuses,saved,queries=setup_review(monkeypatch)
    scholar={
        'name':'Jane Smith','affiliation':'Example University','verified_email':'','homepage':'',
        'research_interests':[],
        'papers':[
            pub.Publication(
                'The 13th International Workshop on Genetic Improvement (GI @ ICSE 2024)',
                2024,'',URL,'GOOGLE_SCHOLAR',
                'The 13th International Workshop on Genetic Improvement (GI @ ICSE 2024) | Jane Smith, John Doe | ICSE Companion Proceedings',
                authors='Jane Smith, John Doe', venue='ICSE Companion Proceedings'
            )
        ],
        'pagination_status':'END_OF_LIST','pages_fetched':1,'works_limit':300,
    }
    monkeypatch.setattr(pub,'fetch_scholar_profile',lambda *args,**kwargs:scholar)
    monkeypatch.setattr(pub,'review_publication_identity',lambda **kwargs:pytest.fail('Qwen identity not needed'))
    monkeypatch.setattr(
        pub,'review_scholar_publication_candidates',
        lambda **kwargs: SimpleNamespace(
            status='VALID', errors=[],
            data={'items':[{'candidate_id':'1','decision':'UNCERTAIN','confidence':0.62,'reason':'Could be proceedings or service.'}]},
        ),
    )
    result=pub.review_queued_scholar_candidates(1)
    assert result['status']=='REVIEW_REQUIRED'
    assert not saved
    assert result['publication_rows_review_required']==1
    filter_step=next(step for step in result['steps'] if step['step']=='SCHOLAR_PUBLICATION_FILTER')
    assert filter_step['status']=='REVIEW_REQUIRED'
    assert filter_step['review_required'][0]['title'].startswith('The 13th International Workshop')
