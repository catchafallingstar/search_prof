import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready
from radar_store import (
    cancel_radar_job,
    list_radar_operations,
    request_topic_index,
    review_faculty_identity,
    retry_radar_job,
    retry_unresolved_identities,
)
from settings import setting_bool, setting_int
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

IDENTITY_STATUS_LABELS = {
    "VERIFIED": "Faculty confirmed",
    "NOT_FACULTY": "Not an eligible professor",
    "CONFLICT": "Identity conflict",
    "MANUAL_REVIEW": "Needs staff confirmation",
    "UNVERIFIED": "Not enough evidence",
}

IDENTITY_METHOD_LABELS = {
    "official_directory": "Automatic rules",
    "official_directory_openalex_history": "Official page + affiliation history",
    "automatic_search": "Automatic search",
    "gemini_assisted": "Gemini-assisted extraction",
    "manual_review": "Staff override",
}


def render_identity_context(identity: dict) -> None:
    """Show the research and source trail needed to distinguish namesakes."""
    st.markdown("**Candidate context**")
    topics = [str(value) for value in identity.get("matching_topics") or [] if value]
    if topics:
        st.write(f"Found while indexing: {', '.join(topics)}")
    if identity.get("research_domain"):
        st.write(f"Imported research label: {identity['research_domain']}")
    st.write(
        "Imported institution (may be historical): "
        f"{identity.get('institution_name') or 'Not available'}"
    )
    openalex_url = str(identity.get("openalex_id") or "").strip()
    if openalex_url.startswith(("https://", "http://")):
        st.link_button("Open OpenAlex author record", openalex_url)

    papers = list(identity.get("identity_papers") or [])
    st.markdown("**Papers that produced this candidate**")
    if papers:
        position_labels = {
            "first": "First author",
            "middle": "Middle author",
            "last": "Last author",
        }
        paper_rows = []
        for paper in papers:
            source_url = str(
                paper.get("doi") or paper.get("openalex_id") or ""
            ).strip()
            paper_rows.append(
                {
                    "Paper": paper.get("title") or "Untitled paper",
                    "Year": paper.get("publication_year"),
                    "Authorship": position_labels.get(
                        str(paper.get("author_position") or "").casefold(),
                        paper.get("author_position") or "Not recorded",
                    ),
                    "Source": source_url,
                }
            )
        st.dataframe(
            paper_rows,
            width="stretch",
            hide_index=True,
            column_config={
                "Paper": st.column_config.TextColumn(width="large"),
                "Source": st.column_config.LinkColumn(display_text="Open"),
            },
        )
    else:
        st.info("No originating paper is stored for this candidate.")

    evidence_rows = list(identity.get("identity_evidence") or [])
    if evidence_rows:
        st.markdown("**Official pages checked automatically**")
        st.dataframe(
            [
                {
                    "Page says": " · ".join(
                        value
                        for value in (
                            row.get("observed_title"),
                            row.get("observed_institution"),
                        )
                        if value
                    ) or "Identity found",
                    "Result": IDENTITY_STATUS_LABELS.get(
                        row.get("verification_status"),
                        row.get("verification_status") or "Unresolved",
                    ),
                    "Checked": row.get("checked_at"),
                    "Source": row.get("source_url"),
                }
                for row in evidence_rows
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "Source": st.column_config.LinkColumn(display_text="Open"),
            },
        )


def render_identity_editor(
    identity: dict,
    owner_user_id: int,
    *,
    key_prefix: str,
    default_action: str = "Keep automatic decision",
) -> None:
    method = str(
        identity.get("decision_method")
        or identity.get("faculty_verification_method")
        or "automatic_search"
    )
    st.caption(
        f"Decision: {IDENTITY_STATUS_LABELS.get(identity['faculty_status'], identity['faculty_status'])}"
        f" · Method: {IDENTITY_METHOD_LABELS.get(method, method)}"
    )
    if method == "gemini_assisted":
        st.warning(
            "Gemini helped extract the evidence. ScholarRadar validated the quoted "
            "text and source, but this remains an automatic decision that you can override."
        )
    elif identity["faculty_status"] in {"CONFLICT", "MANUAL_REVIEW"}:
        st.warning("This person is hidden from public results until the identity is resolved.")
    render_identity_context(identity)
    reason = identity.get("review_reason") or identity.get("decision_reason")
    if reason:
        st.write(reason)
    if identity.get("faculty_source_url"):
        st.link_button("Open evidence page", identity["faculty_source_url"])

    with st.form(f"{key_prefix}_{identity['id']}"):
        institution_name = st.text_input(
            "Current institution (staff correction)",
            value=identity.get("institution_name") or "",
        )
        faculty_title = st.text_input(
            "Faculty title", value=identity.get("faculty_title") or ""
        )
        source_url = st.text_input(
            "Current official faculty page",
            value=identity.get("faculty_source_url") or "",
        )
        actions = [
            "Keep automatic decision",
            "Retry automatic check",
            "Confirm faculty identity",
            "Mark as not faculty",
        ]
        action_index = actions.index(default_action) if default_action in actions else 0
        action = st.selectbox("Staff action", actions, index=action_index)
        save = st.form_submit_button("Save staff action", type="primary")
    if not save:
        return
    if action == "Keep automatic decision":
        st.info("No change was made.")
        return
    decision = {
        "Confirm faculty identity": "VERIFIED",
        "Retry automatic check": "RETRY",
        "Mark as not faculty": "NOT_FACULTY",
    }[action]
    if decision == "VERIFIED" and not is_official_institution_url(source_url):
        st.error("Use an official university page before confirming faculty status.")
        return
    review_faculty_identity(
        owner_user_id,
        int(identity["id"]),
        decision,
        institution_name=institution_name,
        faculty_title=faculty_title,
        source_url=source_url,
    )
    st.success("Staff decision saved. It now takes priority over automatic checks.")
    st.rerun()


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

