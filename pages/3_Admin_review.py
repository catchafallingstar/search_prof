import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready, fetch_pending_reviews, review_item
from ui import configure_page, is_http_url, navigation

configure_page("Account and opening review")
navigation()
account_controls()

st.title("Account and opening review")
st.caption(
    "Review role requests and openings submitted on ScholarRadar."
)
if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()

user, admin = require_site_admin()
st.caption(f"{admin['admin_role'].title()} access")
if admin["admin_role"] == "owner":
    if st.button("Professor database"):
        st.switch_page("pages/5_Radar_control.py")
    if st.button("Staff access"):
        st.switch_page("pages/4_Admin_accounts.py")

items = fetch_pending_reviews(user["id"])
if not items:
    st.success("Nothing is waiting for review.")
    st.stop()

review_labels = {
    "profile": "Faculty role",
    "membership": "University staff role",
    "opportunity": "Opening",
}
role_count = sum(item["review_type"] in {"profile", "membership"} for item in items)
opening_count = sum(item["review_type"] == "opportunity" for item in items)
role_metric, opening_metric = st.columns(2)
role_metric.metric("Role requests", role_count)
opening_metric.metric("Submitted openings", opening_count)

for item in items:
    with st.container(border=True):
        st.caption(
            f"{review_labels.get(item['review_type'], 'Review')} · "
            f"Submitted {item['created_at']:%Y-%m-%d}"
        )
        st.subheader(item["subject"])
        submitter = item.get("display_name") or "ScholarRadar discovery"
        submitter_email = item.get("email") or "automated public-evidence scan"
        st.write(f"**Submitter:** {submitter} ({submitter_email})")
        st.write(f"**Institution:** {item['institution_name']}")
        detail_label = "Research area" if item["review_type"] == "opportunity" else "Department"
        st.write(f"**{detail_label}:** {item.get('detail') or 'Not provided'}")
        if item.get("evidence_url") and is_http_url(item["evidence_url"]):
            st.link_button("Open official evidence", item["evidence_url"])
        if item["review_type"] in {"profile", "membership"}:
            confirmation_text = (
                "I checked that the official university page matches the submitter's "
                "name, institution, role/title, and department."
            )
        else:
            confirmation_text = (
                "I checked the official opportunity/application page and the submitted details."
            )
        evidence_checked = st.checkbox(
            confirmation_text,
            key=f"checked_{item['review_type']}_{item['id']}",
        )
        notes = st.text_area("Reviewer notes", key=f"notes_{item['review_type']}_{item['id']}")
        approve_col, reject_col = st.columns(2)
        if approve_col.button(
            "Approve",
            type="primary",
            disabled=not evidence_checked,
            key=f"approve_{item['review_type']}_{item['id']}",
        ):
            review_item(item["review_type"], item["id"], True, user["id"], notes)
            st.rerun()
        if reject_col.button("Reject", key=f"reject_{item['review_type']}_{item['id']}"):
            if not notes.strip():
                st.error("Add reviewer notes before rejecting.")
            else:
                review_item(item["review_type"], item["id"], False, user["id"], notes)
                st.rerun()
