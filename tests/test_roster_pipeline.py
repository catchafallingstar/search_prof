from ingestion import faculty_roster
from ingestion.faculty_roster import (
    RosterMember,
    classify_faculty_page,
    eligible_research_group_leader,
    parse_faculty_directory,
    validate_faculty_directory,
    validate_directory_detail,
    validate_faculty_profile,
)
from ingestion.roster_topic_index import score_paper


def test_directory_parser_keeps_roles_attributed_to_each_person() -> None:
    html = """
    <h2>Faculty</h2>
    <div class="card"><a href="/people/ada">Ada Lovelace</a><span>Associate Professor</span></div>
    <div class="card"><a href="/people/grace">Grace Hopper</a><span>Professor Emerita</span></div>
    <h2>Students</h2>
    <div class="card"><a href="/people/alex">Alex Student</a><span>Graduate Assistant</span></div>
    """
    members = parse_faculty_directory(html, "https://example.edu/faculty")
    assert [(m.name, m.appointment_type) for m in members] == [
        ("Ada Lovelace", "PRIMARY"),
        ("Grace Hopper", "EMERITUS"),
    ]


def test_mixed_directory_excludes_explicit_nonfaculty_before_profile_validation() -> None:
    html = """
    <title>College Faculty and Staff Directory</title><main><h1>Directory</h1>
    <div class="card"><a href="/directory/ada">Ada Lovelace</a><span>Professor</span></div>
    <div class="card"><a href="/directory/rit">Ruiting Wang</a><span>PhD Student</span></div>
    <div class="card"><a href="/directory/jamie">Jamie Garlinghouse</a><span>Career Consultant</span></div>
    </main>
    """
    members = parse_faculty_directory(html, "https://example.edu/directory")
    assert [member.name for member in members] == ["Ada Lovelace"]


def test_roster_member_preserves_original_card_html_for_evidence_review() -> None:
    html = """
    <h1>Faculty</h1>
    <div class="card" data-person="ada"><a href="/ada">Ada Lovelace</a> Professor</div>
    <div class="card"><a href="/grace">Grace Hopper</a> Professor</div>
    <div class="card"><a href="/barbara">Barbara Liskov</a> Professor</div>
    """
    members = parse_faculty_directory(html, "https://example.edu/faculty")
    assert 'data-person="ada"' in members[0].raw_card_html


def test_faculty_page_validation_accepts_a_roster() -> None:
    html = """
    <title>Department of Computer Science Faculty</title><h1>Faculty</h1>
    <div><a href="/people/ada">Ada Lovelace</a> Associate Professor</div>
    <div><a href="/people/grace">Grace Hopper</a> Professor</div>
    <div><a href="/people/barbara">Barbara Liskov</a> Professor</div>
    """
    members, reason = validate_faculty_directory(html, "https://example.edu/faculty")
    assert reason == "APPROVED"
    assert len(members) == 3


def test_faculty_page_validation_rejects_awards_and_single_profiles() -> None:
    awards = """
    <title>Faculty Awards</title><h1>Faculty award</h1>
    <div><a href="/people/ada">Ada Lovelace</a> Professor</div>
    <div><a href="/people/grace">Grace Hopper</a> Professor</div>
    <div><a href="/people/barbara">Barbara Liskov</a> Professor</div>
    """
    assert validate_faculty_directory(
        awards, "https://example.edu/awards/faculty"
    )[1] == "NON_DIRECTORY_PATH"
    profile = "<title>Ada Lovelace, Professor</title><h1>Faculty</h1>"
    assert validate_faculty_directory(
        profile, "https://example.edu/people/ada"
    )[1] == "NO_EXTRACTABLE_FACULTY_RECORDS"


def test_directory_parser_rejects_navigation_and_arrow_labels() -> None:
    html = """
    <h2>Faculty</h2>
    <div><a href="/partners">Corporate Partners</a> Professor</div>
    <div><a href="/people/wu">Shandong Wu »</a> Professor</div>
    <div><a href="/people/ada">Ada Lovelace</a> Assistant Professor</div>
    """
    assert [member.name for member in parse_faculty_directory(
        html, "https://example.edu/faculty"
    )] == ["Ada Lovelace"]


