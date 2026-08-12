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
        .sr-nav {
          display: flex;
          align-items: center;
          gap: 1.5rem;
          padding: .2rem 0 1.15rem;
          margin-bottom: .25rem;
          border-bottom: 1px solid rgba(49, 51, 63, .18);
        }
        .sr-brand {
          flex: 1 1 auto;
          font-size: 1.2rem;
          font-weight: 750;
          color: #1e5d3b;
          white-space: nowrap;
          text-decoration: none !important;
        }
        .sr-brand:hover,
        .sr-brand:focus-visible {
          color: #17482e;
          text-decoration: none !important;
        }
        .sr-nav-links {
          display: grid;
          grid-template-columns: repeat(4, minmax(8.5rem, 1fr));
          gap: .7rem;
        }
        .sr-nav-links a {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 2.5rem;
          padding: .45rem .9rem;
          border: 1px solid rgba(49, 51, 63, .22);
          border-radius: .55rem;
          background: #ffffff;
          color: #253129 !important;
          font-weight: 600;
          line-height: 1.15;
          text-align: center;
          text-decoration: none !important;
          box-shadow: 0 1px 2px rgba(20, 40, 28, .04);
          transition: border-color .15s ease, background-color .15s ease,
                      transform .15s ease;
        }
        .sr-nav-links a:hover,
        .sr-nav-links a:focus-visible {
          border-color: #2f6d4b;
          background: #edf7f0;
          color: #17482e !important;
          transform: translateY(-1px);
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
          .sr-brand { color: #a9ddba; }
          .sr-brand:hover, .sr-brand:focus-visible { color: #d7f3df; }
          .sr-nav { border-bottom-color: rgba(230, 239, 232, .18); }
          .sr-nav-links a {
            border-color: rgba(230, 239, 232, .24);
            background: #19221c;
            color: #e4eee7 !important;
          }
          .sr-nav-links a:hover,
          .sr-nav-links a:focus-visible {
            border-color: #91cba4;
            background: #223129;
            color: #ffffff !important;
          }
          .sr-kicker { color: #91cba4; }
          .sr-hero p, .sr-evidence { color: #aeb9b1; }
          div[data-testid="stVerticalBlockBorderWrapper"] { background: #19221c; }
        }
        @media (max-width: 760px) {
          .block-container { padding-top: 4rem; }
          .sr-nav { align-items: stretch; flex-direction: column; gap: .8rem; }
          .sr-nav-links { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def navigation() -> None:
    st.markdown(
        """
        <nav class="sr-nav" aria-label="Primary navigation">
          <a class="sr-brand" href="/" target="_self" aria-label="ScholarRadar home">◉ ScholarRadar</a>
          <div class="sr-nav-links">
            <a href="/" target="_self">Browse</a>
            <a href="/Post_an_opening" target="_self">Post an opening</a>
            <a href="/Verification" target="_self">Verification</a>
            <a href="/Admin_review" target="_self">Staff</a>
          </div>
        </nav>
        """,
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
