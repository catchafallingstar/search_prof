from dataclasses import replace

import pytest

from ingestion.faculty_roster import (
    RosterMember, _clean_display_person_name, classify_faculty_page,
    parse_faculty_directory, validate_faculty_profile, eligible_research_group_leader,
)
from ingestion.ollama_evidence import validate_review


@pytest.mark.parametrize('caption,name', [
    ('Amrita Gautam outdoor headshot.', 'Amrita Gautam'),
    ('Jeff McGuirk headshot.', 'Jeff McGuirk'),
    ('Bassam Shaer headshot.', 'Bassam Shaer'),
    ('Algan, Ece', 'Ece Algan'),
])
def test_caption_names(caption, name):
    assert _clean_display_person_name(caption) == name


@pytest.mark.parametrize('header,values', [
    (['Name', 'Title', 'Department'], ['Lovelace, Ada', 'Associate Professor', 'Computing']),
    (['Name', 'Department', 'Title'], ['Lovelace, Ada', 'Computing', 'Associate Professor']),
    (['Name', 'Position', 'Department'], ['Lovelace, Ada', 'Associate Professor', 'Computing']),
])
def test_structured_grid_keeps_column_boundaries(header, values):
    headings = ''.join(f'<div class="vc_column_container">{value}</div>' for value in header)
    rows = ''
    for index in range(3):
        cells = ''.join(f'<div class="vc_column_container">{value}</div>' for value in values)
        cells = cells.replace('Lovelace, Ada', f'<a href="/profile/ada-{index}">Lovelace, Ada</a>')
        rows += f'<div id="profile-inside-row">{cells}</div>'
    html = f'<main><div class="vc_row">{headings}</div><div class="js-wpv-view-layout">{rows}</div></main>'
    members = parse_faculty_directory(html, 'https://example.edu/directory/')
    assert len(members) == 3
    assert all((m.name, m.title, m.section_heading) == ('Ada Lovelace', 'Associate Professor', 'Computing') for m in members)


def test_official_acronym_university_paragraph_roster():
    html = '<title>Faculty | CSUSB</title><main><h1>Faculty</h1>'
    for name in ('Ada Lovelace', 'Grace Hopper', 'Jane Smith'):
        html += f'<p><a href="/profile/{name.replace(" ", "-")}">{name}</a> (Associate Professor)<br>Email</p>'
    result = classify_faculty_page(html + '</main>', 'https://www.csusb.edu/computing/faculty')
    assert result.status == 'APPROVED_ROSTER'
    assert len(result.members) == 3


def test_current_structured_rank_beats_historical_biography():
    member = RosterMember('Ada Lovelace', 'Distinguished University Professor',
                          'https://example.edu/ada', 'PRIMARY', '', structured_title=True)
    result = validate_faculty_profile('<title>Ada Lovelace</title><main><h1>Ada Lovelace</h1><p>Previously an assistant professor.</p></main>', member.profile_url, member, 'Example', 'example.edu')
    assert result[0] == 'PROFILE_VERIFIED'
    assert result[2].title == member.title


@pytest.mark.parametrize('title', ['Business Manager II', 'Circulation Supervisor', 'Emeritus'])
def test_structured_non_group_role_cannot_be_promoted_by_biography(title):
    member = RosterMember('Ada Lovelace', title, 'https://example.edu/ada', 'PRIMARY', '', structured_title=True)
    result = validate_faculty_profile('<title>Ada Lovelace</title><main><h1>Ada Lovelace</h1>Works with Professor Smith.</main>', member.profile_url, member, 'Example', 'example.edu')
    assert result[0] == 'NOT_GROUP_LEADING_FACULTY'


@pytest.mark.parametrize('html', ['<title>Just a moment...</title>Verifying connection', '<html></html>'])
def test_fetch_technical_states_are_not_identity_mismatches(html):
    member = RosterMember('Ada Lovelace', 'Professor', 'https://example.edu/ada', 'PRIMARY', '')
    assert validate_faculty_profile(html, member.profile_url, member, 'Example', 'example.edu')[0] == 'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE'


def test_lists_of_exact_quotes_are_normalized_but_inventions_fail():
    data = {'record_type': 'FACULTY', 'evidence': ['Ada Lovelace', 'Associate Professor']}
    assert validate_review(data, 'Ada Lovelace | Associate Professor') == ()
    assert isinstance(data['evidence'], dict)
    data['evidence'] = ['Invented Professor']
    assert validate_review(data, 'Ada Lovelace | Associate Professor')


def test_compound_teaching_rank_stays_excluded():
    assert not eligible_research_group_leader('Assistant Teaching Professor', 'PRIMARY')
    assert not eligible_research_group_leader('Senior Instructor', 'PRIMARY')


def test_profile_header_keeps_current_rank():
    member = RosterMember('Ada Lovelace', '', 'https://example.edu/ada', 'PRIMARY', '')
    html = '<main><header><h1>Ada Lovelace</h1>Associate Professor</header><p>Previously an assistant professor.</p></main>'
    result = validate_faculty_profile(html, member.profile_url, member, 'Example', 'example.edu')
    assert result[0] == 'PROFILE_VERIFIED'
    assert result[2].title == 'Associate Professor'