def test_employee_resources_page_is_not_a_faculty_roster() -> None:
    html = """
    <title>Faculty &amp; Staff</title><main><h1>Faculty &amp; Staff</h1>
    <p>Resources for faculty and staff.</p>
    <a href="/blackboard">Blackboard Learn</a>
    <h2>News</h2><a href="/news/jordan-smith">Jordan Smith</a>
    <a href="/news/taylor-jones">Taylor Jones</a>
    <a href="/news/morgan-lee">Morgan Lee</a></main>
    """
    members, reason = validate_faculty_directory(
        html, "https://example.edu/home/faculty-staff"
    )
    assert members == []
    assert reason == "PAGE_PURPOSE_IS_NOT_A_FACULTY_ROSTER"


def test_regions_near_a_faculty_heading_never_become_people() -> None:
    html = """
    <title>Faculty</title><h1>Faculty</h1>
    <div><a href="/regions/middle-east">Middle East</a> Professor</div>
    <div><a href="/regions/north-america">North America</a> Professor</div>
    <div><a href="/regions/latin-america">Latin America</a> Professor</div>
    <div><a href="/regions/russia">Russia and Soviet Union</a> Professor</div>
    """
    assert parse_faculty_directory(html, "https://example.edu/faculty") == []


def test_lab_people_page_is_classified_but_never_approved() -> None:
    html = """
    <title>Do Lab People</title><h1>Faculty and researchers</h1>
    <div class="person"><a href="/dolab/ada">Ada Lovelace</a> Professor</div>
    <div class="person"><a href="/dolab/grace">Grace Hopper</a> Professor</div>
    <div class="person"><a href="/dolab/barbara">Barbara Liskov</a> Professor</div>
    """
    result = classify_faculty_page(html, "https://labs.example.edu/dolab/people")
    assert result.page_type == "LAB_MEMBERS"
    assert result.status == "NOT_A_ROSTER"


def test_profile_requires_matching_name_role_and_official_domain() -> None:
    member = RosterMember("Ada Lovelace", "Professor", "https://example.edu/people/ada", "PRIMARY", "Ada Lovelace Professor")
    status, _, verified, evidence = validate_faculty_profile(
        "<title>Ada Lovelace</title><h1>Ada Lovelace</h1><p>Associate Professor</p>",
        member.profile_url, member, "Example University", "example.edu",
    )
    assert status == "PROFILE_VERIFIED"
    assert verified.title == "Associate Professor"
    assert evidence["official_domain"] == "example.edu"
    assert validate_faculty_profile(
        "<title>North America</title><h1>North America</h1><p>Professor</p>",
        member.profile_url, member, "Example University", "example.edu",
    )[0] == "NAME_MISMATCH"


def test_profile_searches_all_main_content_without_storing_whole_page() -> None:
    member = RosterMember("Regina Lopata Logan", "Assistant Professor", "https://example.edu/regina", "PRIMARY", "")
    html = f"<title>Regina Lopata Logan</title><nav>{'navigation ' * 12000}</nav><main><h1>Regina Lopata Logan</h1><p>Assistant Professor of Education.</p></main>"
    status, _, _, evidence = validate_faculty_profile(
        html, member.profile_url, member, "Example University", "example.edu"
    )
    assert status == "PROFILE_VERIFIED"
    assert len(evidence["evidence_excerpt"]) < 1000
    assert "navigation" not in evidence["evidence_excerpt"]


def test_historical_language_must_belong_to_profile_subject() -> None:
    member = RosterMember("Bruce Levitt", "Professor", "https://example.edu/bruce", "PRIMARY", "")
    unrelated = """<title>Bruce Levitt</title><main><h1>Bruce Levitt</h1>
        <p>Professor of Theatre.</p><h2>Remembering Diane Rodriguez</h2>
        <p>Shorna Allred is a former faculty member.</p>
        <p>Bruce Levitt is an award recipient.</p></main>"""
    assert validate_faculty_profile(
        unrelated, member.profile_url, member, "Example University", "example.edu"
    )[0] == "PROFILE_VERIFIED"
    memorial = """<title>In Memoriam: Bruce Levitt</title><main><h1>Bruce Levitt</h1>
        <p>Professor of Theatre.</p></main>"""
    assert validate_faculty_profile(
        memorial, member.profile_url, member, "Example University", "example.edu"
    )[0] == "HISTORICAL_PROFILE"


def test_curated_faculty_groups_are_enrichment_only() -> None:
    html = """<title>Faculty Fellows</title><main><h1>Faculty Fellows</h1>
      <div class="card"><a href="/ada">Ada Lovelace</a> Professor</div>
      <div class="card"><a href="/grace">Grace Hopper</a> Professor</div>
      <div class="card"><a href="/barbara">Barbara Liskov</a> Professor</div></main>"""
    result = classify_faculty_page(html, "https://example.edu/program/faculty-fellows")
    assert result.status == "NOT_A_ROSTER"
    assert result.page_type == "CURATED_FACULTY_GROUP"


