import io
import json
import os
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

import requests
from ingestion import verify_faculty as v
from ingestion import verification_audit as a
from ingestion import websearch as w
from ingestion.identity_sources import cv_homepage, linked_profile_leads

CANDIDATE = {'name': 'Jane Smith', 'institution_name': 'Example University',
             'institution_domain': 'example.edu'}


class ExpandedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            'FACULTY_IDENTITY_PASS_QUERIES': '10', 'FACULTY_IDENTITY_PASS_RESULTS': '100',
            'FACULTY_IDENTITY_PASS_PAGES': '20', 'FACULTY_IDENTITY_PASS_SECONDS': '90',
            'FACULTY_IDENTITY_VERBOSE_LOG': 'false'}))
        self.stack.enter_context(patch.object(v, 'fetch_orcid_clues', return_value=None))
        self.stack.enter_context(patch.object(v, 'enrich_candidate_metadata_affiliations', side_effect=lambda c, **kw: c))
        self.stack.enter_context(patch.object(v, 'enrich_candidate_paper_affiliations', side_effect=lambda c, **kw: c))
        self.stack.enter_context(patch.object(v, 'is_public_http_url', return_value=True))

    def response(self, html, status=200):
        response = requests.Response()
        response.status_code = status
        response.headers['content-type'] = 'text/html'
        response._content = html.encode()
        response._content_consumed = True
        return response

    def test_second_query_targets_supported_school_not_long_paper(self):
        def search(query, **kw):
            if 'site:example.edu' in query:
                return [{'href': 'https://example.edu/faculty/jane', 'title': 'Jane Smith'}]
            return [{'href': 'https://publisher.com/paper', 'title': 'A journal article'}]
        with patch.object(v, 'search_web', side_effect=search) as get, patch.object(v, 'inspect_faculty_result', return_value={'status': 'VERIFIED', 'source_url': 'https://example.edu/faculty/jane'}):
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(get.call_args_list[1].args[0], '"Jane Smith" site:example.edu')
        self.assertEqual(len(result['search_audit']['results']), 1)
        self.assertEqual(result['search_audit']['links_collected'], 2)

    def test_first_faculty_profile_stops_without_fetching_rest_of_batch(self):
        links = [{'href': f'https://example.edu/faculty/{i}', 'title': 'Jane Smith'} for i in range(10)]
        with patch.object(v, 'search_web', return_value=links) as search, \
             patch.object(v, 'inspect_faculty_result', return_value={'status': 'VERIFIED', 'source_url': links[0]['href']}) as inspect:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(inspect.call_count, 1)
        self.assertIn('Stopped early', result['search_audit']['stopping_reason'])

    def test_student_profile_stops_without_searching_for_counterexample(self):
        links = [{'href': 'https://example.edu/people/jane', 'title': 'Jane Smith'}]
        with patch.object(v, 'search_web', return_value=links) as search, \
             patch.object(v, 'inspect_faculty_result', return_value={'status': 'NOT_FACULTY', 'title': 'PhD Student'}) as inspect:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(inspect.call_count, 1)

    def test_personal_and_lab_decisions_stop_on_first_search(self):
        for url, title in [('https://jane.github.io/', 'Jane Smith'),
                           ('https://janelab.com/team', 'Jane Smith Research Lab')]:
            with self.subTest(url=url), patch.object(v, 'search_web', return_value=[{'href': url, 'title': title}]) as search, \
                 patch.object(v, 'inspect_researcher_profile_result', return_value={'status': 'VERIFIED', 'source_url': url}) as inspect:
                result = v.verify_faculty_candidate(CANDIDATE)
            self.assertEqual(result['status'], 'VERIFIED')
            self.assertEqual(search.call_count, 1)
            self.assertEqual(inspect.call_count, 1)

    def test_conflict_stops_before_later_old_school_page(self):
        links = [{'href': 'https://other.edu/faculty/jane', 'title': 'Jane Smith'},
                 {'href': 'https://example.edu/faculty/jane', 'title': 'Jane Smith'}]
        with patch.object(v, 'search_web', return_value=links) as search, \
             patch.object(v, 'inspect_faculty_result', side_effect=[
                 {'status': 'CONFLICT', 'method': 'institution_mismatch_review', 'source_url': links[0]['href']},
                 {'status': 'VERIFIED', 'source_url': links[1]['href']},
             ]) as inspect:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(inspect.call_count, 1)

    def test_linkedin_clue_does_not_stop_before_actual_profile(self):
        with patch.object(v, 'search_web', side_effect=[
            [{'href': 'https://linkedin.com/in/jane', 'title': 'Jane Smith', 'body': 'Jane Smith PhD candidate at Example University'}],
            [{'href': 'https://example.edu/people/jane', 'title': 'Jane Smith'}],
        ]) as search, patch.object(v, 'inspect_faculty_result', return_value={'status': 'NOT_FACULTY', 'title': 'PhD Student'}):
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(search.call_count, 2)

    def test_repeated_links_do_not_count_toward_hundred(self):
        with patch.object(v, 'search_web', return_value=[{'href': 'https://publisher.com/paper', 'title': 'Article'}]) as search:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertGreater(search.call_count, 1)
        self.assertEqual(result['search_audit']['links_collected'], 1)
        self.assertLessEqual(search.call_count, 10)

    def test_hundred_links_cap_and_noise_retains_ten(self):
        count = 0
        def search(query, max_results):
            nonlocal count
            rows = [{'href': f'https://publisher.com/paper/{i}', 'title': 'Journal article'} for i in range(count, count + min(10, max_results))]
            count += len(rows)
            return rows
        candidate = {**CANDIDATE, 'name': 'Jane M Smith', 'recent_papers': [
            {'title': f'A supporting paper with a unique title {i}', 'matched_query': 'Biology'} for i in range(3)]}
        with patch.object(v, 'search_web', side_effect=search) as get:
            result = v.verify_faculty_candidate(candidate)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(result['search_audit']['links_collected'], 30)
        self.assertEqual(len(result['search_audit']['results']), 10)
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertTrue(result['review_recommended'])
        self.assertFalse(any(k.startswith('_') for k in result['search_audit']))

    def test_news_follows_link_but_is_not_itself_verified(self):
        news = 'https://news.example.edu/welcome/jane'
        profile = 'https://example.edu/faculty/jane'
        html = f'<title>Jane Smith joins us</title><p>Assistant Professor Jane Smith</p><a href="{profile}">Jane Smith</a>'
        with patch.object(v, 'search_web', return_value=[{'href': news, 'title': 'Jane Smith'}]), \
             patch.object(v.requests, 'get', return_value=self.response(html)), \
             patch.object(v, 'inspect_faculty_result', return_value={'status': 'CONFLICT', 'method': 'institution_mismatch_review', 'source_url': profile}) as inspect:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[1]['href'], profile)
        self.assertTrue(any(r.get('discovered_from') == news for r in result['search_audit']['results']))

    def test_cv_follows_personal_root_student_and_stops(self):
        cv = 'https://jane.github.io/files/cv.pdf'
        response = self.response('pdf')
        response.headers['content-type'] = 'application/pdf'
        with patch.object(v, 'search_web', return_value=[{'href': cv, 'title': 'Jane Smith CV'}]) as search, \
             patch.object(v.requests, 'get', return_value=response), \
             patch.object(v, 'inspect_researcher_profile_result', return_value={'status': 'NOT_FACULTY', 'title': 'PhD Candidate'}) as inspect:
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(inspect.call_args.args[1]['href'], 'https://jane.github.io/')
        self.assertEqual(search.call_count, 1)

    def test_team_heading_and_matching_paper_support_own_role(self):
        url = 'https://janelab.com/team'
        html = '<title>Team</title><h3>Jane Smith</h3><p>Jane Smith Assistant Professor at Example University.</p><p>Exact supporting research paper title</p>'
        audit = a.new_audit(CANDIDATE)
        token = a.CURRENT.set(audit)
        self.addCleanup(a.CURRENT.reset, token)
        with patch.object(v.requests, 'get', return_value=self.response(html)), patch.object(v, '_paper_identity_link', return_value=True):
            result = v.inspect_researcher_profile_result(CANDIDATE, {'href': url, 'title': 'Jane Smith lab'})
        self.assertEqual(result['status'], 'VERIFIED')

    def test_blocked_200_reports_source_blocked(self):
        with patch.object(v, 'search_web', return_value=[{'href': 'https://example.edu/faculty/jane', 'title': 'Jane Smith'}]), \
             patch.object(v.requests, 'get', return_value=self.response('<title>Request unsuccessful</title><p>Incapsula access denied</p>')):
            result = v.verify_faculty_candidate(CANDIDATE)
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertEqual(result['failure_code'], 'SOURCE_BLOCKED')
        self.assertIn('blocked', result['reason'].lower())

    def test_only_an_active_search_has_a_deadline(self):
        audit = a.new_audit(CANDIDATE)
        token = a.CURRENT.set(audit)
        self.addCleanup(a.CURRENT.reset, token)
        self.assertGreater(a.remaining_seconds(), 3600)
        a.begin_search(90)
        self.assertLessEqual(a.remaining_seconds(), 90)
        a.end_search()
        self.assertGreater(a.remaining_seconds(), 3600)

    def test_logs_include_discarded_results_but_database_audit_does_not(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'FACULTY_IDENTITY_VERBOSE_LOG': 'true', 'FACULTY_IDENTITY_PASS_QUERIES': '1'}), redirect_stdout(output), \
             patch.object(v, 'search_web', return_value=[{'href': f'https://publisher.com/{i}', 'title': 'Article\nnot a log event'} for i in range(20)]):
            result = v.verify_faculty_candidate(CANDIDATE)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len([e for e in events if e['event'] == 'identity_search_result']), 20)
        self.assertEqual(events[-1]['event'], 'identity_decision')
        self.assertEqual(len(result['search_audit']['results']), 10)

    def test_identity_queries_do_not_write_raw_search_cache(self):
        token = a.CURRENT.set(a.new_audit(CANDIDATE))
        self.addCleanup(a.CURRENT.reset, token)
        with patch.object(w, '_read_cache', return_value=None), patch.object(w, '_write_cache') as write, \
             patch.object(w, '_provider_names', return_value=['langsearch']), \
             patch.object(w, '_provider_strategy', return_value='fallback'), \
             patch.object(w, 'provider_capacity', return_value={'retry_after_seconds': 0}), \
             patch.object(w, '_run_provider', return_value=[]):
            self.assertEqual(w.search_web('"Jane Smith" Example University', 10), [])
        write.assert_not_called()

    def test_profile_following_is_bounded_and_rejects_unsafe_urls(self):
        self.assertIsNone(cv_homepage('https://example.edu/documents/cv.pdf'))
        leads = linked_profile_leads('https://example.edu/news/jane', [
            {'href': 'javascript:alert(1)', 'label': 'Jane Smith'},
            {'href': '/faculty/jane', 'label': 'Jane Smith'},
            {'href': '/faculty/other', 'label': 'Other Person'}], lambda t: t == 'Jane Smith')
        self.assertEqual([r['href'] for r in leads], ['https://example.edu/faculty/jane'])


if __name__ == '__main__':
    unittest.main()
