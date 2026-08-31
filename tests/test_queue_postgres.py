"""Real query tests use only session-local temporary tables, never the real queue."""
import os
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from db import get_db_connection
from radar_store import fetch_topic_candidate_ids, claim_next_radar_job
from ingestion.verify_faculty import get_cached_faculty_decisions
from ingestion.verify_faculty import _save_result


@unittest.skipUnless(os.getenv("RUN_DB_INTEGRATION") == "1", "explicit database test required")
class QueuePostgresTests(unittest.TestCase):
    def test_conflict_is_saved_without_retry_and_reused_indefinitely(self):
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('''
                    CREATE TEMP TABLE professors (LIKE public.professors INCLUDING DEFAULTS);
                    CREATE TEMP TABLE faculty_verification_evidence (LIKE public.faculty_verification_evidence INCLUDING DEFAULTS INCLUDING INDEXES);
                    INSERT INTO professors(id, name, institution_name, faculty_status)
                        VALUES (-999, 'Test Person', 'Example University', 'UNVERIFIED');
                ''')
                with patch('ingestion.verify_faculty.get_db_connection', side_effect=lambda: nullcontext(connection)):
                    _save_result({'id': -999, 'institution_name': 'Example University'},
                        {'status': 'CONFLICT', 'institution_name': 'Other University', 'method': 'institution_mismatch_review',
                         'source_url': 'https://example.edu/test', 'evidence_text': 'Test evidence'})
                    cursor.execute("SELECT faculty_status, next_identity_check_at, institution_name FROM professors WHERE id = -999")
                    self.assertEqual(cursor.fetchone(), {'faculty_status': 'CONFLICT', 'next_identity_check_at': None,
                                                       'institution_name': 'Example University'})
                    cursor.execute("UPDATE professors SET faculty_checked_at = NOW() - INTERVAL '365 days' WHERE id = -999")
                    self.assertEqual(get_cached_faculty_decisions([-999])['decided_ids'], [-999])

    def test_recent_checks_reused_and_search_only_jobs_remain_unclaimed(self):
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TEMP TABLE institutions (id INT, country_code TEXT);
                    CREATE TEMP TABLE professors (
                        id INT, institution_id INT, faculty_status TEXT DEFAULT 'UNVERIFIED',
                        faculty_verification_method TEXT, faculty_verification_version INT DEFAULT 0,
                        faculty_checked_at TIMESTAMPTZ, next_identity_check_at TIMESTAMPTZ,
                        identity_retry_at TIMESTAMPTZ, identity_search_pending BOOLEAN DEFAULT FALSE,
                        faculty_source_url TEXT, homepage_url TEXT, orcid_id TEXT
                    );
                    CREATE TEMP TABLE radar_topic_professors (
                        radar_topic_id INT, professor_id INT, is_current_match BOOLEAN DEFAULT TRUE, result_rank INT DEFAULT 1
                    );
                    CREATE TEMP TABLE radar_jobs (
                        id INT, job_type TEXT, radar_topic_id INT, professor_id INT,
                        status TEXT DEFAULT 'queued', available_at TIMESTAMPTZ DEFAULT NOW(),
                        attempts INT DEFAULT 0, max_attempts INT DEFAULT 5, priority INT DEFAULT 85,
                        locked_at TIMESTAMPTZ, locked_by TEXT, started_at TIMESTAMPTZ,
                        completed_at TIMESTAMPTZ, last_error TEXT, updated_at TIMESTAMPTZ DEFAULT NOW(),
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    INSERT INTO institutions VALUES (1, 'US');
                    INSERT INTO professors(id, institution_id, faculty_status, faculty_checked_at)
                        VALUES (1, 1, 'VERIFIED', NOW() - INTERVAL '5 days');
                    INSERT INTO professors(id, institution_id, identity_search_pending) VALUES (2, 1, TRUE);
                    INSERT INTO professors(id, institution_id, homepage_url) VALUES (3, 1, 'https://example.edu/faculty');
                    INSERT INTO radar_topic_professors(radar_topic_id, professor_id) VALUES (1,1), (1,2), (2,3);
                    INSERT INTO radar_jobs(id, job_type, radar_topic_id) VALUES (10, 'VERIFY_FACULTY', 1), (20, 'VERIFY_FACULTY', 2);
                """)
                with patch("radar_store.get_db_connection", side_effect=lambda: nullcontext(connection)), \
                     patch("ingestion.verify_faculty.get_db_connection", side_effect=lambda: nullcontext(connection)), \
                     patch("radar_store._target_country_code", return_value="US"):
                    self.assertEqual(fetch_topic_candidate_ids(1), [2])
                    self.assertEqual(fetch_topic_candidate_ids(1, direct_only=True), [])
                    self.assertEqual(get_cached_faculty_decisions([1]), {"verified_ids": [], "decided_ids": [1]})
                    first = claim_next_radar_job("test", search_ready=False)
                    self.assertEqual(first["id"], 20)
                    self.assertIsNone(claim_next_radar_job("test", search_ready=False))
                    cursor.execute("SELECT attempts, status FROM radar_jobs WHERE id = 10")
                    self.assertEqual(cursor.fetchone(), {"attempts": 0, "status": "queued"})
                    self.assertEqual(claim_next_radar_job("test", search_ready=True)["id"], 10)
