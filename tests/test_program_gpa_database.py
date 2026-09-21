"""Opt-in integration tests: only disposable scholarradar_gpa_sandbox_* databases."""
import os
import uuid
import pytest
from psycopg.conninfo import conninfo_to_dict
from db import get_db_connection
from ingestion import program_gpa as gpa
from radar_store import enqueue_radar_job, claim_next_radar_job, fetch_indexed_professors

@pytest.fixture
def program(monkeypatch):
    url=os.getenv('GPA_TEST_DATABASE_URL')
    if not url:pytest.skip('Set GPA_TEST_DATABASE_URL to a disposable sandbox database.')
    if not conninfo_to_dict(url).get('dbname','').startswith('scholarradar_gpa_sandbox_'):
        pytest.fail('Refusing a non-sandbox database.')
    monkeypatch.setenv('DATABASE_URL',url)
    with get_db_connection() as c:
        assert c.info.dbname.startswith('scholarradar_gpa_sandbox_')
        with c.cursor() as cur:
            cur.execute('INSERT INTO institutions(name) VALUES(%s) RETURNING id',('GPA test '+uuid.uuid4().hex,));iid=cur.fetchone()['id']
            cur.execute("INSERT INTO graduate_programs(institution_id,program_name,degree_type,verified_at) VALUES(%s,'Computer Science','PhD',NOW()) RETURNING id",(iid,));pid=cur.fetchone()['id']
            cur.execute("INSERT INTO program_admission_sources(program_id,source_url,official_domain,requirement_level,applicability_verified,applicability_note) VALUES(%s,'https://example.edu/phd','example.edu','PROGRAM',TRUE,'Test verified PhD admissions')",(pid,))
    monkeypatch.setattr(gpa,'fetch_official_text',lambda *args:'Applicants must have a minimum GPA of 3.0 on a 4.0 scale.')
    yield pid,iid
    with get_db_connection() as c:
        with c.cursor() as cur:
            cur.execute('DELETE FROM radar_jobs WHERE program_id IN (SELECT id FROM graduate_programs WHERE institution_id=%s)',(iid,))
            cur.execute('DELETE FROM program_admission_requirements WHERE institution_id=%s',(iid,))
            cur.execute('DELETE FROM graduate_programs WHERE institution_id=%s',(iid,))
            cur.execute('DELETE FROM institutions WHERE id=%s',(iid,))

def query(sql,params):
    with get_db_connection() as c:
        with c.cursor() as cur:
            cur.execute(sql,params)
            return cur.fetchone() if cur.description else None

def test_save_and_failure_preserves_evidence(program,monkeypatch):
    pid,_=program
    assert gpa.check_program_gpa(pid)['minimum_gpa']==3
    before=query('SELECT * FROM program_admission_requirements WHERE program_id=%s',(pid,))
    def fail(*a):raise TimeoutError('test outage')
    monkeypatch.setattr(gpa,'fetch_official_text',fail)
    with pytest.raises(TimeoutError):gpa.check_program_gpa(pid)
    assert query('SELECT * FROM program_admission_requirements WHERE program_id=%s',(pid,))==before
    assert query('SELECT count(*) AS n FROM program_admission_evidence WHERE program_id=%s',(pid,))['n']==1

def test_manual_override_survives_automatic_check(program):
    pid,_=program
    gpa.check_program_gpa(pid)
    query('UPDATE program_admission_requirements SET manual_override=TRUE,minimum_gpa=3.2 WHERE program_id=%s',(pid,))
    assert gpa.check_program_gpa(pid)['manual_override_preserved']
    assert float(query('SELECT minimum_gpa FROM program_admission_requirements WHERE program_id=%s',(pid,))['minimum_gpa'])==3.2
    assert query('SELECT count(*) AS n FROM program_admission_evidence WHERE program_id=%s',(pid,))['n']==2

def test_queue_deduplicates_program_not_professor(program):
    pid,_=program
    a=enqueue_radar_job('CHECK_PROGRAM_GPA',program_id=pid,priority=95)
    b=enqueue_radar_job('CHECK_PROGRAM_GPA',program_id=pid,priority=99)
    assert a['id']==b['id'] and b['reused'] and b['priority']==99
    with pytest.raises(ValueError):enqueue_radar_job('CHECK_PROGRAM_GPA',professor_id=1)

def test_distinct_degrees_and_programs_do_not_collide(program):
    pid,iid=program
    first=gpa.check_program_gpa(pid)
    second=query("INSERT INTO graduate_programs(institution_id,program_name,degree_type,verified_at) VALUES(%s,'Computer Science','MS',NOW()) RETURNING id",(iid,))['id']
    query("INSERT INTO program_admission_sources(program_id,source_url,official_domain,requirement_level,applicability_verified,applicability_note) VALUES(%s,'https://example.edu/ms','example.edu','PROGRAM',TRUE,'Verified MS page')",(second,))
    gpa.check_program_gpa(second)
    assert query('SELECT count(*) AS n FROM program_admission_requirements WHERE institution_id=%s',(iid,))['n']==2

def test_gpa_claim_does_not_wait_for_search_provider(program):
    pid,_=program
    job=enqueue_radar_job('CHECK_PROGRAM_GPA',program_id=pid,priority=100)
    # Only our job is made oldest; other queue work is excluded in this sandbox test.
    query("UPDATE radar_jobs SET created_at=NOW()-INTERVAL '30 days',available_at=NOW()-INTERVAL '30 days' WHERE id=%s",(job['id'],))
    all_types=['CHECK_HIRING','CHECK_GRANTS','MATCH_FACULTY_PUBLICATIONS','ENRICH_CLASSIFY_PAPER','QWEN_REVIEW_PUBLICATION','QWEN_REVIEW_INTERESTS','DISCOVER_FACULTY_DIRECTORIES','CRAWL_FACULTY_DIRECTORY','INDEX_ROSTER_TOPIC']
    claimed=claim_next_radar_job('gpa-sandbox-test',excluded_job_types=all_types,search_ready=False)
    assert claimed['id']==job['id']
    from ingestion.index_worker import process_job, _job_outcome
    result,more=process_job(claimed)
    assert result['minimum_gpa']==3 and not more
    assert _job_outcome('CHECK_PROGRAM_GPA',result)=='APPROVED'

def test_public_query_uses_explicit_program_links(program):
    # Execute the actual public SQL so changed lateral joins/columns are validated.
    fetch_indexed_professors('machine learning')
