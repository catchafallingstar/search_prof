"""Regressions for the August 31 ten-person diagnostic, without external calls."""
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock
import requests
from ingestion import verify_faculty as v, verification_audit as a, websearch as w
from ingestion.identity_sources import canonical_source_url, source_kind, text_url_leads


class VerificationRepairTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'FACULTY_IDENTITY_VERBOSE_LOG': 'false'}))
        self.stack.enter_context(patch.object(v, 'is_public_http_url', return_value=True))
        self.stack.enter_context(patch.object(v, 'fetch_orcid_clues', return_value=None))
        for helper in ('enrich_candidate_metadata_affiliations', 'enrich_candidate_paper_affiliations'):
            self.stack.enter_context(patch.object(v, helper, side_effect=lambda c, **kw: c))

    def audit(self, name='Jane Smith'):
        audit = a.new_audit({'name': name})
        token = a.CURRENT.set(audit)
        self.addCleanup(a.CURRENT.reset, token)
        return audit

    @staticmethod
    def response(html, status=200):
        r = requests.Response()
        r.status_code = status
        r._content = html.encode()
        r._content_consumed = True
        r.headers['content-type'] = 'text/html'
        return r

    def test_unrelated_search_is_not_outage_or_paid_fallback(self):
        audit = self.audit()
        with patch.object(w, '_read_cache', return_value=None), \
             patch.object(w, '_provider_names', return_value=['langsearch', 'searchapi']), \
             patch.object(w, '_provider_strategy', return_value='fallback'), \
             patch.object(w, 'provider_capacity', return_value={'retry_after_seconds': 0}), \
             patch.object(w, '_run_provider', return_value=[{'href': 'https://example.org/weather', 'title': 'Weather forecast'}]) as get:
            self.assertEqual(w.search_web('"Jane Smith" Example University', 10), [])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(audit['retrieval_events'][0]['outcome'], 'NO_USEFUL_RESULTS')
        self.assertEqual(len(audit['retrieval_events'][0]['sample']), 1)

    def test_real_outage_still_defers(self):
        self.audit()
        with patch.object(w, '_read_cache', return_value=None), \
             patch.object(w, '_provider_names', return_value=['langsearch']), \
             patch.object(w, '_provider_strategy', return_value='fallback'), \
             patch.object(w, 'provider_capacity', return_value={'retry_after_seconds': 0}), \
             patch.object(w, '_run_provider', side_effect=w.SearchProviderUnavailable('Quota exhausted', 300)):
            with self.assertRaises(w.SearchUnavailable):
                w.search_web('"Jane Smith" Example University', 10)

    def test_cached_unrelated_results_are_not_profiles(self):
        audit = self.audit()
        with patch.object(w, '_read_cache', return_value=[{'title': 'Weather', 'href': 'https://example.org/'}]):
            self.assertEqual(w.search_web('"Jane Smith" university'), [])
        self.assertEqual(audit['retrieval_events'][0]['provider'], 'cache')

    def test_fragments_share_fetch_but_query_identity_is_preserved(self):
        self.audit()
        with patch.object(v.requests, 'get', return_value=self.response('<title>Jane</title><p>Profile</p>')) as get:
            v._fetch_official_page('https://example.edu/profile?id=12#research')
            v._fetch_official_page('https://example.edu/profile?id=12#papers')
        self.assertEqual(get.call_count, 1)
        self.assertNotEqual(canonical_source_url('https://example.edu/profile?id=12'),
                            canonical_source_url('https://example.edu/profile?id=13'))

    def test_raw_script_challenge_is_blocked_not_js_profile(self):
        audit = self.audit()
        url = 'https://example.edu/person'
        with patch.object(v.requests, 'get', return_value=self.response('<script src="/_Incapsula_Resource?a=123"></script>')):
            text, _ = v._fetch_official_page(url)
        self.assertEqual(text, '')
        self.assertEqual(audit['_documents'][url]['failure_code'], 'SOURCE_BLOCKED')
        self.assertGreater(audit['_documents'][url]['response_bytes'], 0)

    def test_js_shell_has_specific_diagnostic(self):
        audit = self.audit()
        url = 'https://example.edu/person'
        with patch.object(v.requests, 'get', return_value=self.response('<title>Directory</title><div id="root"></div><script src="app.js"></script>')):
            v._fetch_official_page(url)
        self.assertEqual(audit['_documents'][url]['failure_code'], 'NO_READABLE_CONTENT')

    def test_nested_team_heading_nickname_and_pronouns(self):
        self.audit('Boran Ma')
        html = ('<title>Team</title><div><h4>Boran Ma, she/her</h4></div><div><p>Assistant professor at SPSE</p>'
                '<p>Dr. Boran “Bo” Ma is an Assistant Professor at the University of Southern Mississippi.</p>'
                '<p>Previously Bo was a postdoctoral associate at Duke University.</p></div>'
                '<h3>Graduate Students</h3><h4>Other Person</h4><p>PhD candidate</p>')
        with patch.object(v.requests, 'get', return_value=self.response(html)), \
             patch.object(v, '_paper_identity_link', return_value=True):
            result = v.inspect_researcher_profile_result(
                {'name': 'Boran Ma', 'institution_name': 'University of Southern Mississippi'},
                {'href': 'https://maresearchlab.com/team', 'title': 'Boran Ma'})
        self.assertEqual(result['status'], 'VERIFIED')

    def test_student_linked_from_official_directory_needs_no_exact_paper(self):
        self.audit('Tianyi Zhang')
        html = '<title>Tianyi Zhang</title><h1>Tianyi Zhang</h1><p>Final-year Ph.D. student @ Stanford AI</p><h2>Publications</h2>'
        with patch.object(v.requests, 'get', return_value=self.response(html)):
            result = v.inspect_researcher_profile_result(
                {'name': 'Tianyi Zhang', 'institution_name': 'Stanford University'},
                {'href': 'https://tiiiger.github.io/', 'title': 'Tianyi Zhang',
                 '_official_link_institution': 'Stanford University'})
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['method'], 'official_directory_profile_link')

    def test_individual_profile_role_subheading_is_not_lost(self):
        self.audit('Jinglun Feng')
        html = ('<title>Jinglun Feng | Personal site</title><h2>Jinglun Feng</h2>'
                '<h3>Ph.D. Candidate</h3><h3>City College of New York</h3>'
                '<p>Jinglun Feng is an EE Ph.D. student at City College of New York.</p>')
        with patch.object(v.requests, 'get', return_value=self.response(html)), \
             patch.object(v, '_paper_identity_link', return_value=True):
            result = v.inspect_researcher_profile_result(
                {'name': 'Jinglun Feng', 'institution_name': 'City College of New York'},
                {'href': 'https://jing-lun.github.io/authors/admin/', 'title': 'Jinglun Feng'})
        self.assertEqual(result['status'], 'NOT_FACULTY')

    def test_lab_old_affiliation_checks_current_official_profile_once(self):
        candidate = {'name': 'Jane Smith', 'institution_name': 'Example University'}
        with patch.object(v, 'search_web', side_effect=[
                [{'href': 'https://janelab.com/team', 'title': 'Jane Smith Research Lab'}],
                [{'href': 'https://other.edu/faculty/jane', 'title': 'Jane Smith'}]]) as search, \
             patch.object(v, 'inspect_researcher_profile_result', return_value={
                'status': 'VERIFIED', 'method': 'researcher_profile_publication_link'}), \
             patch.object(v, 'inspect_faculty_result', return_value={
                'status': 'CONFLICT', 'method': 'institution_mismatch_review'}):
            result = v.verify_faculty_candidate(candidate)
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[1].args[0], '"Jane Smith" faculty profile')

    def test_lab_only_does_not_claim_current_university(self):
        with patch.object(v, 'search_web', return_value=[{
                'href': 'https://janelab.com/team', 'title': 'Jane Smith Research Lab'}]), \
             patch.object(v, 'inspect_researcher_profile_result', return_value={
                'status': 'VERIFIED', 'method': 'researcher_profile_publication_link'}):
            result = v.verify_faculty_candidate({'name': 'Jane Smith', 'institution_name': 'Example University'})
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertEqual(result['failure_code'], 'CURRENT_AFFILIATION_UNCONFIRMED')

    def test_personal_student_without_link_or_paper_remains_unresolved(self):
        self.audit('Tianyi Zhang')
        html = '<title>Tianyi Zhang</title><h1>Tianyi Zhang</h1><p>PhD student at Stanford University</p>'
        with patch.object(v.requests, 'get', return_value=self.response(html)):
            result = v.inspect_researcher_profile_result(
                {'name': 'Tianyi Zhang', 'institution_name': 'Stanford University'},
                {'href': 'https://tiiiger.github.io/', 'title': 'Tianyi Zhang'})
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_domain_hints_do_not_accept_news_lookalike(self):
        self.assertTrue(v._domain_matches_institution('https://wagner.nyu.edu/faculty/jane', 'New York University'))
        self.assertFalse(v._domain_matches_institution('https://nyunews.com/person', 'New York University'))

    def test_compound_names_are_preserved_in_queries(self):
        with patch.object(v, 'search_web', return_value=[]) as search:
            v.verify_faculty_candidate({'name': 'Tohid Kargar Tasooji', 'institution_name': 'University of Georgia', 'institution_domain': 'uga.edu'})
        self.assertTrue(all('"Tohid Kargar Tasooji"' in call.args[0] for call in search.call_args_list))

    def test_regional_scholar_spaced_email_domain(self):
        found = v._verified_email_domain_hints('Jane Smith', [{'href': 'https://scholar.google.co.cr/citations?user=test',
            'title': 'Jane Smith', 'body': 'Verified email at uga . edu'}])
        self.assertEqual(found, ['uga.edu'])

    def test_split_surname_is_only_a_scholar_domain_clue(self):
        found = v._verified_email_domain_hints('Tohid Kargar Tasooji', [{
            'href': 'https://scholar.google.com/citations?user=test',
            'title': 'Tohid Kargar Ta Sooji', 'body': 'Verified email at uga . edu'}])
        self.assertEqual(found, ['uga.edu'])

    def test_home_anchor_does_not_hide_same_url_publications_anchor(self):
        audit = self.audit()
        url = 'https://jane.github.io/authors/admin/'
        audit['_documents'] = {url: {'links': [
            {'href': 'https://jane.github.io/', 'label': 'Home'},
            {'href': 'https://jane.github.io/#publications', 'label': 'Publications'}]}}
        with patch.object(v, '_fetch_official_page', return_value=('Supporting publication', 'Jane')) as get:
            text = v._fetch_related_publication_text(url)
        self.assertEqual(text, 'Supporting publication')
        get.assert_called_once_with('https://jane.github.io/')

    def test_spaced_cv_url_is_a_locator(self):
        self.assertEqual(text_url_leads('Website: wp . nyu . edu / egan'), ['https://wp.nyu.edu/egan'])

    def test_article_pdf_is_not_cv_and_mirrors_are_not_personal(self):
        for url in ['https://publisher.org/article.pdf', 'https://gohighhorse.com/about/boran', 'https://grokipedia.com/page/Jane']:
            self.assertIsNone(source_kind(url, 'Jane Smith research lab', name_matches=True, profile_title=True))
        self.assertEqual(source_kind('https://example.edu/files/jane-cv.pdf', 'Jane Smith', name_matches=True), 'cv')

    def test_cv_download_extracts_links_without_deciding_role(self):
        audit = self.audit()
        response = self.response('%PDF-test')
        response.headers['content-type'] = 'application/pdf'
        page = MagicMock()
        page.extract_text.return_value = 'Jane Smith. Website https://jane.example.com/about'
        page.get.return_value = []
        with patch.object(v.requests, 'get', return_value=response), patch.object(v, 'PdfReader') as reader:
            reader.return_value.pages = [page]
            v._fetch_official_page('https://example.edu/jane-cv.pdf')
        doc = audit['_documents']['https://example.edu/jane-cv.pdf']
        self.assertEqual(doc['links'][0]['href'], 'https://jane.example.com/about')
        self.assertNotIn('status', doc)


if __name__ == '__main__':
    unittest.main()