st.subheader(f"Ambiguous identities ({len(operations['identity_review'])})")
if operations["identity_review"]:
    st.caption(
        "These records have multiple plausible faculty matches and remain hidden. "
        "Retry them together before reviewing individual people."
    )
    if st.button("Retry unresolved identities automatically"):
        queued_identities = retry_unresolved_identities(int(user["id"]), limit=100)
        st.success(f"Queued {queued_identities} identities for another automatic check.")
        st.rerun()
    identity_limit = int(st.session_state.get("identity_review_limit", 10))
    visible_identities = operations["identity_review"][:identity_limit]
    for identity in visible_identities:
        status_label = {
            "CONFLICT": "Multiple possible faculty matches",
            "MANUAL_REVIEW": "Needs confirmation",
        }.get(identity["faculty_status"], identity["faculty_status"])
        with st.expander(f"{identity['name']} · {status_label}"):
            render_identity_editor(
                identity,
                int(user["id"]),
                key_prefix="identity_review",
                default_action="Retry automatic check",
            )
    if identity_limit < len(operations["identity_review"]):
        if st.button("Show 10 more identity reviews"):
            st.session_state["identity_review_limit"] = identity_limit + 10
            st.rerun()
else:
    st.success("No faculty identity currently requires manual review.")

st.subheader("Recent automatic identity decisions")
st.caption(
    "These decisions do not require routine staff approval. Review or override one "
    "only when its evidence appears wrong. Staff overrides take priority."
)
ai_used = int(operations["identity_ai_usage"].get("request_count") or 0)
ai_limit = setting_int("GEMINI_IDENTITY_DAILY_LIMIT", 25, 0, 500)
if setting_bool("GEMINI_IDENTITY_ENABLED", False):
    st.caption(f"Gemini fallback usage today: {ai_used} of {ai_limit} app-limited calls.")
else:
    st.caption("Gemini fallback is disabled; the rule-based verifier is still active.")

automatic_decisions = operations["identity_decisions"]
if automatic_decisions:
    category = st.selectbox(
        "Show automatic decisions",
        ["All", "Faculty confirmed", "Not eligible", "Unresolved"],
    )
    category_statuses = {
        "All": {"VERIFIED", "NOT_FACULTY", "CONFLICT", "MANUAL_REVIEW", "UNVERIFIED"},
        "Faculty confirmed": {"VERIFIED"},
        "Not eligible": {"NOT_FACULTY"},
        "Unresolved": {"CONFLICT", "MANUAL_REVIEW", "UNVERIFIED"},
    }
    visible_decisions = [
        row for row in automatic_decisions
        if row["faculty_status"] in category_statuses[category]
    ]
    decision_rows = [
        {
            "Professor": row["name"],
            "Decision": IDENTITY_STATUS_LABELS.get(
                row["faculty_status"], row["faculty_status"]
            ),
            "Institution": row.get("institution_name") or "—",
            "Title": row.get("faculty_title") or "—",
            "Method": IDENTITY_METHOD_LABELS.get(
                row.get("decision_method")
                or row.get("faculty_verification_method")
                or "automatic_search",
                row.get("decision_method")
                or row.get("faculty_verification_method")
                or "Automatic",
            ),
            "Checked": row.get("faculty_checked_at"),
        }
        for row in visible_decisions
    ]
    st.dataframe(decision_rows, width="stretch", hide_index=True)
    if visible_decisions:
        selected_identity = st.selectbox(
            "Review or edit one person",
            visible_decisions,
            format_func=lambda row: (
                f"{row['name']} · "
                f"{IDENTITY_STATUS_LABELS.get(row['faculty_status'], row['faculty_status'])}"
            ),
        )
        with st.expander("Evidence and staff override"):
            render_identity_editor(
                selected_identity,
                int(user["id"]),
                key_prefix="automatic_identity",
            )
else:
    st.info("No automatic identity decisions have been recorded yet.")

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
