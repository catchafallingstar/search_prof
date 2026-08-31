import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import requests

from ingestion import websearch as w
from ingestion.search_budget import provider_limits, reservation_delay, SearchBudgetWait

RESULT = {'href': 'https://example.edu/faculty/jane', 'title': 'Jane Smith', 'body': 'Professor'}


class LangSearchTests(unittest.TestCase):
    @patch.object(w.requests, 'post')
    def test_request_and_result_mapping(self, post):
        post.return_value.json.return_value = {'code': 200, 'data': {'webPages': {'value': [
            {'url': RESULT['href'], 'name': 'Jane Smith', 'snippet': 'Professor'}]}}}
        self.assertEqual(w._langsearch_search('Jane Smith University', 20, 'test-secret'), [RESULT])
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.langsearch.com/v1/web-search')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-secret')
        self.assertEqual(kwargs['json'], {'query': 'Jane Smith University', 'count': 10,
                                         'summary': False, 'freshness': 'noLimit'})
        self.assertNotIn('test-secret', str(kwargs['json']))

    @patch.object(w.requests, 'post')
    def test_valid_empty_distinct_from_bad_response(self, post):
        post.return_value.json.return_value = {'code': 200, 'data': {'webPages': {'value': []}}}
        self.assertEqual(w._langsearch_search('a', 3, 'secret'), [])
        for payload in ({}, [], {'code': 200, 'data': None},
                        {'code': 200, 'data': {'webPages': {'value': [None]}}},
                        {'code': 200, 'data': {'webPages': {'value': [{'url': 5}]}}}):
            post.return_value.json.return_value = payload
            with self.assertRaises(ValueError):
                w._langsearch_search('a', 3, 'secret')

    @patch.object(w.requests, 'post')
    def test_envelope_error_preserves_status_and_retry_after_not_provider_message(self, post):
        for code in (401, 402, 429, 500):
            post.return_value.json.return_value = {'code': code, 'msg': 'echoed-secret'}
            post.return_value.headers = {'Retry-After': '120'}
            with self.assertRaises(requests.HTTPError) as caught:
                w._langsearch_search('a', 3, 'secret')
            self.assertEqual(caught.exception.response.status_code, code)
            self.assertEqual(caught.exception.response.headers['Retry-After'], '120')
            self.assertNotIn('echoed-secret', str(caught.exception))

    @patch.object(w.requests, 'post')
    def test_http_429_not_empty(self, post):
        response = requests.Response()
        response.status_code = 429
        post.return_value.raise_for_status.side_effect = requests.HTTPError(response=response)
        with self.assertRaises(requests.HTTPError):
            w._langsearch_search('a', 3, 'secret')
        post.return_value.json.assert_not_called()

    def test_missing_key_skipped_and_present_key_first(self):
        values = {'SEARCH_PROVIDERS': 'langsearch,searchapi,parallel,ddgs', 'SEARCHAPI_API_KEY': 'a'}
        with patch.object(w, 'setting', side_effect=lambda key: values.get(key, '')):
            self.assertEqual(w._provider_names(), ['searchapi', 'ddgs'])
            values['LANGSEARCH_API_KEY'] = 'b'
            self.assertEqual(w._provider_names(), ['langsearch', 'searchapi', 'ddgs'])

    def test_limits_have_no_accidental_lifetime_cap(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider_limits('langsearch'), (2.0, 900, 0, 0))
        with patch.dict(os.environ, {'LANGSEARCH_MIN_INTERVAL_MS': '1', 'LANGSEARCH_DAILY_LIMIT': '10000'}):
            limits = provider_limits('langsearch')
            self.assertGreaterEqual(limits[0], 1)
            self.assertLessEqual(limits[1], 1000)

    def test_shared_delay_and_daily_cap(self):
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        row = {'now': now, 'utc_day': now.date(), 'usage_day': now.date(), 'requests_today': 0,
               'next_utc_day': now + timedelta(hours=12), 'next_request_at': now + timedelta(seconds=2)}
        self.assertEqual(reservation_delay(row, 2, 900)[0], 2)
        row['requests_today'] = 900
        self.assertEqual(reservation_delay(row, 2, 900)[0], 43200)

    @patch.object(w, '_assert_provider_available')
    @patch.object(w, '_langsearch_search')
    @patch.object(w, '_block_provider')
    @patch.object(w, 'search_slot')
    def test_wait_does_not_send_http_or_create_failure_cooldown(self, slot, block, request, available):
        slot.return_value.__enter__.side_effect = SearchBudgetWait('Waiting for the next search slot', 2)
        with self.assertRaises(w.SearchProviderUnavailable):
            w._run_provider('langsearch', 'Jane Smith', 10)
        slot.assert_called_once_with('langsearch')
        request.assert_not_called()
        block.assert_not_called()

    def test_fallback_stops_at_first_success(self):
        for exhausted, expected in ((False, ['langsearch']), (True, ['langsearch', 'searchapi'])):
            def run(provider, *args):
                if provider == 'langsearch' and exhausted:
                    raise w.SearchProviderUnavailable('Daily cap reached', 43200)
                return [RESULT]
            with patch.object(w, '_read_cache', return_value=None), patch.object(w, '_write_cache'), \
                 patch.object(w, '_provider_names', return_value=['langsearch', 'searchapi', 'ddgs']), \
                 patch.object(w, '_provider_strategy', return_value='fallback'), \
                 patch.object(w, '_run_provider', side_effect=run) as request:
                self.assertEqual(w.search_web('"Jane Smith" University', 10), [RESULT])
                self.assertEqual([call.args[0] for call in request.call_args_list], expected)


if __name__ == '__main__':
    unittest.main()
