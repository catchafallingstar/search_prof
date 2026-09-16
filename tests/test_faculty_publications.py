from ingestion.publication_discovery import (
    Publication, extract_publications, paper_key, same_person_name,
    scholar_identity_decision, alternate_official_profile_matches,
    linked_scholar_profiles,
    extract_research_interests,
    extract_biography_text,
    _dedupe_scholar_profiles,
    _heading_contains_person_name,
    _is_block_page,
)
from ingestion.faculty_roster import eligible_research_group_leader


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
