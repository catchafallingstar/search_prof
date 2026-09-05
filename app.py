import streamlit as st

from auth import account_controls
from db import count_active_opportunities, database_is_ready, fetch_active_opportunities
from radar_store import (
    RADAR_DISCOVERY_VERSION,
    fetch_indexing_runtime_state,
    fetch_indexed_professors,
    fetch_topic_verification_progress,
    request_topic_index,
    request_visible_hiring_refreshes,
)
from ui import (
    configure_page,
    demo_opportunities,
    filter_demo,
    navigation,
    render_professor_prospect,
    render_opportunity,
)


PAGE_SIZE = 25
MAX_VISIBLE_RESULTS = 100


configure_page("Find active PhD opportunities")
navigation()
account_controls()

st.markdown(
        """
        <section class="sr-hero" role="banner" aria-label="ScholarRadar hero">
            <div class="sr-logo" aria-hidden="true">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">
                    <circle cx="12" cy="12" r="10" fill="#00274c" />
                    <path d="M12 6v6l4 2" stroke="#ffcb05" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
                    <circle cx="12" cy="12" r="2" fill="#ffcb05" />
                </svg>
            </div>
            <h1>ScholarRadar<span class="sr-title-accent"> — Active Opportunities</span></h1>
            <p>Find labs actively recruiting PhD students and postdocs — verified openings and public signals.</p>
        </section>
        """,
        unsafe_allow_html=True,
)

saved_search = st.session_state.get(
    "sr_search",
    {"area": "", "position": "All", "institution": ""},
)

with st.form("opportunity_search", border=True):
    area_col, type_col, school_col, button_col = st.columns(
        [2.4, 1.15, 1.65, 0.8], vertical_alignment="bottom"
    )
    area = area_col.text_input(
        "Research area",
        value=saved_search["area"],
        placeholder="Machine learning, robotics, neuroscience…",
    )
    position_options = [
        "All", "PhD", "Postdoc", "Research Assistant", "Masters", "Internship"
    ]
    position = type_col.selectbox(
        "Position",
        position_options,
        index=position_options.index(saved_search["position"]),
    )
    institution = school_col.text_input(
        "University (optional)", value=saved_search["institution"]
    )
    search_submitted = button_col.form_submit_button(
        "Search", type="primary", width="stretch"
    )

ready = database_is_ready()
if search_submitted:
    if len(area.strip()) < 3:
        st.info("Enter a specific research area of at least three characters.")
    else:
        new_search = {
            "area": " ".join(area.split()),
            "position": position,
            "institution": " ".join(institution.split()),
        }
        st.session_state["sr_search"] = new_search
        st.session_state["sr_pending_search"] = new_search
        st.session_state["sr_professor_limit"] = PAGE_SIZE
        st.session_state.pop("sr_last_hiring_poll", None)
        # End this run before any prior-topic result components can be reused.
        # The next run renders the new topic's loading state, then schedules it.
        st.rerun()

pending_search = st.session_state.get("sr_pending_search")
if pending_search and pending_search == saved_search:
    st.session_state.pop("sr_pending_search", None)
    if ready:
        starting = st.status(
            f"Starting search for {saved_search['area']}…",
            state="running",
            expanded=False,
        )
        try:
            request_topic_index(saved_search["area"], desired_results=100)
        except Exception as error:
            starting.update(label="Search could not be scheduled", state="error")
            st.error(f"ScholarRadar could not schedule this research index: {error}")
        else:
            st.rerun()

active_area = saved_search["area"]
active_position = saved_search["position"]
active_institution = saved_search["institution"]

if ready:
    try:
        opportunities = fetch_active_opportunities(
            active_area, active_position, "All"
        )
        if active_institution:
            opportunities = [
                row for row in opportunities
                if active_institution.casefold() in str(row["institution_name"]).casefold()
            ]
    except Exception as error:
        st.error(f"The opportunity database could not be loaded: {error}")
        opportunities = []
