from ingestion.publication_discovery import (
    Publication, extract_publications, paper_key, same_person_name,
    scholar_identity_decision, alternate_official_profile_matches,
    linked_scholar_profiles,
    extract_research_interests,
    extract_biography_text,
    _dedupe_scholar_profiles,
    _heading_contains_person_name,
    _is_block_page,
    linked_directory_person,
    _is_shared_person_directory,
    linked_research_pages,
    personal_site_sections,
)
from ingestion.faculty_roster import eligible_research_group_leader


def test_award_badge_is_not_part_of_publication_title():
    html = '''<main><h2>Publications</h2><li>Fredericks, E. M. (2026).
    [ ⭐ Spirit Award (for being the 26th submitted paper in 2026) ⭐ ] Managing Dermal Reference Guides in the Face of Software Evolution via a CI/CD Pipeline. In SIGBOVIK.</li></main>'''
    papers = extract_publications(html,'https://example.edu/p','PERSONAL_SITE','Erik Fredericks')
    assert len(papers)==1
    assert papers[0].title.startswith('Managing Dermal Reference Guides')
    assert 'Spirit Award' in papers[0].evidence


def test_supplemental_scholar_queue_keeps_verified_regional_identity(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from ingestion import publication_discovery as module
    import radar_store
    url='https://scholar.google.com/citations?user=testid'
    rows=[dict(source_url=url,identity_status='VERIFIED',checked_at=datetime.now(timezone.utc)),
          dict(source_url=url.replace('.com/', '.com.tr/'),identity_status='SOURCE_UNAVAILABLE',checked_at=datetime.now(timezone.utc))]
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,*args): pass
        def fetchall(self): return rows
    @contextmanager
    def connection(): yield SimpleNamespace(cursor=Cursor)
    monkeypatch.setattr(module,'get_db_connection',connection)
    saved=[]; queued=[]
    monkeypatch.setattr(module,'_record_source',lambda *args:saved.append(args))
    monkeypatch.setattr(radar_store,'enqueue_radar_job',lambda *args,**kwargs:queued.append(kwargs) or {'id':12})
    steps=[]
    module._queue_linked_scholar_review(23,[url],steps)
    assert not queued and not saved
    assert steps[0]['status']=='VERIFIED'
    rows.clear(); steps.clear()
    module._queue_linked_scholar_review(23,[url,url+'&hl=en'],steps)
    assert len(queued)==len(saved)==1
    assert steps[0]['status']=='QUEUED'


def test_author_year_publications_under_achievements_include_coauthored_work():
    citations = [
        'Liu, H., & Zhang, Y. (2024). Digital Game-Based Learning on Historical and Cultural Heritage in China: A Systematic Review. In Y. Kang, K. C.C. Yang, M. Mochocki, J. Majewski, P. Schreiber (Eds.), Asian Histories and Heritages in Video Games. (pp.187-208). Routledge.',
        'Lei, M., Clemente, I. M., Liu, H., & Bell, J. (2022). The acceptance of telepresence robots in higher education. International Journal of Social Robotics, 14(4), 1025-1042. [SCI]',
        'Liu, H., Wang, L., & Koehler, M. J. (2019). Exploring the intention behavior gap in the technology acceptance model: A mixed-methods study in the context of foreign language teaching in China. British Journal of Educational Technology, 50(5), 2536-2556. [SSCI]',
        'Liu, H., Lin, C. H., Zhang, D., & Zheng, B. (2018). Chinese language teachers’ perceptions of technology and instructional use of technology: A path analysis. Journal of Educational Computing Research, 56(3), 396-414. [SSCI]',
        'Liu, H., Lin, C. H., & Zhang, D. (2017). Pedagogical beliefs and attitudes toward information and communication technology: a survey of teachers of English as a foreign language in China. Computer Assisted Language Learning, 30(8), 745-765. [SSCI]',
    ]
    html = '<main><h4>Notable Acheivements</h4>' + ''.join('<p>'+c+'</p>' for c in citations) + '</main>'
    papers = extract_publications(html,'https://example.edu/profile','OFFICIAL_PROFILE','Haixia Liu')
    assert len(papers) == 5
    assert [p.year for p in papers] == [2024,2022,2019,2018,2017]
    assert papers[1].title == 'The acceptance of telepresence robots in higher education'
    assert papers[0].venue.startswith('In Y. Kang')
    assert papers[1].authors.startswith('Lei, M.')
    assert [p.evidence for p in papers] == citations
    assert not extract_publications(html,'https://example.edu/profile','OFFICIAL_PROFILE','Jane Smith')


