from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from ingestion import index_worker as worker
from ingestion import websearch as search
import radar_store


def runtime(reason='Waiting for the next search slot', delay=57, blocked=0):
    with patch.object(search, '_provider_names', return_value=['ddgs']), patch.object(search, 'provider_capacity', return_value={'reason': reason, 'retry_after_seconds': delay}), patch.object(search, '_persistent_block_remaining', return_value=blocked):
        return search.search_provider_runtime_state()


def test_normal_pacing_parks_search_capable_jobs_until_available():
    state = runtime()
    assert state['available'] == []
    assert state['waitable'] == ['ddgs']
    assert not search.identity_search_can_run(state)


@pytest.mark.parametrize('reason,delay,blocked', [
    ('Daily search budget reached', 3600, 0),
    ('Provider cooldown', 57, 57),
    ('Search budget database unavailable', 60, 0),
    ('Waiting for the next search slot', 57, 300),
    ('Waiting for the next search slot', 121, 0),
])
def test_real_unavailability_is_not_admitted(reason, delay, blocked):
    assert not search.identity_search_can_run(runtime(reason, delay, blocked))


def test_directory_discovery_queues_only_approved_rosters():
    result = {
        'institution_id': 7,
        'directories': [{'directory_id': 12, 'url': 'https://example.edu/faculty'}],
    }
    with patch.object(worker, 'discover_faculty_directories', return_value=result) as discover, \
         patch.object(worker, 'enqueue_radar_job') as enqueue, \
         patch.object(worker, '_publish_job_progress'):
        actual, more = worker.process_job({
            'id': 1, 'job_type': 'DISCOVER_FACULTY_DIRECTORIES', 'institution_id': 7,
        })
    discover.assert_called_once()
    enqueue.assert_called_once_with(
        'CRAWL_FACULTY_DIRECTORY', faculty_directory_id=12,
        priority=90, max_attempts=8,
    )
    assert actual == result
    assert more is False


def test_publications_are_matched_only_for_one_roster_professor():
    with patch.object(
        worker, 'discover_faculty_publications',
        return_value={'status': 'OFFICIAL_PUBLICATIONS_FOUND', 'papers_imported': 4},
    ) as match, patch.object(worker, '_publish_job_progress'):
        result, more = worker.process_job({
            'id': 2, 'job_type': 'MATCH_FACULTY_PUBLICATIONS', 'professor_id': 42,
        })
    match.assert_called_once()
    assert result['papers_imported'] == 4
    assert more is False
def test_finished_jobs_report_data_outcome_separately() -> None:
    assert worker._job_outcome(
        "CRAWL_FACULTY_DIRECTORY",
        {"profiles_verified": 12, "profiles_pending": 3},
    ) == "REVIEW_REQUIRED"
    assert worker._job_outcome(
        "CRAWL_FACULTY_DIRECTORY",
        {"profiles_verified": 12, "profiles_pending": 0},
    ) == "APPROVED"
    assert worker._job_outcome(
        "MATCH_FACULTY_PUBLICATIONS", {"status": "NO_PUBLICATIONS_FOUND"}
    ) == "NO_PUBLICATIONS_FOUND"
    assert worker._job_outcome(
        "MATCH_FACULTY_PUBLICATIONS", {"status": "SCHOLAR_VERIFIED"}
    ) == "APPROVED"

def test_existing_verified_papers_queue_paper_summary_backfill(monkeypatch):
    rowsets = [
        [{
            'id': 42,
            'name': 'Jane Smith',
            'institution_id': 7,
            'institution_name': 'Example University',
            'department': 'Computer Science',
            'source_url': 'https://scholar.google.com/citations?user=abc',
        }],
        [
            {'title':'Recent paper one','year':2026,'venue':'Journal A','source_url':'https://example.org/1'},
            {'title':'Recent paper two','year':2025,'venue':'Journal B','source_url':'https://example.org/2'},
        ],
    ]

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args, **kwargs): pass
        def fetchall(self): return self.rows

    class Connection:
        def __init__(self, rows): self.rows = rows
        def cursor(self): return Cursor(self.rows)

    @contextmanager
    def connection():
        assert rowsets, 'unexpected database call'
        yield Connection(rowsets.pop(0))

    queued=[]
    monkeypatch.setattr(radar_store, 'get_db_connection', connection)
    monkeypatch.setattr(
        radar_store, 'enqueue_radar_job',
        lambda job_type, **kwargs: queued.append((job_type, kwargs)) or {'reused':False},
    )

    assert radar_store._enqueue_paper_summary_backfill(20) == 1
    assert not rowsets
    assert len(queued) == 1
    job_type, kwargs = queued[0]
    assert job_type == 'QWEN_REVIEW_INTERESTS'
    assert kwargs['professor_id'] == 42
    payload = kwargs['initial_result']['interest_input']
    assert payload['mode'] == 'PAPER_SUMMARY'
    assert payload['source_url'].startswith('https://scholar.google.com/')
    assert [paper['title'] for paper in payload['papers']] == [
        'Recent paper one', 'Recent paper two'
    ]

