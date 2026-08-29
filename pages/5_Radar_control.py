from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready
from radar_store import (
    cancel_radar_job,
    fetch_live_indexing_status,
    list_radar_operations,
    recover_stalled_radar_jobs,
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
    "ENRICH_PROFESSORS": "Check grants and hiring pages",
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
    "official_directory_publication_link": "Official page + matching publication",
    "researcher_profile_publication_link": "Researcher page + matching publication",
    "official_non_appointment_page": "Official guest/event page - not an appointment",
    "automatic_search": "Automatic search",
    "gemini_assisted": "Gemini-assisted extraction",
    "manual_review": "Staff override",
}

ACTIVITY_STAGE_LABELS = {
    "DISCOVER_CANDIDATES": "Extract candidates",
    "REINDEX_RESEARCH": "Update candidates",
    "VERIFY_FACULTY": "Verify identity",
    "REFRESH_FACULTY": "Recheck identity",
    "CHECK_GRANTS": "Check grants",
    "CHECK_HIRING": "Check hiring signal",
    "ENRICH_PROFESSORS": "Check grants and hiring",
}

ACTIVITY_RESULT_LABELS = {
    "VERIFIED": "Faculty identity verified",
    "NOT_FACULTY": "Not an eligible professor",
    "UNVERIFIED": "Not enough evidence",
    "CONFLICT": "Conflicting identity evidence",
    "MANUAL_REVIEW": "Needs staff review",
    "PRESENT": "Hiring statement found",
    "NOT_FOUND": "No matching record found",
    "SOURCE_UNAVAILABLE": "Source unavailable",
    "NOT_CHECKED": "Not checked",
    "FOUND": "Relevant grant found",
}


