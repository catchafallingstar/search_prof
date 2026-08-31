import unittest
from unittest.mock import patch
import requests
from ingestion.websearch import _block_provider, _provider_blocked_until


class NetworkBackoffTests(unittest.TestCase):
    @patch('ingestion.websearch.get_db_connection')
    @patch('ingestion.websearch.setting_int', side_effect=lambda name, default, *_: default)
    def test_transport_failure_short_but_quota_and_rate_limit_stay_long(self, setting, connection):
        try:
            self.assertEqual(_block_provider('unit-test', requests.ReadTimeout('timed out')), 60)
            for status, minimum in ((402, 86400), (429, 3600)):
                response = requests.Response()
                response.status_code = status
                response.headers = {}
                self.assertGreaterEqual(_block_provider('unit-test', requests.HTTPError(response=response)), minimum)
        finally:
            _provider_blocked_until.pop('unit-test', None)
