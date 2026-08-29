from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import streamlit as st


GPA_LABELS = {
    "no_lab_cutoff": "No hard lab GPA cutoff",
    "program_minimum": "Program minimum applies",
    "exceptions_considered": "Exceptions considered",
    "holistic_review": "Holistic review",
    "not_stated": "GPA policy not stated",
}

SOURCE_LABELS = {
    "verified_post": "Professor posted",
    "university_post": "University posted",
    "public_signal": "Public hiring signal",
}


def configure_page(title: str) -> None:
    st.set_page_config(
        page_title=f"{title} | ScholarRadar",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] { background: #f7faf7; }
        [data-testid="stHeader"] { background: rgba(247,250,247,.88); }
        [data-testid="stSidebar"] { display: none; }
        /* The app does not use Streamlit's sidebar, so its expand arrow would
           open an invisible panel and then appear to vanish. */
        [data-testid="stExpandSidebarButton"] { display: none !important; }
        /* Streamlit's fixed header is about 3.5rem tall. Keep the custom
           navigation below it so the header cannot wash out or intercept it. */
        .block-container { max-width: 1180px; padding-top: 4.25rem; padding-bottom: 4rem; }
        .st-key-sr_nav {
          padding: .2rem 0 1.15rem;
          margin-bottom: .25rem;
          border-bottom: 1px solid rgba(49, 51, 63, .18);
        }
        .st-key-sr_nav button {
          min-height: 2.5rem;
          border: 1px solid rgba(49, 51, 63, .22);
          border-radius: .55rem;
          background: #ffffff;
          color: #253129;
          font-weight: 600;
          box-shadow: 0 1px 2px rgba(20, 40, 28, .04);
          transition: border-color .15s ease, background-color .15s ease,
                      transform .15s ease;
        }
        .st-key-sr_nav .sr-nav-link {
          display: flex;
          min-height: 2.5rem;
          align-items: center;
          justify-content: center;
          border: 1px solid rgba(49, 51, 63, .22);
          border-radius: .55rem;
          background: #ffffff;
          color: #253129;
          font-weight: 600;
          text-decoration: none;
          box-shadow: 0 1px 2px rgba(20, 40, 28, .04);
        }
        .st-key-sr_nav .sr-nav-link:hover,
        .st-key-sr_nav .sr-nav-link:focus-visible {
          border-color: #2f6d4b;
          background: #edf7f0;
          color: #17482e;
        }
        .st-key-sr_nav button:hover,
        .st-key-sr_nav button:focus-visible {
          border-color: #2f6d4b;
          background: #edf7f0;
          color: #17482e;
          transform: translateY(-1px);
        }
        .st-key-nav_home button {
          justify-content: flex-start;
          border-color: transparent;
          background: transparent;
          box-shadow: none;
          font-size: 1.2rem;
          font-weight: 750;
          color: #1e5d3b;
          white-space: nowrap;
        }
        .st-key-nav_home button:hover,
        .st-key-nav_home button:focus-visible {
          border-color: transparent;
          background: transparent;
          color: #17482e;
          transform: none;
        }
        .sr-hero { text-align: center; padding: 3.3rem 1rem 1.6rem; }
        .sr-kicker { color: #2f6d4b; font-weight: 700; letter-spacing: .02em; }
        .sr-hero h1 { max-width: 780px; margin: .5rem auto .8rem; font-size: clamp(2.4rem, 5vw, 4rem); line-height: 1.05; }
        .sr-hero p { max-width: 720px; margin: 0 auto; color: #5b685f; font-size: 1.08rem; }
        div[data-testid="stVerticalBlockBorderWrapper"] { background: white; }
        .sr-evidence { color: #637068; font-size: .86rem; }
        @media (prefers-color-scheme: dark) {
          [data-testid="stAppViewContainer"] { background: #111713; }
          [data-testid="stHeader"] { background: rgba(17,23,19,.88); }
          .st-key-sr_nav { border-bottom-color: rgba(230, 239, 232, .18); }
          .st-key-sr_nav button {
            border-color: rgba(230, 239, 232, .24);
            background: #19221c;
            color: #e4eee7;
          }
          .st-key-sr_nav .sr-nav-link {
            border-color: rgba(230, 239, 232, .24);
            background: #19221c;
            color: #e4eee7;
          }
          .st-key-sr_nav button:hover,
          .st-key-sr_nav button:focus-visible,
          .st-key-sr_nav .sr-nav-link:hover,
          .st-key-sr_nav .sr-nav-link:focus-visible {
            border-color: #91cba4;
            background: #223129;
            color: #ffffff;
          }
          .st-key-nav_home button {
            border-color: transparent;
            background: transparent;
            color: #a9ddba;
          }
          .st-key-nav_home button:hover,
          .st-key-nav_home button:focus-visible {
            border-color: transparent;
            background: transparent;
            color: #d7f3df;
          }
          .sr-kicker { color: #91cba4; }
          .sr-hero p, .sr-evidence { color: #aeb9b1; }
          div[data-testid="stVerticalBlockBorderWrapper"] { background: #19221c; }
        }
        @media (max-width: 760px) {
          .block-container { padding-top: 4rem; }
          .st-key-sr_nav [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def navigation() -> None:
    with st.container(key="sr_nav"):
        brand, browse, post, verify, policies, staff = st.columns(
            [2.5, 1.2, 1.6, 1.4, 1.4, 1.1], vertical_alignment="center"
        )
        if brand.button("◉ ScholarRadar", key="nav_home", width="stretch"):
            st.switch_page("app.py")
        if browse.button("Browse", key="nav_browse", width="stretch"):
            st.switch_page("app.py")
        if post.button("Post an opening", key="nav_post", width="stretch"):
            st.switch_page("pages/1_Post_an_opening.py")
        if verify.button("Verification", key="nav_verify", width="stretch"):
            st.switch_page("pages/2_Verification.py")
        if policies.button("About data", key="nav_policies", width="stretch"):
            st.switch_page("pages/6_Data_and_policies.py")
        staff.markdown(
            '<a class="sr-nav-link" href="/Admin_review">Staff</a>',
            unsafe_allow_html=True,
        )


def demo_opportunities() -> list[dict[str, Any]]:
    return [
        {
            "id": -1,
            "title": "Trustworthy AI PhD researcher",
            "institution_name": "Northlake University",
            "professor_name": "Prof. Maya Chen",
            "research_area": "Machine learning",
            "position_type": "PhD",
            "description": "Research on reliable foundation models, evaluation, and human-centered AI.",
            "funding_status": "confirmed",
            "gpa_policy": "holistic_review",
            "international_eligible": True,
            "start_term": "Fall 2027",
            "application_deadline": date.today() + timedelta(days=90),
            "application_url": "https://example.edu/apply",
            "source_kind": "verified_post",
            "verification_status": "verified",
            "sponsored": False,
            "source_url": "https://example.edu/lab/openings",
            "evidence_text": "Role-verified professor submission.",
            "last_checked_at": None,
        },
        {
            "id": -2,
            "title": "Robotics and embodied learning",
            "institution_name": "Western Tech",
            "professor_name": "Prof. Jordan Lee",
            "research_area": "Robotics",
            "position_type": "PhD",
            "description": "Public lab-page evidence indicates active recruitment in robot learning.",
            "funding_status": "unknown",
            "gpa_policy": "not_stated",
            "international_eligible": None,
            "start_term": "Not stated",
            "application_deadline": None,
            "application_url": "https://example.edu/robotics",
            "source_kind": "public_signal",
            "verification_status": "unclaimed",
            "sponsored": False,
            "source_url": "https://example.edu/robotics",
            "evidence_text": "Lab homepage says it is recruiting PhD students.",
            "last_checked_at": None,
        },
        {
            "id": -3,
            "title": "Funded biomedical data science cohort",
            "institution_name": "Lakeshore State",
            "professor_name": "Graduate School",
            "research_area": "Biomedical data science",
            "position_type": "PhD",
            "description": "Five openings across eight faculty laboratories.",
            "funding_status": "confirmed",
            "gpa_policy": "exceptions_considered",
            "international_eligible": True,
            "start_term": "Fall 2027",
            "application_deadline": date.today() + timedelta(days=110),
            "application_url": "https://example.edu/biomed",
            "source_kind": "university_post",
            "verification_status": "verified",
            "sponsored": True,
            "source_url": "https://example.edu/biomed",
            "evidence_text": "University-verified cohort posting.",
            "last_checked_at": None,
        },
    ]


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


_NON_INSTITUTIONAL_PROFILE_HOSTS = {
    "academia.edu",
    "facebook.com",
    "github.com",
    "google.com",
    "linkedin.com",
    "medium.com",
    "orcid.org",
    "researchgate.net",
    "scholar.google.com",
    "twitter.com",
    "x.com",
}


def is_official_institution_url(value: str) -> bool:
    """Reject social, publication, and personal-profile substitutes."""
    if not is_http_url(value):
        return False
    hostname = (urlparse(value.strip()).hostname or "").casefold().removeprefix("www.")
    return not any(
        hostname == blocked or hostname.endswith(f".{blocked}")
        for blocked in _NON_INSTITUTIONAL_PROFILE_HOSTS
    )


def filter_demo(rows: list[dict[str, Any]], area: str, position: str, gpa_policy: str) -> list[dict[str, Any]]:
    term = area.strip().casefold()
    filtered = []
    for row in rows:
        haystack = " ".join(
            str(row.get(key, "")) for key in ("title", "description", "research_area", "professor_name", "institution_name")
        ).casefold()
        if term and term not in haystack:
            continue
        if position != "All" and row["position_type"] != position:
            continue
        if gpa_policy != "All" and row["gpa_policy"] != gpa_policy:
            continue
        filtered.append(row)
    return filtered


def render_opportunity(row: dict[str, Any]) -> None:
    with st.container(border=True):
        badges = []
        if row.get("verification_status") == "verified":
            badges.append("✅ Faculty role verified")
        badges.append(SOURCE_LABELS.get(row.get("source_kind"), "Opportunity"))
        if row.get("sponsored"):
            badges.append("Sponsored")
        st.caption(" · ".join(badges))
        st.subheader(row["title"])
        st.caption(f"{row.get('professor_name') or 'Research team'} · {row['institution_name']}")
        st.write(row["description"])

        funding = {
            "confirmed": "Full or stated funding confirmed",
            "partial": "Partial funding",
            "unknown": "Funding not confirmed",
        }.get(row.get("funding_status"), "Funding not confirmed")
        eligibility = "International applicants eligible" if row.get("international_eligible") else "International eligibility not confirmed"
        st.write(f"**Funding:** {funding}")
        st.write(f"**GPA policy:** {GPA_LABELS.get(row.get('gpa_policy'), 'Not stated')}")
        st.write(f"**Start:** {row.get('start_term') or 'Not stated'} · **Eligibility:** {eligibility}")

        deadline = row.get("application_deadline")
        if deadline:
            st.write(f"**Deadline:** {deadline:%B %d, %Y}")
        source_url = row.get("source_url")
        evidence = row.get("evidence_text") or "No evidence summary supplied."
        st.caption(evidence)
        if is_http_url(str(row.get("application_url") or "")):
            st.link_button("View opportunity", row["application_url"], width="stretch")
        if source_url and is_http_url(str(source_url)) and source_url != row.get("application_url"):
            st.link_button("View evidence", source_url, width="stretch")


def render_radar_candidate(row: dict[str, Any]) -> None:
    """Render machine-found evidence without presenting it as an approved job ad."""
    with st.container(border=True):
        st.caption(
            f"Unreviewed public signal · {str(row.get('confidence') or 'medium').title()} confidence"
        )
        st.subheader(row["professor_name"] or row["title"])
        st.caption(f"{row['institution_name']} · {row['research_area']}")
        st.write(row.get("evidence_text") or row["description"])
        st.write(f"**Possible role:** {row['position_type']}")
        st.write("**GPA policy:** Not stated—do not assume flexibility from this signal.")
        st.warning("This public-web result is awaiting moderator review and is not a confirmed opening.")
        source_url = str(row.get("source_url") or row.get("application_url") or "")
        if is_http_url(source_url):
            st.link_button("Check original evidence", source_url, width="stretch")


def render_professor_prospect(row: dict[str, Any]) -> None:
    """Render the evidence needed to evaluate one verified faculty match."""
    category = row.get("result_category")
    with st.container(border=True):
        if category == "confirmed_opening":
            st.caption("Posted on ScholarRadar")
        elif category == "public_hiring_signal":
            st.caption("Hiring signal found online")
        elif category == "early_career_funded":
            st.caption("Assistant professor · Active public funding · Hiring unknown")
        elif category == "early_career":
            st.caption("Assistant professor · Hiring and funding not confirmed")
        elif category == "funded_lab":
            st.caption("Established faculty · Active public funding · Hiring unknown")
        else:
            st.caption("Verified faculty research match · Hiring and funding not confirmed")

        st.subheader(row["professor_name"])
        verified_title = str(row.get("faculty_title") or "Faculty").strip()
        st.caption(
            f"✅ Faculty role verified · {verified_title} · {row['institution_name']}"
        )
        if row.get("latest_paper_title"):
            year = f" ({row['latest_paper_year']})" if row.get("latest_paper_year") else ""
            st.write(f"**Recent matching paper:** {row['latest_paper_title']}{year}")
            st.caption(f"Matching papers: {row.get('matching_papers') or 0}")
        if row.get("grant_title"):
            st.write(f"**Active grant:** {row['grant_title']} — {row.get('funder') or 'public funder'}")
        elif row.get("grant_sources_checked"):
            st.caption("Funding: no active matching NSF award found")
        else:
            st.caption("Funding: not checked yet")
        if row.get("hiring_evidence"):
            label = "Opening details" if category == "confirmed_opening" else "Hiring text"
            st.write(f"**{label}:**")
            st.write(f"“{row['hiring_evidence']}”")
            if category == "public_hiring_signal":
                st.caption("Check the linked source before contacting the professor.")
        elif row.get("hiring_refresh_needed") or row.get("hiring_check_pending"):
            st.caption("Hiring: checking public pages…")
        elif row.get("public_hiring_check_status") == "SOURCE_UNAVAILABLE":
            st.caption("Hiring: source page could not be checked")
        elif row.get("public_sources_checked"):
            st.caption("Hiring: no public statement found")
        else:
            st.caption("Hiring: not checked yet")

        gpa_policy = str(row.get("lab_gpa_policy") or "not_stated")
        if row.get("hiring_refresh_needed") or row.get("hiring_check_pending"):
            st.write("**Lab GPA:** Checking public pages…")
        elif gpa_policy == "no_lab_cutoff":
            st.write("**Lab GPA:** No lab minimum stated by the source")
        elif gpa_policy == "holistic_review":
            st.write("**Lab GPA:** Holistic review stated by the source")
        elif gpa_policy == "minimum" and row.get("lab_gpa_minimum") is not None:
            st.write(f"**Lab GPA:** Minimum {float(row['lab_gpa_minimum']):.2f}")
        elif gpa_policy == "exceptions_considered":
            st.write("**Lab GPA:** Exceptions may be considered")
        elif row.get("gpa_last_checked_at"):
            st.write("**Lab GPA:** Not stated on the pages checked")
        else:
            st.write("**Lab GPA:** Not checked yet")
        if row.get("lab_gpa_evidence_text"):
            st.caption(f"GPA evidence: {row['lab_gpa_evidence_text']}")
        if row.get("program_gpa_minimum") is not None:
            st.write(f"**Graduate-program minimum found:** {float(row['program_gpa_minimum']):.2f}.")
        links = [
            ("Open source page", row.get("hiring_source_url")),
            ("Check GPA source", row.get("lab_gpa_source_url")),
            ("Official faculty page", row.get("faculty_source_url")),
            ("View active grant", row.get("grant_url")),
            ("Professor/lab page", row.get("homepage_url")),
            ("Recent paper", row.get("latest_paper_url")),
            ("OpenAlex profile", row.get("openalex_id")),
        ]
        for label, url in links:
            if is_http_url(str(url or "")):
                st.link_button(label, str(url), width="stretch")
                break