def test_server_rendered_table_directory_extracts_names_and_profile_links() -> None:
    html = """<title>Directory - College of Computing - Example University</title>
    <main><h1>College of Computing</h1><table>
      <tr><th>First Name</th><th>Last Name</th><th>Office Address</th><th>Department</th><th>Action</th></tr>
      <tr><td>D. Robert</td><td>Adams</td><td>MAK 1</td><td>Computer Science</td><td><a href="/adams">Meet Dr. Adams</a></td></tr>
      <tr><td>Imtiaz</td><td>Ahmad</td><td>MAK 2</td><td>Computer Science</td><td><a href="/ahmad">View</a></td></tr>
      <tr><td>Shakil</td><td>Ahmed</td><td>MAK 3</td><td>Computer Science</td><td><a href="/ahmed">Profile</a></td></tr>
    </table></main>"""
    members = parse_faculty_directory(html, "https://example.edu/computing/directory")
    assert [member.name for member in members] == ["D. Robert Adams", "Imtiaz Ahmad", "Shakil Ahmed"]
    result = classify_faculty_page(html, "https://example.edu/computing/directory")
    assert result.status == "APPROVED_ROSTER"
    assert result.page_type == "MIXED_PEOPLE_DIRECTORY"


def test_image_link_cards_use_the_local_displayed_name() -> None:
    html = """<title>School of Computing Faculty and Staff Directory</title><main><h1>Faculty</h1>
      <div class="node-chunk-image"><div class="text-center">Dr. Ada Lovelace
        <a class="effect-target" href="/sis/faculty-profile-ada-1"></a></div></div>
      <div class="node-chunk-image"><div class="text-center">Grace Hopper
        <a class="effect-target" href="/sis/faculty-profile-grace-2"></a></div></div>
      <div class="node-chunk-image"><div class="text-center">Barbara Liskov
        <a class="effect-target" href="/sis/faculty-profile-barbara-3"></a></div></div>
    </main>"""
    result = classify_faculty_page(html, "https://example.edu/sis/faculty-and-staff-directory-1")
    assert result.status == "APPROVED_ROSTER"
    assert [member.name for member in result.members] == ["Ada Lovelace", "Grace Hopper", "Barbara Liskov"]


def test_placeholder_image_alt_is_never_a_person_name() -> None:
    html = """<title>School Faculty Directory</title><main><h1>Faculty</h1>
      <div class="node-chunk-image"><img alt="default circle gv logo">
        <a href="/sis/faculty-profile-empty-1"></a></div>
      <div class="node-chunk-image"><div>Ada Lovelace<a href="/sis/faculty-profile-ada-2"></a></div></div>
      <div class="node-chunk-image"><div>Grace Hopper<a href="/sis/faculty-profile-grace-3"></a></div></div>
      <div class="node-chunk-image"><div>Barbara Liskov<a href="/sis/faculty-profile-barbara-4"></a></div></div>
    </main>"""
    members = parse_faculty_directory(html, "https://example.edu/sis/faculty-directory")
    assert [member.name for member in members] == ["Ada Lovelace", "Grace Hopper", "Barbara Liskov"]


def test_generic_profile_image_can_use_person_specific_official_slug() -> None:
    html = """<title>School Faculty Directory</title><main><h1>Faculty</h1>
      <div class="node-chunk-image"><img alt="default circle gv logo">
        <a href="/sis/faculty-profile-kate-fairman-91"></a></div>
      <div class="node-chunk-image"><img alt="default circle gv logo">
        <a href="/sis/faculty-profile-rachel-fox-92"></a></div>
      <div class="node-chunk-image"><img alt="default circle gv logo">
        <a href="/sis/faculty-profile-gamal-gasim-93"></a></div>
    </main>"""
    members = parse_faculty_directory(html, "https://example.edu/sis/faculty-directory")
    assert [member.name for member in members] == ["Kate Fairman", "Rachel Fox", "Gamal Gasim"]


