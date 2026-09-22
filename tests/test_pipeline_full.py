from __future__ import annotations

"""
ScholarRadar full pipeline contract/regression tests.

Run normal deterministic tests:
    pytest -vv -s tests/test_pipeline_full.py

Run one stage only:
    pytest -vv -s tests/test_pipeline_full.py -k stage03

Run current official webpages through the parser:
    RUN_LIVE_PIPELINE_TESTS=1 \
    pytest -vv -s tests/test_pipeline_full.py -k live

Run read-only checks against the configured database:
    RUN_DB_PIPELINE_DIAGNOSTICS=1 \
    pytest -vv -s tests/test_pipeline_full.py -k db

Run EVERYTHING:
    RUN_LIVE_PIPELINE_TESTS=1 \
    RUN_DB_PIPELINE_DIAGNOSTICS=1 \
    pytest -vv -s tests/test_pipeline_full.py
"""

import inspect
import json
import os
import re
from dataclasses import asdict, is_dataclass

import pytest
import requests
from bs4 import BeautifulSoup


# ============================================================================
# TEST OUTPUT HELPERS
# ============================================================================


def _jsonable(value):
    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]

    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def checkpoint(
    stage: str,
    *,
    input_data,
    expected,
    actual,
    explanation: str = "",
):
    """
    Print the actual pipeline state before asserting.

    This is deliberately verbose because these tests are intended to answer:

        WHERE did the professor first become wrong?

    rather than only returning PASS / FAIL.
    """

    print("\n")
    print("=" * 88)
    print(f"PIPELINE CHECKPOINT: {stage}")
    print("=" * 88)

    if explanation:
        print("WHAT THIS TEST IS CHECKING:")
        print(explanation)
        print()

    print("INPUT:")
    print(
        json.dumps(
            _jsonable(input_data),
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )

    print("\nEXPECTED:")
    print(
        json.dumps(
            _jsonable(expected),
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )

    print("\nACTUAL:")
    print(
        json.dumps(
            _jsonable(actual),
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


def assert_equal(stage: str, key: str, actual, expected):
    assert actual == expected, (
        f"\n{stage}\n"
        f"{key}\n"
        f"EXPECTED: {expected!r}\n"
        f"ACTUAL:   {actual!r}"
    )


def assert_true(stage: str, key: str, actual):
    assert actual, (
        f"\n{stage}\n"
        f"{key}\n"
        f"EXPECTED: truthy\n"
        f"ACTUAL:   {actual!r}"
    )


def require_live():
    if os.getenv("RUN_LIVE_PIPELINE_TESTS") != "1":
        pytest.skip(
            "Set RUN_LIVE_PIPELINE_TESTS=1 to run current webpages."
        )


def require_db():
    if os.getenv("RUN_DB_PIPELINE_DIAGNOSTICS") != "1":
        pytest.skip(
            "Set RUN_DB_PIPELINE_DIAGNOSTICS=1 "
            "to run read-only database diagnostics."
        )


# ============================================================================
# GOLDEN HTML FIXTURES
# ============================================================================


GSU_TABLE_HTML = """
<html>
<head>
<title>
Department of English Faculty Directory |
Georgia State University
</title>
</head>
<body>
<main>
<h1>Faculty Profile Directory</h1>

<table>
<tr>
    <th>Name</th>
    <th>Dept/Office</th>
    <th>Title</th>
    <th>Email</th>
    <th>Phone</th>
</tr>

<tr>
    <td>
        <a href="/profile/constance-bailey">
            Bailey, Constance
        </a>
    </td>
    <td>English</td>
    <td>Assistant Professor</td>
    <td>
        <a href="mailto:cbailey@example.edu">
            cbailey@example.edu
        </a>
    </td>
    <td>404-555-0001</td>
</tr>

<tr>
    <td>
        <a href="/profile/anna-barattin">
            Barattin, Anna
        </a>
    </td>
    <td>English</td>
    <td>Lecturer</td>
    <td>
        <a href="mailto:abarattin@example.edu">
            abarattin@example.edu
        </a>
    </td>
    <td>404-555-0002</td>
</tr>

<tr>
    <td>
        <a href="/profile/paul-voss">
            Voss, Paul
        </a>
    </td>
    <td>English</td>
    <td>Associate Professor</td>
    <td>
        <a href="mailto:pvoss@example.edu">
            pvoss@example.edu
        </a>
    </td>
    <td>404-555-0003</td>
</tr>

<tr>
    <td>
        <a href="/profile/heather-russel">
            Russel, Heather
        </a>
    </td>
    <td>English</td>
    <td>Department Specialist</td>
    <td>
        <a href="mailto:hrussel@example.edu">
            hrussel@example.edu
        </a>
    </td>
    <td>404-555-0004</td>
</tr>

</table>
</main>
</body>
</html>
"""


GVSU_RESEARCH_DIRECTORY_HTML = """
<html>
<head>
<title>
College of Computing Faculty & Staff Directory |
Grand Valley State University
</title>
</head>
<body>
<main>
<h1>College of Computing Faculty & Staff Directory</h1>

<table>
<tr>
    <th>First Name</th>
    <th>Last Name</th>
    <th>Office Address</th>
    <th>Department</th>
    <th>Email</th>
    <th>Expertise</th>
    <th>Bio & Semester Schedule</th>
</tr>

<tr>
    <td>Sara</td>
    <td>Sutton</td>
    <td>MAK D-2-222</td>
    <td>Information Sciences & Technologies (IST)</td>
    <td>
        <a href="mailto:sutton@example.edu">
            sutton@example.edu
        </a>
    </td>
    <td>
        Cybersecurity, Privacy, Security,
        Artificial Intelligence, Data Science,
        IoT, Cyber-Physical Systems
    </td>
    <td>
        <a href="/computing/sutton-sara-73">
            Meet Dr. Sutton
        </a>
    </td>
</tr>

<tr>
    <td>Samah</td>
    <td>Mansour</td>
    <td>CHS 162</td>
    <td>Information Sciences & Technologies (IST)</td>
    <td>
        <a href="mailto:mansour@example.edu">
            mansour@example.edu
        </a>
    </td>
    <td>
        Cybersecurity, Privacy, Security,
        IoT Security & Authorization,
        IoV Security, Machine Learning,
        Federated Learning, Virtual Reality
    </td>
    <td>
        <a href="/computing/mansour-samah-62">
            Meet Dr. Mansour
        </a>
    </td>
</tr>

<tr>
    <td>Erik</td>
    <td>Fredericks</td>
    <td>MAK D-2-210</td>
    <td>Computer Science (CS)</td>
    <td>
        <a href="mailto:fredericks@example.edu">
            fredericks@example.edu
        </a>
    </td>
    <td>
        Software Engineering & Programming Languages,
        Evolutionary Computation,
        Graphics / Visualization
    </td>
    <td>
        <a href="/computing/fredericks-erik-44">
            Meet Dr. Fredericks
        </a>
    </td>
</tr>

<tr>
    <td>John</td>
    <td>Reynolds</td>
    <td>MAK C-2-308</td>
    <td>Information Sciences & Technologies (IST)</td>
    <td>
        <a href="mailto:reynolds@example.edu">
            reynolds@example.edu
        </a>
    </td>
    <td>
        Accessibility & CS Pedagogy,
        STEM Education
    </td>
    <td>
        <a href="/computing/reynolds-john">
            Meet Dr. Reynolds
        </a>
    </td>
</tr>

</table>
</main>
</body>
</html>
"""


CSUSB_FACULTY_HTML = """
<html>
<head>
<title>
Communication and Media Faculty |
California State University, San Bernardino
</title>
</head>
<body>
<main>
<h1>Faculty</h1>
<h2>Full Time Faculty</h2>

<article class="faculty-card">
    <h3>
        <a href="/communication-media/faculty/ece-algan">
            Ece Algan
        </a>
    </h3>
    <p>Professor</p>
</article>

<article class="faculty-card">
    <h3>
        <a href="/communication-media/faculty/raisa-alvarado">
            Raisa Alvarado
        </a>
    </h3>
    <p>Associate Professor</p>
</article>

<article class="faculty-card">
    <h3>
        <a href="/communication-media/faculty/chandler-bahk">
            Chandler Bahk
        </a>
    </h3>
    <p>Professor</p>
</article>

</main>
</body>
</html>
"""


SARA_PROFILE_HTML = """
<html>
<head>
<title>Sara Sutton | Grand Valley State University</title>
</head>
<body>
<main>
<h1>Sara Sutton, Ph.D.</h1>

<p>
Assistant Professor,
Department of Information Sciences & Technologies
</p>

<p>
Grand Valley State University
</p>

<a href="mailto:sutton@example.edu">
sutton@example.edu
</a>

<h2>Biography</h2>

<p>
Dr. Sara Sutton's research focuses on advancing
cybersecurity in modern computing environments.
</p>

</main>
</body>
</html>
"""


WRONG_PROFILE_HTML = """
<html>
<head>
<title>Michael Brown | Example University</title>
</head>
<body>
<main>
<h1>Michael Brown</h1>
<p>Associate Professor</p>
<p>Example University</p>
</main>
</body>
</html>
"""


GVSU_SHARED_HIRING_HTML = """
<html>
<body>
<main>

<h3>Erik Fredericks</h3>
<p>
It would be great to have a graduate student
interested in search-based software engineering
and robotics.
</p>

<h3>Rahat Ibn Rafiq</h3>
<p>
I'd love to have a graduate student help me
build interactive systems leveraging natural
language processing and machine learning.
</p>

<h3>Sara Sutton</h3>
<p>
I am looking for a graduate student to research
and work on Machine Learning and Cybersecurity
issues related to Cloud Computing and the
Internet of Things.
</p>

<h3>Another Faculty Member</h3>
<p>
Graduate students enrolled in our degree program
must complete thirty credits.
</p>

</main>
</body>
</html>
"""


PUBLICATION_HTML = """
<html>
<body>
<main>

<h2>Publications</h2>
<ul>

<li>
Partha Pratim Pande, John Doe.
Machine Learning for Heterogeneous Manycore Design.
IEEE Transactions on Computers, 2025.
</li>

<li>
PROGRAM COMMITTEE (CASES)
</li>

<li>
Invited Speaker, International Symposium, 2024
</li>

</ul>

</main>
</body>
</html>
"""


# ============================================================================
# CATEGORY HELPER — NO DATABASE REQUIRED
# ============================================================================


def category_by_name(name: str) -> dict:
    from ingestion.research_classification import category_definitions

    for definition in category_definitions():
        if definition.name.casefold() == name.casefold():
            return {
                "category_key": definition.key,
                "canonical_name": definition.name,
                "description": definition.description,
                "aliases": list(definition.aliases),
                "positive_terms": list(definition.positive_terms),
                "exclusion_terms": list(definition.exclusions),
                "breadth": definition.breadth,
            }

    raise AssertionError(f"Research category not found: {name}")


# ============================================================================
# STAGE 01
# OFFICIAL DIRECTORY DISCOVERY
# ============================================================================


def test_stage01_directory_candidate_url():
    from ingestion.university_directory_discovery import _candidate_url

    good = _candidate_url(
        "https://english.gsu.edu/directory/",
        "english.gsu.edu",
    )

    news = _candidate_url(
        "https://english.gsu.edu/news/faculty-award/",
        "english.gsu.edu",
    )

    external = _candidate_url(
        "https://evil.example.com/faculty/",
        "english.gsu.edu",
    )

    actual = {
        "official_directory": good,
        "news_page": news,
        "external_domain": external,
    }

    expected = {
        "official_directory":
            "https://english.gsu.edu/directory",
        "news_page": "",
        "external_domain": "",
    }

    checkpoint(
        "STAGE 01 — DIRECTORY DISCOVERY",
        input_data={
            "institution_domain": "english.gsu.edu",
            "candidate_urls": [
                "official directory",
                "news page",
                "external site",
            ],
        },
        expected=expected,
        actual=actual,
        explanation=(
            "Only a plausible official-domain faculty/directory "
            "URL should enter the roster pipeline."
        ),
    )

    assert_equal(
        "STAGE 01",
        "official directory",
        good,
        expected["official_directory"],
    )
    assert_equal("STAGE 01", "news", news, "")
    assert_equal("STAGE 01", "external", external, "")


# ============================================================================
# STAGE 02
# FACULTY PAGE CLASSIFICATION
# ============================================================================


def test_stage02_gsu_page_is_approved_roster():
    from ingestion.faculty_roster import classify_faculty_page

    result = classify_faculty_page(
        GSU_TABLE_HTML,
        "https://english.gsu.edu/directory/",
    )

    actual = {
        "status": result.status,
        "page_type": result.page_type,
        "reason": result.reason,
        "member_count": len(result.members),
    }

    checkpoint(
        "STAGE 02 — GSU PAGE CLASSIFICATION",
        input_data="GSU-style table with Name / Dept/Office / Title",
        expected={
            "status": "APPROVED_ROSTER",
            "member_count": ">= 3",
        },
        actual=actual,
        explanation=(
            "A real departmental faculty/profile directory should "
            "not become UNCERTAIN_REQUIRES_REVIEW."
        ),
    )

    assert_equal(
        "STAGE 02 GSU",
        "status",
        result.status,
        "APPROVED_ROSTER",
    )

    assert len(result.members) >= 3


def test_stage02_csusb_explicit_faculty_page_is_approved():
    from ingestion.faculty_roster import classify_faculty_page

    result = classify_faculty_page(
        CSUSB_FACULTY_HTML,
        "https://www.csusb.edu/communication-media/faculty",
    )

    actual = {
        "status": result.status,
        "page_type": result.page_type,
        "reason": result.reason,
        "members": [
            {
                "name": member.name,
                "title": member.title,
            }
            for member in result.members
        ],
    }

    checkpoint(
        "STAGE 02 — EXPLICIT FACULTY PAGE",
        input_data="Faculty → Full Time Faculty → repeated professor cards",
        expected={
            "status": "APPROVED_ROSTER",
            "members": [
                "Ece Algan",
                "Raisa Alvarado",
                "Chandler Bahk",
            ],
        },
        actual=actual,
        explanation=(
            "An explicit official university Faculty page with "
            "repeated attributable professor cards should not be "
            "sent to staff review."
        ),
    )

    assert_equal(
        "STAGE 02 CSUSB",
        "status",
        result.status,
        "APPROVED_ROSTER",
    )


# ============================================================================
# STAGE 03
# ROSTER ROW/TABLE PARSING
# ============================================================================


def test_stage03_gsu_table_preserves_title_and_department():
    from ingestion.faculty_roster import parse_faculty_directory

    members = parse_faculty_directory(
        GSU_TABLE_HTML,
        "https://english.gsu.edu/directory/",
    )

    bailey = next(
        (
            member
            for member in members
            if member.name == "Constance Bailey"
        ),
        None,
    )

    assert bailey is not None, (
        "GSU row was not extracted at all. "
        "Failure is in table roster parsing."
    )

    actual = {
        "name": bailey.name,
        "title": bailey.title,
        "department": bailey.section_heading,
        "appointment_type": bailey.appointment_type,
        "email": bailey.email,
    }

    expected = {
        "name": "Constance Bailey",
        "title": "Assistant Professor",
        "department": "English",
        "appointment_type": "PRIMARY",
    }

    checkpoint(
        "STAGE 03 — GSU STRUCTURED TABLE ROW",
        input_data={
            "Name": "Bailey, Constance",
            "Dept/Office": "English",
            "Title": "Assistant Professor",
        },
        expected=expected,
        actual=actual,
        explanation=(
            "This catches the exact GSU bug: the source table has "
            "the Title and Dept/Office fields, so neither is allowed "
            "to disappear during parsing."
        ),
    )

    assert_equal(
        "STAGE 03",
        "name",
        bailey.name,
        "Constance Bailey",
    )

    assert_equal(
        "STAGE 03",
        "title",
        bailey.title,
        "Assistant Professor",
    )

    assert_equal(
        "STAGE 03",
        "Dept/Office → department",
        bailey.section_heading,
        "English",
    )


# ============================================================================
# STAGE 04
# NAME NORMALIZATION / IMAGE ALT TEXT
# ============================================================================


@pytest.mark.parametrize(
    "raw_name,expected_name",
    [
        (
            "Jeff McGuirk headshot.",
            "Jeff McGuirk",
        ),
        (
            "Bassam Shaer headshot.",
            "Bassam Shaer",
        ),
        (
            "Matthew Long headshot.",
            "Matthew Long",
        ),
        (
            "Professor Sara Sutton",
            "Sara Sutton",
        ),
        (
            "Bailey, Constance",
            "Constance Bailey",
        ),
    ],
)
def test_stage04_clean_person_names(
    raw_name,
    expected_name,
):
    from ingestion.faculty_roster import _clean_display_person_name

    actual_name = _clean_display_person_name(raw_name)

    checkpoint(
        "STAGE 04 — PERSON NAME NORMALIZATION",
        input_data={"raw_name": raw_name},
        expected={"clean_name": expected_name},
        actual={"clean_name": actual_name},
        explanation=(
            "Accessibility/image-alt text such as 'headshot' "
            "must never become part of canonical identity."
        ),
    )

    assert_equal(
        "STAGE 04",
        raw_name,
        actual_name,
        expected_name,
    )


# ============================================================================
# STAGE 05
# INDIVIDUAL PROFILE VALIDATION
# ============================================================================


def test_stage05_valid_profile_identity_and_role():
    from ingestion.faculty_roster import (
        RosterMember,
        validate_faculty_profile,
    )

    member = RosterMember(
        name="Sara Sutton",
        title="",
        profile_url="https://www.gvsu.edu/computing/sutton-sara-73",
        appointment_type="PRIMARY",
        excerpt="Sara Sutton",
    )

    status, reason, verified, evidence = validate_faculty_profile(
        SARA_PROFILE_HTML,
        member.profile_url,
        member,
        "Grand Valley State University",
        "gvsu.edu",
    )

    actual = {
        "status": status,
        "reason": reason,
        "verified_name": verified.name,
        "verified_title": verified.title,
        "role_source": evidence.get("role_source"),
    }

    expected = {
        "status": "PROFILE_VERIFIED",
        "verified_name": "Sara Sutton",
        "verified_title": "Assistant Professor",
    }

    checkpoint(
        "STAGE 05 — PROFILE VALIDATION",
        input_data={
            "roster_name": "Sara Sutton",
            "roster_title": "",
            "profile_has": [
                "Sara Sutton",
                "Assistant Professor",
            ],
        },
        expected=expected,
        actual=actual,
        explanation=(
            "A correct official profile should resolve missing "
            "roster title instead of producing ROLE_UNCLEAR."
        ),
    )

    assert_equal(
        "STAGE 05",
        "status",
        status,
        "PROFILE_VERIFIED",
    )

    assert_equal(
        "STAGE 05",
        "title",
        verified.title,
        "Assistant Professor",
    )


def test_stage05_real_name_mismatch_stays_mismatch():
    from ingestion.faculty_roster import (
        RosterMember,
        validate_faculty_profile,
    )

    member = RosterMember(
        name="Sara Sutton",
        title="",
        profile_url="https://example.edu/faculty/sara-sutton",
        appointment_type="PRIMARY",
        excerpt="Sara Sutton",
    )

    status, reason, _, _ = validate_faculty_profile(
        WRONG_PROFILE_HTML,
        member.profile_url,
        member,
        "Example University",
        "example.edu",
    )

    checkpoint(
        "STAGE 05 — TRUE NAME MISMATCH",
        input_data={
            "expected_person": "Sara Sutton",
            "profile_person": "Michael Brown",
        },
        expected={
            "status": "NAME_MISMATCH",
        },
        actual={
            "status": status,
            "reason": reason,
        },
        explanation=(
            "Fixing false name mismatches must not make genuine "
            "identity conflicts automatically pass."
        ),
    )

    assert_equal(
        "STAGE 05",
        "genuine mismatch",
        status,
        "NAME_MISMATCH",
    )


# ============================================================================
# STAGE 06
# RESEARCH-GROUP LEADER ELIGIBILITY
# ============================================================================


@pytest.mark.parametrize(
    "title,appointment_type,expected",
    [
        ("Assistant Professor", "PRIMARY", True),
        ("Associate Professor", "PRIMARY", True),
        ("Professor", "PRIMARY", True),
        ("Research Professor", "RESEARCH", True),

        ("Senior Instructor", "PRIMARY", False),
        ("Lecturer", "PRIMARY", False),
        ("Senior Lecturer", "PRIMARY", False),
        ("Adjunct Professor", "ADJUNCT", False),
        ("Visiting Professor", "VISITING", False),
        ("Professor Emeritus", "EMERITUS", False),
        ("Department Specialist", "PRIMARY", False),
    ],
)
def test_stage06_research_group_leader_eligibility(
    title,
    appointment_type,
    expected,
):
    from ingestion.faculty_roster import (
        eligible_research_group_leader,
    )

    actual = eligible_research_group_leader(
        title,
        appointment_type,
    )

    checkpoint(
        "STAGE 06 — PROFESSOR ELIGIBILITY",
        input_data={
            "title": title,
            "appointment_type": appointment_type,
        },
        expected={
            "eligible_research_group_leader": expected,
        },
        actual={
            "eligible_research_group_leader": actual,
        },
        explanation=(
            "A person can be parsed correctly but still be "
            "out of scope for the research-professor index."
        ),
    )

    assert_equal(
        "STAGE 06",
        title,
        actual,
        expected,
    )


# ============================================================================
# STAGE 07
# ROW-SPECIFIC DEPARTMENT MUST WIN DURING PROMOTION
# ============================================================================


def test_stage07_promotion_uses_row_specific_department():
    from ingestion.faculty_roster import crawl_directory

    source = inspect.getsource(crawl_directory)

    has_row_specific_precedence = bool(
        re.search(
            r"member_department\s*=.*?"
            r"member\.section_heading.*?"
            r"directory\[.?department.?\]",
            source,
            re.S,
        )
    )

    checkpoint(
        "STAGE 07 — PROFESSOR PROMOTION / DEPARTMENT",
        input_data=(
            "Roster row department='English', "
            "generic directory title='Faculty Directory'"
        ),
        expected={
            "row_specific_department_precedes_page_fallback": True,
        },
        actual={
            "row_specific_department_precedes_page_fallback":
                has_row_specific_precedence,
        },
        explanation=(
            "Professor.department must come from the person's "
            "structured row when available, not a generic page title."
        ),
    )

    assert_true(
        "STAGE 07",
        "member.section_heading precedence",
        has_row_specific_precedence,
    )


# ============================================================================
# STAGE 08
# STRUCTURED OFFICIAL RESEARCH INTERESTS
# ============================================================================


def test_stage08_structured_research_interests_preserved():
    from ingestion.faculty_roster import parse_faculty_directory

    members = parse_faculty_directory(
        GVSU_RESEARCH_DIRECTORY_HTML,
        "https://www.gvsu.edu/computing/directory-176",
    )

    sara = next(
        member
        for member in members
        if member.name == "Sara Sutton"
    )

    expected_interests = (
        "Cybersecurity",
        "Privacy",
        "Security",
        "Artificial Intelligence",
        "Data Science",
        "IoT",
        "Cyber-Physical Systems",
    )

    actual = {
        "department": sara.section_heading,
        "research_interests": sara.research_interests,
    }

    expected = {
        "department":
            "Information Sciences & Technologies (IST)",
        "research_interests": expected_interests,
    }

    checkpoint(
        "STAGE 08 — STRUCTURED RESEARCH INTERESTS",
        input_data="GVSU structured Expertise column",
        expected=expected,
        actual=actual,
        explanation=(
            "Structured official interests should survive as "
            "separate evidence units."
        ),
    )

    assert_equal(
        "STAGE 08",
        "department",
        sara.section_heading,
        expected["department"],
    )

    assert_equal(
        "STAGE 08",
        "research interests",
        sara.research_interests,
        expected_interests,
    )


# ============================================================================
# STAGE 09
# PUBLICATION EXTRACTION
# ============================================================================


def test_stage09_publication_extraction_rejects_obvious_service_junk():
    from ingestion.publication_discovery import extract_publications

    papers = extract_publications(
        PUBLICATION_HTML,
        "https://example.edu/pande/publications",
        "PERSONAL_SITE",
        subject_name="Partha Pratim Pande",
    )

    titles = [paper.title for paper in papers]

    actual = {
        "titles": titles,
        "contains_real_ml_paper":
            any(
                "Machine Learning" in title
                for title in titles
            ),
        "contains_program_committee":
            any(
                "PROGRAM COMMITTEE" in title.upper()
                for title in titles
            ),
        "contains_invited_speaker":
            any(
                "INVITED SPEAKER" in title.upper()
                for title in titles
            ),
    }

    expected = {
        "contains_real_ml_paper": True,
        "contains_program_committee": False,
        "contains_invited_speaker": False,
    }

    checkpoint(
        "STAGE 09 — PUBLICATION EXTRACTION",
        input_data=[
            "real publication",
            "PROGRAM COMMITTEE",
            "Invited Speaker",
        ],
        expected=expected,
        actual=actual,
        explanation=(
            "A publications page can contain service/event records. "
            "Being on the page does not make every row a paper."
        ),
    )

    assert actual["contains_real_ml_paper"]
    assert not actual["contains_program_committee"]
    assert not actual["contains_invited_speaker"]


# ============================================================================
# STAGE 10
# PUBLICATION QUALITY GATE
# ============================================================================


@pytest.mark.parametrize(
    "title",
    [
        "PROGRAM COMMITTEE (CASES)",
        "Special Issue on Benchmarking Machine Learning Systems",
        "Invited Speaker at International Symposium",
        "Public Lecture on American Muslims",
        "123 Main Street 404-555-1212 name@example.edu",
    ],
)
def test_stage10_publication_quality_rejects_junk(title):
    from ingestion.publication_quality import (
        REJECT,
        scholar_publication_quality,
    )

    result = scholar_publication_quality(
        title=title,
    )

    checkpoint(
        "STAGE 10 — PUBLICATION QUALITY",
        input_data={"title": title},
        expected={"decision": REJECT},
        actual={
            "decision": result.decision,
            "reasons": result.reasons,
        },
        explanation=(
            "Service records, event records, and contact/address "
            "records must not become research papers."
        ),
    )

    assert_equal(
        "STAGE 10",
        title,
        result.decision,
        REJECT,
    )


def test_stage10_real_scholarly_record_is_accepted():
    from ingestion.publication_quality import (
        ACCEPT,
        scholar_publication_quality,
    )

    result = scholar_publication_quality(
        title=(
            "Machine Learning for Heterogeneous "
            "Manycore Design"
        ),
        authors="Partha Pratim Pande, Jane Doe",
        venue="IEEE Transactions on Computers",
        year=2025,
    )

    checkpoint(
        "STAGE 10 — REAL PUBLICATION QUALITY",
        input_data={
            "title":
                "Machine Learning for Heterogeneous Manycore Design",
            "authors":
                "Partha Pratim Pande, Jane Doe",
            "venue":
                "IEEE Transactions on Computers",
            "year": 2025,
        },
        expected={"decision": ACCEPT},
        actual={
            "decision": result.decision,
            "reasons": result.reasons,
        },
    )

    assert_equal(
        "STAGE 10",
        "real publication",
        result.decision,
        ACCEPT,
    )


# ============================================================================
# STAGE 11
# PAPER LANDING-PAGE METADATA EXTRACTION
# ============================================================================


def test_stage11_paper_metadata_extraction():
    from ingestion.research_classification import extract_page_metadata

    html = """
    <html>
    <head>
        <meta
            name="citation_title"
            content="Machine Learning for Intrusion Detection"
        >
        <meta
            name="citation_abstract"
            content="We use machine learning to detect network intrusions."
        >
        <meta
            name="citation_doi"
            content="10.1000/example"
        >
    </head>
    </html>
    """

    metadata = extract_page_metadata(html)

    checkpoint(
        "STAGE 11 — PAPER METADATA",
        input_data="citation meta tags",
        expected={
            "title":
                "Machine Learning for Intrusion Detection",
            "abstract":
                "We use machine learning to detect network intrusions.",
            "doi":
                "10.1000/example",
        },
        actual=metadata,
        explanation=(
            "Abstract/title metadata must belong to the actual "
            "paper before classification uses it."
        ),
    )

    assert_equal(
        "STAGE 11",
        "title",
        metadata["title"],
        "Machine Learning for Intrusion Detection",
    )


# ============================================================================
# STAGE 12
# RESEARCH CLASSIFICATION
# ============================================================================


def test_stage12_separate_ai_and_security_do_not_fuse():
    from ingestion.research_classification import (
        classify_interest_units,
    )

    category = category_by_name("AI Security")

    result = classify_interest_units(
        category,
        [
            "Artificial Intelligence",
            "Security",
        ],
    )

    checkpoint(
        "STAGE 12 — AI + SECURITY FALSE MERGE",
        input_data=[
            "Artificial Intelligence",
            "Security",
        ],
        expected={
            "AI Security":
                "NOT AUTO_ACCEPTED from separate labels",
        },
        actual=result,
        explanation=(
            "Two separate interests may not be stitched together "
            "to manufacture a narrower specialty."
        ),
    )

    assert result["decision"] != "AUTO_ACCEPTED"


def test_stage12_real_ai_security_paper_is_accepted():
    from ingestion.research_classification import classify_text

    category = category_by_name("AI Security")

    title = (
        "An Empirical Study Using Microsoft Azure "
        "Auto Machine Learning to Detect Zero-Day Attacks"
    )

    result = classify_text(
        category,
        title,
    )

    checkpoint(
        "STAGE 12 — REAL AI SECURITY",
        input_data={"paper_title": title},
        expected={
            "decision": "AUTO_ACCEPTED",
        },
        actual=result,
        explanation=(
            "AI/ML and security concepts coexist inside the same "
            "paper, so this is legitimate combined evidence."
        ),
    )

    assert_equal(
        "STAGE 12",
        "AI Security",
        result["decision"],
        "AUTO_ACCEPTED",
    )


def test_stage12_literal_cybersecurity_is_accepted():
    from ingestion.research_classification import classify_text

    category = category_by_name("Cybersecurity")

    result = classify_text(
        category,
        "Cybersecurity",
        evidence_kind="explicit_interest",
    )

    checkpoint(
        "STAGE 12 — CYBERSECURITY LABEL",
        input_data="Cybersecurity",
        expected={"decision": "AUTO_ACCEPTED"},
        actual=result,
    )

    assert_equal(
        "STAGE 12",
        "Cybersecurity",
        result["decision"],
        "AUTO_ACCEPTED",
    )


def test_stage12_rpa_is_not_robotics():
    from ingestion.research_classification import classify_text

    category = category_by_name("Robotics")

    result = classify_text(
        category,
        (
            "Robotic Process Automation "
            "Implementation Case Studies in Accounting"
        ),
    )

    checkpoint(
        "STAGE 12 — RPA FALSE ROBOTICS MATCH",
        input_data=(
            "Robotic Process Automation "
            "Implementation Case Studies in Accounting"
        ),
        expected={
            "Robotics":
                "NOT AUTO_ACCEPTED",
        },
        actual=result,
    )

    assert result["decision"] != "AUTO_ACCEPTED"


def test_stage12_real_robotics_is_accepted():
    from ingestion.research_classification import classify_text

    category = category_by_name("Robotics")

    result = classify_text(
        category,
        (
            "Developing a robotic dog for "
            "run-time software engineering research"
        ),
    )

    checkpoint(
        "STAGE 12 — REAL ROBOTICS",
        input_data=(
            "Developing a robotic dog for "
            "run-time software engineering research"
        ),
        expected={"decision": "AUTO_ACCEPTED"},
        actual=result,
    )

    assert_equal(
        "STAGE 12",
        "Robotics",
        result["decision"],
        "AUTO_ACCEPTED",
    )


def test_stage12_parent_field_explicit_interest():
    from ingestion.research_classification import classify_text

    category = category_by_name("Physics")

    result = classify_text(
        category,
        "Condensed Matter Physics",
        evidence_kind="explicit_interest",
    )

    checkpoint(
        "STAGE 12 — PHYSICS SUBFIELD",
        input_data="Condensed Matter Physics",
        expected={
            "Physics": "AUTO_ACCEPTED",
        },
        actual=result,
    )

    assert_equal(
        "STAGE 12",
        "Physics",
        result["decision"],
        "AUTO_ACCEPTED",
    )


def test_stage12_organization_name_not_education_research():
    from ingestion.research_classification import classify_text

    category = category_by_name("Education")

    result = classify_text(
        category,
        "American Council on Education",
        evidence_kind="explicit_interest",
    )

    checkpoint(
        "STAGE 12 — EDUCATION FALSE POSITIVE",
        input_data="American Council on Education",
        expected={
            "Education":
                "NOT AUTO_ACCEPTED",
        },
        actual=result,
    )

    assert result["decision"] != "AUTO_ACCEPTED"


# ============================================================================
# STAGE 13
# TOPIC PAPER SCORING
# ============================================================================


def test_stage13_topic_scoring_related_vs_unrelated():
    from ingestion.roster_topic_index import score_paper

    related_score, related_reason = score_paper(
        "Cybersecurity",
        "Introducing Zero Trust in a Cybersecurity Course",
    )

    unrelated_score, unrelated_reason = score_paper(
        "Cybersecurity",
        "Victorian Poetry and Nineteenth Century Literature",
    )

    checkpoint(
        "STAGE 13 — TOPIC PAPER SCORING",
        input_data={
            "topic": "Cybersecurity",
            "related":
                "Introducing Zero Trust in a Cybersecurity Course",
            "unrelated":
                "Victorian Poetry and Nineteenth Century Literature",
        },
        expected={
            "related_score": "> unrelated_score",
        },
        actual={
            "related_score": related_score,
            "related_reason": related_reason,
            "unrelated_score": unrelated_score,
            "unrelated_reason": unrelated_reason,
        },
    )

    assert related_score > unrelated_score


# ============================================================================
# STAGE 14
# HIRING LANGUAGE DETECTION
# ============================================================================


@pytest.mark.parametrize(
    "text",
    [
        (
            "I am looking for a graduate student "
            "to work on machine learning."
        ),
        (
            "I'd love to have a graduate student "
            "help me build interactive systems."
        ),
        (
            "It would be great to have a graduate student "
            "interested in this project."
        ),
    ],
)
def test_stage14_hiring_language_positive(text):
    from ingestion.matchers import clean_and_extract_hiring_quote

    result = clean_and_extract_hiring_quote(text)

    checkpoint(
        "STAGE 14 — HIRING LANGUAGE",
        input_data=text,
        expected={
            "hiring_quote": "non-empty",
        },
        actual={
            "hiring_quote": result,
        },
        explanation=(
            "Natural recruiting language should be recognized, "
            "not only phrases containing the word 'recruiting'."
        ),
    )

    assert result


def test_stage14_program_description_is_not_hiring():
    from ingestion.matchers import clean_and_extract_hiring_quote

    text = (
        "Graduate students enrolled in the program "
        "must complete 30 credits."
    )

    result = clean_and_extract_hiring_quote(text)

    checkpoint(
        "STAGE 14 — NON-HIRING GRADUATE-STUDENT TEXT",
        input_data=text,
        expected={
            "hiring_quote": "",
        },
        actual={
            "hiring_quote": result,
        },
    )

    assert_equal(
        "STAGE 14",
        "non-hiring text",
        result,
        "",
    )


# ============================================================================
# STAGE 15
# SHARED HIRING PAGE — PROFESSOR-SCOPED ATTRIBUTION
# ============================================================================


def _snapshot_from_hiring_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    blocks = []

    for node in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p"]
    ):
        text = " ".join(
            node.get_text(" ", strip=True).split()
        )

        if text:
            blocks.append({
                "tag": node.name,
                "text": text,
            })

    all_text = " ".join(
        soup.get_text(" ", strip=True).split()
    )

    sentences = [
        " ".join(value.split())
        for value in re.split(
            r"(?<=[.!?])\s+",
            all_text,
        )
        if value.strip()
    ]

    return {
        "accessible": True,
        "url":
            "https://www.gvsu.edu/computing/"
            "faculty-research-opportunities-159",
        "final_url":
            "https://www.gvsu.edu/computing/"
            "faculty-research-opportunities-159",
        "title": "Faculty Research Opportunities",
        "sentences": sentences,
        "gpa_sentences": [],
        "blocks": blocks,
        "links": [],
    }


def test_stage15_shared_page_sara_gets_only_sara_text():
    from ingestion.parse_hiring_signals import (
        _scoped_professor_sentences,
    )

    snapshot = _snapshot_from_hiring_html(
        GVSU_SHARED_HIRING_HTML
    )

    professor = {
        "id": 1,
        "name": "Sara Sutton",
        "institution_name":
            "Grand Valley State University",
    }

    sentences = _scoped_professor_sentences(
        professor,
        snapshot,
    )

    joined = " ".join(sentences)

    actual = {
        "sentences": sentences,
        "contains_sara_statement":
            "looking for a graduate student" in joined.casefold(),
        "contains_erik_statement":
            "it would be great" in joined.casefold(),
        "contains_rahat_statement":
            "i'd love" in joined.casefold(),
    }

    expected = {
        "contains_sara_statement": True,
        "contains_erik_statement": False,
        "contains_rahat_statement": False,
    }

    checkpoint(
        "STAGE 15 — SHARED PAGE ATTRIBUTION / SARA",
        input_data="GVSU shared faculty opportunity page",
        expected=expected,
        actual=actual,
        explanation=(
            "A professor may receive only the recruiting statement "
            "inside their own block."
        ),
    )

    assert actual["contains_sara_statement"]
    assert not actual["contains_erik_statement"]
    assert not actual["contains_rahat_statement"]


def test_stage15_shared_page_erik_does_not_get_sara_signal():
    from ingestion.parse_hiring_signals import (
        _scoped_professor_sentences,
    )

    snapshot = _snapshot_from_hiring_html(
        GVSU_SHARED_HIRING_HTML
    )

    professor = {
        "id": 2,
        "name": "Erik Fredericks",
        "institution_name":
            "Grand Valley State University",
    }

    sentences = _scoped_professor_sentences(
        professor,
        snapshot,
    )

    joined = " ".join(sentences).casefold()

    checkpoint(
        "STAGE 15 — SHARED PAGE ATTRIBUTION / ERIK",
        input_data="GVSU shared faculty opportunity page",
        expected={
            "contains_erik_statement": True,
            "contains_sara_statement": False,
        },
        actual={
            "sentences": sentences,
            "contains_erik_statement":
                "it would be great" in joined,
            "contains_sara_statement":
                "i am looking for a graduate student" in joined,
        },
    )

    assert "it would be great" in joined
    assert "i am looking for a graduate student" not in joined


# ============================================================================
# STAGE 15B
# COMPLETE SINGLE-PROFESSOR HIRING EXTRACTION
# ============================================================================


def test_stage15b_process_single_professor_shared_hiring_page(
    monkeypatch,
):
    import ingestion.parse_hiring_signals as hiring

    snapshot = _snapshot_from_hiring_html(
        GVSU_SHARED_HIRING_HTML
    )

    monkeypatch.setattr(
        hiring,
        "_fetch_hiring_page_snapshot",
        lambda url: snapshot,
    )

    professor = {
        "id": 123,
        "name": "Sara Sutton",
        "institution_name":
            "Grand Valley State University",
        "official_institution_domain":
            "gvsu.edu",
        "official_faculty_source_url":
            "https://www.gvsu.edu/computing/"
            "faculty-research-opportunities-159",
        "faculty_source_url": "",
        "prior_hiring_sources": [],
        "saved_profile_sources": [],
    }

    result = hiring.process_single_professor(
        professor
    )

    signal = result.get("signal")

    actual = {
        "check_status": result.get("check_status"),
        "signal": signal,
        "quote":
            signal[2] if signal else None,
    }

    checkpoint(
        "STAGE 15B — COMPLETE HIRING EXTRACTION",
        input_data={
            "professor": "Sara Sutton",
            "source":
                "shared GVSU research-opportunity page",
        },
        expected={
            "check_status": "PRESENT",
            "quote_contains":
                "looking for a graduate student",
        },
        actual=actual,
        explanation=(
            "This follows the actual process_single_professor() "
            "logic through page attribution and hiring quote extraction."
        ),
    )

    assert signal is not None

    assert (
        "looking for a graduate student"
        in signal[2].casefold()
    )


# ============================================================================
# STAGE 16
# WORKER OUTCOME MAPPING
# ============================================================================


@pytest.mark.parametrize(
    "job_type,result,expected",
    [
        (
            "DISCOVER_FACULTY_DIRECTORIES",
            {"directories": [{"id": 1}]},
            "APPROVED",
        ),
        (
            "DISCOVER_FACULTY_DIRECTORIES",
            {"directories": []},
            "REVIEW_REQUIRED",
        ),
        (
            "CRAWL_FACULTY_DIRECTORY",
            {
                "profiles_pending": 0,
                "profiles_verified": 10,
            },
            "APPROVED",
        ),
        (
            "CRAWL_FACULTY_DIRECTORY",
            {
                "profiles_pending": 3,
                "profiles_verified": 10,
            },
            "REVIEW_REQUIRED",
        ),
        (
            "MATCH_FACULTY_PUBLICATIONS",
            {
                "status":
                    "OFFICIAL_PUBLICATIONS_FOUND",
            },
            "APPROVED",
        ),
        (
            "MATCH_FACULTY_PUBLICATIONS",
            {
                "status":
                    "SOURCE_UNAVAILABLE",
            },
            "SOURCE_UNAVAILABLE",
        ),
        (
            "CHECK_HIRING",
            {
                "timed_out": True,
            },
            "SOURCE_UNAVAILABLE",
        ),
    ],
)
def test_stage16_worker_outcome_mapping(
    job_type,
    result,
    expected,
):
    from ingestion.index_worker import _job_outcome

    actual = _job_outcome(
        job_type,
        result,
    )

    checkpoint(
        "STAGE 16 — WORKER OUTCOME",
        input_data={
            "job_type": job_type,
            "result": result,
        },
        expected={
            "worker_data_outcome": expected,
        },
        actual={
            "worker_data_outcome": actual,
        },
        explanation=(
            "A successfully executed job may still produce "
            "REVIEW_REQUIRED or SOURCE_UNAVAILABLE. "
            "That is different from a technical job failure."
        ),
    )

    assert_equal(
        "STAGE 16",
        job_type,
        actual,
        expected,
    )


# ============================================================================
# STAGE 17
# TECHNICAL PROFILE FAILURES MUST NOT COUNT AS STAFF REVIEW
# ============================================================================


def test_stage17_profile_fetch_failure_excluded_from_staff_review():
    import radar_store

    source = inspect.getsource(
        radar_store.list_radar_operations
    )

    occurrence_count = source.count(
        "ROSTER_CONFIRMED_PROFILE_UNAVAILABLE"
    )

    actual = {
        "exclusion_mentions":
            occurrence_count,
    }

    expected = {
        "exclusion_mentions":
            ">= 2",
    }

    checkpoint(
        "STAGE 17 — TECHNICAL FETCH STATE VS STAFF REVIEW",
        input_data={
            "status":
                "ROSTER_CONFIRMED_PROFILE_UNAVAILABLE",
        },
        expected=expected,
        actual=actual,
        explanation=(
            "HTTPError / ConnectTimeout / ConnectionError are "
            "automatic retry states. They should be excluded from "
            "both the Needs staff review COUNT and the displayed "
            "roster_member_issues query."
        ),
    )

    assert occurrence_count >= 2, (
        "\nROSTER_CONFIRMED_PROFILE_UNAVAILABLE is still "
        "being treated as staff review somewhere in "
        "list_radar_operations()."
    )


# ============================================================================
# STAGE 18
# READ-ONLY DATABASE DIAGNOSTIC
# ============================================================================


def test_stage18_db_staff_review_breakdown():
    require_db()

    from db import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
            SELECT current_database() AS database
            """)

            database = cur.fetchone()["database"]

            cur.execute("""
            SELECT
                COUNT(*) FILTER (
                    WHERE validation_version < 3
                    AND validation_status NOT IN (
                        'PENDING',
                        'PROFILE_VERIFIED',
                        'ROSTER_VERIFIED',
                        'REJECTED',
                        'NOT_A_PERSON',
                        'HISTORICAL_PROFILE',
                        'NOT_GROUP_LEADING_FACULTY'
                    )
                ) AS historical_v2_review,

                COUNT(*) FILTER (
                    WHERE validation_version >= 3
                    AND validation_status='ROLE_UNCLEAR'
                ) AS current_role_unclear,

                COUNT(*) FILTER (
                    WHERE validation_version >= 3
                    AND validation_status='NAME_MISMATCH'
                ) AS current_name_mismatch,

                COUNT(*) FILTER (
                    WHERE validation_status=
                    'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE'
                ) AS technical_profile_unavailable

            FROM roster_member_candidates
            """)

            roster = dict(cur.fetchone())

            cur.execute("""
            SELECT
                COUNT(*) AS pending_identity_reviews
            FROM professor_identity_review_queue
            WHERE status='PENDING'
            """)

            identity = dict(cur.fetchone())

    actual = {
        "database": database,
        **roster,
        **identity,
    }

    checkpoint(
        "STAGE 18 — CURRENT DATABASE REVIEW BACKLOG",
        input_data={
            "configured_database": database,
        },
        expected={
            "historical_v2_review":
                "0 after historical migration/requeue",
            "technical_profile_unavailable":
                "may exist but must NOT count as staff review",
            "current_role_unclear":
                "should become small after parser fixes",
            "current_name_mismatch":
                "small genuine edge-case count expected",
        },
        actual=actual,
        explanation=(
            "This does not mutate the database. "
            "It tells you whether old validator results or "
            "new parser problems are creating the review count."
        ),
    )

    # Historical v2 unresolved decisions should eventually be zero
    # after you run the migration/requeue cleanup.
    assert roster["historical_v2_review"] == 0, (
        "\nHistorical validation-version-2 staff-review rows "
        "still exist. Requeue them with the current validator."
    )


# ============================================================================
# STAGE 19A
# LIVE CURRENT GSU DIRECTORY
# ============================================================================


def test_stage19_live_gsu_directory():
    require_live()

    from ingestion.faculty_roster import parse_faculty_directory

    url = "https://english.gsu.edu/directory/"

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent":
                "ScholarRadar pipeline regression test"
        },
    )

    response.raise_for_status()

    members = parse_faculty_directory(
        response.text,
        url,
    )

    bailey = next(
        (
            member
            for member in members
            if member.name == "Constance Bailey"
        ),
        None,
    )

    actual = {
        "members_found": len(members),
        "constance_bailey":
            None if bailey is None else {
                "name": bailey.name,
                "title": bailey.title,
                "department":
                    bailey.section_heading,
            },
    }

    expected = {
        "Constance Bailey": {
            "title": "Assistant Professor",
            "department": "English",
        },
    }

    checkpoint(
        "STAGE 19 LIVE — GSU CURRENT WEBSITE",
        input_data=url,
        expected=expected,
        actual=actual,
        explanation=(
            "This feeds the current official GSU HTML through "
            "your production parser. It is the strongest regression "
            "test for the massive GSU ROLE_UNCLEAR cluster."
        ),
    )

    assert bailey is not None

    assert_equal(
        "LIVE GSU",
        "title",
        bailey.title,
        "Assistant Professor",
    )

    assert_equal(
        "LIVE GSU",
        "department",
        bailey.section_heading,
        "English",
    )


# ============================================================================
# STAGE 19B
# LIVE CURRENT UWF HEADSHOT ALT TEXT
# ============================================================================


def test_stage19_live_uwf_headshot_name_cleanup():
    require_live()

    from ingestion.faculty_roster import _clean_display_person_name

    url = "https://uwf.edu/hmcse/faculty/"

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent":
                "ScholarRadar pipeline regression test"
        },
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    alt_values = [
        str(node.get("alt") or "")
        for node in soup.find_all("img", alt=True)
    ]

    jeff_alt = next(
        (
            value
            for value in alt_values
            if "Jeff McGuirk" in value
        ),
        None,
    )

    if jeff_alt is None:
        pytest.skip(
            "Current UWF HTML no longer exposes "
            "Jeff McGuirk in image alt text."
        )

    cleaned = _clean_display_person_name(
        jeff_alt
    )

    checkpoint(
        "STAGE 19 LIVE — UWF ALT-TEXT NAME",
        input_data={
            "raw_alt": jeff_alt,
        },
        expected={
            "clean_name": "Jeff McGuirk",
        },
        actual={
            "clean_name": cleaned,
        },
        explanation=(
            "Image accessibility text must not create a false "
            "PROFILE_NAME_DOES_NOT_MATCH_ROSTER result."
        ),
    )

    assert_equal(
        "LIVE UWF",
        "clean name",
        cleaned,
        "Jeff McGuirk",
    )


# ============================================================================
# STAGE 19C
# LIVE CURRENT GVSU SHARED HIRING PAGE
# ============================================================================


def test_stage19_live_gvsu_hiring_attribution():
    require_live()

    from ingestion.parse_hiring_signals import (
        _fetch_hiring_page_snapshot,
        _scoped_professor_sentences,
    )

    url = (
        "https://www.gvsu.edu/computing/"
        "faculty-research-opportunities-159"
    )

    snapshot = _fetch_hiring_page_snapshot(
        url
    )

    sara = {
        "id": 1,
        "name": "Sara Sutton",
        "institution_name":
            "Grand Valley State University",
    }

    rahat = {
        "id": 2,
        "name": "Rahat Ibn Rafiq",
        "institution_name":
            "Grand Valley State University",
    }

    sara_sentences = _scoped_professor_sentences(
        sara,
        snapshot,
    )

    rahat_sentences = _scoped_professor_sentences(
        rahat,
        snapshot,
    )

    sara_text = " ".join(
        sara_sentences
    ).casefold()

    rahat_text = " ".join(
        rahat_sentences
    ).casefold()

    actual = {
        "sara_sentences":
            sara_sentences,
        "rahat_sentences":
            rahat_sentences,
    }

    checkpoint(
        "STAGE 19 LIVE — GVSU HIRING ATTRIBUTION",
        input_data=url,
        expected={
            "Sara":
                "looking for a graduate student",
            "Rahat":
                "love to have a graduate student",
            "cross_attribution":
                False,
        },
        actual=actual,
        explanation=(
            "The current shared page contains multiple professors. "
            "Each recruiting statement must stay with its owner."
        ),
    )

    assert (
        "looking for a graduate student"
        in sara_text
    )

    assert (
        "graduate student"
        in rahat_text
    )

    assert (
        "looking for a graduate student"
        not in rahat_text
    )


# ============================================================================
# FINAL SUMMARY TEST
# ============================================================================


def test_stage99_pipeline_contract_versions():
    import radar_store
    import ingestion.publication_discovery as publication
    import ingestion.research_classification as classification

    actual = {
        "faculty_verification_version":
            radar_store.FACULTY_VERIFICATION_VERSION,
        "research_profile_version":
            radar_store.RESEARCH_PROFILE_VERSION,
        "publication_discovery_version":
            publication.PUBLICATION_DISCOVERY_VERSION,
        "classification_version":
            classification.CLASSIFICATION_VERSION,
    }

    checkpoint(
        "STAGE 99 — CURRENT PIPELINE VERSIONS",
        input_data="Current code",
        expected={
            "faculty_verification_version": 20,
            "research_profile_version": 3,
            "publication_discovery_version": 16,
            "classification_version": 3,
        },
        actual=actual,
        explanation=(
            "If one of these changes later, old review decisions "
            "may become historical and should be considered for "
            "reprocessing instead of manual review."
        ),
    )

    assert_equal(
        "STAGE 99",
        "faculty version",
        actual["faculty_verification_version"],
        20,
    )

    assert_equal(
        "STAGE 99",
        "research profile version",
        actual["research_profile_version"],
        3,
    )

    assert_equal(
        "STAGE 99",
        "publication version",
        actual["publication_discovery_version"],
        16,
    )

    assert_equal(
        "STAGE 99",
        "classification version",
        actual["classification_version"],
        3,
    )
