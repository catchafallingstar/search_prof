import streamlit as st

from auth import account_controls
from ui import configure_page, navigation

configure_page("Verification")
navigation()
account_controls()

st.title("How verification works")
st.write("School email is only the first identity signal. It does not prove that the account belongs to a professor.")

steps = [
    ("1. Identity", "The user signs in through an OIDC identity provider and supplies an institutional email."),
    ("2. Faculty role", "A moderator matches the name, institution, title, and department against an official university directory page."),
    ("3. Supporting evidence", "ORCID or approval from an already verified institutional administrator can strengthen the request, but neither replaces role verification."),
    ("4. Moderated first post", "A verified account's opening is still reviewed before publication."),
    ("5. Expiration", "Faculty verification is rechecked yearly; opportunities expire after 90 days unless renewed."),
]
for heading, body in steps:
    with st.container(border=True):
        st.subheader(heading)
        st.write(body)

st.warning("Verification badges and organic ranking cannot be purchased. Sponsored placements must be visibly labeled.")

