import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready
from radar_store import (
    cancel_radar_job,
    list_radar_operations,
    request_topic_index,
    retry_radar_job,
)
from ui import configure_page, navigation


configure_page("Radar operations")
navigation()
account_controls()

st.title("Radar indexing operations")
st.write(
    "Monitor the shared professor index, its background worker, identity-review "
    "queue, and failed external-source checks."
)
if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()

user, admin = require_site_admin()
if admin["admin_role"] != "owner":
    st.error("Only the site owner can manage indexing operations.")
    st.stop()
st.caption("Signed in with owner authority")

with st.form("schedule_topic"):
    research_area = st.text_input(
        "Research area to index",
        placeholder="Adversarial machine learning, robotics, neuroscience…",
    )
    schedule = st.form_submit_button("Add or refresh shared index", type="primary")

if schedule:
    try:
        topic, job = request_topic_index(
            research_area,
            requested_by=int(user["id"]),
            desired_results=100,
        )
        if job and job.get("reused"):
            st.info("This research area is already queued or being indexed.")
        elif job:
            st.success("The research area was added to the indexing queue.")
        else:
            st.success("The shared index is current; no duplicate work was created.")
        st.caption(
            f"Current coverage: {int(topic.get('verified_count') or 0)} verified "
            f"from {int(topic.get('candidates_seen') or 0)} candidates."
        )
    except Exception as error:
        st.error(str(error))

operations = list_radar_operations(int(user["id"]))
counts = {row["status"]: int(row["count"]) for row in operations["job_counts"]}
metric_columns = st.columns(5)
for column, status in zip(
    metric_columns, ["queued", "running", "failed", "completed", "cancelled"]
):
    column.metric(status.title(), counts.get(status, 0))

st.subheader("Worker health")
if operations["workers"]:
    st.dataframe(operations["workers"], width="stretch", hide_index=True)
    if not any(row["healthy"] for row in operations["workers"]):
        st.error(
            "No worker heartbeat has been seen in the last 10 minutes. Public searches "
            "still return stored results, but new research areas will not advance."
        )
else:
    st.error("No indexing worker has registered yet. Start it with: make worker")

st.subheader("Shared research indexes")
if operations["topics"]:
    st.dataframe(operations["topics"], width="stretch", hide_index=True)
else:
    st.info("No research areas have been requested yet.")

st.subheader("Background jobs")
if operations["jobs"]:
    st.dataframe(operations["jobs"], width="stretch", hide_index=True)
    action_columns = st.columns(2)
    failed_ids = [row["id"] for row in operations["jobs"] if row["status"] == "failed"]
    cancellable_ids = [
        row["id"] for row in operations["jobs"]
        if row["status"] in {"queued", "failed"}
    ]
    with action_columns[0]:
        if failed_ids:
            retry_id = st.selectbox("Failed job", failed_ids, key="retry_job_id")
            if st.button("Retry failed job"):
                retry_radar_job(int(user["id"]), int(retry_id))
                st.rerun()
    with action_columns[1]:
        if cancellable_ids:
            cancel_id = st.selectbox(
                "Queued/failed job", cancellable_ids, key="cancel_job_id"
            )
            if st.button("Cancel selected job"):
                cancel_radar_job(int(user["id"]), int(cancel_id))
                st.rerun()
else:
    st.info("No indexing jobs have been recorded yet.")

st.subheader("Faculty identities needing review")
if operations["identity_review"]:
    st.dataframe(operations["identity_review"], width="stretch", hide_index=True)
    st.caption(
        "CONFLICT and MANUAL_REVIEW records remain hidden from public results. "
        "Use retained evidence before changing an identity decision."
    )
else:
    st.success("No faculty identity currently requires manual review.")
