from unittest.mock import patch
import pytest
from ingestion import index_worker as worker
from ingestion import websearch as search


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
