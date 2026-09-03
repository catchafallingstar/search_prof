import os
import unittest
from unittest.mock import patch
from ingestion import verify_faculty as v
from ingestion import verification_audit as audit
from ingestion.websearch import SearchUnavailable


class BoundedIdentityTests(unittest.TestCase):
    def run_candidate(self, results):
        with patch.dict(os.environ, {'FACULTY_IDENTITY_PASS_QUERIES':'2'}), \
             patch.object(v, 'fetch_orcid_clues', return_value=None), \
             patch.object(v, 'assess_identity_with_gemini', return_value=None), \
             patch.object(v, 'inspect_faculty_result', return_value={'status':'UNVERIFIED','reason':'No official profile'}), \
             patch.object(v, 'inspect_researcher_profile_result', return_value={'status':'UNVERIFIED'}), \
             patch.object(v, 'search_web', return_value=results) as search:
            decision=v.verify_faculty_candidate({'name':'Nadia Ahbab','institution_name':'Old Dominion University'})
        return decision,search

    def test_snippet_is_saved_but_does_not_become_student_decision(self):
        decision,search=self.run_candidate([{'href':'https://www.linkedin.com/in/nadia', 'title':'Nadia Ahbab', 'body':'Nadia Ahbab PhD Candidate at Old Dominion University'}])
        self.assertEqual(decision['status'],'NOT_FACULTY')
        self.assertEqual(decision['method'], 'completed_three_query_no_faculty_profile')
        self.assertLessEqual(search.call_count,3)
        self.assertIn('Snippet suggests',decision['search_audit']['results'][0]['snippet_hint'])
        self.assertEqual(decision['search_audit']['outcome'],'NOT_FACULTY')

    def test_twenty_links_are_one_query_not_twenty_queries(self):
        decision,search=self.run_candidate([{'href':f'https://example.edu/people/{i}', 'title':'Nadia Ahbab', 'body':'Nadia Ahbab'} for i in range(20)])
        self.assertEqual(search.call_count,1)
        self.assertEqual(len(decision['search_audit']['results']),20)
        self.assertEqual(search.call_args.kwargs['max_results'],20)

    def test_no_results_completes_unverified_pass(self):
        decision,search=self.run_candidate([])
        self.assertEqual(decision['status'],'NOT_FACULTY')
        self.assertEqual(decision['method'], 'completed_three_query_no_faculty_profile')
        self.assertLessEqual(search.call_count,3)

    def test_outage_preserves_audit_without_identity_verdict(self):
        with patch.object(v,'fetch_orcid_clues',return_value=None), patch.object(v,'assess_identity_with_gemini',return_value=None), patch.object(v,'search_web',side_effect=SearchUnavailable('unavailable',60)):
            with self.assertRaises(SearchUnavailable) as caught:
                v.verify_faculty_candidate({'name':'Jane Smith','institution_name':'Example University'})
        self.assertEqual(caught.exception.search_audit['outcome'],'SOURCE_WAIT')
        self.assertIsNone(audit.CURRENT.get())

    def test_middle_initial_and_ascii_hyphen(self):
        self.assertTrue(v._identity_context('Molly J. Crockett','Molly J Crockett Associate Professor'))
        self.assertTrue(v._profile_title_matches('Jane Smith','Jane Smith - Example University'))

    def test_named_family_professorship_and_advisor_safety(self):
        self.assertIsNotNone(v._attributed_role('Jane Smith','Jane Smith Morton and Claire Goulder and Family Professor',v.FACULTY_TITLE_PATTERN))
        self.assertIsNone(v._attributed_role('Jane Smith','Jane Smith PhD candidate supervised by Professor John Doe',v.FACULTY_TITLE_PATTERN))

    def test_endowed_title_and_abbreviated_given_name_are_preserved(self):
        text = ('L. Burak Kara George Tallman Ladd and Florence Barrett Ladd '
                'Professor, Mechanical Engineering')
        context = v._identity_context('Levent Burak Kara', text)
        role = v._attributed_role('Levent Burak Kara', context, v.FACULTY_TITLE_PATTERN)
        self.assertIsNotNone(role)
        self.assertEqual(
            v._detailed_faculty_title('Levent Burak Kara', context, role),
            'George Tallman Ladd and Florence Barrett Ladd Professor',
        )

    def test_google_sites_is_not_blanket_blocked(self):
        with patch.object(v,'_fetch_official_page',return_value=('', '')) as fetch:
            v.inspect_researcher_profile_result({'name':'Jane Smith'}, {'href':'https://sites.google.com/view/janesmith','title':'Jane Smith'})
        fetch.assert_called_once()

    def test_news_cannot_be_a_faculty_profile(self):
        with patch.object(v,'_fetch_official_page',return_value=('Jane Smith Assistant Professor at Example University','Jane Smith')), patch.object(v,'_institution_for_domain',return_value='Example University'):
            decision=v.inspect_faculty_result({'name':'Jane Smith','institution_name':'Example University'}, {'href':'https://example.edu/news/jane','title':'Jane Smith'})
        self.assertEqual(decision['status'],'UNVERIFIED')
        self.assertIn('News/event',decision['reason'])

    def test_staff_links_allow_linkedin_without_allowing_unsafe_schemes(self):
        self.assertTrue(audit.safe_source_link('https://www.linkedin.com/in/nadia'))
        self.assertFalse(audit.safe_source_link('javascript:alert(1)'))
        self.assertFalse(audit.safe_source_link('http://127.0.0.1/admin'))
        self.assertFalse(audit.safe_source_link('https://user:secret@example.com/'))

    def test_candidate_audits_do_not_leak_into_each_other(self):
        first,_=self.run_candidate([{'href':'https://example.edu/one','title':'Nadia Ahbab','body':'Nadia Ahbab'}])
        second,_=self.run_candidate([])
        self.assertEqual(len(first['search_audit']['results']),1)
        self.assertEqual(second['search_audit']['results'],[])
        self.assertIsNone(audit.CURRENT.get())
