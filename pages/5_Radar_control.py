from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready
from ingestion.websearch import search_provider_runtime_state
from radar_store import (
    cancel_radar_job, fetch_live_indexing_status, list_radar_operations,
    recover_stalled_radar_jobs, request_topic_index, retry_radar_job,
    save_publication_review_candidate, reject_publication_candidate,
    approve_scholar_publication_row, reject_scholar_publication_row,
    requeue_unresolved_publication_reviews,
    save_roster_review_record, save_manual_research_profile,
)
from ui import configure_page, navigation

JOB_LABELS = {
    "DISCOVER_FACULTY_DIRECTORIES": "Find official faculty pages",
    "CRAWL_FACULTY_DIRECTORY": "Import approved faculty roster",
    "MATCH_FACULTY_PUBLICATIONS": "Match faculty publications",
    "QWEN_REVIEW_PUBLICATION": "Review publication identity with Qwen",
    'QWEN_REVIEW_INTERESTS': 'Review research interests with Qwen',
    "ENRICH_CLASSIFY_PAPER": "Resolve abstract and classify paper",
    "INDEX_ROSTER_TOPIC": "Match paper evidence to research area",
    "CHECK_HIRING": "Check hiring pages",
    "CHECK_GRANTS": "Check grants",
    "CHECK_PROGRAM_GPA": "Check graduate-program GPA",
}


def _local_time(value) -> str:
    if value is None:
        return "Time unavailable"
    try:
        name = str(st.context.timezone or "").strip()
        zone = ZoneInfo(name) if name else timezone.utc
    except (AttributeError, ZoneInfoNotFoundError):
        zone = timezone.utc
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z")


configure_page("Radar control")
navigation()
account_controls()
st.title("Radar control")
st.write("Monitor the university-first faculty index and resolve evidence problems.")
if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()
user, admin = require_site_admin()
if admin["admin_role"] != "owner":
    st.error("Only the site owner can manage indexing operations.")
    st.stop()


