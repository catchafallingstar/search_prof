import streamlit as st

from auth import account_controls
from db import count_active_opportunities, database_is_ready, fetch_active_opportunities
from ingestion.radar_pipeline import execute_radar
from settings import setting_bool
from ui import (
    GPA_LABELS,
    configure_page,
    demo_opportunities,
    filter_demo,
    navigation,
    render_professor_prospect,
    render_opportunity,
)

configure_page("Find active PhD opportunities")
navigation()
account_controls()

st.markdown(
    """
    <section class="sr-hero">
      <div class="sr-kicker">OPPORTUNITY-FIRST PhD SEARCH</div>
      <h1>Find labs that are actively looking for people like you</h1>
      <p>Search verified openings and fresh public hiring signals. See funding,
      requirements, and evidence before you spend time applying.</p>
    </section>
    """,
    unsafe_allow_html=True,
)

with st.form("opportunity_search", border=True):
    area_col, type_col, gpa_col, count_col, button_col = st.columns(
        [2.2, 1.05, 1.35, 0.8, 0.75], vertical_alignment="bottom"
    )
    area = area_col.text_input("Research area", placeholder="Machine learning, robotics, neuroscience…")
    position = type_col.selectbox("Looking for", ["All", "PhD", "Postdoc", "Research Assistant", "Masters", "Internship"])
    gpa_policy = gpa_col.selectbox("GPA policy", ["All", *GPA_LABELS.keys()], format_func=lambda key: "All policies" if key == "All" else GPA_LABELS[key])
    results_wanted = count_col.selectbox("Professors", [10, 25, 50, 100], index=1)
    include_live_radar = st.checkbox(
        "Include a live public-web radar when searching",
        value=True,
        help="A new topic can take about one minute. Recent identical scans are reused.",
    )
    include_unknown_gpa = st.checkbox(
        "Include promising professors when their GPA policy and current hiring status are not confirmed",
        value=True,
        help=(
            "Useful when you want broader options. These professors are never labeled GPA-flexible "
            "unless a verified source explicitly says so."
        ),
    )
    search_submitted = button_col.form_submit_button("Search", type="primary", width="stretch")

ready = database_is_ready()
if ready:
    try:
        opportunities = fetch_active_opportunities(area, position, gpa_policy)
    except Exception as error:
        st.error(f"The opportunity database could not be loaded: {error}")
        opportunities = []
else:
    st.info("Preview mode: connect PostgreSQL and apply db.sql to replace these example opportunities with live data.")
    opportunities = filter_demo(demo_opportunities(), area, position, gpa_policy)

radar_professors = []
public_radar_enabled = setting_bool("PUBLIC_RADAR_ENABLED", True)
if search_submitted and include_live_radar and ready and public_radar_enabled:
    if len(area.strip()) < 3:
        st.info("Enter a specific research area of at least three characters to run the web radar.")
    else:
        status = st.status("Starting targeted public-web radar…", expanded=True)
        progress_bar = st.progress(0)
        progress_text = st.empty()

        def show_progress(stage: str, percent: int, counters: dict[str, int]) -> None:
            status.update(label=stage, state="running", expanded=True)
            progress_bar.progress(percent)
            summary = " · ".join(
                f"{key.replace('_', ' ').title()}: {value}"
                for key, value in counters.items()
                if value
            )
            progress_text.caption(summary or "Preparing sources…")

        try:
            scan = execute_radar(
                area,
                target_professors=results_wanted,
                progress_callback=show_progress,
            )
            radar_professors = scan["professors"]
            if position != "All":
                radar_professors = [
                    professor
                    for professor in radar_professors
                    if professor["result_category"] != "hiring_signal"
                    or professor.get("position_type") == position
                ]
            run = scan["run"]
            if run["status"] == "running":
                status.update(
                    label="This topic is already being scanned; refresh shortly",
                    state="running",
                    expanded=False,
                )
            else:
                status.update(
                    label="Radar scan complete" + (" (recent cache reused)" if scan["cached"] else ""),
                    state="complete",
                    expanded=False,
                )
                progress_bar.progress(100)
        except Exception as error:
            status.update(label="Radar scan could not complete", state="error", expanded=True)
            st.error(str(error))

st.subheader(f"Active opportunities ({len(opportunities)})")
st.caption("Every result identifies its source, verification level, and evidence. Funding signals are not treated as confirmed openings.")

if not opportunities:
    indexed_count = count_active_opportunities() if ready else 0
    if ready and indexed_count == 0:
        st.warning(
            "No approved on-site openings have been indexed yet. Submit this search with the "
            "live public-web radar enabled to look for recruiting evidence outside ScholarRadar."
        )
    else:
        st.warning("No indexed opportunities match those filters. Try All positions and All policies.")
        if gpa_policy != "All":
            st.caption(
                "Radar-discovered public signals use 'GPA policy not stated' unless an authorized "
                "submitter explicitly supplies a GPA policy."
            )
else:
    columns = st.columns(3)
    for index, opportunity in enumerate(opportunities):
        with columns[index % 3]:
            render_opportunity(opportunity)

if radar_professors and include_unknown_gpa:
    st.subheader(f"Professor radar ({len(radar_professors)})")
    st.caption(
        "The radar separates confirmed openings, public hiring signals, opportunity indicators, "
        "and research-only matches. A grant or new-lab signal never proves that a professor is hiring."
    )
    if gpa_policy != "All":
        st.info(
            "The professor results below have GPA policy not stated. They are broader options, "
            "not matches to your selected GPA policy. Verify both lab and graduate-school rules."
        )
    category_sections = [
        ("hiring_signal", "Strong public hiring signals"),
        ("likely_hiring", "Promising labs — hiring not confirmed"),
        ("research_match", "Research-matched professors"),
    ]
    for category, heading in category_sections:
        rows = [row for row in radar_professors if row["result_category"] == category]
        if not rows:
            continue
        st.markdown(f"#### {heading} ({len(rows)})")
        columns = st.columns(2)
        for index, professor in enumerate(rows):
            with columns[index % 2]:
                render_professor_prospect(professor)
elif radar_professors and not include_unknown_gpa:
    st.info(
        f"The radar found {len(radar_professors)} research-matched professors, but their GPA "
        "policies are not verified. Enable the broader-professor option to see them."
    )
elif search_submitted and include_live_radar and area.strip() and ready:
    st.info(
        "No relevant professors were discovered for this exact query in the recent research "
        "sources checked. Try a related or slightly broader research phrase."
    )

st.divider()
cta, action = st.columns([4, 1], vertical_alignment="center")
cta.subheader("Are you faculty or a university recruiter?")
cta.write("Complete role verification, then publish an opening with a clear expiration date.")
if action.button("Start verification →", width="stretch"):
    st.switch_page("pages/1_Post_an_opening.py")
