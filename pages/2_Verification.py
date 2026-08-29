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
    ("3. Opening review", "A verified account's opening is reviewed before publication."),
    ("4. Expiration", "Faculty verification is rechecked yearly; opportunities expire after 90 days unless renewed."),
]
for heading, body in steps:
    with st.container(border=True):
        st.subheader(heading)
        st.write(body)

st.warning("Verification badges and organic ranking cannot be purchased. Sponsored placements must be visibly labeled.")

if st.button("Start role verification", type="primary"):
    st.switch_page("pages/1_Post_an_opening.py")