else:
    st.info(
        "Preview mode: connect PostgreSQL and apply db.sql to replace these "
        "example opportunities with live data."
    )
    opportunities = filter_demo(
        demo_opportunities(), active_area, active_position, "All"
    )

st.subheader(f"Posted on ScholarRadar ({len(opportunities)})")
st.caption("Openings submitted on ScholarRadar and approved for display.")

if not opportunities:
    indexed_count = count_active_opportunities() if ready else 0
    if ready and indexed_count == 0:
        st.warning(
            "No approved ScholarRadar openings yet."
        )
    elif active_area:
        st.warning("No approved openings match these filters.")
else:
    columns = st.columns(3)
    for index, opportunity in enumerate(opportunities):
        with columns[index % 3]:
            render_opportunity(opportunity)

@st.fragment(run_every="4s")
def render_radar_panel(
    research_area: str,
    position_type: str,
    institution_filter: str,
    database_ready: bool,
) -> None:
    """Refresh results without rerunning or resetting the search form."""
    radar_professors: list[dict] = []
    topic = None
    if database_ready and research_area:
        visible_limit = int(st.session_state.get("sr_professor_limit", PAGE_SIZE))
        try:
            topic, radar_professors = fetch_indexed_professors(
                research_area,
                position_type=position_type,
                gpa_policy="All",
                institution=institution_filter,
                limit=visible_limit,
            )
            if topic and radar_professors:
                request_visible_hiring_refreshes(
                    int(topic["id"]),
                    [int(row["professor_id"]) for row in radar_professors],
                )
        except Exception as error:
            st.error(f"The verified professor index could not be loaded: {error}")

    if research_area and topic:
        available = int(topic.get("verified_count") or 0)
        if topic.get("status") in {"new", "indexing", "partial"}:
            candidates = int(topic.get("candidates_seen") or 0)
            active_job_type = topic.get("active_job_type")
            active_job_status = topic.get("active_job_status")
            runtime = fetch_indexing_runtime_state()
            verification_progress = fetch_topic_verification_progress(int(topic["id"]))
            stage_labels = {
                ("DISCOVER_CANDIDATES", "queued"): "Waiting to find matching researchers",
                ("DISCOVER_CANDIDATES", "running"): (
                    "Finding researchers from matching papers"
                ),
                ("REINDEX_RESEARCH", "queued"): "Waiting to update this research area",
                ("REINDEX_RESEARCH", "running"): "Updating matching researchers",
                ("VERIFY_FACULTY", "queued"): "Waiting to verify faculty identities",
                ("VERIFY_FACULTY", "running"): (
                    "Verifying faculty on university pages"
                ),
                ("CHECK_GRANTS", "queued"): "Waiting to check grants",
                ("CHECK_GRANTS", "running"): "Checking grants",
                ("CHECK_HIRING", "queued"): "Waiting to check hiring pages",
                ("CHECK_HIRING", "running"): (
                    "Checking professor and lab pages"
                ),
            }
            indexing_label = stage_labels.get(
                (active_job_type, active_job_status),
                "Current discovery pass complete"
                if topic.get("sources_exhausted")
                else "No indexing task is currently scheduled",
            )
            job_is_active = active_job_status in {"queued", "running"}
            indexing_state = "running" if job_is_active else "complete"
            index_status = st.status(
                indexing_label, state=indexing_state, expanded=False
            )
            checked = int(verification_progress["identities_checked"])
            pending = int(verification_progress["identities_pending"])
            index_status.write(
                f"**{candidates}** candidates · **{checked}** checked · "
                f"**{available}** verified"
            )
            if pending:
                index_status.caption(f"{pending} identity checks remaining")
            if active_job_status == "queued":
                if int(runtime["healthy_workers"]) == 0:
                    index_status.error(
                        "No indexing worker is connected. The task is saved, but it "
                        "cannot begin until the worker starts."
                    )
                else:
                    index_status.caption("Waiting for the background worker")
            elif active_job_status == "running":
                index_status.caption("Updates automatically")
            if job_is_active:
                st.caption("Indexing status updates automatically.")

    if radar_professors:
        total_count = int(
            radar_professors[0].get("total_count") or len(radar_professors)
        )
        visible_limit = int(st.session_state.get("sr_professor_limit", PAGE_SIZE))
        visible_count = min(len(radar_professors), total_count)
        st.subheader(f"Professor radar ({total_count})")
        st.caption(
            "Posted openings first, then online hiring statements, possible opportunities, "
            "and other research matches."
        )
        st.caption("Lab GPA information does not replace graduate-program requirements.")
        page_text, page_action = st.columns([4, 1], vertical_alignment="center")
        page_text.caption(
            f"Showing {visible_count} of {min(total_count, MAX_VISIBLE_RESULTS)} "
            "available verified matches."
        )
        if visible_limit < min(total_count, MAX_VISIBLE_RESULTS):
            if page_action.button(
                "Load next 25", key="load_more_top", width="stretch"
            ):
                st.session_state["sr_professor_limit"] = min(
                    MAX_VISIBLE_RESULTS, visible_limit + PAGE_SIZE
                )
                st.rerun(scope="fragment")
        category_sections = [
            (
                {"confirmed_opening"},
                "Posted on ScholarRadar",
                None,
            ),
            (
                {"public_hiring_signal"},
                "Hiring signals found online",
                "Quoted from the linked source. Check the page before contacting the professor.",
            ),
            (
                {"early_career_funded", "early_career", "funded_lab"},
                "Possible opportunities — hiring not confirmed",
                None,
            ),
            (
                {"research_match"},
                "Other verified faculty matches — hiring unknown",
                None,
            ),
        ]
        for idx, (categories, heading, explanation) in enumerate(category_sections):
            rows = [
                row
                for row in radar_professors
                if row["result_category"] in categories
            ]
            if not rows:
                continue
            st.markdown(
                f'<h3 class="sr-section-heading" id="section-{idx}" aria-level="3">{heading} ({len(rows)})</h3>',
                unsafe_allow_html=True,
            )
            if explanation:
                st.caption(explanation)
            columns = st.columns(2)
            for index, professor in enumerate(rows):
                with columns[index % 2]:
                    render_professor_prospect(professor)

        if visible_limit < min(total_count, MAX_VISIBLE_RESULTS):
            if st.button("Load next 25", key="load_more_bottom", width="stretch"):
                st.session_state["sr_professor_limit"] = min(
                    MAX_VISIBLE_RESULTS, visible_limit + PAGE_SIZE
                )
                st.rerun(scope="fragment")
        elif total_count > MAX_VISIBLE_RESULTS:
            st.caption("Showing the first 100 verified faculty matches.")

        if any(
            bool(row.get("hiring_refresh_needed") or row.get("hiring_check_pending"))
            for row in radar_professors
        ):
            st.caption("Hiring-page checks are continuing. Results update automatically.")
    elif research_area and topic:
        if int(topic.get("discovery_version") or 0) < RADAR_DISCOVERY_VERSION:
            st.info(
                "Refreshing research matches with the latest relevance checks. "
                "Updated results will appear when this pass finishes."
            )
        else:
            st.info(
                "No verified matches yet. This research area is being indexed."
            )
    elif research_area and database_ready:
        st.info(
            "Starting this research area. Verified matches will appear here."
        )


render_radar_panel(active_area, active_position, active_institution, ready)

st.divider()
cta, action = st.columns([4, 1], vertical_alignment="center")
cta.subheader("Are you faculty or a university recruiter?")
cta.write("Complete role verification, then publish an opening with a clear expiration date.")
if action.button("Start verification →", width="stretch"):
    st.switch_page("pages/1_Post_an_opening.py")
