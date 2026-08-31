import unittest
from unittest.mock import patch
import requests

from ingestion import websearch as search


RESULT = {'href': 'https://example.edu/jane', 'title': 'Jane Smith', 'body': 'Professor'}


class ParallelProviderTests(unittest.TestCase):
    @patch.object(search.requests, 'post')
    def test_parallel_service_uses_own_endpoint_and_auth(self, post):
        post.return_value.json.return_value = {'results': [
            {'url': RESULT['href'], 'title': 'Jane Smith', 'excerpts': ['Professor']}]}
        self.assertEqual(search._parallel_search('Jane Smith University', 5, 'secret'), [RESULT])
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.parallel.ai/v1/search')
        self.assertEqual(kwargs['headers']['x-api-key'], 'secret')
        self.assertEqual(kwargs['json']['mode'], 'fast')
        self.assertEqual(kwargs['json']['search_queries'], ['Jane Smith University'])
        self.assertNotIn('max_results', kwargs['json'])

    @patch.object(search.requests, 'post')
    def test_empty_and_invalid_payloads_distinguished(self, post):
        post.return_value.json.return_value = {'results': []}
        self.assertEqual(search._parallel_search('a', 3, 'secret'), [])
        for payload in ({}, {'error': 'secret'}, {'results': None}, {'results': [None]},
                        {'results': [{'excerpts': 'not a list'}]}):
            post.return_value.json.return_value = payload
            with self.assertRaises(ValueError) as error:
                search._parallel_search('a', 3, 'secret')
            self.assertNotIn('secret', str(error.exception))

    @patch.object(search.requests, 'post')
    def test_402_is_not_an_empty_search(self, post):
        response = requests.Response()
        response.status_code = 402
        post.return_value.raise_for_status.side_effect = requests.HTTPError(response=response)
        with self.assertRaises(requests.HTTPError):
            search._parallel_search('a', 3, 'secret')

    @patch.object(search, '_assert_provider_available')
    @patch.object(search, '_block_provider', return_value=86400)
    @patch.object(search, 'search_slot')
    @patch.object(search, '_parallel_search')
    def test_402_creates_clear_fallback_error(self, request, slot, block, available):
        response = requests.Response()
        response.status_code = 402
        request.side_effect = requests.HTTPError(response=response)
        with self.assertRaisesRegex(search.SearchProviderUnavailable, 'insufficient available credits'):
            search._run_provider('parallel', 'Jane Smith', 3)
        block.assert_called_once()

    def test_parallel_key_is_independent_of_strategy(self):
        values = {'SEARCH_PROVIDERS': 'searchapi,parallel,ddgs', 'SEARCHAPI_API_KEY': 'a',
                  'PARALLEL_API_KEY': 'b', 'SEARCH_PROVIDER_STRATEGY': 'fallback'}
        with patch.object(search, 'setting', side_effect=lambda name: values.get(name, '')):
            self.assertEqual(search._provider_names(), ['searchapi', 'parallel', 'ddgs'])
            self.assertEqual(search._provider_strategy(), 'fallback')
            values['PARALLEL_API_KEY'] = ''
            self.assertEqual(search._provider_names(), ['searchapi', 'ddgs'])

    def test_sequential_chain_stops_on_first_success_and_falls_back_on_exhaustion(self):
        for exhausted, expected in (
            (set(), ['searchapi']),
            ({'searchapi'}, ['searchapi', 'parallel']),
            ({'searchapi', 'parallel'}, ['searchapi', 'parallel', 'ddgs']),
        ):
            def answer(provider, *args):
                if provider in exhausted:
                    raise search.SearchProviderUnavailable('quota exhausted', 86400)
                return [RESULT]
            with patch.object(search, '_read_cache', return_value=None), \
                 patch.object(search, '_write_cache'), \
                 patch.object(search, '_provider_names', return_value=['searchapi', 'parallel', 'ddgs']), \
                 patch.object(search, '_provider_strategy', return_value='fallback'), \
                 patch.object(search, 'ThreadPoolExecutor') as executor, \
                 patch.object(search, '_run_provider', side_effect=answer) as run:
                self.assertEqual(search.search_web('"Jane Smith" University', 3), [RESULT])
                self.assertEqual([call.args[0] for call in run.call_args_list], expected)
                executor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