def test_official_embedded_people_feed_keeps_only_explicit_faculty(monkeypatch) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"name": "Ada Lovelace", "title": ["Associate Professor"],
                 "profile_url": "https://people.wsu.edu/profile/ada/", "email": "ada@wsu.edu"},
                {"name": "Grace Hopper", "title": ["Professor"],
                 "profile_url": "https://people.wsu.edu/profile/grace/"},
                {"name": "Barbara Liskov", "title": ["Institute Professor"],
                 "profile_url": "https://people.wsu.edu/profile/barbara/"},
                {"name": "Alex Student", "title": ["Ph.D. Student"],
                 "profile_url": "https://people.wsu.edu/profile/alex/"},
            ]

    monkeypatch.setattr(faculty_roster.requests, "get", lambda *args, **kwargs: Response())
    html = """<title>College Faculty and Staff Directory | Washington State University</title>
      <main><h1>Faculty and Staff Directory</h1>
      <div data-base-url="https://people.wsu.edu" data-directory="32120"></div></main>"""
    result = classify_faculty_page(
        html, "https://murrow.wsu.edu/faculty-staff-directory/", fetch_embedded=True
    )
    assert result.status == "APPROVED_ROSTER"
    assert [member.name for member in result.members] == ["Ada Lovelace", "Grace Hopper", "Barbara Liskov"]


def test_non_roster_faculty_pages_are_rejected_automatically() -> None:
    cases = [
        ("https://business.wsu.edu/faculty-led-programs", "Faculty-Led Programs"),
        ("https://example.edu/people/united-way-campaign-11", "United Way Campaign"),
        ("https://example.edu/accessibility/faculty-staff-disability-support-17", "Faculty & Staff Disability Support"),
        ("https://example.edu/inclusion/faculty-staff-associations", "Faculty Staff Associations"),
    ]
    for url, title in cases:
        result = classify_faculty_page(f"<title>{title}</title><main><h1>{title}</h1></main>", url)
        assert result.status == "NOT_A_ROSTER"
        assert result.reason == "PAGE_PURPOSE_IS_NOT_A_FACULTY_ROSTER"


def test_profile_accepts_explicit_visiting_or_affiliate_faculty_roles() -> None:
    visiting = RosterMember("Abdullah Husain", "", "https://example.edu/husain", "PRIMARY", "")
    result = validate_faculty_profile(
        "<title>Husain, Abdullah - Example University</title><main><h1>Abdullah Husain</h1>"
        "<p>Visiting Faculty, Department of Computer Science</p></main>",
        visiting.profile_url, visiting, "Example University", "example.edu",
    )
    assert result[0] == "PROFILE_VERIFIED"
    assert result[2].appointment_type == "VISITING"

    affiliate = RosterMember("Anirudh Chowdhary", "", "https://example.edu/chowdhary", "AFFILIATE", "")
    result = validate_faculty_profile(
        "<title>Chowdhary, Anirudh - Example University</title><main><h1>Anirudh Chowdhary</h1>"
        "<p>Affiliate Faculty, Department of Information Sciences</p></main>",
        affiliate.profile_url, affiliate, "Example University", "example.edu",
    )
    assert result[0] == "PROFILE_VERIFIED"
    assert result[2].appointment_type == "AFFILIATE"


def test_profile_can_confirm_identity_while_official_roster_supplies_role() -> None:
    member = RosterMember(
        "Jane Smith", "Associate Professor", "https://example.edu/jane", "PRIMARY",
        "Jane Smith — Associate Professor",
    )
    result = validate_faculty_profile(
        "<title>Jane Smith</title><main><h1>Jane Smith</h1><p>Research interests</p></main>",
        member.profile_url, member, "Example University", "example.edu",
    )
    assert result[0] == "PROFILE_VERIFIED"
    assert result[1] == "OFFICIAL_ROSTER_ROLE_PROFILE_IDENTITY"
    assert result[2].title == "Associate Professor"
    assert result[3]["role_source"] == "official_roster"


def test_directory_detail_accepts_part_time_faculty_and_extracts_fields() -> None:
    member = RosterMember(
        "Jane Smith", "", "https://example.edu/directory?record=1", "PRIMARY", "Jane Smith"
    )
    html = """<main><p><strong>First Name</strong>Jane</p>
      <p><strong>Last Name</strong>Smith</p>
      <p><strong>Role(s)</strong>Part-Time Faculty</p>
      <p><strong>Email</strong>jane@example.edu</p>
      <p><strong>Office Address</strong>Hall 101</p></main>"""
    status, reason, checked, evidence = validate_directory_detail(
        html, member, "example.edu"
    )
    assert status == "ROSTER_VERIFIED"
    assert reason == "OFFICIAL_DIRECTORY_DETAIL_RECORD"
    assert checked.appointment_type == "PART_TIME"
    assert checked.email == "jane@example.edu"
    assert evidence["role_source"] == "official_roster_detail"