def test_heading_independent_citations_require_subject_and_publication_details():
    html = '''<main><h2>Achievements</h2>
    <p>Liu, H. (2019). PhD in Education. Michigan State University.</p>
    <p>Liu, H. (2024). Distinguished Teaching Award. Example University.</p>
    <p>Smith, J. (2024). A paper about Haixia Liu. Journal of Education, 1(2), 3-5.</p>
    <p>Liu, Y. (2024). Another person's research paper. Journal of Education, 1(2), 3-5.</p></main>'''
    assert not extract_publications(html,'https://example.edu/profile','OFFICIAL_PROFILE','Haixia Liu')


def test_author_year_fallback_deduplicates_explicit_section():
    html = '<main><h2>Publications</h2><ul><li><p>Liu, H. (2024). A real research paper. Journal of Education, 1(2), 3-5.</p></li></ul></main>'
    papers = extract_publications(html,'https://example.edu/profile','OFFICIAL_PROFILE','Haixia Liu')
    assert len(papers)==1


def test_nested_book_metadata_is_not_a_separate_publication():
    html = '''<main><p><strong>Numerous refereed articles and two book publications:</strong></p>
    <ul><li>In the Shadows of the Master: Al-Mutanabbis Legacy
    <ul><li>Berkshire: Berkshire Academic Press, 2012.</li></ul></li>
    <li>Dictionary of Literary Biography: Twentieth-Century Arab Writers.
    <ul><li>Edited by Majd Al-Mallah and Coeli Fitzpatrick. Detroit, Gale Group, 2009.</li></ul></li></ul>
    <h2>Bio</h2><p>Not a publication</p></main>'''
    papers = extract_publications(html, 'https://example.edu/profile', 'OFFICIAL_PROFILE')
    assert [(p.title,p.year) for p in papers] == [
        ('In the Shadows of the Master: Al-Mutanabbis Legacy',2012),
        ('Dictionary of Literary Biography: Twentieth-Century Arab Writers',2009)]
    assert 'Berkshire Academic Press' in papers[0].evidence
    assert 'Edited by Majd' in papers[1].evidence


def test_nested_category_retains_individual_publications():
    html = '''<main><h2>Publications</h2><ul><li>Books and journal articles
    <ul><li>“A Real Research Article” (2025)</li><li>“Another Real Research Article” (2024)</li></ul>
    </li></ul></main>'''
    assert [p.title for p in extract_publications(html,'https://example.edu/profile','OFFICIAL_PROFILE')] == ['A Real Research Article','Another Real Research Article']


def test_biography_mentioning_publication_is_not_heading():
    html = '''<main><p><strong>Biographical Sketch</strong><br>
    She has a book scheduled for publication.</p>
    <p>PhD Women’s, Gender, and Sexuality Studies, The Ohio State University</p></main>'''
    assert not extract_publications(html, 'https://example.edu/profile', 'OFFICIAL_PROFILE')