@st.fragment(run_every="5s")
def live_panel() -> None:
    live = fetch_live_indexing_status(int(user["id"]), recent_limit=10)
    operations = list_radar_operations(int(user["id"]), limit=250)
    counts = {str(row["status"]): int(row["count"]) for row in operations["job_counts"]}
    columns = st.columns(2)
    columns[0].metric("Background running", counts.get("running", 0))
    columns[1].metric("Jobs waiting", counts.get("queued", 0))
    quality = operations.get("quality_counts") or {}
    quality_columns = st.columns(4)
    quality_columns[0].metric("Faculty approved", int(quality.get("approved_faculty") or 0))
    quality_columns[1].metric("Needs staff review", int(quality.get("needs_staff_review") or 0))
    quality_columns[2].metric("Automatically rejected", int(quality.get("automatically_rejected") or 0))
    quality_columns[3].metric("Technical failures", int(quality.get("technical_failures") or 0))

    st.subheader("Live activity")
    worker = live.get("worker") or {}
    if worker:
        progress = worker.get("progress") or {}
        stage = str(progress.get("live_stage") or worker.get("job_type") or "Background work")
        subject = (worker.get("professor_name") or progress.get("member_name")
                   or worker.get("institution_name") or worker.get("requested_query")
                   or "Shared index")
        st.success(f"Working now: {JOB_LABELS.get(stage, stage)} — {subject}")
        st.write(progress.get("live_detail") or "Processing the current task.")
        if progress.get("paper_title"):
            st.write(f"Paper: {progress['paper_title']}")
        if progress.get("source_url") or worker.get("directory_url"):
            st.caption(f"Source: {progress.get('source_url') or worker.get('directory_url')}")
    else:
        st.info("No worker is active. Waiting jobs remain queued until `make start` or `make worker` runs.")

    state = search_provider_runtime_state()
    if not state["available"]:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=state["retry_after_seconds"])
        st.caption("Directory-recovery searches are waiting for the shared search slot. "
                   f"Next capacity check: {_local_time(retry_at)}.")

    st.markdown("**Recent activity**")
    recent = list(live.get("activity_logs") or [])[:10]
    if not recent:
        st.caption("No completed activity yet.")
    for entry in recent:
        with st.container(border=True):
            stage = JOB_LABELS.get(str(entry.get("stage") or ""), str(entry.get("stage") or "Background work"))
            subject = entry.get("name") or entry.get("institution_name") or "Shared index"
            st.markdown(f"**{_local_time(entry.get('activity_at'))} · {stage} · {subject}**")
            areas = entry.get("research_areas") or []
            audit_steps = entry.get("audit_steps") or []
            interest_step = next((step for step in reversed(audit_steps)
                                  if step.get("step") == "RESEARCH_INTERESTS"), {})
            paper_area_step = next((step for step in reversed(audit_steps)
                                    if step.get("step") == "PAPER_RESEARCH_AREAS"), {})
            display_area_step = (
                paper_area_step if paper_area_step.get("interests") else interest_step
            )
            if display_area_step.get("interests"):
                areas = display_area_step["interests"]
            entry_stage = str(entry.get("stage") or "")
            area_text = ", ".join(areas) if areas else (
                "Not established yet" if entry_stage in {
                    "MATCH_FACULTY_PUBLICATIONS", "QWEN_REVIEW_PUBLICATION"
                }
                else "No accepted research category yet"
                if entry_stage == "ENRICH_CLASSIFY_PAPER"
                else "Not linked"
            )
            if not areas and paper_area_step.get('status')=='AWAITING_MODEL_REVIEW':
                area_text = 'Verified papers found — awaiting Qwen research-area review'
            elif not areas and paper_area_step.get('status')=='NO_SUPPORTED_AREAS':
                area_text = 'Verified papers reviewed — no broad research area accepted'
            elif not areas and interest_step.get('status')=='AWAITING_MODEL_REVIEW':
                area_text = ('Biography found — awaiting Qwen review'
                             if interest_step.get('evidence_status')=='BIOGRAPHY_FOUND'
                             else 'Research-profile review is pending')
            elif not areas and interest_step.get('status')=='MODEL_UNAVAILABLE':
                area_text = 'Review unavailable in this run — interests not established'
            elif not areas and interest_step.get('status')=='NO_SUPPORTED_INTERESTS':
                area_text = ('No direct research interests found' if interest_step.get('evidence_status')=='NO_DIRECT_INTEREST_EVIDENCE'
                             else 'Evidence checked — no research areas accepted')
            if entry_stage == "ENRICH_CLASSIFY_PAPER":
                st.caption(
                    f"Professor profile: {area_text} · "
                    f"University: {entry.get('institution_name') or 'Not available'}"
                )
                paper_categories = [
                    str(value) for value in (entry.get("paper_categories") or [])
                    if str(value).strip()
                ]
                st.caption(
                    "Paper category: "
                    + (", ".join(paper_categories) if paper_categories else "none accepted")
                )
            else:
                st.caption(
                    f"Research area: {area_text} · "
                    f"University: {entry.get('institution_name') or 'Not available'}"
                )
            linked_professors = entry.get("linked_professors") or []
            if str(entry.get("stage") or "") == "ENRICH_CLASSIFY_PAPER" and linked_professors:
                label = "Linked professor" if len(linked_professors) == 1 else "Linked professors"
                st.caption(f"{label}: {', '.join(str(value) for value in linked_professors)}")
            abstract_status = str(entry.get("abstract_status") or "")
            if abstract_status == "NO_DIRECT_METADATA_SOURCE":
                st.caption(
                    "Abstract metadata: no direct paper landing page is stored; "
                    "the profile/publications URL is retained as provenance only."
                )
            elif abstract_status == "TITLE_CONFLICT":
                st.caption(
                    "Abstract metadata: fetched page title did not match this paper closely enough; "
                    "the page was not used as paper-level evidence."
                )
            if paper_area_step.get("interests"):
                st.caption(
                    "Medium confidence · Research areas summarized from identity-verified papers; "
                    "supporting paper titles are retained in the audit step."
                )
            elif interest_step.get("interests"):
                if interest_step.get("evidence_method") == "EXPLICIT_PROFILE_SECTION":
                    st.caption(
                        "High confidence · Explicit research interests found on a verified faculty, personal, or lab page."
                    )
                elif interest_step.get("evidence_method") == "QWEN_BIO_SUMMARY":
                    st.caption(
                        "Medium-low confidence · Research areas summarized from a verified subject-local biography."
                    )
            if interest_step.get('extracted_interests') and not interest_step.get('interests'):
                st.caption('Extracted website interests (unreviewed): '+', '.join(interest_step['extracted_interests']))
            if interest_step.get('status')=='AWAITING_MODEL_REVIEW':
                st.info('Qwen review is pending. The worker retries saved evidence periodically; publication searches are not repeated.')
            if paper_area_step.get('status')=='AWAITING_MODEL_REVIEW':
                st.info('Verified papers are saved. Qwen paper-area summarization will retry without repeating publication discovery.')
            if entry.get("evidence_text"):
                st.write(f"Paper/evidence: {str(entry['evidence_text'])[:500]}")
            st.write(f"Result: {entry.get('result_status') or 'Finished successfully'}")
            if any(step.get('step')=='QWEN_SCHOLAR_REVIEW' and step.get('status')=='QUEUED'
                   for step in entry.get('audit_steps') or []):
                st.info('Google Scholar identity verification and publication extraction are queued as a separate background job.')
            if entry.get("result_detail"):
                st.caption(str(entry["result_detail"])[:500])
            if entry.get("source_url"):
                st.caption(f"Source: {entry['source_url']}")
            for step in entry.get("audit_steps") or []:
                label = str(step.get("step") or "Step").replace("_", " ").title()
                status = str(step.get("status") or "UNKNOWN").replace("_", " ")
                details = []
                if step.get("papers_found") is not None:
                    details.append(f"papers: {step['papers_found']}")
                if step.get("results_returned") is not None:
                    details.append(f"results: {step['results_returned']}")
                if step.get("scholar_candidates") is not None:
                    details.append(f"Scholar profiles: {step['scholar_candidates']}")
                if step.get("saved_candidates") is not None:
                    details.append(f"saved candidates: {step['saved_candidates']}")
                if step.get("papers_imported") is not None:
                    details.append(f"newly linked: {step['papers_imported']}")
                if step.get("rows_seen") is not None:
                    details.append(f"Scholar rows: {step['rows_seen']}")
                if step.get("accepted_rows") is not None:
                    details.append(f"accepted publications: {step['accepted_rows']}")
                if step.get("rejected_rows") is not None:
                    details.append(f"rejected non-publications: {step['rejected_rows']}")
                if step.get("review_required_rows") is not None:
                    details.append(f"staff review: {step['review_required_rows']}")
                if step.get("qwen_reviewed_rows") is not None:
                    details.append(f"ambiguous rows sent to Qwen: {step['qwen_reviewed_rows']}")
                if step.get('papers_already_linked') is not None:
                    details.append(f"already linked: {step['papers_already_linked']}")
                if step.get("deterministic_decision"):
                    details.append(f"decision: {step['deterministic_decision']}")
                if step.get("decision_signals"):
                    details.append(
                        "signals: " + ", ".join(step["decision_signals"])
                    )
                if step.get("qwen_same_person"):
                    details.append(f"Qwen same person: {step['qwen_same_person']}")
                if step.get("qwen_confidence") is not None:
                    details.append(f"Qwen confidence: {step['qwen_confidence']}")
                if step.get("qwen_matching_signals"):
                    details.append(
                        "Qwen evidence: " + ", ".join(
                            str(value) for value in step["qwen_matching_signals"]
                        )
                    )
                if step.get("qwen_conflicts"):
                    details.append(
                        "Qwen conflicts: " + ", ".join(
                            str(value) for value in step["qwen_conflicts"]
                        )
                    )
                if step.get("interests"):
                    details.append(
                        "fields: " + ", ".join(str(value) for value in step["interests"])
                    )
                if step.get("source_url"):
                    details.append(str(step["source_url"]))
                if step.get("reason"):
                    details.append(str(step["reason"]))
                suffix = f" · {' · '.join(details)}" if details else ""
                st.caption(f"• {label}: {status}{suffix}")
                if step.get("step") == "SCHOLAR_PUBLICATION_FILTER":
                    review_rows = step.get("review_required") or []
                    if review_rows:
                        titles = [str(row.get("title") or "") for row in review_rows[:8]]
                        st.warning(
                            "Scholar rows need staff review and were not indexed as papers: "
                            + "; ".join(title for title in titles if title)
                        )


