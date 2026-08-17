import streamlit as st

from auth import account_controls, require_site_admin
from db import (
    database_is_ready,
    fetch_admin_audit_log,
    grant_site_moderator,
    list_users_for_admin,
    revoke_site_moderator,
)
from ui import configure_page, navigation

configure_page("Admin accounts")
navigation()
account_controls()

st.title("Administrator accounts")
st.write("Only the single site owner can grant or revoke moderator access.")

if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()

owner, _ = require_site_admin(owner_only=True)
st.success(f"Owner authority confirmed for {owner['email']}")
if st.button("Return to moderation queue"):
    st.switch_page("pages/3_Admin_review.py")

users = list_users_for_admin(owner["id"])
grant_candidates = [
    row for row in users
    if row["id"] != owner["id"]
    and not (row.get("admin_role") == "moderator" and row.get("revoked_at") is None)
]
active_moderators = [
    row for row in users
    if row.get("admin_role") == "moderator" and row.get("revoked_at") is None
]

st.subheader("Grant moderator access")
if not grant_candidates:
    st.info("No eligible signed-in users are available. A person must sign in once before you can grant access.")
else:
    selected_grant = st.selectbox(
        "User",
        grant_candidates,
        format_func=lambda row: f"{row['display_name']} · {row['email']}",
        key="grant_moderator_user",
    )
    if st.button("Grant moderator access", type="primary"):
        grant_site_moderator(owner["id"], selected_grant["id"])
        st.success(f"Moderator access granted to {selected_grant['email']}.")
        st.rerun()

st.subheader("Active moderators")
if not active_moderators:
    st.info("No additional moderators have been created. You remain the only administrator.")
else:
    selected_revoke = st.selectbox(
        "Moderator",
        active_moderators,
        format_func=lambda row: f"{row['display_name']} · {row['email']}",
        key="revoke_moderator_user",
    )
    confirm = st.checkbox(f"I want to revoke moderator access for {selected_revoke['email']}.")
    if st.button("Revoke moderator access", disabled=not confirm):
        revoke_site_moderator(owner["id"], selected_revoke["id"])
        st.success(f"Moderator access revoked for {selected_revoke['email']}.")
        st.rerun()

st.subheader("Recent administration activity")
events = fetch_admin_audit_log(owner["id"], 50)
if events:
    st.dataframe(events, width="stretch", hide_index=True)
else:
    st.info("No administration activity has been recorded yet.")
