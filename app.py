import streamlit as st

from auth import account_controls
from db import count_active_opportunities, database_is_ready, fetch_active_opportunities
from radar_store import fetch_indexed_professors, request_topic_index
from ui import (
    GPA_LABELS,
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
    <section class="sr-hero">
      <div class="sr-kicker">OPPORTUNITY-FIRST PhD SEARCH</div>
      <h1>Find labs that are actively looking for people like you</h1>
      <p>Search verified openings and an expanding index of verified faculty.
      See funding, requirements, and evidence before you spend time applying.</p>
    </section>
    """,
    unsafe_allow_html=True,
)

saved_search = st.session_state.get(
    "sr_search",
    {"area": "", "position": "All", "gpa_policy": "All", "institution": ""},
)

with st.form("opportunity_search", border=True):
    area_col, type_col, gpa_col, school_col, button_col = st.columns(
        [2.1, 1.05, 1.35, 1.4, 0.75], vertical_alignment="bottom"
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
        "Looking for",
        position_options,
        index=position_options.index(saved_search["position"]),
    )
    gpa_options = ["All", *GPA_LABELS.keys()]
    gpa_policy = gpa_col.selectbox(
        "GPA policy",
        gpa_options,
        index=gpa_options.index(saved_search["gpa_policy"]),
        format_func=lambda key: "All policies" if key == "All" else GPA_LABELS[key],
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
        st.session_state["sr_search"] = {
            "area": " ".join(area.split()),
            "position": position,
            "gpa_policy": gpa_policy,
            "institution": " ".join(institution.split()),
        }
        st.session_state["sr_professor_limit"] = PAGE_SIZE
        saved_search = st.session_state["sr_search"]
        if ready:
            try:
                request_topic_index(saved_search["area"], desired_results=100)
            except Exception as error:
                st.error(f"ScholarRadar could not schedule this research index: {error}")

active_area = saved_search["area"]
active_position = saved_search["position"]
active_gpa = saved_search["gpa_policy"]
active_institution = saved_search["institution"]

if ready:
    try:
        opportunities = fetch_active_opportunities(
            active_area, active_position, active_gpa
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
        demo_opportunities(), active_area, active_position, active_gpa
    )

st.subheader(f"Confirmed ScholarRadar openings ({len(opportunities)})")
st.caption(
    "Every result identifies its source, verification level, and evidence. "
    "Funding signals are not treated as confirmed openings."
)

if not opportunities:
    indexed_count = count_active_opportunities() if ready else 0
    if ready and indexed_count == 0:
        st.warning(
            "No approved on-site openings have been indexed yet. Faculty and "
            "university recruiters can submit an opening for verification."
        )
    elif active_area:
        st.warning("No approved openings match these filters.")
else:
    columns = st.columns(3)
    for index, opportunity in enumerate(opportunities):
        with columns[index % 3]:
            render_opportunity(opportunity)

radar_professors: list[dict] = []
topic = None
if ready and active_area:
    visible_limit = int(st.session_state.get("sr_professor_limit", PAGE_SIZE))
    try:
        topic, radar_professors = fetch_indexed_professors(
            active_area,
            position_type=active_position,
            gpa_policy=active_gpa,
            institution=active_institution,
            limit=visible_limit,
        )
    except Exception as error:
        st.error(f"The verified professor index could not be loaded: {error}")

if active_area and topic:
    available = int(topic.get("verified_count") or 0)
    if topic.get("status") in {"new", "indexing", "partial"}:
        candidates = int(topic.get("candidates_seen") or 0)
        topic_identity = str(topic["topic_key"])
        if st.session_state.get("sr_seen_topic") != topic_identity:
            st.session_state["sr_seen_topic"] = topic_identity
            st.session_state["sr_seen_verified"] = available

        refresh_requested = (
            st.session_state.pop("sr_refresh_requested", False)
            and st.session_state.pop("sr_refresh_topic", None) == topic_identity
        )
        if refresh_requested:
            previous_available = int(
                st.session_state.get("sr_seen_verified", available)
            )
            newly_verified = max(0, available - previous_available)
            if newly_verified:
                st.success(
                    f"Refresh complete: {newly_verified} new verified faculty "
                    f"profile{'s' if newly_verified != 1 else ''} added."
                )
            else:
                st.info(
                    "Refresh complete: no new verified profiles since your last "
                    "check. Indexing is still continuing in the background."
                )
            st.session_state["sr_seen_verified"] = available

        active_job_type = topic.get("active_job_type")
        active_job_status = topic.get("active_job_status")
        stage_labels = {
            ("DISCOVER_CANDIDATES", "queued"): "Research discovery is waiting to start",
            ("DISCOVER_CANDIDATES", "running"): "Finding research-matched candidates",
            ("REINDEX_RESEARCH", "queued"): "Research refresh is waiting to start",
            ("REINDEX_RESEARCH", "running"): "Refreshing research matches",
            ("VERIFY_FACULTY", "queued"): "Faculty verification is waiting to start",
            ("VERIFY_FACULTY", "running"): "Verifying faculty identities",
            ("CHECK_GRANTS", "queued"): "Funding checks are waiting to start",
            ("CHECK_GRANTS", "running"): "Checking public funding evidence",
            ("CHECK_HIRING", "queued"): "Hiring checks are waiting to start",
            ("CHECK_HIRING", "running"): "Checking public hiring evidence",
        }
        indexing_label = stage_labels.get(
            (active_job_type, active_job_status),
            "Current research set checked"
            if topic.get("sources_exhausted")
            else "Indexing in the background",
        )
        indexing_state = "complete" if topic.get("sources_exhausted") else "running"
        index_status = st.status(
            indexing_label, state=indexing_state, expanded=False
        )
        index_status.write(
            f"**{available}** verified faculty profiles currently available."
        )
        index_status.write(
            f"**{candidates}** research-matched candidates discovered."
        )
        if not topic.get("sources_exhausted"):
            index_status.write(
                "ScholarRadar is verifying identities and checking hiring and "
                "funding evidence. You can leave this page while it works."
            )
            if active_job_status == "queued":
                index_status.write(
                    "The worker is finishing another scheduled task first. This "
                    "research area is safely queued and has not failed."
                )

        if st.button("Refresh results", key="refresh_index"):
            st.session_state["sr_refresh_requested"] = True
            st.session_state["sr_refresh_topic"] = topic_identity
            st.rerun()

if radar_professors:
    total_count = int(radar_professors[0].get("total_count") or len(radar_professors))
    visible_limit = int(st.session_state.get("sr_professor_limit", PAGE_SIZE))
    visible_count = min(len(radar_professors), total_count)
    st.subheader(f"Professor radar ({total_count})")
    st.caption(
        "Results are ordered by evidence strength: confirmed openings, current public "
        "hiring evidence, early-career/funding indicators, and research fit. A title "
        "or grant never proves that a professor is hiring."
    )
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
            st.rerun()
    if active_gpa != "All":
        st.info(
            "Professor profiles with GPA policy not stated are broader options, not "
            "matches to the selected GPA policy. Verify both lab and graduate-school rules."
        )
    category_sections = [
        (
            {"confirmed_opening", "public_hiring_signal"},
            "Hiring now — current evidence",
            "Approved ScholarRadar openings appear first, followed by moderated public recruiting evidence.",
        ),
        (
            {"early_career_funded", "early_career", "funded_lab"},
            "Likely opportunities — hiring not confirmed",
            "Early-career faculty and relevant active funding can make outreach promising, but neither proves an opening.",
        ),
        (
            {"research_match"},
            "Other verified faculty matches — hiring unknown",
            "Verified faculty with matching recent research and no current hiring, early-career, or relevant-funding indicator.",
        ),
    ]
    for categories, heading, explanation in category_sections:
        rows = [row for row in radar_professors if row["result_category"] in categories]
        if not rows:
            continue
        st.markdown(f"#### {heading} ({len(rows)})")
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
            st.rerun()
    elif total_count > MAX_VISIBLE_RESULTS:
        st.caption("Showing the first 100 verified faculty matches.")
elif active_area and topic:
    st.info(
        "No verified professor profile matches these filters yet. ScholarRadar has "
        "recorded the research area and will add results after identity checks pass."
    )
elif active_area and ready:
    st.info(
        "ScholarRadar has recorded this research area. Verified professor matches "
        "will appear as the shared index grows."
    )

st.divider()
cta, action = st.columns([4, 1], vertical_alignment="center")
cta.subheader("Are you faculty or a university recruiter?")
cta.write("Complete role verification, then publish an opening with a clear expiration date.")
if action.button("Start verification →", width="stretch"):
    st.switch_page("pages/1_Post_an_opening.py")