live_panel()

st.subheader("Research areas")
with st.form("schedule_topic"):
    area = st.text_input("Add or refresh a research area")
    submitted = st.form_submit_button("Queue research-area index")
if submitted and area.strip():
    _, job = request_topic_index(area.strip(), requested_by=int(user["id"]))
    st.success("Research-area indexing is queued." if job else "This research area is current.")
    st.rerun()

operations = list_radar_operations(int(user["id"]), limit=250)
if operations["topics"]:
    st.dataframe([{
        "Research area": row["requested_query"], "Stage": row["coverage_stage"],
        "Faculty matches": row["verified_count"], "Papers": row["papers_found"],
        "Problems": row["problem_count"], "Last updated": row["last_indexed_at"],
    } for row in operations["topics"]], width="stretch", hide_index=True)
else:
    st.info("No research areas have been requested yet.")

st.subheader("Needs staff attention")
failed = [job for job in operations["jobs"] if job["status"] in {"failed", "stalled"}]
if failed:
    st.markdown("**Failed or stalled jobs**")
    st.dataframe([{
        "Task": JOB_LABELS.get(row["job_type"], row["job_type"]),
        "University": row.get("institution_name") or "—",
        "Professor": row.get("professor_name") or "—",
        "Problem": row.get("last_error") or "Worker heartbeat expired",
    } for row in failed], width="stretch", hide_index=True)
    selected = st.selectbox("Failed task", failed, format_func=lambda row: f"#{row['id']} {JOB_LABELS.get(row['job_type'], row['job_type'])}")
    left, right = st.columns(2)
    if left.button("Retry selected task"):
        retry_radar_job(int(user["id"]), int(selected["id"]))
        st.rerun()
    if right.button("Stop selected task"):
        cancel_radar_job(int(user["id"]), int(selected["id"]))
        st.rerun()
