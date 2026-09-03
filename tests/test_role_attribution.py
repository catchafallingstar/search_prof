import unittest
from unittest.mock import patch

from ingestion.verify_faculty import (
    _current_nonfaculty_role,
    _detailed_faculty_title,
    _employer_after_role,
    _generic_current_nonfaculty_employer,
    _explicit_role_currentness,
    _identity_evidence_rows,
    _identity_matches,
    _profile_title_matches,
    _public_snippet_nonfaculty_consensus,
    _role_category,
    inspect_faculty_result,
    inspect_researcher_profile_result,
    verify_faculty_candidate,
)


class RoleAttributionTests(unittest.TestCase):
    def test_navigation_text_is_not_part_of_faculty_title(self):
        text = (
            'Dr. Jugal Kalita Links & Tools Apply Now '
            'Dr. Jugal Kalita Professor (719) 255-3432'
        )
        from ingestion.verify_faculty import _attributed_role, FACULTY_TITLE_PATTERN
        role = _attributed_role('Jugal Kalita', text, FACULTY_TITLE_PATTERN)
        self.assertEqual(_detailed_faculty_title('Jugal Kalita', text, role), 'Professor')

    def test_faculty_title_cleanup_preserves_named_titles(self):
        from ingestion.verify_faculty import _clean_faculty_title
        self.assertEqual(_clean_faculty_title('MS Assistant Professor'), 'Assistant Professor')
        self.assertEqual(_clean_faculty_title('| Adjunct Professor'), 'Adjunct Professor')
        self.assertEqual(_clean_faculty_title('Prof.'), 'Professor')
        self.assertEqual(
            _clean_faculty_title('Richard and Kathy Leventhal Professor'),
            'Richard and Kathy Leventhal Professor',
        )

    def test_seminar_page_is_never_a_primary_faculty_profile(self):
        from ingestion.verify_faculty import _faculty_source_quality, _is_news_or_event
        url = 'https://example.edu/seminars/jane-smith-materials-science'
        self.assertTrue(_is_news_or_event(url))
        self.assertEqual(_faculty_source_quality(url), 0)

    def test_contact_email_is_never_an_employer(self):
        text = (
            'Soumith Chintala I am an AI researcher, engineer and community builder. '
            'You can reach me at myfirstname@gmail.com. Currently at Thinking Machines.'
        )
        role = _current_nonfaculty_role('Soumith Chintala', text)
        self.assertEqual(_employer_after_role(text, role), 'Thinking Machines')

    def test_generic_current_company_employment_needs_no_job_dictionary(self):
        text = (
            'Jason Ansel I am currently working on PyTorch compiler infrastructure '
            'at Meta where I build production systems.'
        )
        self.assertEqual(
            _generic_current_nonfaculty_employer('Jason Ansel', text), 'Meta'
        )

    def test_partial_end_dates_use_the_precision_the_source_supplies(self):
        from ingestion.verify_faculty import _explicit_role_currentness
        self.assertEqual(_explicit_role_currentness('End date: 2026'), 'CURRENT')
        self.assertEqual(_explicit_role_currentness('End date: 2026-08'), 'HISTORICAL')
        self.assertEqual(_explicit_role_currentness('End date: 2026-08-31'), 'HISTORICAL')

    def test_appointment_date_preserves_year_month_or_day_precision(self):
        from ingestion.verify_faculty import _extract_appointment_date
        value, precision = _extract_appointment_date(
            'She joined the faculty in September 2024'
        )
        self.assertEqual((value.isoformat(), precision), ('2024-09-01', 'MONTH'))
        value, precision = _extract_appointment_date('He starts on October 15, 2026')
        self.assertEqual((value.isoformat(), precision), ('2026-10-15', 'DAY'))

    def official(self, text, name='Jane Smith', university='Example University', page_university='Example University', url='https://example.edu/people/jane'):
        with patch('ingestion.verify_faculty._institution_for_domain', return_value=page_university), \
             patch('ingestion.verify_faculty._fetch_official_page', return_value=(text, name)):
            return inspect_faculty_result({'name': name, 'institution_name': university},
                {'title': name, 'body': text, 'href': url})

    def test_saiteja_student_does_not_borrow_mentors_title(self):
        result = self.official('Saiteja Malisetty is a dedicated Ph.D. candidate in Computing and Information Science under the mentorship of Professor Hesham H Ali.', name='Saiteja Malisetty')
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertIn('Candidate', result['title'])

    def test_surname_first_profile_allows_omitted_middle_initial(self):
        self.assertTrue(
            _profile_title_matches(
                'Ashty S. Karim', 'Karim, Ashty | Faculty | Northwestern Engineering'
            )
        )

    def test_joined_and_hyphenated_compound_name_match(self):
        self.assertTrue(_identity_matches('Guo-Wei Wei', 'Guowei Wei | Department of Mathematics'))
        self.assertTrue(_profile_title_matches('Guo-Wei Wei', 'Guowei Wei | Department of Mathematics'))

    def test_common_academic_role_abbreviations_are_normalized(self):
        for role in ('Asst. Prof.', 'Assoc Prof.', 'Adj. Prof.', 'Lect.', 'Instr.'):
            with self.subTest(role=role):
                self.assertEqual(_role_category(role), 'FACULTY')

    def test_cmu_endowed_professor_profile_is_verified_with_full_title(self):
        text = (
            'Levent Burak Kara George Tallman Ladd and Florence Barrett Ladd '
            'Professor, Mechanical Engineering. L. Burak Kara is a professor '
            'in the Department of Mechanical Engineering at Carnegie Mellon University.'
        )
        result = self.official(
            text,
            name='Levent Burak Kara',
            university='Carnegie Mellon University',
            page_university='Carnegie Mellon University',
            url='https://engineering.cmu.edu/directory/bios/kara-burak.html',
        )
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(
            result['title'],
            'George Tallman Ladd and Florence Barrett Ladd Professor',
        )

    def test_lab_page_can_attach_role_through_official_university_link(self):
        candidate = {
            'name': 'Levent Burak Kara',
            'institution_name': 'Carnegie Mellon University',
        }
        text = (
            'Levent Burak Kara Professor of Mechanical Engineering. '
            'Visual Design and Engineering Lab at Carnegie Mellon University.'
        )
        document = {
            'headings': ['Levent Burak Kara'],
            'profile_sections': [text],
            'links': [{
                'href': 'https://www.cmu.edu/',
                'label': 'Carnegie Mellon University',
            }],
        }
        from ingestion import verification_audit as audit
        audit_state = audit.new_audit(candidate)
        audit_state['_documents'] = {
            'https://levburkara.github.io/team/': document,
        }
        token = audit.CURRENT.set(audit_state)
        try:
            with (
                patch('ingestion.verify_faculty._fetch_official_page', return_value=(text, 'Levent Burak Kara')),
                patch('ingestion.verify_faculty._fetch_related_publication_text', return_value=''),
            ):
                result = inspect_researcher_profile_result(
                    candidate,
                    {'title': 'Levent Burak Kara', 'body': text,
                     'href': 'https://levburkara.github.io/team/'},
                )
        finally:
            audit.CURRENT.reset(token)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['title'], 'Professor')

    def test_plural_graduate_student_group_is_attributed(self):
        result = self.official(
            'Other authors are UCLA Samueli graduate students Ziyuan Che, Chrystal Duan, and Xiao Wan.',
            name='Ziyuan Che',
            university='University of California, Los Angeles',
            page_university='University of California, Los Angeles',
            url='https://samueli.ucla.edu/research/device',
        )
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'STUDENT')

    def test_structured_linkedin_grad_headline_is_not_faculty(self):
        result = _public_snippet_nonfaculty_consensus(
            'Ziyuan Che',
            [{
                'href': 'https://www.linkedin.com/in/ziyuan-che/',
                'title': 'Ziyuan Che - Cambridge, Massachusetts | LinkedIn',
                'body': 'headline: Grad student in CCB\nEducation: University of California, Los Angeles',
            }],
            {'name': 'Ziyuan Che', 'institution_name': 'University of California, Los Angeles'},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'STUDENT')

    def test_student_path_can_supply_explicit_nonfaculty_evidence(self):
        result = self.official('Jane Smith PhD student advised by Professor John Doe.', url='https://example.edu/graduate-students/jane')
        self.assertEqual(result['status'], 'NOT_FACULTY')

    def test_postdoc_does_not_borrow_supervisors_role(self):
        self.assertEqual(self.official('Jane Smith postdoctoral fellow working with Professor John Doe.')['status'], 'NOT_FACULTY')

    def test_faculty_mentoring_students_is_not_a_student(self):
        self.assertEqual(self.official('Jane Smith Associate Professor. She supervises doctoral students and graduate researchers.')['status'], 'VERIFIED')

    def test_old_student_role_does_not_override_current_professor(self):
        self.assertEqual(self.official('Jane Smith Assistant Professor. Jane Smith was a PhD student at Example University.')['status'], 'VERIFIED')

    def test_expired_structured_linkedin_role_is_historical(self):
        self.assertEqual(
            _explicit_role_currentness('title: Postdoctoral Fellow end date: 2026-02-01 is current: false'),
            'HISTORICAL',
        )

    @patch('ingestion.verify_faculty.enrich_candidate_metadata_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.enrich_candidate_paper_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.fetch_orcid_clues', return_value=None)
    @patch('ingestion.verify_faculty.is_public_http_url', return_value=True)
    def test_new_research_institute_faculty_profile_overrides_old_postdoc_page(self, *_):
        candidate = {
            'name': 'Ritesh Kumar',
            'institution_name': 'University of Chicago',
            'recent_papers': [{'title': 'Electrolytomics', 'matched_query': 'Data science'}],
        }
        results = [
            {
                'title': 'Ritesh Kumar - ETI',
                'body': 'Ritesh Kumar Postdoctoral Fellow at University of Chicago',
                'href': 'https://energytech.pme.uchicago.edu/people/ritesh-kumar/',
            },
            {
                'title': 'Ritesh Kumar - TCG Crest',
                'body': 'Ritesh Kumar joined TCG CREST',
                'href': 'https://www.tcgcrest.org/people/ritesh-kumar/',
            },
        ]

        def fetch(url):
            if 'uchicago.edu' in url:
                return (
                    'Ritesh Kumar Postdoctoral Fellow, University of Chicago. Joined in 2022.',
                    'Ritesh Kumar - ETI',
                )
            return (
                'Ritesh Kumar is an Assistant Professor at the Research Institute for Sustainable Energy (RISE), TCG CREST. '
                'He previously joined the University of Chicago as an AI in Science Fellow.',
                'Ritesh Kumar - TCG Crest',
            )

        with patch('ingestion.verify_faculty.search_web', return_value=results) as search, \
             patch('ingestion.verify_faculty._fetch_official_page', side_effect=fetch), \
             patch('ingestion.verify_faculty._institution_for_domain', return_value='University of Chicago'):
            result = verify_faculty_candidate(candidate)

        self.assertEqual(result['status'], 'OUT_OF_SCOPE')
        self.assertEqual(result['role_category'], 'FACULTY')
        self.assertIn('Assistant Professor', result['title'])
        self.assertIn('TCG CREST', result['institution_name'])
        self.assertEqual(search.call_count, 1)

    @patch('ingestion.verify_faculty.enrich_candidate_metadata_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.enrich_candidate_paper_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.fetch_orcid_clues', return_value=None)
    @patch('ingestion.verify_faculty.is_public_http_url', return_value=True)
    def test_honored_faculty_profile_triggers_current_affiliation_query(self, *_):
        candidate = {
            'name': 'Guo-Wei Wei',
            'institution_name': 'Michigan State University',
            'institution_domain': 'msu.edu',
            'recent_papers': [{'title': 'Machine Learning in Materials Science', 'matched_query': 'Machine learning'}],
        }
        returned = [
            [{'title': 'Guowei Wei | Honored Faculty', 'body': 'Guowei Wei MSU Foundation Professor',
              'href': 'https://msu.edu/honoredfaculty/directory/wei-guowei.html'}],
            [{'title': 'Guowei Wei | Michigan State University', 'body': 'Guowei Wei MSU Foundation Professor',
              'href': 'https://users.math.msu.edu/users/weig/'}],
            [{'title': 'Guowei Wei | Department of Mathematics', 'body': 'Guowei Wei Professor and GRA Eminent Scholar',
              'href': 'https://math.franklin.uga.edu/directory/people/guowei-wei'}],
        ]

        def fetched(url):
            if 'uga.edu' in url:
                return ('Guowei Wei Professor and GRA Eminent Scholar at the University of Georgia.',
                        'Guowei Wei | Department of Mathematics')
            return ('Guowei Wei MSU Research Foundation Distinguished Professor at Michigan State University.',
                    'Guowei Wei | Honored Faculty' if 'honoredfaculty' in url else 'Guowei Wei | Michigan State University')

        with patch('ingestion.verify_faculty.search_web', side_effect=returned) as search, \
             patch('ingestion.verify_faculty._fetch_official_page', side_effect=fetched):
            result = verify_faculty_candidate(candidate)
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['institution_name'], 'University of Georgia')
        self.assertEqual(search.call_count, 3)
        self.assertEqual(search.call_args_list[2].args[0], '"Guo-Wei Wei" professor current university')

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

    def personal(self, name, text, title=None, institution='Example University'):
        with patch('ingestion.verify_faculty._fetch_official_page', return_value=(text, title or name)), \
             patch('ingestion.verify_faculty._fetch_related_publication_text', return_value=''):
            return inspect_researcher_profile_result(
                {'name': name, 'institution_name': institution},
                {'title': title or name, 'body': name, 'href': 'https://person.example/profile'},
            )

    def test_exact_name_personal_page_decides_current_industry_role(self):
        result = self.personal(
            'Yuezhan Tao',
            'Yuezhan Tao. I am a Software Engineer at Zoox. I earned my Ph.D. from the University of Pennsylvania.',
            institution='University of Pennsylvania',
        )
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'INDUSTRY')
        self.assertEqual(result['observed_employer'], 'Zoox')

    def test_exact_name_personal_page_decides_current_postdoc(self):
        result = self.personal(
            'Yuheng Qiu',
            'Yuheng Qiu. I am a Postdoctoral Scientist at Amazon FAR. I received my Ph.D. from Carnegie Mellon University.',
            institution='Carnegie Mellon University',
        )
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'POSTDOC')

    def test_exact_name_personal_page_decides_staff_research_scientist(self):
        result = self.personal(
            'Sudharshan Suresh',
            "Sudharshan Suresh. I'm a senior staff research scientist and technical lead at Boston Dynamics. I earned my Ph.D. at CMU.",
            institution='Carnegie Mellon University',
        )
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertIn(result['role_category'], {'RESEARCHER', 'INDUSTRY'})

    def test_parenthetical_alias_does_not_break_name_attribution(self):
        title = 'Rui-Feng Wang (Swee-Fong Wong) | University of Florida'
        self.assertTrue(_profile_title_matches('Rui-Feng Wang', title))
        result = self.personal(
            'Rui-Feng Wang',
            "Rui-Feng Wang (Swee-Fong Wong). I'm currently an Agricultural Robotic Researcher at University of Florida.",
            title=title,
            institution='University of Florida',
        )
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'RESEARCHER')

    def test_non_us_professor_is_out_of_scope_not_unverified(self):
        result = self.personal(
            'Xiaoyu Cui',
            'Xiaoyu Cui Professor, Northeastern University, Shenyang, China.',
            institution='Northeastern University',
        )
        self.assertEqual(result['status'], 'OUT_OF_SCOPE')
        self.assertEqual(result['role_category'], 'FACULTY')

    def test_personal_current_faculty_claim_is_confirmed_on_new_official_domain(self):
        candidate = {
            'name': 'Tuo Zhao',
            'institution_name': 'Princeton University',
            'recent_papers': [{'title': 'Soft Origami Robots', 'matched_query': 'Robotics'}],
        }

        def fetch(url):
            if 'gatech.edu' in url:
                return ('Tuo Zhao Associate Professor at Georgia Institute of Technology.', 'Tuo Zhao | People')
            return ('Tuo Zhao. I am an Associate Professor at Georgia Tech.', 'Tuo Zhao')

        with patch('ingestion.verify_faculty.enrich_candidate_metadata_affiliations', side_effect=lambda value, **_: value), \
             patch('ingestion.verify_faculty.enrich_candidate_paper_affiliations', side_effect=lambda value, **_: value), \
             patch('ingestion.verify_faculty.fetch_orcid_clues', return_value=None), \
             patch('ingestion.verify_faculty.is_public_http_url', return_value=True), \
             patch('ingestion.verify_faculty._fetch_official_page', side_effect=fetch), \
             patch('ingestion.verify_faculty._institution_for_domain', return_value='Georgia Institute of Technology'), \
             patch('ingestion.verify_faculty.search_web', side_effect=[
                 [{'title': 'Tuo Zhao', 'body': 'Tuo Zhao Associate Professor at Georgia Tech',
                   'href': 'https://tourzhao.github.io/'}],
                 [{'title': 'Tuo Zhao | People', 'body': 'Tuo Zhao Associate Professor',
                   'href': 'https://people.research.gatech.edu/tuo-zhao'}],
             ]) as search:
            result = verify_faculty_candidate(candidate)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['institution_name'], 'Georgia Institute of Technology')
        self.assertEqual(search.call_count, 2)

    def test_identity_evidence_rows_keep_url_excerpt_and_role_attribution(self):
        rows = _identity_evidence_rows({
            'status': 'NOT_FACULTY',
            'source_url': 'https://person.example/',
            'title': 'Software Engineer',
            'role_category': 'INDUSTRY',
            'observed_employer': 'Zoox',
            'evidence_text': 'I am a Software Engineer at Zoox.',
            'search_audit': {
                'results': [{
                    'url': 'https://linkedin.com/in/person',
                    'source_kind': 'linkedin',
                    'snippet': 'Software Engineer at Zoox',
                }],
            },
        })
        self.assertEqual(len(rows), 2)
        linkedin = next(row for row in rows if 'linkedin.com' in row['source_url'])
        self.assertEqual(linkedin['source_type'], 'LINKEDIN_SNIPPET')
        self.assertEqual(linkedin['extracted_text'], 'Software Engineer at Zoox')

    def test_linkedin_and_scholar_nonfaculty_snippets_form_consensus(self):
        result = _public_snippet_nonfaculty_consensus('Jane Smith', [
            {
                'href': 'https://www.linkedin.com/in/jane',
                'title': 'Jane Smith - Software Engineer at Example Company',
                'body': 'Jane Smith is currently a Software Engineer at Example Company',
            },
            {
                'href': 'https://scholar.google.com/citations?user=jane',
                'title': 'Jane Smith',
                'body': 'Jane Smith PhD candidate, Example University',
            },
        ])
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['method'], 'public_profile_snippet_consensus')

    def test_one_exact_linkedin_engineering_manager_is_nonfaculty(self):
        result = _public_snippet_nonfaculty_consensus('Sowmya Myneni', [{
            'href': 'https://www.linkedin.com/in/sowmya-myneni',
            'title': 'Sowmya Myneni - Engineering Manager at Wells Fargo',
            'body': 'Engineering Manager at Wells Fargo',
        }])
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['role_category'], 'INDUSTRY')
        self.assertEqual(result['method'], 'linkedin_no_faculty_headline')

    @patch.dict('os.environ', {'FACULTY_IDENTITY_PASS_PAGES': '20'})
    @patch('ingestion.verify_faculty.enrich_candidate_metadata_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.enrich_candidate_paper_affiliations', side_effect=lambda value, **_: value)
    @patch('ingestion.verify_faculty.fetch_orcid_clues', return_value=None)
    @patch('ingestion.verify_faculty.search_web', return_value=[])
    def test_three_completed_searches_without_faculty_are_low_confidence_not_faculty(
        self, search, *_
    ):
        result = verify_faculty_candidate({
            'name': 'Onyinye Obioha-Val',
            'institution_name': 'University of the District of Columbia',
            'institution_domain': 'udc.edu',
            'recent_papers': [{'matched_query': 'Global Cybersecurity'}],
        })
        self.assertEqual(result['status'], 'NOT_FACULTY')
        self.assertEqual(result['method'], 'completed_three_query_no_faculty_profile')
        self.assertLess(result['confidence'], 0.8)
        self.assertTrue(result['review_recommended'])
        self.assertEqual(search.call_count, 3)
        self.assertEqual([call.args[0] for call in search.call_args_list], [
            '"Onyinye Obioha-Val" "University of the District of Columbia"',
            '"Onyinye Obioha-Val" site:udc.edu',
            '"Onyinye Obioha-Val" "University of the District of Columbia" Global Cybersecurity',
        ])


if __name__ == '__main__':
    unittest.main()
