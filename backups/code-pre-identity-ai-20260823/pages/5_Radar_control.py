import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready
from radar_store import (
    cancel_radar_job,
    list_radar_operations,
    request_topic_index,
    review_faculty_identity,
    retry_radar_job,
)
from ui import configure_page, is_official_institution_url, navigation


JOB_LABELS = {
    "DISCOVER_CANDIDATES": "Find matching researchers",
    "VERIFY_FACULTY": "Verify faculty identities",
    "REFRESH_FACULTY": "Recheck faculty identity",
    "CHECK_GRANTS": "Check grants",
    "CHECK_HIRING": "Check hiring pages",
    "REINDEX_RESEARCH": "Update research area",
}

STATUS_LABELS = {
    "queued": "Waiting",
    "running": "Working now",
    "stalled": "May be stuck",
    "failed": "Needs attention",
    "completed": "Finished",
    "cancelled": "Stopped",
}


configure_page("Professor database")
navigation()
account_controls()

st.title("Professor database")
st.write("Add research areas and review work that needs your attention.")
if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()

user, admin = require_site_admin()
if admin["admin_role"] != "owner":
    st.error("Only the site owner can manage indexing operations.")
    st.stop()
st.caption("Owner access")

with st.form("schedule_topic"):
    research_area = st.text_input(
        "Research area",
        placeholder="Adversarial machine learning, robotics, neuroscience…",
    )
    schedule = st.form_submit_button("Add or update", type="primary")

if schedule:
    try:
        topic, job = request_topic_index(
            research_area,
            requested_by=int(user["id"]),
            desired_results=100,
        )
        if job and job.get("reused"):
            st.info("This research area is already being updated.")
        elif job:
            st.success("Added to the work queue.")
        else:
            st.success("This research area is current.")
        st.caption(
            f"Current coverage: {int(topic.get('verified_count') or 0)} verified "
            f"from {int(topic.get('candidates_seen') or 0)} candidates."
        )
    except Exception as error:
        st.error(str(error))

operations = list_radar_operations(int(user["id"]))
counts = {row["status"]: int(row["count"]) for row in operations["job_counts"]}
metric_columns = st.columns(6)
for column, status in zip(
    metric_columns,
    ["queued", "running", "stalled", "failed", "completed", "cancelled"],
):
    column.metric(STATUS_LABELS[status], counts.get(status, 0))
st.caption("These numbers count background tasks, not professors.")
if counts.get("stalled", 0):
    st.error(
        "A job has exceeded its expected runtime. Restart the indexing worker; "
        "the durable queue will recover and retry unfinished work."
    )

st.subheader("Background worker")
if operations["workers"]:
    healthy_workers = [row for row in operations["workers"] if row["healthy"]]
    latest_worker = operations["workers"][0]
    if healthy_workers:
        st.success("Active")
        st.caption(f"Last check-in: {latest_worker['last_seen_at']}")
    else:
        st.error(
            "Offline. Stored results still work, but new indexing cannot continue."
        )
else:
    st.error("No worker has connected. Start the site with: make start")

st.subheader("Research areas")
if operations["topics"]:
    topic_rows = [
        {
            "Research area": row["requested_query"],
            "Stage": row["status"],
            "Verified": row["verified_count"],
            "Candidates": row["candidates_seen"],
            "Papers": row["papers_found"],
            "Last updated": row["last_indexed_at"],
            "Problem": row["last_error"],
        }
        for row in operations["topics"]
    ]
    st.dataframe(topic_rows, width="stretch", hide_index=True)
else:
    st.info("No research areas have been requested yet.")

