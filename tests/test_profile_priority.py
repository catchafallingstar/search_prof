"""Offline regressions: profiles first without promoting snippets or guest pages."""
import os
import unittest
from unittest.mock import patch
from ingestion import verify_faculty as v


class ProfilePriorityTests(unittest.TestCase):
    def inspect(self, name, university, url, text, title=None, observed=None):
        with patch.object(v, '_fetch_official_page', return_value=(text, title or name)), \
             patch.object(v, '_institution_for_domain', return_value=observed or university):
            return v.inspect_faculty_result(
                {'name': name, 'institution_name': university},
                {'title': name, 'href': url})

    def test_merz_faculty_membership_without_rank_inference(self):
        result = self.inspect('Kenneth M. Merz', 'Michigan State University',
            'https://www.chemistry.msu.edu/faculty-research/faculty-members/merz-kenneth.aspx',
            'Kenneth Merz Research Computational Approaches to Biomolecular Systems '
            'Area(s) of Interest Selected Publications Random Forest Refinement', 'Kenneth Merz')
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['title'], 'Member of the faculty')
        self.assertEqual(result['method'], 'official_faculty_membership')

    def test_generic_profile_url_does_not_prove_faculty(self):
        result = self.inspect('Jane Smith', 'Example University',
            'https://example.edu/people/jane',
            'Jane Smith Research Selected Publications')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_faculty_members_path_does_not_override_student(self):
        result = self.inspect('Jane Smith', 'Example University',
            'https://example.edu/faculty-members/jane',
            'Jane Smith PhD candidate Research Selected Publications')
        self.assertEqual(result['status'], 'NOT_FACULTY')

    def test_faculty_directory_list_not_individual(self):
        result = self.inspect('Jane Smith', 'Example University',
            'https://example.edu/faculty-members/all',
            'Jane Smith Research Selected Publications', 'Faculty Directory')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_boran_asu_profile_matches_asu_candidate(self):
        result = self.inspect('Boran Ma', 'Arizona State University',
            'https://search.asu.edu/profile/5770865',
            'Boran Ma Assistant Professor, Chemical Engineering', 'Boran Ma | ASU Search')
        self.assertEqual(result['status'], 'VERIFIED')

    def test_boran_old_university_is_conflict_not_missing_evidence(self):
        result = self.inspect('Boran Ma', 'University of Southern Mississippi',
            'https://search.asu.edu/profile/5770865',
            'Boran Ma Assistant Professor, Chemical Engineering', 'Boran Ma | ASU Search',
            'Arizona State University')
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['method'], 'institution_mismatch_review')

    def test_mixed_asu_label_is_not_automatically_faculty(self):
        result = self.inspect('Boran Ma', 'Arizona State University',
            'https://search.asu.edu/profile/5770865',
            'Boran Ma Visiting Scholar/Faculty/Researcher', 'Boran Ma | ASU Search')
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertIn('combined', result['reason'])

    def test_news_subdomain_is_not_a_profile(self):
        result = self.inspect('Boran Ma', 'Arizona State University',
            'https://news.engineering.asu.edu/welcome/boran-ma/',
            'Boran Ma Assistant Professor', 'Boran Ma')
        self.assertEqual(result['status'], 'UNVERIFIED')
        self.assertIn('News/event', result['reason'])

    def test_jr_suffix_in_profile_title(self):
        self.assertTrue(v._profile_title_matches('Kenneth M. Merz', 'Kenneth M. Merz, Jr.'))

    def run_search(self, results, pages=3):
        candidate = {'name': 'Jane Smith', 'institution_name': 'Example University',
                     'institution_domain': 'example.edu'}
        with patch.dict(os.environ, {'FACULTY_IDENTITY_PASS_PAGES': str(pages),
                                    'FACULTY_IDENTITY_PASS_QUERIES': '2'}), \
             patch.object(v, 'fetch_orcid_clues', return_value=None), \
             patch.object(v, 'search_web', side_effect=results) as search, \
             patch.object(v, 'inspect_faculty_result', side_effect=lambda c, r:
                {'status': 'VERIFIED'} if '/faculty/jane' in r['href'] else
                {'status': 'UNVERIFIED', 'reason': 'No role'}) as inspect, \
             patch.object(v, 'inspect_researcher_profile_result') as personal:
            decision = v.verify_faculty_candidate(candidate)
        return decision, inspect, personal, search

    def test_profiles_rank_before_news_and_linkedin(self):
        links = [{'title': 'Jane Smith', 'href': f'https://example.edu/news/{i}'} for i in range(9)]
        links.append({'title': 'Jane Smith', 'href': 'https://example.edu/faculty/jane'})
        decision, inspect, _, _ = self.run_search([links])
        self.assertEqual(decision['status'], 'VERIFIED')
        self.assertEqual(inspect.call_count, 1)

    def test_snippet_only_links_do_not_consume_reading_budget(self):
        links = [{'title': 'Jane Smith', 'href': f'https://linkedin.com/in/{i}',
                  'body': 'Jane Smith PhD candidate'} for i in range(9)]
        official = {'title': 'Jane Smith', 'href': 'https://example.edu/faculty/jane'}
        decision, inspect, personal, search = self.run_search([links, [official]])
        self.assertEqual(decision['status'], 'VERIFIED')
        self.assertEqual(search.call_count, 2)
        self.assertEqual(inspect.call_count, 1)
        personal.assert_not_called()
        self.assertIn('Snippet suggests', decision['search_audit']['results'][0]['snippet_hint'])

    def test_second_query_has_reserved_page_budget(self):
        links = [{'title': 'Jane Smith', 'href': f'https://example.edu/people/{i}'} for i in range(10)]
        official = {'title': 'Jane Smith', 'href': 'https://example.edu/faculty/jane'}
        decision, inspect, _, _ = self.run_search([links, [official]], pages=10)
        self.assertEqual(decision['status'], 'VERIFIED')
        self.assertEqual(inspect.call_count, 6)


if __name__ == '__main__':
    unittest.main()