def test_directory_detail_rejects_explicit_staff_role_without_review() -> None:
    member = RosterMember(
        "Jane Smith", "", "https://example.edu/directory?record=1", "PRIMARY", "Jane Smith"
    )
    html = """<main><p><strong>First Name</strong>Jane</p>
      <p><strong>Last Name</strong>Smith</p>
      <p><strong>Role(s)</strong>Office Coordinator</p></main>"""
    status, reason, _, evidence = validate_directory_detail(html, member, "example.edu")
    assert status == "NOT_A_PERSON"
    assert reason == "DIRECTORY_DETAIL_IS_NOT_FACULTY"
    assert evidence["observed_role"] == "Office Coordinator"


def test_profile_name_allows_middle_name_omission_but_not_different_given_name() -> None:
    member = RosterMember("Rahat Ibn Rafiq", "", "https://example.edu/rafiq", "PRIMARY", "")
    matching = "<title>Rafiq, Rahat</title><main><h1>Rahat Rafiq</h1><p>Associate Professor</p></main>"
    assert validate_faculty_profile(
        matching, member.profile_url, member, "Example University", "example.edu"
    )[0] == "PROFILE_VERIFIED"
    wrong = "<title>Rafiq, James</title><main><h1>James Rafiq</h1><p>Associate Professor</p></main>"
    assert validate_faculty_profile(
        wrong, member.profile_url, member, "Example University", "example.edu"
    )[0] == "NAME_MISMATCH"


def test_topic_score_requires_direct_paper_text() -> None:
    assert score_paper("natural language processing", "Natural language processing for medicine")[0] > 0
    assert score_paper("natural language processing", "Robot grasp planning")[0] == 0


def test_broad_taxonomy_is_not_topic_evidence() -> None:
    score, text = score_paper(
        "ergonomics and musculoskeletal disorders",
        "Reinforcement learning for robot navigation",
        "A broad contribution to artificial intelligence and robotics.",
    )
    assert score == 0
    assert text == ""



def test_albany_style_table_uses_name_column_not_see_profile_action():
    html = """
    <title>Faculty &amp; Staff - Albany State University</title>
    <main><h1>Faculty &amp; Staff</h1>
    <table>
      <tr><th>NAME</th><th>POSITION</th><th>E-MAIL</th><th>PROFILE</th></tr>
      <tr><td>Jain, Ashok</td><td>Professor</td><td>ashok.jain@asurams.edu</td>
          <td><a href="/profiles/ashok-jain">See Profile</a></td></tr>
      <tr><td>Kabir, Md Niamul</td><td>Assistant Professor</td><td>kabir@asurams.edu</td>
          <td><a href="/profiles/md-niamul-kabir">See Profile</a></td></tr>
      <tr><td>Lee, Yong Jin</td><td>Professor</td><td>yong.lee@asurams.edu</td>
          <td><a href="/profiles/yong-jin-lee">See Profile</a></td></tr>
    </table></main>
    """
    members = parse_faculty_directory(
        html,
        "https://www.asurams.edu/academic-affairs/faculty-staff.php",
    )
    assert [member.name for member in members] == [
        "Ashok Jain",
        "Md Niamul Kabir",
        "Yong Jin Lee",
    ]
    assert all(member.name.casefold() != "see profile" for member in members)


def test_action_profile_link_recovers_name_from_same_card():
    html = """
    <title>Faculty Directory</title><main><h1>College Faculty</h1>
      <div class="faculty-card"><h3>Jane Doe</h3><p>Associate Professor</p>
        <a href="/faculty/jane-doe">See Profile</a></div>
      <div class="faculty-card"><h3>John Smith</h3><p>Assistant Professor</p>
        <a href="/faculty/john-smith">View Profile</a></div>
      <div class="faculty-card"><h3>Mary Jones</h3><p>Professor</p>
        <a href="/faculty/mary-jones">Read More</a></div>
    </main>
    """
    members = parse_faculty_directory(html, "https://example.edu/faculty-directory")
    assert [member.name for member in members] == ["Jane Doe", "John Smith", "Mary Jones"]


def test_teaching_professor_tracks_do_not_enter_research_group_pipeline() -> None:
    assert not eligible_research_group_leader(
        "Assistant Teaching Professor", "PRIMARY"
    )
    assert not eligible_research_group_leader(
        "Associate Teaching Professor", "PRIMARY"
    )
    assert not eligible_research_group_leader(
        "Teaching Professor", "PRIMARY"
    )
    assert eligible_research_group_leader(
        "Assistant Professor", "PRIMARY"
    )
    assert eligible_research_group_leader(
        "Research Professor", "RESEARCH"
    )
