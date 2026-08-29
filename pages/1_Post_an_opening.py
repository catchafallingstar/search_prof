from datetime import date, timedelta

import streamlit as st

from auth import account_controls, require_user
from db import (
    database_is_ready,
    get_institution_membership,
    get_professor_profile,
    submit_institution_membership,
    submit_opportunity,
    submit_professor_profile,
)
from ui import (
    GPA_LABELS,
    configure_page,
    is_http_url,
    is_official_institution_url,
    navigation,
)


OFFICIAL_TITLES = [
    "Assistant Professor",
    "Associate Professor",
    "Professor",
    "Research Professor",
    "Lecturer",
    "University Recruiter",
    "Graduate Recruitment Director",
    "Other",
]

configure_page("Post an opening")
navigation()
account_controls()

st.title("Post an opening")
st.write("Only role-verified faculty or approved university administrators can publish opportunities.")

if not database_is_ready():
    st.error("PostgreSQL is not ready. Configure DATABASE_URL and apply db.sql before accepting submissions.")
    st.stop()

user = require_user()
profile = get_professor_profile(user["id"])
membership = get_institution_membership(user["id"])

credential = profile or membership
can_resubmit = bool(
    credential and credential["verification_status"] in {"rejected", "expired"}
)

if not credential or can_resubmit:
    heading = "Resubmit role verification" if can_resubmit else "Step 1: Request role verification"
    st.subheader(heading)
    st.info("An institutional email is supporting evidence, not proof of faculty or university authority. A moderator will compare this request with an official institutional page.")
    account_options = ["Faculty member", "University administrator or recruiter"]
    default_index = 1 if membership else 0
    account_type = st.radio(
        "I am submitting as",
        account_options,
        index=default_index,
        horizontal=True,
        disabled=can_resubmit,
        help="After the first request, the account type is locked for safer review.",
    )
    with st.form("role_verification"):
        institution = st.text_input(
            "Institution",
            value=credential["institution_name"] if credential else "",
            placeholder="University of Michigan",
        )
        saved_title = str(credential["title"] if credential else "")
        title_choice = st.selectbox(
            "Official title",
            OFFICIAL_TITLES,
            index=(
                OFFICIAL_TITLES.index(saved_title)
                if saved_title in OFFICIAL_TITLES
                else (OFFICIAL_TITLES.index("Other") if saved_title else 0)
            ),
        )
        custom_title = ""
        if title_choice == "Other":
            custom_title = st.text_input("Title shown on the university page", value=saved_title)
        title = custom_title if title_choice == "Other" else title_choice
        department = st.text_input(
            "Department", value=(credential.get("department") or "") if credential else ""
        )
        official_url = st.text_input(
            "University profile URL",
            value=credential["official_profile_url"] if credential else "",
            help="Use a university-controlled directory or staff page, not LinkedIn or ResearchGate.",
        )
        submitted = st.form_submit_button("Submit for review", type="primary")
    if submitted:
        if not all(value.strip() for value in (institution, title, department, official_url)):
            st.error("Complete every field.")
        elif not is_official_institution_url(official_url):
            st.error("Use a university-controlled profile page, not a social or publication profile.")
        else:
            try:
                if account_type == "Faculty member":
                    submit_professor_profile(user["id"], institution, title, department, official_url)
                else:
                    submit_institution_membership(user["id"], institution, title, department, official_url)
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.success("Verification request submitted. A moderator must approve it before you can post.")
                st.rerun()
    st.stop()

status = credential["verification_status"]
if status != "verified":
    message = {
        "pending": "Your faculty-role verification is awaiting moderator review.",
        "rejected": "Your verification was rejected. Update the official evidence and submit again.",
        "expired": "Your verification expired and must be renewed.",
    }.get(status, "Your faculty role is not verified.")
    st.warning(message)
    st.stop()

role_label = "Faculty role verified" if credential is profile else "University role verified"
st.success(f"{role_label} · {credential['title']} · {credential['institution_name']}")
st.subheader("Step 2: Submit the opening for moderation")

with st.form("opportunity_submission"):
    title = st.text_input("Opportunity title")
    professor_name = st.text_input("Professor or laboratory name", value=user["display_name"])
    research_area = st.text_input("Research area")
    position_type = st.selectbox("Position type", ["PhD", "Postdoc", "Research Assistant", "Masters", "Internship"])
    description = st.text_area("Description", height=150)
    funding_status = st.selectbox("Funding", ["confirmed", "partial", "unknown"], format_func=str.title)
    gpa_policy = st.selectbox("GPA policy", list(GPA_LABELS), format_func=lambda key: GPA_LABELS[key])
    international_eligible = st.checkbox("International applicants are eligible")
    start_term = st.text_input("Start term", placeholder="Fall 2027")
    deadline = st.date_input("Application deadline", value=date.today() + timedelta(days=90), min_value=date.today())
    application_url = st.text_input("Official application or laboratory URL")
    attestation = st.checkbox("I confirm this opening is accurate and I am authorized to publish it.")
    submitted = st.form_submit_button("Submit for moderation", type="primary")

if submitted:
    required = (title, professor_name, research_area, description, start_term, application_url)
    if not all(value.strip() for value in required):
        st.error("Complete every required field.")
    elif not is_http_url(application_url):
        st.error("Enter a valid HTTP or HTTPS application URL.")
    elif not attestation:
        st.error("Confirm the accuracy and authorization attestation.")
    else:
        try:
            opportunity_id = submit_opportunity(
                user["id"],
                {
                    "title": title,
                    "professor_name": professor_name,
                    "research_area": research_area,
                    "position_type": position_type,
                    "description": description,
                    "funding_status": funding_status,
                    "gpa_policy": gpa_policy,
                    "international_eligible": international_eligible,
                    "start_term": start_term,
                    "application_deadline": deadline,
                    "application_url": application_url,
                },
            )
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
        else:
            st.success(f"Opening #{opportunity_id} was submitted for moderation. It is not public yet.")