def _format_device_time(value) -> str:
    if value is None:
        return "Time unavailable"
    try:
        timezone_name = str(st.context.timezone or "").strip()
    except Exception:
        timezone_name = ""
    try:
        target_timezone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError:
        target_timezone = None
    if target_timezone is None:
        try:
            offset_minutes = int(st.context.timezone_offset)
            target_timezone = timezone(-timedelta(minutes=offset_minutes))
        except (AttributeError, TypeError, ValueError):
            target_timezone = timezone.utc
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(target_timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def render_identity_context(identity: dict) -> None:
    """Show the research and source trail needed to distinguish namesakes."""
    st.markdown("**Candidate context**")
    topics = [str(value) for value in identity.get("matching_topics") or [] if value]
    if topics:
        st.write(f"Current search matches: {', '.join(topics)}")
    else:
        st.warning(
            "This person is not currently included in public search results. "
            "The record is kept here only for identity review."
        )
    st.write(
        "Imported institution (may be historical): "
        f"{identity.get('institution_name') or 'Not available'}"
    )
    openalex_url = str(identity.get("openalex_id") or "").strip()
    if openalex_url.startswith(("https://", "http://")):
        st.link_button("Open OpenAlex author record", openalex_url)

    position_labels = {
        "first": "First author",
        "middle": "Middle author",
        "last": "Last author",
    }
    topic_papers = list(identity.get("topic_paper_evidence") or [])
    st.markdown("**Papers supporting current search matches**")
    if topic_papers:
        supporting_ids = {
            str(paper.get("openalex_id") or "") for paper in topic_papers
        }
        st.dataframe(
            [
                {
                    "Research area": paper.get("research_area"),
                    "Paper": paper.get("title") or "Untitled paper",
                    "Year": paper.get("publication_year"),
                    "Matched phrase": paper.get("matched_query"),
                    "Relevance": paper.get("relevance_score"),
                    "Source": str(
                        paper.get("doi") or paper.get("openalex_id") or ""
                    ).strip(),
                }
                for paper in topic_papers
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "Paper": st.column_config.TextColumn(width="large"),
                "Source": st.column_config.LinkColumn(display_text="Open"),
            },
        )
    else:
        supporting_ids = set()
        st.info(
            "No current topic-specific paper evidence is stored. Older records "
            "will gain this evidence after that research area is reindexed."
        )

    papers = [
        paper
        for paper in identity.get("identity_papers") or []
        if str(paper.get("openalex_id") or "") not in supporting_ids
    ]
    st.markdown("**Other recent papers for identity checking**")
    if papers:
        st.dataframe(
            [
                {
                    "Paper": paper.get("title") or "Untitled paper",
                    "Year": paper.get("publication_year"),
                    "Authorship": position_labels.get(
                        str(paper.get("author_position") or "").casefold(),
                        paper.get("author_position") or "Not recorded",
                    ),
                    "Source": str(
                        paper.get("doi") or paper.get("openalex_id") or ""
                    ).strip(),
                }
                for paper in papers
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "Paper": st.column_config.TextColumn(width="large"),
                "Source": st.column_config.LinkColumn(display_text="Open"),
            },
        )
    else:
        st.caption("No additional papers are stored for identity checking.")

    historical_topics = [
        str(value) for value in identity.get("historical_topics") or [] if value
    ]
    if historical_topics:
        with st.expander("Older search matches no longer shown publicly"):
            st.write(", ".join(historical_topics))

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


@st.fragment(run_every="5s")
def render_live_activity() -> None:
    live = fetch_live_indexing_status(int(user["id"]))
    worker = live.get("worker") or {}
    totals = live.get("totals") or {}
    queue_counts = live.get("queue_counts") or {}
    unique_candidates = int(totals.get("unique_candidates") or 0)
    remaining = int(totals.get("pending_identities") or 0)
    checked = max(0, unique_candidates - remaining)

    st.subheader("Live indexing activity")
    if worker:
        progress = worker.get("progress") or {}
        current_stage = str(
            progress.get("live_stage") or worker.get("job_type") or ""
        )
        task = ACTIVITY_STAGE_LABELS.get(current_stage, current_stage or "Background work")
        subject = (
            worker.get("requested_query")
            or worker.get("professor_name")
            or "shared professor index"
        )
        st.success(f"Working now: {task} — {subject}")
        st.caption(
            str(progress.get("live_detail") or "Processing the current background task.")
            + " This panel refreshes every five seconds."
        )
        current_professors = list(progress.get("live_professors") or [])
        if current_professors:
            with st.container(border=True):
                st.markdown("**Current professor batch**")
                for professor in current_professors:
                    st.write(
                        f"• {professor.get('name') or 'Unknown person'} — "
                        f"imported institution: "
                        f"{professor.get('institution_name') or 'not available'}"
                    )
                    if professor.get("homepage_url"):
                        st.caption(f"Imported page: {professor['homepage_url']}")
    else:
        st.warning("No active worker has checked in during the last two minutes.")

    metrics = st.columns(4)
    metrics[0].metric("Candidates collected", f"{unique_candidates:,}")
    metrics[1].metric("Identity checks complete", f"{checked:,}")
    metrics[2].metric("Still to check", f"{remaining:,}")
    metrics[3].metric(
        "Verified faculty",
        f"{int(totals.get('verified_faculty') or 0):,}",
    )
    if unique_candidates:
        st.progress(
            min(1.0, checked / unique_candidates),
            text=f"Identity review coverage: {checked:,} of {unique_candidates:,}",
        )

    since_start = int(totals.get("checked_since_worker_start") or 0)
    verified_since_start = int(totals.get("verified_since_worker_start") or 0)
    waiting = int(queue_counts.get("queued") or 0)
    running = int(queue_counts.get("running") or 0)
    st.caption(
        f"This worker run checked {since_start:,} unique identities and confirmed "
        f"{verified_since_start:,} faculty. {running} verification task is active; "
        f"{waiting} are waiting their turn."
    )

    providers = list(live.get("providers") or [])
    primary_provider = providers[0] if providers else None
    if primary_provider and primary_provider.get("status") == "healthy":
        st.caption(
            f"Search provider healthy · Last successful search: "
            f"{primary_provider.get('last_success_at')}"
        )
    elif primary_provider:
        st.warning(
            f"Search provider {primary_provider.get('status') or 'unavailable'}: "
            f"{primary_provider.get('last_error') or 'waiting to retry'}"
        )

    recent = list(live.get("activity_logs") or [])
    st.markdown("**Most recent activity (20)**")
    if recent:
        for entry in recent[:20]:
            stage = ACTIVITY_STAGE_LABELS.get(
                str(entry.get("stage") or ""),
                str(entry.get("stage") or "Background work"),
            )
            result = ACTIVITY_RESULT_LABELS.get(
                str(entry.get("result_status") or ""),
                str(entry.get("result_status") or "Completed"),
            )
            activity_at = entry.get("activity_at")
            time_label = _format_device_time(activity_at)
            fields = ", ".join(entry.get("research_areas") or []) or "Not linked"
            with st.container(border=True):
                st.markdown(
                    f"**{time_label} · {stage} · "
                    f"{entry.get('name') or 'Unknown professor'}**"
                )
                st.caption(
                    f"Field: {fields} · Imported institution: "
                    f"{entry.get('institution_name') or 'not available'}"
                )
                page_facts = " · ".join(
                    str(value)
                    for value in (
                        entry.get("observed_title"),
                        entry.get("observed_institution"),
                    )
                    if value
                )
                if page_facts:
                    st.write(f"Page identified: {page_facts}")
                evidence = str(entry.get("evidence_text") or "").strip()
                if evidence:
                    st.write(f"Evidence: {evidence[:500]}")
                st.write(f"Result: {result}")
                result_detail = str(entry.get("result_detail") or "").strip()
                if result_detail and result_detail != evidence:
                    st.caption(result_detail[:500])
                if entry.get("source_url"):
                    st.caption(f"Source: {entry['source_url']}")
    else:
        st.info("No completed indexing activity has been recorded yet.")


render_live_activity()

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
    st.warning(
        "A task is overdue. If the worker is active, it will stop and retry the "
        "task automatically. If the worker is offline, restart it below."
    )
    if st.button("Recover overdue tasks"):
        recovered = recover_stalled_radar_jobs(int(user["id"]))
        if recovered:
            st.success(f"Recovered {recovered} abandoned task(s).")
            st.rerun()
        else:
            st.info(
                "No abandoned task was recovered. An active worker still owns "
                "the task, so it is safer to let its timeout finish."
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
        st.code("# Local site and worker\nmake start\n\n# Standalone worker only\nmake worker")
        st.caption(
            "For a hosted site, restart the separate worker service in its hosting dashboard."
        )
else:
    st.error("No worker has connected. Start it in a terminal.")
    st.code("# Local site and worker\nmake start\n\n# Standalone worker only\nmake worker")
    st.caption(
        "For a hosted site, start the separate worker service in its hosting dashboard."
    )

blocked_providers = [
    row for row in operations.get("search_providers", [])
    if row.get("actively_blocked")
]
blocked_openalex = [
    row for row in blocked_providers
    if str(row.get("provider_name") or "").casefold() == "openalex"
]
blocked_identity_search = [
    row for row in blocked_providers
    if str(row.get("provider_name") or "").casefold() != "openalex"
]
if blocked_openalex:
    next_retry = max(row["blocked_until"] for row in blocked_openalex)
    st.warning(
        "Research discovery is paused because OpenAlex reached its current "
        "request limit. Saved candidates and results are safe. Discovery will "
        f"retry automatically after {next_retry}."
    )
if blocked_identity_search:
    next_retry = max(row["blocked_until"] for row in blocked_identity_search)
    st.warning(
        "Faculty verification is paused because the public search engines are "
        "temporarily unavailable. Existing results still work, queued identities "
        "are safe, and the worker will retry automatically after "
        f"{next_retry}."
    )

st.subheader("Research areas")
if operations["topics"]:
    st.caption(
        "Candidates come from relevant papers. Exact evidence counts verified "
        "faculty with a saved supporting paper. Hiring checked means checked in "
        "the last 24 hours; it does not mean a position was found."
    )
    topic_rows = [
        {
            "Research area": row["requested_query"],
            "Stage": row["coverage_stage"],
            "Candidates": row["candidates_seen"],
            "Verified faculty": row["verified_count"],
            "Exact evidence": row["exact_evidence_professors"],
            "Hiring checked": row["fresh_hiring_checked"],
            "Problems": row["problem_count"],
            "Last updated": row["last_indexed_at"],
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
