import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.verify_faculty import (
    _ascii_fold,
    _domain_matches_institution,
    _edu_domain,
    _institution_for_domain,
    _institution_name_from_title,
    _institution_search_aliases,
    _institution_similarity,
    _openalex_move_corroborates,
    _profile_root_result,
    inspect_faculty_result,
    inspect_researcher_profile_result,
    validate_ai_identity_assessment,
    verify_faculty_candidate,
)
from ingestion.websearch import SearchUnavailable


PROJECT_DIR = Path(__file__).resolve().parents[1]


class FacultyVerificationTests(unittest.TestCase):
    def test_name_folding_handles_non_decomposing_latin_letters(self) -> None:
        cases = {
            "Şafak Kayıkçı": "Safak Kayikci",
            "Søren Łukasz": "Soren Lukasz",
            "François L'Œuf": "Francois LOEuf",
            "Þórður Đorđević": "Thordur Dordevic",
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(_ascii_fold(original), expected)

    def test_turkish_original_matches_ascii_faculty_profile(self) -> None:
        from ingestion.verify_faculty import _identity_matches, _profile_title_matches

        self.assertTrue(_identity_matches(
            "Şafak Kayıkçı",
            "Safak Kayikci Assistant Professor of Teaching at Florida Atlantic University",
        ))
        self.assertTrue(_profile_title_matches(
            "Şafak Kayıkçı", "Safak Kayikci | Florida Atlantic University"
        ))

    @patch(
        "ingestion.verify_faculty._institution_for_domain",
        return_value="Florida Atlantic University",
    )
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_turkish_name_verifies_from_ascii_official_profile(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Safak Kayikci Assistant Professor of Teaching, Department of "
            "Electrical Engineering and Computer Science, Florida Atlantic University.",
            "Safak Kayikci | Florida Atlantic University",
        )
        result = inspect_faculty_result(
            {
                "name": "Şafak Kayıkçı",
                "institution_name": "Florida Atlantic University",
            },
            {
                "title": "Safak Kayikci | Florida Atlantic University",
                "body": "Assistant Professor of Teaching at Florida Atlantic University",
                "href": "https://www.fau.edu/engineering/directory/faculty/kayikci/",
            },
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertIn("Assistant Professor", result["title"])

    def setUp(self):
        # These unit tests exercise identity rules, never live DOI discovery.
        locator = patch('ingestion.paper_affiliations._pdf_url_from_doi', return_value='')
        locator.start()
        self.addCleanup(locator.stop)

    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.openalex_get_json")
    @patch("ingestion.verify_faculty.setting_bool", return_value=False)
    def test_identity_verification_skips_optional_openalex_support_by_default(
        self, _enabled, openalex, fetch_page
    ) -> None:
        _institution_for_domain.cache_clear()
        fetch_page.return_value = ("Example University", "Example University")
        self.assertEqual(_institution_for_domain("example.edu"), "Example University")
        openalex.assert_not_called()
        _institution_for_domain.cache_clear()

    @patch(
        "ingestion.verify_faculty.search_web",
        side_effect=SearchUnavailable("providers blocked", 900),
    )
    def test_provider_outage_is_not_converted_to_unverified(self, _search) -> None:
        with self.assertRaises(SearchUnavailable):
            verify_faculty_candidate(
                {
                    "name": "Outage Candidate",
                    "institution_name": "Example University",
                    "recent_papers": [],
                }
            )

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="University of Arizona")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_new_assistant_professor_move_requires_review(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Jingdi Chen Assistant Professor of Electrical and Computer Engineering. "
            "After a postdoc at Carnegie Mellon University, she started the ANNIE Research Group.",
            "Jingdi Chen | Electrical and Computer Engineering",
        )
        result = inspect_faculty_result(
            {"name": "Jingdi Chen", "institution_name": "Carnegie Mellon University"},
            {
                "title": "Jingdi Chen | Electrical and Computer Engineering",
                "body": "Faculty at the University of Arizona",
                "href": "https://ece.engineering.arizona.edu/faculty-staff/faculty/jingdi-chen",
            },
        )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["institution_name"], "University of Arizona")
        self.assertIn("Assistant Professor", result["title"])

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="UNC Charlotte")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_official_student_page_does_not_become_professor(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Jinal Bhanubhai Butani graduate student and data scientist at UNC Charlotte.",
            "Jinal Bhanubhai Butani",
        )
        result = inspect_faculty_result(
            {"name": "Jinal Bhanubhai Butani", "institution_name": "UNC Charlotte"},
            {
                "title": "Jinal Bhanubhai Butani",
                "body": "UNC Charlotte researcher",
                "href": "https://example.charlotte.edu/jinal-butani",
            },
        )
        self.assertEqual(result["status"], "NOT_FACULTY")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Southern Illinois University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_directory_text_cannot_assign_another_persons_title(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Ning Weng Faculty list. Mehdi Ghasemirahaghi Assistant Professor.",
            "Mehdi Ghasemirahaghi Faculty | SIU",
        )
        result = inspect_faculty_result(
            {"name": "Ning Weng", "institution_name": "Southern Illinois University"},
            {
                "title": "Mehdi Ghasemirahaghi Faculty | SIU",
                "body": "Ning Weng appears in the faculty navigation",
                "href": "https://academics.siu.edu/engineering/faculty/mehdi.php",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Stanford University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_extra_name_token_does_not_verify_a_different_person(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Syed Wali Kamal Clinical Assistant Professor at Stanford University.",
            "Syed Wali Kamal | Stanford Medicine",
        )
        result = inspect_faculty_result(
            {"name": "Syed Wali", "institution_name": "Texas A&M University"},
            {
                "title": "Syed Wali Kamal | Stanford Medicine",
                "body": "Syed Wali Kamal, Assistant Professor",
                "href": "https://med.stanford.edu/profiles/syed-kamal",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Hong Kong Polytechnic University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_unproven_leading_given_name_needs_identity_corroboration(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Daniel Xiapu Luo Professor at The Hong Kong Polytechnic University.",
            "Daniel Xiapu Luo's homepage",
        )
        result = inspect_faculty_result(
            {"name": "Xiapu Luo", "institution_name": "Hong Kong Polytechnic University"},
            {
                "title": "Xiapu Luo | PolyU",
                "body": "Professor at The Hong Kong Polytechnic University",
                "href": "https://www4.comp.polyu.edu.hk/~csxluo/",
            },
        )
        # An extra first name could be an alias, but name overlap alone does
        # not establish that. Do not accept arbitrary longer names as matches.
        self.assertEqual(result["status"], "UNVERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Rowan University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_research_vocabulary_does_not_change_faculty_identity(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Bokyung Kim Professor of Public Relations and Advertising. "
            "Her scholarship studies strategic communication and media.",
            "Bokyung Kim | Rowan University",
        )
        result = inspect_faculty_result(
            {
                "name": "Bokyung Kim",
                "institution_name": "Rowan University",
                "research_domain": "AI security",
            },
            {
                "title": "Bokyung Kim | Rowan University",
                "body": "Professor of Public Relations and Advertising",
                "href": "https://www.rowan.edu/communication/faculty/bokyung-kim.html",
            },
        )
        self.assertEqual(result["status"], "VERIFIED")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Rowan University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_candidate_verifier_keeps_identity_separate_from_research_fit(
        self, search_web, fetch_page, _institution
    ) -> None:
        search_web.return_value = [
            {
                "title": "Bokyung Kim | Rowan University",
                "body": "Professor of Public Relations and Advertising",
                "href": "https://www.rowan.edu/communication/faculty/bokyung-kim.html",
            }
        ]
        fetch_page.return_value = (
            "Bokyung Kim Professor of Public Relations and Advertising.",
            "Bokyung Kim | Rowan University",
        )
        result = verify_faculty_candidate(
            {
                "name": "Bokyung Kim",
                "institution_name": "Rowan University",
                "research_domain": "AI security",
            }
        )
        self.assertEqual(result["status"], "VERIFIED")

    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=False)
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Stanford University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_same_name_faculty_at_different_institution_is_a_conflict(
        self, fetch_page, _institution, _move
    ) -> None:
        fetch_page.return_value = (
            "Alex Smith Assistant Professor at Stanford University.",
            "Alex Smith | Stanford University",
        )
        result = inspect_faculty_result(
            {
                "name": "Alex Smith",
                "institution_name": "University of Arizona",
                "research_domain": "robotics",
            },
            {
                "title": "Alex Smith | Stanford University",
                "body": "Assistant Professor at Stanford University",
                "href": "https://engineering.stanford.edu/people/alex-smith",
            },
        )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertIn("different person", result["evidence_text"])

    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=False)
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Shandong University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_matching_publication_at_non_us_institution_is_out_of_scope(
        self, fetch_page, _institution, _move
    ) -> None:
        paper_title = "A survey on large language model security and privacy"
        fetch_page.return_value = (
            "Yue Zhang Professor at Shandong University. "
            f"Selected publication: {paper_title}.",
            "Yue Zhang | Shandong University",
        )
        result = inspect_faculty_result(
            {
                "name": "Yue Zhang",
                "institution_name": "Drexel University",
                "recent_papers": [{"title": paper_title, "doi": ""}],
            },
            {
                "title": "Yue Zhang | Shandong University",
                "body": "Professor at Shandong University",
                "href": "https://faculty.sdu.edu.cn/yue-zhang",
            },
        )
        self.assertEqual(result["status"], "OUT_OF_SCOPE")
        self.assertEqual(result["method"], "non_us_official_faculty_profile")

    def test_common_international_academic_domains_are_recognized(self) -> None:
        self.assertEqual(_edu_domain("https://faculty.sdu.edu.cn/yue"), "sdu.edu.cn")
        self.assertEqual(_edu_domain("https://www.cam.ac.uk/people"), "cam.ac.uk")
        self.assertEqual(_edu_domain("https://www.unsw.edu.au/staff"), "unsw.edu.au")

    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.search_web", return_value=[])
    def test_identity_search_uses_exactly_three_targeted_queries(
        self, search_web, _gemini
    ) -> None:
        verify_faculty_candidate({
            "name": "Yue Zhang",
            "institution_name": "Drexel University",
            "recent_papers": [{
                "title": "A survey on large language model security and privacy",
                "doi": "https://doi.org/10.1016/j.hcc.2024.100211",
                "matched_query": "AI security",
            }],
        })
        queries = [call.args[0] for call in search_web.call_args_list]
        self.assertEqual(queries, [
            '"Yue Zhang" "Drexel University"',
            '"Yue Zhang" site:drexel.edu',
            '"Yue Zhang" "Drexel University" AI security',
        ])

    @patch.dict('os.environ', {'FACULTY_IDENTITY_PASS_PAGES': '10', 'FACULTY_IDENTITY_PASS_QUERIES': '4'})
    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.inspect_faculty_result")
    @patch("ingestion.verify_faculty.search_web")
    def test_candidate_search_stops_after_the_page_limit(
        self, search_web, inspect_result, _gemini
    ) -> None:
        search_web.side_effect = [
            [
                {
                    "title": f"Alexandra Researcher | Result {query_index}-{result_index}",
                    "body": "Alexandra Researcher possible faculty page",
                    "href": f"https://school{query_index}.edu/person/{result_index}",
                }
                for result_index in range(5)
            ]
            for query_index in range(4)
        ]
        inspect_result.return_value = {"status": "UNVERIFIED"}
        verify_faculty_candidate({
            "name": "Alexandra Researcher",
            "institution_name": "Example University",
            "recent_papers": [{
                "title": "A distinctive research paper title",
                "doi": "https://doi.org/10.1000/example",
            }],
        })
        self.assertLessEqual(search_web.call_count, 4)
        self.assertEqual(inspect_result.call_count, 10)

    def test_nonstandard_international_domain_matches_its_institution(self) -> None:
        self.assertTrue(
            _domain_matches_institution(
                "https://www.ualberta.ca/computing-science/people/faculty/cor-paul.html",
                "University of Alberta",
            )
        )
        self.assertTrue(
            _domain_matches_institution(
                "https://www.vu.nl/en/about-vu/more-about/people",
                "Vrije Universiteit Amsterdam",
            )
        )
        self.assertFalse(
            _domain_matches_institution(
                "https://social.example.com/person",
                "University of Alberta",
            )
        )

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="University of Alberta")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_nonstandard_international_official_page_is_out_of_scope(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "Cor-Paul Bezemer Professor at the University of Alberta.",
            "Cor-Paul Bezemer | University of Alberta",
        )
        result = inspect_faculty_result(
            {"name": "Cor-Paul Bezemer", "institution_name": "University of Alberta"},
            {
                "title": "Cor-Paul Bezemer | University of Alberta",
                "body": "Professor in Computing Science",
                "href": "https://www.ualberta.ca/computing-science/people/faculty/cor-paul-bezemer.html",
            },
        )
        self.assertEqual(result["status"], "OUT_OF_SCOPE")

    def test_academic_pdf_can_reveal_the_personal_homepage_root(self) -> None:
        result = _profile_root_result(
            "Abhik Roychoudhury",
            {
                "href": "https://www.comp.nus.edu.sg/~abhik/pdf/paper.pdf",
                "body": "Abhik Roychoudhury is a Professor",
            },
        )
        self.assertEqual(result["href"], "https://www.comp.nus.edu.sg/~abhik/")

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Hong Kong Polytechnic University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_google_scholar_email_domain_locates_non_us_out_of_scope_page(
        self, search_web, fetch_page, _institution
    ) -> None:
        def results(query, max_results=5):
            if "site:comp.polyu.edu.hk" in query:
                return [{
                    "title": "Xiapu Luo | PolyU",
                    "body": "Professor at Hong Kong Polytechnic University",
                    "href": "https://www4.comp.polyu.edu.hk/~csxluo/",
                }]
            if query == '"Xiapu Luo" "Hong Kong Polytechnic University"':
                return [{
                    "title": "Xiapu Luo - Google Scholar",
                    "body": "Xiapu Luo. The Hong Kong Polytechnic University. Verified email at comp.polyu.edu.hk",
                    "href": "https://scholar.google.com/citations?user=example",
                }]
            return []

        search_web.side_effect = results
        fetch_page.return_value = (
            "Xiapu Luo Professor, Department of Computing, The Hong Kong Polytechnic University.",
            "Xiapu Luo | PolyU",
        )
        result = verify_faculty_candidate({
            "name": "Xiapu Luo",
            "institution_name": "Hong Kong Polytechnic University",
            "recent_papers": [{
                "title": "Large Language Models for Software Engineering: A Systematic Literature Review",
                "doi": "",
            }],
        })
        self.assertEqual(result["status"], "OUT_OF_SCOPE")
        self.assertTrue(any(
            "site:comp.polyu.edu.hk" in call.args[0]
            for call in search_web.call_args_list
        ))

    @patch("ingestion.verify_faculty._fetch_related_publication_text")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_paper_linked_non_us_faculty_profile_is_out_of_scope(
        self, fetch_page, related_text
    ) -> None:
        paper = "A 2030 Roadmap for Software Engineering"
        fetch_page.return_value = (
            "Abhik Roychoudhury Provost's Chair Professor National University of Singapore.",
            "Abhik Roychoudhury",
        )
        related_text.return_value = f"Selected paper: {paper}."
        result = inspect_researcher_profile_result(
            {
                "name": "Abhik Roychoudhury",
                "institution_name": "National University of Singapore",
                "recent_papers": [{"title": paper, "doi": ""}],
            },
            {
                "title": "Abhik Roychoudhury",
                "body": "Professor at National University of Singapore",
                "href": "https://abhikrc.com/",
            },
        )
        self.assertEqual(result["status"], "OUT_OF_SCOPE")
        self.assertEqual(result["method"], "non_us_faculty_profile")

    @patch("ingestion.verify_faculty._fetch_related_publication_text")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_paper_linked_researcher_student_profile_is_not_faculty(
        self, fetch_page, related_text
    ) -> None:
        fetch_page.return_value = (
            "Example Person PhD student at Example University.",
            "Example Person",
        )
        related_text.return_value = "Distinctive Identity Research Paper"
        result = inspect_researcher_profile_result(
            {
                "name": "Example Person",
                "institution_name": "Example University",
                "recent_papers": [{"title": "Distinctive Identity Research Paper", "doi": ""}],
            },
            {
                "title": "Example Person",
                "body": "PhD student",
                "href": "https://example-person.net/",
            },
        )
        self.assertEqual(result["status"], "NOT_FACULTY")

    def test_institution_matching_handles_diacritics(self) -> None:
        self.assertGreaterEqual(
            _institution_similarity(
                "University of Hawaiʻi at Mānoa",
                "University of Hawaii System",
            ),
            0.5,
        )

    def test_institution_search_aliases_cover_common_forms(self) -> None:
        self.assertIn("NUS", _institution_search_aliases("National University of Singapore"))
        self.assertIn("UAlberta", _institution_search_aliases("University of Alberta"))
        self.assertEqual(
            _institution_search_aliases("Hong Kong Polytechnic University")[0],
            "PolyU",
        )

    @patch("ingestion.verify_faculty._openalex_exact_name_profiles")
    def test_fragmented_openalex_records_can_corroborate_a_move(self, profiles) -> None:
        profiles.return_value = (
            ("A5093879783", ("University of Mount Union",)),
            ("A5115739777", ("University of San Diego",)),
        )
        self.assertTrue(
            _openalex_move_corroborates(
                {
                    "name": "Vahraz Honary",
                    "openalex_id": "https://openalex.org/A5093879783",
                    "institution_name": "University of Mount Union",
                },
                "University of San Diego",
            )
        )

    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=True)
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="University of San Diego")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_verifier_does_not_follow_an_unrelated_employer_hint(
        self, search_web, fetch_page, _institution, _move
    ) -> None:
        def results(query, max_results=5):
            if "University of San Diego" in query:
                return [{
                    "title": "Faculty | University of San Diego Catalog",
                    "body": "Vahraz Honary (2022) Assistant Professor of Electrical Engineering",
                    "href": "https://undergraduate.catalog.sandiego.edu/academics/undergraduatefaculty",
                }]
            if query == '"Vahraz Honary" faculty professor':
                return [{
                    "title": "Vahraz Honary - University of San Diego | LinkedIn",
                    "body": "Experience: University of San Diego",
                    "href": "https://www.linkedin.com/in/vahraz-honary",
                }]
            return []

        search_web.side_effect = results
        fetch_page.return_value = (
            "Vahraz Honary (2022) Assistant Professor of Electrical Engineering",
            "Faculty | University of San Diego Catalog",
        )
        result = verify_faculty_candidate({
            "name": "Vahraz Honary",
            "openalex_id": "https://openalex.org/A5093879783",
            "institution_name": "University of Mount Union",
            "recent_papers": [],
        })
        self.assertEqual(result["status"], "NOT_FACULTY")
        self.assertLess(result["confidence"], 0.8)
        queries = [call.args[0] for call in search_web.call_args_list]
        self.assertFalse(any("University of San Diego" in query for query in queries))
        self.assertNotIn('"Vahraz Honary" faculty professor', queries)

    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations")
    @patch("ingestion.verify_faculty.enrich_candidate_metadata_affiliations")
    @patch("ingestion.verify_faculty.search_web")
    def test_metadata_and_pdf_evidence_precede_search_fallback(
        self, search_web, metadata, pdf_fallback, _gemini
    ) -> None:
        events: list[str] = []

        def metadata_result(candidate, max_papers=3):
            events.append("metadata")
            return candidate

        def search_result(query, max_results=5):
            events.append("search")
            return []

        def pdf_result(candidate, max_papers=3):
            events.append("pdf")
            return candidate

        metadata.side_effect = metadata_result
        search_web.side_effect = search_result
        pdf_fallback.side_effect = pdf_result
        result = verify_faculty_candidate({
            "name": "Example Researcher",
            "institution_name": "Example University",
            "recent_papers": [{"title": "A Relevant Paper", "doi": ""}],
        })
        self.assertEqual(result["status"], "NOT_FACULTY")
        self.assertEqual(result["method"], "completed_three_query_no_faculty_profile")
        self.assertEqual(events[0], "metadata")
        self.assertLess(events.index("pdf"), events.index("search"))

    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty.enrich_candidate_paper_affiliations")
    @patch("ingestion.verify_faculty.enrich_candidate_metadata_affiliations")
    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=False)
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Stanford University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_paper_affiliation_and_different_official_university_require_review(
        self, search_web, fetch_page, _institution, _move, metadata,
        paper_fallback, _gemini
    ) -> None:
        search_web.return_value = [{
            "title": "Alex Smith | Stanford University",
            "body": "Alex Smith Assistant Professor at Stanford University",
            "href": "https://engineering.stanford.edu/people/alex-smith",
        }]
        fetch_page.return_value = (
            "Alex Smith Assistant Professor at Stanford University.",
            "Alex Smith | Stanford University",
        )
        candidate = {
            "name": "Alex Smith",
            "institution_name": "University of Arizona",
            "recent_papers": [{"title": "Identity Paper", "doi": ""}],
        }
        metadata.side_effect = lambda value, max_papers=3: value
        paper_fallback.return_value = {
            **candidate,
            "paper_affiliations": [{
                "status": "MATCHED",
                "institution_name": "University of Arizona",
                "method": "open_pdf",
            }],
        }
        result = verify_faculty_candidate(candidate)
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["method"], "institution_mismatch_review")

    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=False)
    @patch("ingestion.verify_faculty._institution_for_domain", return_value="Stanford University")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_one_different_university_profile_requires_review(
        self, search_web, fetch_page, _institution, _move, _gemini
    ) -> None:
        search_web.return_value = [{
            "title": "Alex Smith | Stanford University",
            "body": "Alex Smith Assistant Professor at Stanford University",
            "href": "https://engineering.stanford.edu/people/alex-smith",
        }]
        fetch_page.return_value = (
            "Alex Smith Assistant Professor at Stanford University.",
            "Alex Smith | Stanford University",
        )
        result = verify_faculty_candidate({
            "name": "Alex Smith",
            "openalex_id": "https://openalex.org/A123",
            "institution_name": "University of Arizona",
            "recent_papers": [],
        })
        self.assertEqual(result["status"], "CONFLICT")
        self.assertIn("staff review is required", result["evidence_text"])

    @patch("ingestion.verify_faculty.assess_identity_with_gemini", return_value=None)
    @patch("ingestion.verify_faculty._openalex_move_corroborates", return_value=False)
    @patch("ingestion.verify_faculty._institution_for_domain")
    @patch("ingestion.verify_faculty._fetch_official_page")
    @patch("ingestion.verify_faculty.search_web")
    def test_namesake_links_are_retained_but_stop_on_first_decisive_conflict(
        self, search_web, fetch_page, institution, _move, _gemini
    ) -> None:
        search_web.return_value = [
            {
                "title": "Yue Zhang | Baruch College",
                "body": "Yue Zhang Professor at City University of New York",
                "href": "https://www.baruch.cuny.edu/profiles/faculty/Yue-Zhang",
            },
            {
                "title": "Yue Zhang | Stanford University",
                "body": "Yue Zhang Professor at Stanford University",
                "href": "https://engineering.stanford.edu/faculty/yue-zhang",
            },
        ]
        fetch_page.side_effect = lambda url: (
            (
                "Yue Zhang Professor at City University of New York.",
                "Yue Zhang | Baruch College",
            )
            if "cuny.edu" in url
            else (
                "Yue Zhang Professor at Stanford University.",
                "Yue Zhang | Stanford University",
            )
        )
        institution.side_effect = lambda domain: (
            "City University of New York" if domain == "cuny.edu"
            else "Stanford University"
        )
        result = verify_faculty_candidate(
            {
                "name": "Yue Zhang",
                "openalex_id": "https://openalex.org/A5100333755",
                "institution_name": "Drexel University",
                "recent_papers": [
                    {
                        "title": "A survey on large language model security and privacy",
                        "doi": "https://doi.org/10.1016/j.hcc.2024.100211",
                    }
                ],
            }
        )
        self.assertEqual(result["status"], "CONFLICT")
        alternatives = result["alternative_evidence"]
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(search_web.call_count, 1)
        self.assertEqual(fetch_page.call_count, 1)
        self.assertEqual(len(result['search_audit']['results']), 2)
        uninspected = [r for r in result['search_audit']['results'] if r['inspection'] == 'Not inspected in this pass']
        self.assertEqual(len(uninspected), 1)
        self.assertNotIn(uninspected[0]['url'], {row['source_url'] for row in alternatives})

    def test_doctoral_graduate_page_cannot_verify_faculty(self) -> None:
        result = inspect_faculty_result(
            {"name": "Jalal Ghadermazi", "institution_name": "University of South Florida"},
            {
                "title": "Jalal Ghadermazi",
                "body": "Major Professor and doctoral graduate",
                "href": "https://www.usf.edu/people/recent-doctoral-graduates.aspx",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_non_university_page_cannot_verify_faculty(self) -> None:
        result = inspect_faculty_result(
            {"name": "Jingdi Chen", "institution_name": "University of Arizona"},
            {
                "title": "Jingdi Chen",
                "body": "Assistant Professor",
                "href": "https://social.example.com/jingdi",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_official_root_title_can_name_current_institution(self) -> None:
        self.assertEqual(
            _institution_name_from_title(
                "University of Arizona | The University of Arizona"
            ),
            "University of Arizona",
        )

    @patch("ingestion.verify_faculty._institution_for_domain", return_value="University of Mount Union")
    @patch("ingestion.verify_faculty._fetch_official_page")
    def test_old_faculty_hiring_announcement_does_not_prove_current_role(
        self, fetch_page, _institution
    ) -> None:
        fetch_page.return_value = (
            "In 2020, Vahraz Honary joins the School of Engineering as an assistant professor.",
            "Mount Union Announces New Faculty Hires for 2020-2021",
        )
        result = inspect_faculty_result(
            {"name": "Vahraz Honary", "institution_name": "University of Mount Union"},
            {
                "title": "Mount Union Announces New Faculty Hires for 2020-2021",
                "body": "Vahraz Honary joins as an assistant professor",
                "href": "https://www.mountunion.edu/news/new-faculty-hires-2020",
            },
        )
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_ai_assistance_requires_literal_evidence_and_publication_link(self) -> None:
        title = "Secure Learning for Networked Systems"
        page_text = (
            "Alex Smith Assistant Professor at Stanford University. "
            f"Selected publication: {title}."
        )
        candidate = {
            "name": "Alex Smith",
            "institution_name": "University of Arizona",
            "recent_papers": [{"title": title, "doi": ""}],
        }
        pages = [{
            "status": "CONFLICT",
            "source_url": "https://engineering.stanford.edu/people/alex-smith",
            "institution_name": "Stanford University",
            "page_title": "Alex Smith | Stanford Engineering",
            "_page_text": page_text,
        }]
        assessment = {
            "decision": "VERIFIED",
            "selected_source_url": pages[0]["source_url"],
            "observed_title": "Assistant Professor",
            "observed_institution": "Stanford University",
            "identity_evidence_quote": "Alex Smith Assistant Professor at Stanford University.",
            "identity_link_quote": f"Selected publication: {title}.",
            "reason": "The official profile contains the same publication.",
            "confidence": 0.93,
            "model_name": "test-model",
            "prompt_version": 1,
        }
        result = validate_ai_identity_assessment(candidate, pages, assessment)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["method"], "institution_mismatch_review")

    def test_ai_assistance_rejects_a_quote_not_present_on_the_page(self) -> None:
        candidate = {"name": "Alex Smith", "institution_name": "Stanford University"}
        pages = [{
            "source_url": "https://engineering.stanford.edu/people/alex-smith",
            "institution_name": "Stanford University",
            "_page_text": "Alex Smith Assistant Professor.",
        }]
        assessment = {
            "decision": "VERIFIED",
            "selected_source_url": pages[0]["source_url"],
            "observed_title": "Assistant Professor",
            "observed_institution": "Stanford University",
            "identity_evidence_quote": "Alex Smith won a fictional award.",
            "identity_link_quote": "",
            "reason": "Unsupported",
            "confidence": 0.99,
        }
        self.assertIsNone(validate_ai_identity_assessment(candidate, pages, assessment))

    def test_public_query_requires_verified_faculty(self) -> None:
        source = (PROJECT_DIR / "db.py").read_text(encoding="utf-8")
        self.assertIn("AND p.faculty_status = 'VERIFIED'", source)
        self.assertIn("p.faculty_verification_version >= 8", source)

    def test_schema_retains_verification_evidence(self) -> None:
        schema = (PROJECT_DIR / "db.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS faculty_verification_evidence", schema)
        self.assertIn("faculty_status TEXT NOT NULL DEFAULT 'UNVERIFIED'", schema)
        self.assertIn("faculty_verification_version INTEGER NOT NULL DEFAULT 0", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS ai_usage_daily", schema)
        self.assertIn("decision_method TEXT", schema)
        self.assertIn("faculty_verification_version = 5", schema)
        self.assertIn("New conflicts require multiple plausible official", schema)

    def test_staff_page_exposes_automatic_decisions_and_overrides(self) -> None:
        source = (PROJECT_DIR / "pages" / "5_Radar_control.py").read_text(encoding="utf-8")
        self.assertIn("Recent automatic identity decisions", source)
        self.assertIn("Gemini-assisted extraction", source)
        self.assertIn("Official page + affiliation history", source)
        self.assertIn("Official page + matching publication", source)
        self.assertIn("Staff overrides take priority", source)


if __name__ == "__main__":
    unittest.main()
