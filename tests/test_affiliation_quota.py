import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ingestion.affiliation_extract import author_institution, institutions_in_text
from ingestion.paper_affiliations import extract_paper_affiliation, enrich_candidate_metadata_affiliations
from ingestion.paper_affiliations import _resolve_open_pdf_url, _pdf_url_from_doi
from ingestion.homepagefinder import is_public_http_url
from ingestion.provider_quota import tavily_remaining, brave_quota
from ingestion.search_budget import reservation_delay
from ingestion.search_budget import SearchBudgetWait
from ingestion.websearch import _run_provider, SearchProviderUnavailable, search_web
from identity_schedule import minimum_recheck_sql


class AffiliationQuotaTests(unittest.TestCase):
    def test_missing_university_can_be_extracted(self):
        institution, _ = author_institution('Jane Smith', 'Jane Smith\nUniversity of Colorado\nAbstract\nResearch')
        self.assertEqual(institution, 'University of Colorado')

    def test_numbered_authors_do_not_borrow_coauthor_institution(self):
        text = 'Jane Smith¹, John Doe²\n1 University of Colorado\n2 Stanford University\nAbstract'
        self.assertEqual(author_institution('Jane Smith', text)[0], 'University of Colorado')
        self.assertEqual(author_institution('John Doe', text)[0], 'Stanford University')

    def test_ambiguous_multiple_affiliations_stay_unknown(self):
        text = 'Jane Smith, John Doe\nUniversity of Colorado\nStanford University\nAbstract'
        self.assertEqual(author_institution('Jane Smith', text)[0], '')

    def test_references_do_not_supply_affiliations(self):
        self.assertEqual(author_institution('Jane Smith', 'Abstract\nJane Smith Stanford University')[0], '')

    @patch('ingestion.paper_affiliations._download_pdf')
    def test_author_metadata_without_imported_institution_is_enough_for_query_anchor(self, download):
        result = extract_paper_affiliation({'name': 'Jane Smith', 'institution_name': ''},
                    {'raw_affiliation_text': 'Department of Computing, Stanford University, USA'})
        self.assertEqual(result['institution_name'], 'Stanford University')
        download.assert_not_called()

    def test_old_unsafe_pdf_cache_is_not_trusted(self):
        candidate = {'name': 'Jane Smith', 'recent_papers': [{'affiliation_status': 'MATCHED',
            'affiliation_version': 0, 'affiliation_institution': 'Wrong University'}]}
        self.assertEqual(enrich_candidate_metadata_affiliations(candidate)['paper_affiliations'], [])

    def test_tavily_checks_account_and_key_and_paid_opt_in(self):
        payload = {'account': {'plan_limit': 1000, 'plan_usage': 1000, 'paygo_limit': 100, 'paygo_usage': 20},
                   'key': {'limit': 500, 'usage': 450}}
        self.assertEqual(tavily_remaining(payload), 0)
        self.assertEqual(tavily_remaining(payload, True), 50)
        with self.assertRaises(ValueError):
            tavily_remaining({})

    def test_brave_uses_exhausted_window_not_just_month(self):
        headers = {'X-RateLimit-Limit': '1, 15000', 'X-RateLimit-Remaining': '0, 100', 'X-RateLimit-Reset': '1, 6000'}
        self.assertEqual(brave_quota(headers), (0, 1))
        headers['X-RateLimit-Remaining'] = '0, 0'
        self.assertEqual(brave_quota(headers), (0, 6000))
        self.assertIsNone(brave_quota({}))

    def test_remote_zero_prevents_request_but_expired_quota_can_be_rechecked(self):
        now = datetime.now(timezone.utc)
        row = {'now': now, 'utc_day': now.date(), 'remote_remaining': 0,
               'remote_reset_at': now + timedelta(seconds=300)}
        self.assertEqual(reservation_delay(row, 2, 200)[0], 300)
        row['remote_reset_at'] = now - timedelta(seconds=1)
        self.assertEqual(reservation_delay(row, 2, 200)[0], 0)

    def test_conflict_excluded_from_automatic_due_checks(self):
        self.assertIn("faculty_status IS DISTINCT FROM 'CONFLICT'", minimum_recheck_sql())

    def test_pdf_links_allowed_only_for_paper_downloads(self):
        self.assertFalse(is_public_http_url('https://example.edu/paper.pdf'))
        self.assertTrue(is_public_http_url('https://example.edu/paper.pdf', allow_pdf=True))
        self.assertFalse(is_public_http_url('http://127.0.0.1/paper.pdf', allow_pdf=True))
        self.assertFalse(is_public_http_url('https://example.edu/paper.zip', allow_pdf=True))
        self.assertEqual(_resolve_open_pdf_url({'pdf_url': 'https://example.edu/paper.pdf'}), 'https://example.edu/paper.pdf')

    @patch('ingestion.paper_affiliations.is_public_http_url', return_value=True)
    @patch('ingestion.paper_affiliations.requests.get')
    def test_doi_landing_pdf_locator_without_openalex(self, get, safe):
        response = get.return_value
        response.status_code = 200
        response.headers = {'Content-Type': 'text/html'}
        response.iter_content.return_value = [b'<meta name="citation_pdf_url" content="https://example.edu/a.pdf">']
        self.assertEqual(_pdf_url_from_doi('https://doi.org/10.1234/test'), 'https://example.edu/a.pdf')
        response.close.assert_called_once()

    @patch('ingestion.paper_affiliations.is_public_http_url', side_effect=[True, False])
    @patch('ingestion.paper_affiliations.requests.get')
    def test_doi_redirect_to_private_network_is_not_followed(self, get, safe):
        response = get.return_value
        response.status_code = 302
        response.headers = {'Location': 'http://127.0.0.1/paper.pdf'}
        self.assertEqual(_pdf_url_from_doi('10.1234/test'), '')
        self.assertEqual(get.call_count, 1)

    @patch('ingestion.websearch._assert_provider_available')
    @patch('ingestion.websearch.check_tavily_quota', side_effect=SearchBudgetWait('quota exhausted', 300))
    @patch('ingestion.websearch._tavily_search')
    @patch('ingestion.websearch.search_slot')
    def test_exhausted_remote_quota_never_spends_a_search_request(self, slot, request, *_):
        with self.assertRaises(SearchProviderUnavailable):
            _run_provider('tavily', 'Jane Smith University', 3)
        slot.assert_not_called()
        request.assert_not_called()

    @patch('ingestion.websearch._read_cache', return_value=None)
    @patch('ingestion.websearch._write_cache')
    @patch('ingestion.websearch._provider_names', return_value=['tavily', 'brave', 'ddgs'])
    @patch('ingestion.websearch._provider_strategy', return_value='parallel')
    @patch('ingestion.websearch.setting_int', return_value=2)
    @patch('ingestion.websearch._run_provider')
    def test_exhausted_parallel_apis_reach_final_duckduckgo(self, run, *_):
        def answer(provider, *args):
            if provider != 'ddgs':
                raise SearchProviderUnavailable('quota exhausted', 300)
            return [{'href': 'https://example.edu/jane', 'title': 'Jane Smith', 'body': 'Professor'}]
        run.side_effect = answer
        self.assertEqual(search_web('"Jane Smith" University')[0]['title'], 'Jane Smith')
        self.assertEqual(run.call_args_list[-1].args[0], 'ddgs')


if __name__ == '__main__':
    unittest.main()