else:
    st.success("No failed or stalled background jobs.")

if operations["directory_issues"]:
    st.markdown("**Faculty pages needing review**")
    st.dataframe(operations["directory_issues"], width="stretch", hide_index=True)
if operations["faculty_page_issues"]:
    st.markdown("**Faculty pages requiring a decision**")
    st.caption("Only genuinely ambiguous pages appear here. Rejected pages and temporary fetch retries are handled automatically.")
    reason_labels = {
        "NO_FACULTY_SCOPE_HEADING": "The page contains people but does not establish a faculty-directory scope.",
        "NO_REPEATED_ATTRIBUTABLE_FACULTY_CARDS": "The page may be a directory, but its person records could not be separated safely.",
        "NO_PROFILE_HOSTS": "The extracted people do not have usable profile links.",
        "NO_CANONICAL_ORGANIZATIONAL_SCOPE": "The page is not clearly owned by a university, college, school, department, or program.",
    }
    st.dataframe([{
        "University": row["institution_name"],
        "Page": row.get("final_url") or row.get("candidate_url"),
        "Why review is needed": reason_labels.get(
            str(row.get("classification_reason") or ""),
            str(row.get("classification_reason") or "The page type is uncertain."),
        ),
        "Found by": "Official site" if row.get("discovery_method") == "OFFICIAL_NAVIGATION" else "Search recovery",
    } for row in operations["faculty_page_issues"]], width="stretch", hide_index=True)
