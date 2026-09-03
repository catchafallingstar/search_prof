"""Offline regressions from the 30-person report.

HTML below is a small representative fixture, not a fresh fetch or complete
saved website. Tests must not spend search credits or change identity records.
"""
import os
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import requests

from ingestion import verify_faculty as v, verification_audit as a, websearch as w
from ingestion.identity_sources import source_kind, text_url_leads, excluded_profile_source
from ingestion.institution_domains import academic_domain_hint, record_for_name, offshore_appointment


class VerificationRepairsV2Tests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            'FACULTY_IDENTITY_VERBOSE_LOG': 'false', 'FACULTY_IDENTITY_PASS_QUERIES': '10',
            'FACULTY_VERIFY_OPENALEX_SUPPORT_ENABLED': 'false'}))
        self.stack.enter_context(patch.object(v, 'is_public_http_url', return_value=True))
        self.stack.enter_context(patch.object(v, 'fetch_orcid_clues', return_value=None))
        for helper in ('enrich_candidate_metadata_affiliations', 'enrich_candidate_paper_affiliations'):
            self.stack.enter_context(patch.object(v, helper, side_effect=lambda c, **kw: c))
        self.stack.enter_context(patch.object(v, 'get_db_connection', side_effect=AssertionError('No DB in unit test')))

    def audit(self, name='Jane Smith'):
        audit = a.new_audit({'name': name})
        token = a.CURRENT.set(audit)
        self.addCleanup(a.CURRENT.reset, token)
        return audit

    @staticmethod
    def response(html, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = html.encode('utf-8')
        response._content_consumed = True
        response.headers['content-type'] = 'text/html'
        response.encoding = 'ISO-8859-1'  # requests' problematic default
        return response

    def official(self, name, institution, url, html):
        self.audit(name)
        with patch.object(v.requests, 'get', return_value=self.response(html)):
            return v.inspect_faculty_result({'name': name, 'institution_name': institution},
                {'href': url, 'title': name})

    def test_rene_html_decoding_then_role_verification(self):
        result = self.official('René Burress', 'University of Central Missouri',
            'https://www.ucmo.edu/faculty/rene',
            '<meta charset="utf-8"><title>René Burress</title><h1>René Burress</h1>'
            '<p>Professor of Library Science, University of Central Missouri.</p>')
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertNotIn('Ã', result['evidence_text'])

    def test_utf8_without_meta_is_not_latin1(self):
        self.audit('René Burress')
        with patch.object(v.requests, 'get', return_value=self.response('<title>René Burress</title><p>Professor</p>')):
            text, _ = v._fetch_official_page('https://ucmo.edu/profile')
        self.assertIn('René', text)

    def test_flores_degree_suffix_and_jing_cao_role_suffix(self):
        self.assertTrue(v._profile_title_matches('Andrew R. Flores', 'Andrew R. Flores, Ph.D. | Home'))
        self.assertTrue(v._profile_title_matches('Jing Cao', 'Jing Cao, Professor of Statistics'))

    def test_another_longer_full_name_is_not_matching_profile(self):
        self.assertFalse(v._profile_title_matches('Mohammed W. Abdulrahman',
                         'Abdallah Abdulfattah Mohammed Abdulrahman'))
        self.assertFalse(v._profile_title_matches('Jin Yang', 'Jin Wei Yang Zhang'))

    def test_faculty_path_cannot_override_different_full_profile_name(self):
        result = self.official('Mohammed W. Abdulrahman', 'Rochester Institute of Technology',
            'https://rit.edu/faculty/another-person',
            '<title>Abdallah Abdulfattah Mohammed Abdulrahman</title>'
            '<h1>Abdallah Abdulfattah Mohammed Abdulrahman</h1><p>Associate Professor</p>')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_math_unicode_profile_heading(self):
        self.assertTrue(v._profile_title_matches('Humphrey Shi', '𝙷𝚞𝚖𝚙𝚑𝚛𝚎𝚢 𝚂𝚑𝚒'))

    def test_humphrey_shared_first_person_subject(self):
        text = 'Humphrey Shi I am Vice President of High-Performance AI at NVIDIA, a professor at Georgia Tech, and an engineer-researcher.'
        role = v._attributed_role('Humphrey Shi', text, v.FACULTY_TITLE_PATTERN)
        self.assertIsNotNone(role)
        self.assertIn('professor', role.group().lower())

    def test_shared_clause_does_not_borrow_an_advisors_role(self):
        for text in ('Jane Smith I am a student working with my advisor, a professor at Georgia Tech.',
                     'Jane Smith I was a researcher, a professor at Georgia Tech.',
                     'Jane Smith I am a student. John Doe is a professor at Georgia Tech.'):
            with self.subTest(text=text):
                self.assertIsNone(v._attributed_role('Jane Smith', text, v.FACULTY_TITLE_PATTERN))

    def test_humphrey_alias_attached_to_faculty_not_company(self):
        self.audit('Humphrey Shi')
        text = ('Humphrey Shi I am Vice President of High-Performance AI at NVIDIA, '
                'a professor at Georgia Tech, and an engineer-researcher. Distinctive Computer Vision Research Paper')
        with patch.object(v, '_fetch_official_page', return_value=(text, 'Humphrey Shi')):
            result = v.inspect_researcher_profile_result(
                {'name': 'Humphrey Shi', 'institution_name': 'Georgia Institute of Technology',
                 'recent_papers': [{'title': 'Distinctive Computer Vision Research Paper'}]},
                {'href': 'https://www.humphreyshi.com/', 'title': 'Humphrey Shi'})
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['institution_name'], 'Georgia Institute of Technology')

    def test_ordinary_captcha_widget_is_not_blocked(self):
        audit = self.audit('Boran Ma')
        url = 'https://maresearchlab.com/team'
        html = '<title>Boran Ma</title><h1>Boran Ma</h1><p>Assistant Professor at University of Southern Mississippi. Publications and research.</p><script src="https://google.com/recaptcha/api.js"></script>'
        with patch.object(v.requests, 'get', return_value=self.response(html)):
            text, _ = v._fetch_official_page(url)
        self.assertIn('Assistant Professor', text)
        self.assertNotEqual(audit['_documents'][url].get('failure_code'), 'SOURCE_BLOCKED')

    def test_real_challenge_remains_blocked(self):
        audit = self.audit()
        with patch.object(v.requests, 'get', return_value=self.response('<title>Just a moment</title><p>Verify you are human</p>')):
            self.assertEqual(v._fetch_official_page('https://msu.edu/person')[0], '')
        self.assertEqual(audit['_documents']['https://msu.edu/person']['failure_code'], 'SOURCE_BLOCKED')

    def test_soft_404_explains_removed_profile(self):
        result = self.official('Boran Ma', 'University of Southern Mississippi',
            'https://usm.edu/profile?id=2417501', '<title>Faculty Profile</title><p>No Faculty Found. No faculty member found with ID provided.</p>')
        self.assertEqual(result['failure_code'], 'PROFILE_REMOVED')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_js_shell_not_a_captcha(self):
        audit = self.audit()
        with patch.object(v.requests, 'get', return_value=self.response('<title>Faculty Directory</title><div id="root"></div><script src="app.js"></script>')):
            v._fetch_official_page('https://msu.edu/scholar')
        self.assertEqual(audit['_documents']['https://msu.edu/scholar']['failure_code'], 'NO_READABLE_CONTENT')

    def test_pdf_urls_never_join_lines(self):
        urls = text_url_leads('american.edu/spa/faculty/\n /globe\nscholar.google.com/\nANDREW')
        self.assertNotIn('https://american.edu/spa/faculty//globe', urls)
        self.assertNotIn('https://scholar.google.com/ANDREW', urls)
        self.assertEqual(text_url_leads('Website: wp . nyu . edu / egan'), ['https://wp.nyu.edu/egan'])

    def test_embedded_pdf_links_precede_extracted_text(self):
        audit = self.audit()
        response = self.response('%PDF-test')
        response.headers['content-type'] = 'application/pdf'
        page, annotation = MagicMock(), MagicMock()
        page.extract_text.return_value = 'Jane Smith. https://wrong.example.com/truncated'
        annotation.get_object.return_value = {'/A': {'/URI': 'https://jane.github.io/'}}
        page.get.return_value = [annotation]
        with patch.object(v.requests, 'get', return_value=response), patch.object(v, 'PdfReader') as reader:
            reader.return_value.pages = [page]
            v._fetch_official_page('https://example.edu/jane-cv.pdf')
        self.assertEqual(audit['_documents']['https://example.edu/jane-cv.pdf']['links'][0]['href'], 'https://jane.github.io/')

    def test_aggregators_never_become_personal_even_on_followed_link(self):
        for host in ('opengovpay.com', 'dblp.uni-trier.de', 'gradnova.com', 'capneteq.com',
                     'sciprofiles.com', 'myprofreviews.com', 'x.com'):
            with self.subTest(host=host):
                self.assertIsNone(source_kind('https://' + host + '/profile/jane',
                    'Jane Smith professor personal lab', name_matches=True, profile_title=True))

    def test_repository_is_not_faculty_profile(self):
        self.assertIsNone(source_kind('https://aquila.usm.edu/fac_pubs/21841',
            'Nyzaireyus Harrison', name_matches=True, official=True))

    def test_no_crawl_into_aggregator_about_page(self):
        with patch.object(v, 'search_web', return_value=[{'href': 'https://capneteq.com/profile/jin-yang',
                'title': 'Jin Yang', 'body': 'Researcher profile'}]), \
             patch.object(v, '_fetch_official_page', side_effect=AssertionError('Must not fetch aggregator')):
            result = v.verify_faculty_candidate({'name': 'Jin Yang', 'institution_name': 'Massachusetts Institute of Technology'})
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['method'], 'completed_three_query_no_faculty_profile')

    def test_institution_mapping_does_not_confuse_missouri(self):
        self.assertTrue(v._domain_matches_institution('https://ucmo.edu/faculty', 'University of Central Missouri'))
        self.assertFalse(v._domain_matches_institution('https://missouri.edu/faculty', 'University of Central Missouri'))
        self.assertEqual(v._institution_similarity('University of Missouri', 'University of Central Missouri'), 0)

    def test_gmail_is_never_an_academic_locator(self):
        for host in ('gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'mit.edu.evil.com'):
            self.assertEqual(academic_domain_hint(host), '')
        self.assertEqual(academic_domain_hint('gatech.edu'), 'gatech.edu')

    def test_missing_domain_uses_registry_then_stops_on_profile(self):
        expected = '"Zengyi Huang" site:gwu.edu'
        def search(query, **kwargs):
            return [{'href': 'https://gwu.edu/faculty/zengyi', 'title': 'Zengyi Huang'}] if query == expected else []
        with patch.object(v, 'search_web', side_effect=search) as get, \
             patch.object(v, 'inspect_faculty_result', return_value={'status': 'VERIFIED'}):
            result = v.verify_faculty_candidate({'name': 'Zengyi Huang', 'institution_name': 'The George Washington University'})
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(get.call_count, 2)

    def test_new_cv_email_domain_creates_next_targeted_query(self):
        def fetch(url):
            audit = a.CURRENT.get()
            text = 'Jane Smith jane@campus.edu'
            audit.setdefault('_documents', {})[url] = {'text': text, 'links': [], 'finished': True}
            return text, 'CV document'
        def search(query, **kw):
            if query == '"Jane Smith" "Example University"':
                return [{'href': 'https://example.com/jane-cv.pdf', 'title': 'Jane Smith CV'}]
            return []
        with patch.object(v, 'search_web', side_effect=search) as get, patch.object(v, '_fetch_official_page', side_effect=fetch):
            v.verify_faculty_candidate({'name': 'Jane Smith', 'institution_name': 'Example University'})
        queries = [call.args[0] for call in get.call_args_list]
        self.assertIn('"Jane Smith" site:campus.edu', queries)
        self.assertEqual(queries[1], '"Jane Smith" site:campus.edu')

    def test_split_name_is_retrieval_lead_not_different_spelling(self):
        self.assertTrue(w._results_match_query_anchor('"Michael Variny" "Ohio University"',
            [{'title': 'Paper authors Michael Vari Ny, Jay Wilhelm; Ohio University'}]))
        self.assertFalse(w._results_match_query_anchor('"Michael Variny" "Ohio University"',
            [{'title': 'Michael Varney Ohio University'}]))

    def test_targeted_directory_can_survive_missing_name_excerpt(self):
        self.assertTrue(w._results_match_query_anchor('"Yunjie Tian" site:buffalo.edu',
            [{'href': 'https://engineering.buffalo.edu/people/faculty', 'title': 'Faculty directory'}]))
        self.assertFalse(w._results_match_query_anchor('"Yunjie Tian" site:buffalo.edu',
            [{'href': 'https://buffalo.edu.evil.com/people/faculty', 'title': 'Faculty directory'}]))

    def test_dubai_verified_role_kept_separate_from_us_scope(self):
        result = self.official('Mohammed W. Abdulrahman', 'Rochester Institute of Technology',
            'https://www.rit.edu/dubai/directory/mwacad-mohammed-abdulrahman',
            '<title>Mohammed W. Abdulrahman</title><h1>Mohammed W. Abdulrahman</h1><p>Associate Professor of Mechanical Engineering.</p>')
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['scope_status'], 'OUT_OF_SCOPE')
        self.assertEqual(result['country_code'], 'AE')
        self.assertIn('Dubai', result['institution_name'])
        self.assertIsNone(offshore_appointment('https://rit.edu/directory/person'))

    def test_saved_scope_is_a_separate_campus_not_parent_country_mutation(self):
        result = {'status': 'VERIFIED', 'source_url': 'https://rit.edu/dubai/directory/person',
                  'institution_name': 'Rochester Institute of Technology — Dubai', 'country_code': 'AE', 'source_domain': 'rit.edu'}
        with patch.object(v, 'get_db_connection') as conn:
            cursor = conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = {'id': 2}
            v._save_result({'id': 1, 'institution_name': 'Rochester Institute of Technology', 'institution_id': 1}, result)
        inserts = [c for c in cursor.execute.call_args_list if 'INSERT INTO institutions' in c.args[0]]
        self.assertEqual(inserts[0].args[1], ('Rochester Institute of Technology — Dubai', 'AE'))

    def test_guest_speaker_not_host_university_faculty(self):
        with patch.object(v, '_institution_for_domain', return_value='Harvard University'), \
             patch.object(v, '_fetch_official_page', return_value=(
                 'Christopher Hartwell Guest Speaker Professor at Another University.', 'Christopher Hartwell')):
            result = v.inspect_faculty_result({'name': 'Christopher Hartwell', 'institution_name': 'Harvard University'},
                {'href': 'https://harvard.edu/people/christopher', 'title': 'Christopher Hartwell'})
        self.assertNotEqual(result['status'], 'VERIFIED')

    def test_news_page_cannot_verify_even_with_explicit_professor_title(self):
        with patch.object(v, '_fetch_official_page', return_value=('Boran Ma Assistant Professor at Arizona State University.', 'Boran Ma')):
            result = v.inspect_faculty_result({'name': 'Boran Ma', 'institution_name': 'Arizona State University'},
                {'href': 'https://news.asu.edu/welcome/boran-ma', 'title': 'Boran Ma'})
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_different_university_still_conflict(self):
        result = self.official('Boran Ma', 'University of Southern Mississippi',
            'https://search.asu.edu/profile/5770865',
            '<title>Boran Ma</title><h1>Boran Ma</h1><p>Assistant Professor at Arizona State University.</p>')
        self.assertEqual(result['status'], 'CONFLICT')

    def test_previously_blocked_profile_reused_before_search(self):
        candidate = {'name': 'Kenneth M. Merz', 'institution_name': 'Michigan State University',
                     'identity_search_audit': {'pages': [{'url': 'https://msu.edu/faculty/merz', 'status': 'UNVERIFIED', 'failure_code': 'SOURCE_BLOCKED'}]}}
        with patch.object(v, 'inspect_faculty_result', return_value={'status': 'VERIFIED'}), \
             patch.object(v, 'search_web', side_effect=AssertionError('Do not rediscover known URL')):
            self.assertEqual(v.verify_faculty_candidate(candidate)['status'], 'VERIFIED')

    def test_known_profile_checked_before_pdf_when_university_already_known(self):
        candidate = {'name': 'Jane Smith', 'institution_name': 'Example University',
                     'faculty_source_url': 'https://example.edu/faculty/jane',
                     'recent_papers': [{'title': 'Some paper without raw metadata'}]}
        with patch.object(v, 'inspect_faculty_result', return_value={'status': 'VERIFIED'}), \
             patch.object(v, 'enrich_candidate_paper_affiliations', side_effect=AssertionError('No unnecessary PDF download')):
            self.assertEqual(v.verify_faculty_candidate(candidate)['status'], 'VERIFIED')

    def test_different_named_individual_profile_is_not_a_general_directory(self):
        with patch.object(v, 'search_web', return_value=[{'href': 'https://smu.edu/faculty/jia-zhang', 'title': 'Jia Zhang'}]), \
             patch.object(v, '_fetch_official_page', side_effect=AssertionError('Do not fetch another named person')):
            result = v.verify_faculty_candidate({'name': 'Jing Cao', 'institution_name': 'Southern Methodist University'})
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['method'], 'completed_three_query_no_faculty_profile')

    def test_us_filter_applies_before_sql_pagination(self):
        import radar_store
        with patch.object(radar_store, 'get_db_connection') as conn, \
             patch.object(radar_store, '_target_country_code', return_value='US'), \
             patch.object(radar_store, 'fetch_radar_topic', return_value={
                 'id': 1, 'discovery_version': radar_store.RADAR_DISCOVERY_VERSION
             }):
            cursor = conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = []
            radar_store.fetch_indexed_professors('Robotics')
        query = cursor.execute.call_args.args[0]
        self.assertIn("COALESCE(p.faculty_source_url, '') !~* %s", query)
        self.assertLess(query.index("COALESCE(p.faculty_source_url, '') !~* %s"), query.rindex('LIMIT'))

    def test_old_student_page_does_not_override_completed_doctorate_homepage(self):
        child, root = 'https://person.github.io/authors/admin/', 'https://person.github.io/'
        html = {
            child: '<title>Jinglun Feng</title><h1>Jinglun Feng</h1><p>PhD candidate at City College of New York.</p><a href="/">Home</a><p>Distinctive Research Paper About Robotics</p>',
            root: '<title>Jinglun Feng</title><h1>Jinglun Feng</h1><p>I completed my PhD in 2023 at City College of New York.</p><p>Distinctive Research Paper About Robotics</p>'}
        with patch.object(v.requests, 'get', side_effect=lambda url, **kw: self.response(html[url])), \
             patch.object(v, 'search_web', return_value=[{'href': child, 'title': 'Jinglun Feng', 'body': 'Personal homepage'}]):
            result = v.verify_faculty_candidate({'name': 'Jinglun Feng', 'institution_name': 'City College of New York',
                'recent_papers': [{'title': 'Distinctive Research Paper About Robotics'}]})
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertEqual(result['failure_code'], 'CURRENT_ROLE_UNCONFIRMED')


if __name__ == '__main__':
    unittest.main()