def test_discovery_visits_personal_sections_before_search(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from ingestion import publication_discovery as module
    professor = dict(id=1, name='Kimberly McKee', institution_id=1,
                     institution_name='Example University', faculty_title='Professor',
                     faculty_source_url='https://example.edu/profile')
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): pass
        def fetchone(self): return professor
    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor)
    pages = {
        'https://example.edu/profile': '<main><a href="https://mckeekimberly.com/">mckeekimberly.com</a><a href="https://scholar.google.com/citations?user=example">Scholar</a></main>',
        'https://mckeekimberly.com/': '<nav><a href="/research/">Research</a><a href="/about/">About</a></nav><main>Kimberly McKee</main>',
        'https://mckeekimberly.com/research/': '<main><h2>Publications</h2><p>“A Genuine Research Paper” (2025)</p></main>',
        'https://mckeekimberly.com/about/': '<main><h1>Kimberly McKee</h1><a href="https://scholar.google.de/citations?user=personal">Scholar</a></main>',
    }
    visited = []
    def get(url, **kwargs):
        visited.append(url)
        return SimpleNamespace(text=pages[url],url=url,raise_for_status=lambda:None)
    monkeypatch.setattr(module,'get_db_connection',connection)
    monkeypatch.setattr(module.requests,'get',get)
    monkeypatch.setattr(module,'_record_source',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'_status',lambda *args:None)
    monkeypatch.setattr(module,'_save',lambda pid,papers,callback:len(papers))
    scholar_queues=[]
    monkeypatch.setattr(module,'_queue_linked_scholar_review',lambda pid,urls,steps,**kwargs:scholar_queues.append((urls,kwargs.get('discovered_by','OFFICIAL_PROFILE_LINK'))) if urls else None)
    monkeypatch.setattr(module,'_publication_search',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('Search before linked sections')))
    result = module.discover_faculty_publications(1)
    assert result['papers_imported'] == 1
    assert scholar_queues == [(['https://scholar.google.com/citations?user=example'],'OFFICIAL_PROFILE_LINK'),
                              (['https://scholar.google.de/citations?user=personal'],'LINKED_RESEARCH_PAGE')]
    assert set(visited) == set(pages)


def test_publications_stop_at_education_subheading_and_root_boundary():
    html = '''<main><h2>Publications</h2><p>“A Genuine Research Paper” (2025)</p>
    <h3>Education</h3><p>PhD Women’s Studies, The Ohio State University</p></main>
    <p>Something outside the profile container</p>'''
    assert [p.title for p in extract_publications(html, 'https://example.edu/p', 'OFFICIAL_PROFILE')] == ['A Genuine Research Paper']


def test_person_named_bare_domain_and_bounded_sections():
    html = '<main><a href="https://mckeekimberly.com/">mckeekimberly.com</a></main>'
    assert linked_research_pages(html, 'https://example.edu/p', 'Kimberly McKee') == ['https://mckeekimberly.com/']
    assert not linked_research_pages(html, 'https://example.edu/p', 'Jane Smith')
    html = '''<nav><a href="/research/">Research</a><a href="/about/">About</a>
    <a href="https://other.example/research">Research</a><a href="/contact/">Contact</a></nav>'''
    assert personal_site_sections(html, 'https://mckeekimberly.com/') == ['https://mckeekimberly.com/research/', 'https://mckeekimberly.com/about/']


def test_book_citation_outside_publications_retains_title_not_editor_initial():
    html = '''<main><h2>Notable Achievements</h2><p>McKee, Kimberly, and Denise A. Delgado, eds. Degrees of Difference: Reflections of Women of Color on Graduate School (Champaign, IL: University of Illinois Press, 2020).</p></main>'''
    papers = extract_publications(html, 'https://example.edu/p', 'OFFICIAL_PROFILE', 'Kimberly McKee')
    assert [(p.title,p.year) for p in papers] == [('Degrees of Difference: Reflections of Women of Color on Graduate School',2020)]
    assert not extract_publications(html, 'https://example.edu/p', 'OFFICIAL_PROFILE', 'Jane Smith')


def test_directory_detail_link_is_person_local():
    html = '''<main><table><tr><td>Daisy</td><td>Fredricks</td>
    <td><a href="?recordId=1">View</a></td></tr>
    <tr><td>Jane</td><td>Smith</td><td><a href="?recordId=2">View</a></td>
    </tr></table></main>'''
    assert linked_directory_person(html, 'https://example.edu/directory',
                                   'Daisy Fredricks') == 'https://example.edu/directory?recordId=1'
    assert not linked_directory_person(html, 'https://example.edu/directory', 'Nobody Else')
    assert _is_shared_person_directory(html)
    assert not _is_shared_person_directory('<main><h1>Daisy Fredricks</h1></main>')


