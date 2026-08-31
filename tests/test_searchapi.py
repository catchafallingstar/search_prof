import os
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from ingestion import websearch as search
from ingestion import provider_quota as quota
from ingestion.search_budget import SearchBudgetWait, provider_limits


RESULT = {'href': 'https://example.edu/jane', 'title': 'Jane Smith', 'body': 'Professor'}


class SearchAPITests(unittest.TestCase):
    @patch.object(search.requests, 'get')
    def test_adapter_uses_header_and_maps_organic_results(self, get):
        get.return_value.json.return_value = {
            'search_metadata': {'status': 'Success'},
            'organic_results': [{'title': 'Jane Smith', 'link': RESULT['href'], 'snippet': 'Professor'}]}
        self.assertEqual(search._searchapi_search('Jane Smith', 5, 'secret'), [RESULT])
        args, kwargs = get.call_args
        self.assertEqual(args[0], 'https://www.searchapi.io/api/v1/search')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer secret')
        self.assertEqual(kwargs['params']['engine'], 'google')
        self.assertNotIn('api_key', kwargs['params'])

    @patch.object(search.requests, 'get')
    def test_explicit_success_without_organic_results_is_empty(self, get):
        get.return_value.json.return_value = {'search_metadata': {'status': 'Success'}}
        self.assertEqual(search._searchapi_search('a', 3, 'secret'), [])

    @patch.object(search.requests, 'get')
    def test_failed_and_malformed_payloads_do_not_become_empty(self, get):
        for payload in ({}, [], {'error': 'secret echoed'},
                        {'search_metadata': {'status': 'Processing'}},
                        {'search_metadata': {'status': 'Success'}, 'organic_results': None}):
            get.return_value.json.return_value = payload
            with self.assertRaises(ValueError) as raised:
                search._searchapi_search('a', 3, 'secret')
            self.assertNotIn('secret', str(raised.exception))

    @patch.object(search.requests, 'get')
    def test_http_failure_is_not_empty(self, get):
        get.return_value.raise_for_status.side_effect = requests.HTTPError('HTTP 401')
        with self.assertRaises(requests.HTTPError):
            search._searchapi_search('a', 3, 'secret')

    def test_trial_with_zero_monthly_allowance_still_has_credits(self):
        self.assertEqual(quota.searchapi_remaining({'account': {'monthly_allowance': 0,
            'remaining_credits': 100}}), 100)

    def test_hourly_window_and_zero_balance(self):
        self.assertEqual(quota.searchapi_remaining({'account': {'remaining_credits': 100},
            'api_usage': {'hourly_rate_limit': 50, 'searches_this_hour': 50}}), 0)
        self.assertEqual(quota.searchapi_remaining({'account': {'remaining_credits': 0}}), 0)
        for value in (None, True, '100', float('inf')):
            with self.assertRaises(ValueError):
                quota.searchapi_remaining({'account': {'remaining_credits': value}})

    @patch.object(quota, 'setting', return_value='test-secret')
    @patch.object(quota, 'save_remote_quota')
    @patch.object(quota.requests, 'get')
    @patch.object(quota, 'get_db_connection')
    def test_quota_auth_error_is_explicit_and_secret_safe(self, connection, get, save, setting):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{'acquired': True}, None]
        response = requests.Response()
        response.status_code = 401
        get.return_value.raise_for_status.side_effect = requests.HTTPError('test-secret', response=response)
        with self.assertRaises(SearchBudgetWait) as raised:
            quota.check_searchapi_quota()
        self.assertIn('SEARCHAPI_API_KEY', str(raised.exception))
        self.assertIn('401', str(raised.exception))
        self.assertNotIn('test-secret', str(raised.exception))
        save.assert_called_once_with('searchapi', None, 300)

    @patch.object(search, '_assert_provider_available')
    @patch.object(search, 'check_searchapi_quota', side_effect=SearchBudgetWait('quota exhausted', 300))
    @patch.object(search, 'search_slot')
    @patch.object(search, '_searchapi_search')
    def test_zero_quota_does_not_spend_a_search(self, request, slot, *_):
        with self.assertRaises(search.SearchProviderUnavailable):
            search._run_provider('searchapi', 'Jane', 3)
        request.assert_not_called()
        slot.assert_not_called()

    def test_provider_key_detection_and_defaults(self):
        with patch.object(search, 'setting', side_effect=lambda key: {
            'SEARCH_PROVIDERS': 'searchapi,tavily,ddgs', 'SEARCHAPI_API_KEY': 'secret'
        }.get(key, '')):
            self.assertEqual(search._provider_names(), ['searchapi', 'ddgs'])
        with patch.dict(os.environ, {'SEARCHAPI_MIN_INTERVAL_MS': '2000'}):
            self.assertEqual(provider_limits('searchapi')[0], 2)

    def run_search(self, names, strategy, handler):
        with patch.object(search, '_read_cache', return_value=None), \
             patch.object(search, '_write_cache'), \
             patch.object(search, '_provider_names', return_value=names), \
             patch.object(search, '_provider_strategy', return_value=strategy), \
             patch.object(search, '_run_provider', side_effect=handler) as run:
            result = search.search_web('"Jane Smith" university', 5)
        return result, [call.args[0] for call in run.call_args_list]

    def test_parallel_with_one_api_does_not_also_call_ddgs(self):
        result, used = self.run_search(['searchapi', 'ddgs'], 'parallel', lambda *args: [RESULT])
        self.assertEqual(result, [RESULT])
        self.assertEqual(used, ['searchapi'])

    def test_two_apis_really_run_concurrently_and_merge_duplicates(self):
        barrier = threading.Barrier(2)
        def answer(provider, *args):
            self.assertNotEqual(provider, 'ddgs')
            barrier.wait(timeout=3)
            return [RESULT]
        result, used = self.run_search(['searchapi', 'tavily', 'ddgs'], 'parallel', answer)
        self.assertEqual(result, [RESULT])
        self.assertEqual(set(used), {'searchapi', 'tavily'})

    def test_parallel_exhaustion_reaches_ddgs(self):
        def answer(provider, *args):
            if provider == 'ddgs':
                return [RESULT]
            raise search.SearchProviderUnavailable('quota exhausted', 300)
        result, used = self.run_search(['searchapi', 'tavily', 'ddgs'], 'parallel', answer)
        self.assertEqual(result, [RESULT])
        self.assertEqual(used[-1], 'ddgs')

    def test_fallback_uses_next_api(self):
        def answer(provider, *args):
            if provider == 'searchapi':
                raise search.SearchProviderUnavailable('quota exhausted', 300)
            return [RESULT]
        result, used = self.run_search(['searchapi', 'tavily', 'ddgs'], 'fallback', answer)
        self.assertEqual(used, ['searchapi', 'tavily'])


if __name__ == '__main__':
    unittest.main()
