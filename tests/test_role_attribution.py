import unittest
from unittest.mock import patch

from ingestion.verify_faculty import inspect_faculty_result, inspect_researcher_profile_result, verify_faculty_candidate


class RoleAttributionTests(unittest.TestCase):
    def official(self, text, name='Jane Smith', university='Example University', page_university='Example University', url='https://example.edu/people/jane'):
        with patch('ingestion.verify_faculty._institution_for_domain', return_value=page_university), \
             patch('ingestion.verify_faculty._fetch_official_page', return_value=(text, name)):
            return inspect_faculty_result({'name': name, 'institution_name': university},
                {'title': name, 'body': text, 'href': url})

    def test_saiteja_student_does_not_borrow_mentors_title(self):
        result = self.official('Saiteja Malisetty is a dedicated Ph.D. candidate in Computing and Information Science under the mentorship of Professor Hesham H Ali.', name='Saiteja Malisetty')
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertIn('Candidate', result['title'])

    def test_student_path_can_supply_explicit_nonfaculty_evidence(self):
        result = self.official('Jane Smith PhD student advised by Professor John Doe.', url='https://example.edu/graduate-students/jane')
        self.assertEqual(result['status'], 'NOT_FACULTY')

    def test_postdoc_does_not_borrow_supervisors_role(self):
        self.assertEqual(self.official('Jane Smith postdoctoral fellow working with Professor John Doe.')['status'], 'NOT_FACULTY')

    def test_faculty_mentoring_students_is_not_a_student(self):
        self.assertEqual(self.official('Jane Smith Associate Professor. She supervises doctoral students and graduate researchers.')['status'], 'VERIFIED')

    def test_old_student_role_does_not_override_current_professor(self):
        self.assertEqual(self.official('Jane Smith Assistant Professor. Jane Smith was a PhD student at Example University.')['status'], 'VERIFIED')

    def test_role_belonging_to_another_person_is_not_faculty(self):
        result = self.official('Jane Smith works with Professor John Doe.')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_missing_role_is_not_a_student(self):
        self.assertEqual(self.official('Jane Smith studies robotics at Example University.')['status'], 'UNVERIFIED')

    def test_different_university_requires_conflict_for_either_role(self):
        for role in ('Assistant Professor', 'PhD student'):
            self.assertEqual(self.official(f'Jane Smith {role} at Other University.',
                page_university='Other University')['status'], 'CONFLICT')

    def test_conflicting_current_roles_do_not_become_verified(self):
        self.assertEqual(self.official('Jane Smith Assistant Professor. Jane Smith PhD student.')['status'], 'UNVERIFIED')

    def test_personal_student_with_paper_does_not_borrow_professor_title(self):
        text = 'Jane Smith PhD student at Example University, supervised by Professor John Doe. Distinctive Robotics Research Paper'
        with patch('ingestion.verify_faculty._fetch_official_page', return_value=(text, 'Jane Smith')), \
             patch('ingestion.verify_faculty._fetch_related_publication_text', return_value=''):
            result = inspect_researcher_profile_result({'name': 'Jane Smith', 'institution_name': 'Example University',
                'recent_papers': [{'title': 'Distinctive Robotics Research Paper'}]},
                {'title': 'Jane Smith', 'href': 'https://janesmith.net', 'body': 'Jane Smith'})
        self.assertEqual(result['status'], 'NOT_FACULTY')

    @patch('ingestion.verify_faculty.fetch_orcid_clues', return_value=None)
    @patch('ingestion.verify_faculty.inspect_faculty_result', return_value={
        'status': 'NOT_FACULTY', 'source_url': 'https://example.edu/people/jane', 'title': 'PhD Student'})
    @patch('ingestion.verify_faculty.search_web', return_value=[
        {'title': 'Jane Smith', 'body': 'Jane Smith PhD student', 'href': 'https://example.edu/people/jane'}])
    def test_definitive_student_decision_stops_before_buying_another_query(self, search, *_):
        result = verify_faculty_candidate({'name': 'Jane Smith', 'institution_name': 'Example University'})
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(search.call_count, 1)


if __name__ == '__main__':
    unittest.main()