def test_ambiguous_directory_links_do_not_choose_first():
    html = '''<main><article><h2>Daisy Fredricks</h2>
    <a href="/one">View</a><a href="/two">Profile</a></article></main>'''
    assert not linked_directory_person(html, 'https://example.edu/directory', 'Daisy Fredricks')


def test_author_year_citation_keeps_short_quote_and_subtitle():
    html = '''<main><h2>Recent Publications</h2>
    <p>Fredricks, D. (2016). “Talk English!”: Language and identity in classrooms. Journal of Education.</p>
    <h2>Interests</h2><p>Reading instruction and teacher development</p></main>'''
    papers = extract_publications(html, 'https://example.edu/profile', 'OFFICIAL_PROFILE')
    assert len(papers) == 1
    assert papers[0].title == '“Talk English!”: Language and identity in classrooms'


def test_containing_paragraph_is_not_a_publication_heading():
    html = '''<main><p>Research interests in teaching and learning across classrooms.
    This extensive introductory material is not a publication heading.
    <p>Classroom development and multilingual learning</p>
    <p><strong>Recent Publications:</strong></p>
    <p>Fredricks, D. (2021). Understanding teacher learning. Teaching Journal.</p>
    </p></main>'''
    papers = extract_publications(html, 'https://example.edu/profile', 'OFFICIAL_PROFILE')
    assert [p.title for p in papers] == ['Understanding teacher learning']


def test_name_matching_allows_compatible_middle_initials() -> None:
    assert same_person_name("Regina Lopata Logan", "Regina L. Logan")
    assert same_person_name("M. Z. Naser", "M Z Naser")
    assert same_person_name("Naser, M. Z.", "M Z Naser")


def test_name_matching_does_not_merge_different_people() -> None:
    assert not same_person_name("Wei Zhang", "Wei Wang")
    assert not same_person_name("John A. Smith", "James A. Smith")


def test_profile_heading_allows_site_suffix_but_not_partial_name() -> None:
    assert _heading_contains_person_name(
        "Brett Atwood", "Brett Atwood | Murrow College of Communication"
    )
    assert not _heading_contains_person_name(
        "Brett Atwood", "Brett Johnson | Murrow College of Communication"
    )


def test_http_200_access_challenge_is_not_treated_as_profile_content() -> None:
    assert _is_block_page("<html><title>Access Denied</title><h1>Forbidden</h1></html>")
    assert not _is_block_page(
        "<html><title>Jane Smith | University</title><h1>Jane Smith</h1>"
        "<p>Her research discusses access denied errors.</p></html>"
    )


def test_publications_are_extracted_only_from_publication_section() -> None:
    html = """<main><h2>News</h2><li>Not a paper title in this area</li>
    <h2>Selected Publications</h2><ul><li>\"A Reliable Security Paper\" (2025)</li></ul>
    <h2>Teaching</h2><li>Another non-paper entry here</li></main>"""
    papers = extract_publications(html, "https://example.edu/jane", "OFFICIAL_PROFILE")
    assert [paper.title for paper in papers] == ["A Reliable Security Paper"]


def test_publication_paragraphs_are_extracted_from_explicit_section() -> None:
    html = """<main><h2>Scholarly Achievements and Publications</h2>
    <p>Editor in Chief. Gale Case Studies: Intersectional LGBTQ Issues. 2018.</p>
    <h2>Awards</h2><p>This is not a publication.</p></main>"""
    papers = extract_publications(html, "https://example.edu/jane", "OFFICIAL_PROFILE")
    assert len(papers) == 1
    assert "Gale Case Studies" in papers[0].title


def test_bold_paragraph_can_mark_publication_section() -> None:
    html = """<main><p><strong>Scholarly Achievements and Publications:</strong></p>
    <p>Guest editor. Special issue of The Journal of Lesbian Studies. 2014.</p>
    <p><strong>Awards:</strong></p><p>Teaching award, 2015.</p></main>"""
    papers = extract_publications(html, "https://example.edu/jane", "OFFICIAL_PROFILE")
    assert len(papers) == 1
    assert "Journal of Lesbian Studies" in papers[0].title