if operations["roster_member_issues"]:
    st.markdown("**Roster entries that failed individual-profile validation**")
    st.caption("These staged entries are not canonical professors and receive no publication or enrichment jobs.")
    st.dataframe([{
        **row,
        "profile_evidence": json.dumps(
            row.get("profile_evidence") or {}, ensure_ascii=False, default=str
        ),
    } for row in operations["roster_member_issues"]], width="stretch", hide_index=True)
    cases = {int(row['id']): row for row in operations['roster_member_issues']}
    case_id = st.selectbox('Roster person to review', list(cases),
                          format_func=lambda key: f"{cases[key]['displayed_name']} — {cases[key]['institution_name']} (#{key})")
    case = cases[case_id]
    edits = case.get('staff_overrides') or {}
    st.dataframe([case], width='stretch', hide_index=True)
    with st.form(f'edit_roster_{case_id}'):
        name = st.text_input('Name', value=edits.get('name', case.get('displayed_name') or ''))
        title = st.text_input('Role title', value=edits.get('title', case.get('displayed_title') or ''))
        profile = st.text_input('Official profile URL', value=edits.get('profile_url', case.get('profile_url') or ''))
        email = st.text_input('Email', value=edits.get('email', case.get('email') or ''))
        office = st.text_input('Office', value=edits.get('office_address', case.get('office_address') or ''))
        save_roster = st.form_submit_button('Save corrections and revalidate')
    if save_roster:
        try:
            save_roster_review_record(int(user['id']), case_id,
                dict(name=name, title=title, profile_url=profile, email=email, office_address=office))
            st.rerun()
        except ValueError as error:
            st.error(str(error))
if operations.get("research_profile_issues"):
    st.markdown("**Professors needing research-profile review**")
    st.caption(
        "These verified professors had no explicit research-interest statement, "
        "no usable verified publication profile, and no biography that could establish research areas."
    )
    profile_cases = operations["research_profile_issues"]
    st.dataframe([{
        "Professor": row.get("name"),
        "University": row.get("institution_name"),
        "Department": row.get("department") or "—",
        "Faculty role": row.get("faculty_title") or "—",
        "Profile": row.get("faculty_source_url") or "—",
    } for row in profile_cases], width="stretch", hide_index=True)
    selected_profile = st.selectbox(
        "Research profile to review",
        profile_cases,
        format_func=lambda row: (
            f"{row.get('name') or 'Unknown'} — {row.get('institution_name') or 'Unknown university'}"
        ),
    )
    with st.form(f"manual_research_profile_{selected_profile['professor_id']}"):
        primary_field = st.text_input(
            "Primary field",
            value=str(selected_profile.get("department") or ""),
            help="Example: Computer Science, Communication, Mechanical Engineering",
        )
        interest_text = st.text_area(
            "Research areas (one per line or comma-separated)",
            help="Use concise research-area labels. These become staff-reviewed evidence.",
        )
        notes = st.text_area("Review notes", value="Manual research-profile review")
        save_profile = st.form_submit_button("Save research profile")
    if save_profile:
        labels = [
            value.strip()
            for line in interest_text.splitlines()
            for value in line.split(",")
            if value.strip()
        ]
        try:
            save_manual_research_profile(
                int(user["id"]), int(selected_profile["professor_id"]),
                primary_field=primary_field, interests=labels, notes=notes,
            )
            st.success("Manual research profile saved.")
            st.rerun()
        except ValueError as error:
            st.error(str(error))

