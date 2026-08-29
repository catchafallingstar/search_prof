import re
from urllib.parse import quote

import streamlit as st

from auth import account_controls
from settings import setting
from ui import configure_page, navigation


configure_page("Data and policies")
navigation()
account_controls()

st.title("About ScholarRadar data")
st.write(
    "ScholarRadar helps students find research-matched faculty and public recruiting "
    "statements. It does not make admission or employment decisions."
)

st.subheader("What the labels mean")
st.markdown(
    """
- **Posted on ScholarRadar**: a role-verified faculty member or university representative submitted the opening, and a moderator approved it.
- **Hiring signal found online**: ScholarRadar found recruiting language on a page attributed to that professor or laboratory. The original page is the source; the statement may be outdated.
- **Possible opportunity**: research fit, an early-career appointment, or relevant funding may make outreach worthwhile, but hiring is not confirmed.
- **Verified faculty**: an official university source supports the person's faculty identity. It does not prove that the person is hiring.
"""
)

st.subheader("Sources and limits")
st.write(
    "Research matches use public scholarly metadata, including OpenAlex. Faculty identity, "
    "funding, and recruiting evidence come from linked public sources. A grant, title, or "
    "paper does not by itself prove an opening. Always read the original source before contacting a lab."
)

st.subheader("Privacy and acceptable use")
st.write(
    "ScholarRadar stores the account and submission information needed for sign-in, role "
    "verification, moderation, security, and site operation. Do not submit sensitive personal "
    "information, impersonate another person, scrape the service aggressively, or use the data "
    "for spam, harassment, or automated mass outreach."
)

st.subheader("Corrections and removal requests")
st.write(
    "If a profile, source, or recruiting statement is inaccurate, include the person's name, "
    "the ScholarRadar or source URL, and the correction you are requesting."
)
contact_email = setting("CONTACT_EMAIL").strip()
if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact_email):
    subject = quote("ScholarRadar correction or removal request")
    st.link_button(
        "Email a correction request",
        f"mailto:{contact_email}?subject={subject}",
        type="primary",
    )
    st.caption(f"Contact: {contact_email}")
else:
    st.warning("The public correction contact must be configured before launch.")