def test_alternate_official_profile_accepts_name_role_and_domain() -> None:
    professor = {"name": "Danielle DeMuth", "faculty_source_url":
                 "https://www.gvsu.edu/sis/faculty-profile-danielle-demuth-95",
                 "official_institution_domain": ""}
    html = """<main><h1>Danielle DeMuth</h1>
    <p>Associate Professor of Women, Gender, and Sexuality Studies</p></main>"""
    matches, evidence = alternate_official_profile_matches(
        html, "https://www.gvsu.edu/wgs/danielle-demuth-57.htm", professor
    )
    assert matches
    assert evidence["role"] == "Associate Professor"


def test_alternate_official_person_page_can_reuse_approved_roster_role() -> None:
    professor = {
        "name": "Brett Atwood",
        "faculty_title": "Associate Professor",
        "faculty_source_url": "https://people.wsu.edu/profile/brett-atwood/",
        "official_institution_domain": "wsu.edu",
    }
    matches, evidence = alternate_official_profile_matches(
        "<main><h1>Brett Atwood</h1><p>Media strategy and digital culture.</p></main>",
        "https://murrow.wsu.edu/directory/wsu-profile/batwood/",
        professor,
    )
    assert matches
    assert evidence["role_source"] == "APPROVED_ROSTER"
    assert evidence["role"] == "Associate Professor"


def test_alternate_page_rejects_unrelated_person() -> None:
    professor = {"name": "Danielle DeMuth", "faculty_source_url":
                 "https://www.gvsu.edu/sis/faculty-profile-danielle-demuth-95",
                 "official_institution_domain": ""}
    matches, _ = alternate_official_profile_matches(
        "<main><h1>Another Person</h1><p>Professor</p></main>",
        "https://www.gvsu.edu/wgs/another-person.htm", professor,
    )
    assert not matches


def test_explicit_research_interest_section_is_extracted() -> None:
    html = """<main><h1>Jane Smith</h1><h2>Research Interests</h2>
    <ul><li>Digital Marketing</li><li>Artificial Intelligence</li>
    <li>Metaverse Marketing</li></ul><h2>Teaching</h2>
    <p>Introductory communication.</p></main>"""
    interests, excerpt = extract_research_interests(html)
    assert interests == ["Digital Marketing", "Artificial Intelligence", "Metaverse Marketing"]
    assert "Introductory communication" not in excerpt


def test_biography_extraction_stays_inside_bio_section() -> None:
    html = """<main><h2>Biography</h2><p>Jane studies online communities,
    digital communication, and public relations in emerging media.</p>
    <h2>Awards</h2><p>Winner of a teaching award.</p></main>"""
    biography = extract_biography_text(html)
    assert "online communities" in biography
    assert "teaching award" not in biography


def test_paper_key_deduplicates_same_title_and_year() -> None:
    a = Publication("A Good Paper", 2024, "", "https://a", "PERSONAL_SITE", "x")
    b = Publication("A good paper", 2024, "", "https://b", "GOOGLE_SCHOLAR", "y")
    assert paper_key(a) == paper_key(b)


def test_scholar_requires_name_plus_independent_signal() -> None:
    professor = {"name": "Jane Q. Smith", "institution_name": "Example University",
                 "official_institution_domain": "example.edu"}
    status, reasons = scholar_identity_decision(
        professor, {"name": "Jane Smith", "affiliation": "Example University",
                    "verified_email": "", "homepage": ""},
        ["https://example.edu/jane"],
    )
    assert status == "VERIFIED"
    assert reasons == ["compatible_name", "compatible_affiliation"]


def test_scholar_name_only_is_review_not_automatic() -> None:
    professor = {"name": "Jane Smith", "institution_name": "Example University",
                 "official_institution_domain": "example.edu"}
    status, _ = scholar_identity_decision(
        professor, {"name": "Jane Smith", "affiliation": "Other University",
                    "verified_email": "", "homepage": ""}, [],
    )
    assert status == "REVIEW_REQUIRED"