st.subheader("Work queue")
if operations["jobs"]:
    active_jobs = [
        row for row in operations["jobs"]
        if row["status"] in {"queued", "running", "stalled", "failed"}
    ]
    if active_jobs:
        job_rows = [
            {
                "Task": JOB_LABELS.get(row["job_type"], row["job_type"]),
                "Research area": row.get("requested_query") or "—",
                "Professor": row.get("professor_name") or "—",
                "Status": STATUS_LABELS.get(row["status"], row["status"]),
                "Attempts": f"{row['attempts']} / {row['max_attempts']}",
                "Problem": row.get("last_error"),
            }
            for row in active_jobs
        ]
        st.dataframe(job_rows, width="stretch", hide_index=True)
    else:
        st.success("No current task needs attention.")
    action_columns = st.columns(2)
    failed_jobs = [row for row in operations["jobs"] if row["status"] == "failed"]
    cancellable_jobs = [
        row for row in operations["jobs"]
        if row["status"] in {"queued", "failed"}
    ]
    with action_columns[0]:
        if failed_jobs:
            retry_job = st.selectbox(
                "Task to retry",
                failed_jobs,
                format_func=lambda row: (
                    f"{JOB_LABELS.get(row['job_type'], row['job_type'])} · "
                    f"{row.get('requested_query') or row.get('professor_name') or 'General'}"
                ),
                key="retry_job_id",
            )
            if st.button("Retry task"):
                retry_radar_job(int(user["id"]), int(retry_job["id"]))
                st.rerun()
    with action_columns[1]:
        if cancellable_jobs:
            cancel_job = st.selectbox(
                "Task to stop",
                cancellable_jobs,
                format_func=lambda row: (
                    f"{JOB_LABELS.get(row['job_type'], row['job_type'])} · "
                    f"{row.get('requested_query') or row.get('professor_name') or 'General'}"
                ),
                key="cancel_job_id",
            )
            if st.button("Stop task"):
                cancel_radar_job(int(user["id"]), int(cancel_job["id"]))
                st.rerun()
else:
    st.info("No indexing jobs have been recorded yet.")

st.subheader(f"People who need identity review ({len(operations['identity_review'])})")
if operations["identity_review"]:
    identity_limit = int(st.session_state.get("identity_review_limit", 10))
    visible_identities = operations["identity_review"][:identity_limit]
    for identity in visible_identities:
        status_label = {
            "CONFLICT": "Institution or identity mismatch",
            "MANUAL_REVIEW": "Needs confirmation",
        }.get(identity["faculty_status"], identity["faculty_status"])
        with st.expander(f"{identity['name']} · {status_label}"):
            if identity.get("review_reason"):
                st.write(identity["review_reason"])
            with st.form(f"identity_review_{identity['id']}"):
                institution_name = st.text_input(
                    "Institution", value=identity.get("institution_name") or ""
                )
                faculty_title = st.text_input(
                    "Faculty title", value=identity.get("faculty_title") or ""
                )
                source_url = st.text_input(
                    "Official university page",
                    value=identity.get("faculty_source_url") or "",
                )
                decision_label = st.selectbox(
                    "Decision",
                    [
                        "Retry automatic check",
                        "Confirm faculty identity",
                        "Mark as not faculty",
                    ],
                )
                save_decision = st.form_submit_button("Save decision", type="primary")
            if save_decision:
                decision = {
                    "Confirm faculty identity": "VERIFIED",
                    "Retry automatic check": "RETRY",
                    "Mark as not faculty": "NOT_FACULTY",
                }[decision_label]
                if decision == "VERIFIED" and not is_official_institution_url(source_url):
                    st.error("Use an official university page before confirming faculty status.")
                else:
                    review_faculty_identity(
                        int(user["id"]),
                        int(identity["id"]),
                        decision,
                        institution_name=institution_name,
                        faculty_title=faculty_title,
                        source_url=source_url,
                    )
                    st.success("Identity decision saved.")
                    st.rerun()
    if identity_limit < len(operations["identity_review"]):
        if st.button("Show 10 more identity reviews"):
            st.session_state["identity_review_limit"] = identity_limit + 10
            st.rerun()
else:
    st.success("No faculty identity currently requires manual review.")

st.subheader("Hiring-page checks")
hiring = operations["hiring_metrics"]
hiring_columns = st.columns(4)
hiring_columns[0].metric("Statement found", int(hiring.get("present") or 0))
hiring_columns[1].metric("None found", int(hiring.get("not_found") or 0))
hiring_columns[2].metric("Page unavailable", int(hiring.get("unavailable") or 0))
hiring_columns[3].metric("Needs checking", int(hiring.get("stale_or_unchecked") or 0))
st.caption("None found does not mean the professor is not hiring.")
if operations["hiring_issues"]:
    issue_rows = [
        {
            "Professor": row["name"],
            "Institution": row["institution_name"],
            "Last checked": row["public_hiring_checked_at"],
            "Failures": row["public_hiring_failure_count"],
            "Next check": row["public_hiring_next_check_at"],
        }
        for row in operations["hiring_issues"]
    ]
    st.dataframe(issue_rows, width="stretch", hide_index=True)
else:
    st.success("No repeated hiring-source failures are recorded.")

with st.expander("Advanced task history"):
    st.dataframe(operations["jobs"], width="stretch", hide_index=True)
    st.dataframe(operations["workers"], width="stretch", hide_index=True)
