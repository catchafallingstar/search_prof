import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from db import get_db_connection
from ingestion.verify_faculty import _save_result
from radar_store import _attach_identity_review_context


@unittest.skipUnless(os.getenv('RUN_DB_INTEGRATION') == '1', 'opt-in PostgreSQL test')
class IdentityAuditPostgresTests(unittest.TestCase):
    def test_audit_roundtrip_uses_temporary_tables_only(self):
        with get_db_connection() as connection:
            connection.execute('CREATE TEMP TABLE professors (LIKE public.professors INCLUDING DEFAULTS)')
            connection.execute('CREATE TEMP TABLE faculty_verification_evidence (LIKE public.faculty_verification_evidence INCLUDING DEFAULTS)')
            connection.execute("INSERT INTO professors(id,name,institution_name) VALUES (-987654, 'Test Person', 'Example University')")
            @contextmanager
            def borrowed():
                yield connection
            evidence={'outcome':'UNVERIFIED','results':[{'url':'https://linkedin.com/in/example','snippet':'Test Person PhD candidate','inspection':'Snippet only'}]}
            with patch('ingestion.verify_faculty.get_db_connection', borrowed):
                _save_result({'id':-987654,'name':'Test Person','institution_name':'Example University'}, {'status':'UNVERIFIED','search_audit':evidence})
            stored=connection.execute('SELECT identity_search_audit FROM professors WHERE id=-987654').fetchone()
            self.assertEqual(stored['identity_search_audit'],evidence)
            identity={'id':-987654}
            with connection.cursor() as cursor:
                _attach_identity_review_context(cursor,[identity])
            self.assertEqual(identity['identity_search_audit'],evidence)
            connection.rollback()