def test_official_profile_scholar_link_is_found_even_when_icon_has_no_text() -> None:
    html = """<main><a aria-label="Scholar"
      href="https://scholar.google.com/citations?user=wpdiGa0AAAAJ">
      <svg></svg></a></main>"""
    assert linked_scholar_profiles(html, "https://example.edu/erik") == [
        "https://scholar.google.com/citations?user=wpdiGa0AAAAJ"
    ]


def test_official_profile_link_is_an_independent_scholar_identity_signal() -> None:
    professor = {"name": "Erik Fredericks", "institution_name": "Example University",
                 "official_institution_domain": "example.edu"}
    url = "https://scholar.google.com/citations?user=wpdiGa0AAAAJ"
    status, reasons = scholar_identity_decision(
        professor,
        {"name": "Erik Fredericks", "affiliation": "", "verified_email": "",
         "homepage": ""},
        ["https://example.edu/erik"], scholar_url=url,
        official_scholar_urls={url},
    )
    assert status == "VERIFIED"
    assert reasons == ["compatible_name", "linked_from_official_profile"]


def test_scholar_country_domains_dedupe_by_user_id_with_official_url_first() -> None:
    official = "https://scholar.google.com/citations?user=wpdiGa0AAAAJ"
    searched = "https://scholar.google.com.tr/citations?user=wpdiGa0AAAAJ&hl=en"
    assert _dedupe_scholar_profiles([official, searched]) == [official]


def test_only_group_leading_professor_roles_enter_publication_pipeline() -> None:
    assert eligible_research_group_leader("Assistant Professor", "PRIMARY")
    assert eligible_research_group_leader("Research Professor", "RESEARCH")
    assert not eligible_research_group_leader("Adjunct Instructor", "ADJUNCT")
    assert not eligible_research_group_leader("Part-Time Faculty", "PART_TIME")
    assert not eligible_research_group_leader("Adjunct Professor", "ADJUNCT")
    assert not eligible_research_group_leader("Professor Emeritus", "EMERITUS")



def test_branded_institutional_publication_portal_uses_local_publications_label():
    html = """
    <main>
      <h1>Valentina Dargam</h1>
      <p>
        <strong>Publications</strong>
        <a href="https://discovery.fiu.edu/display/person-dargam-valentina">FIU Discovery</a>
      </p>
    </main>
    """
    assert linked_research_pages(
        html,
        "https://cec.fiu.edu/about/directory/profiles/valentina-dargam.html",
        "Valentina Dargam",
    ) == ["https://discovery.fiu.edu/display/person-dargam-valentina"]


def test_nested_scholarly_works_categories_do_not_stop_publication_extraction():
    html = """
    <main>
      <h2>Scholarly &amp; Creative Works</h2>
      <h3>selected scholarly works &amp; creative activities</h3>
      <h4>Article</h4>
      <p>2025 Phosphate salt selection affects mortality and vascular calcification in mice.
      AMERICAN JOURNAL OF PHYSIOLOGY-HEART AND CIRCULATORY PHYSIOLOGY.
      Full Text via DOI: 10.1152/ajpheart.00534.2025</p>
      <h4>Conference</h4>
      <p>2024 Peripheral hemodynamic correlation changes in mice with vascular calcification.
      Full Text via DOI: 10.1364/translational.2024.jm4a.2</p>
      <h3>principal investigator on</h3>
      <p>Modeling Lead and Cadmium Cardiotoxicity awarded 2025-2026.</p>
    </main>
    """
    papers = extract_publications(
        html,
        "https://discovery.fiu.edu/display/person-dargam-valentina",
        "INSTITUTIONAL_RESEARCH_PORTAL",
        "Valentina Dargam",
    )
    assert len(papers) == 2
    assert {paper.doi for paper in papers} == {
        "10.1152/ajpheart.00534.2025",
        "10.1364/translational.2024.jm4a.2",
    }
    assert all("Modeling Lead" not in paper.evidence for paper in papers)