if operations.get("publication_row_issues"):
    st.markdown("**Scholar rows needing publication review**")
    st.caption(
        "These rows were not indexed as papers because deterministic rules and Qwen "
        "could not establish publication status with high confidence."
    )
    review_rows = operations["publication_row_issues"]
    st.dataframe([{
        "Professor": row.get("name"),
        "University": row.get("institution_name"),
        "Title": row.get("title"),
        "Year": row.get("publication_year"),
        "Authors": row.get("authors"),
        "Venue": row.get("venue"),
        "Qwen": row.get("model_decision") or "UNCERTAIN",
        "Confidence": row.get("model_confidence"),
        "Reason": row.get("model_reason"),
    } for row in review_rows], width="stretch", hide_index=True)
    selected_row = st.selectbox(
        "Scholar row to review",
        review_rows,
        format_func=lambda row: (
            f"{row.get('name') or 'Unknown'} — {row.get('title') or 'Untitled'}"
        ),
    )
    st.caption(f"Source: {selected_row.get('source_url') or 'Not available'}")
    left, right = st.columns(2)
    if left.button("Accept as publication", key=f"accept_scholar_row_{selected_row['review_id']}"):
        try:
            approve_scholar_publication_row(
                int(user["id"]), int(selected_row["review_id"])
            )
            st.success("Scholar row accepted and linked as a publication.")
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    if right.button("Reject as non-publication", key=f"reject_scholar_row_{selected_row['review_id']}"):
        try:
            reject_scholar_publication_row(
                int(user["id"]), int(selected_row["review_id"])
            )
            st.success("Scholar row rejected and kept out of the publication index.")
            st.rerun()
        except ValueError as error:
            st.error(str(error))

if operations["publication_identity_issues"]:
    st.markdown("**Publication sources needing review**")
    st.caption("Faculty approval is independent. Scholar papers are attached only after name plus an independent identity signal agree.")
    st.dataframe([{
        "Professor": row.get("name"),
        "University": row.get("institution_name"),
        "Faculty": "Approved" if row.get("faculty_status") == "VERIFIED" else row.get("faculty_status"),
        "Role": row.get("faculty_title") or "Not stated",
        "Publications": row.get("publication_identity_status") or "NOT_CHECKED",
        "Why review is needed": row.get("reason"),
        "Evidence": row.get("evidence"),
    } for row in operations["publication_identity_issues"]], width="stretch", hide_index=True)
    publication_issue = st.selectbox(
        "Publication identity case",
        operations["publication_identity_issues"],
        format_func=lambda row: (
            f"{row.get('name') or 'Unknown'} — "
            f"{row.get('institution_name') or 'Unknown university'}"
        ),
    )
    st.dataframe([{**publication_issue, 'evidence': json.dumps(publication_issue.get('evidence') or {}, default=str)}], hide_index=True)
    with st.form(f"edit_publication_identity_case_{publication_issue['id']}_{publication_issue['professor_id']}"):
        st.write(f"Faculty role: {publication_issue.get('faculty_title') or 'Not stated'}")
        scholar_url = st.text_input(
            "Google Scholar profile candidate",
            value=str(publication_issue.get("candidate_url") or ""),
            help="Saving this URL does not approve it or attach papers. Qwen and deterministic checks run first.",
        )
        save_candidate = st.form_submit_button("Save candidate and queue review")
        reject_candidate = st.form_submit_button("Reject this candidate")
    if save_candidate:
        try:
            save_publication_review_candidate(
                int(user["id"]), int(publication_issue["professor_id"]), scholar_url
            )
            st.success("Candidate saved. Qwen review is queued and may take up to five minutes.")
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    if reject_candidate:
        if not scholar_url.strip():
            st.error("There is no candidate URL to reject.")
        else:
            reject_publication_candidate(
                int(user["id"]), int(publication_issue["professor_id"]), scholar_url
            )
            st.success("Candidate rejected. The professor remains verified, without attached papers.")
            st.rerun()
    if st.button("Queue all unresolved publication reviews"):
        queued = requeue_unresolved_publication_reviews(int(user["id"]))
        st.success(f"Queued {queued} unresolved publication review job(s).")
        st.rerun()
if operations["hiring_issues"]:
    st.markdown("**Hiring pages unavailable**")
    st.dataframe(operations["hiring_issues"], width="stretch", hide_index=True)
if not any((failed, operations["directory_issues"], operations["faculty_page_issues"],
            operations["roster_member_issues"], operations["publication_identity_issues"],
            operations["hiring_issues"])):
    st.success("Nothing currently requires staff review.")

if any(row["status"] == "stalled" for row in operations["jobs"]):
    if st.button("Recover abandoned jobs"):
        count = recover_stalled_radar_jobs(int(user["id"]))
        st.success(f"Recovered {count} job(s).")
        st.rerun()
