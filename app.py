import streamlit as st

from auth import account_controls
from db import count_active_opportunities, database_is_ready, fetch_active_opportunities
from ingestion.matchers import extract_roles_and_funding
from ingestion.radar_pipeline import execute_radar
from settings import setting, setting_bool
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
    results_wanted = count_col.selectbox(
        "Verified faculty goal",
        [10, 25, 50, 100],
        index=1,
        format_func=lambda value: {
            10: "10 — quick",
            25: "25 — standard",
            50: "50 — deep",
            100: "100 — deep index",
        }[value],
        help=(
            "This is a goal, not a guaranteed result count. ScholarRadar hides authors whose "
            "faculty identity cannot be verified. Deep goals use a larger candidate pool and "
            "normally need multiple continuation passes."
        ),
    )
    include_live_radar = st.checkbox(
        "Include a live public-web radar when searching",
        value=True,
        help=(
            "A bounded pass usually takes one to two minutes. If a large search is incomplete, "
            "press Search again to continue with unchecked candidates; completed decisions are reused."
        ),
    )
    include_unknown_gpa = st.checkbox(
        "Include promising professors when their GPA policy and current hiring status are not confirmed",
        value=True,
        help=(
            "Useful when you want broader options. These professors are never labeled GPA-flexible "
            "unless a verified source explicitly says so."
        ),
    )
    continue_incomplete = st.checkbox(
        "Continue checking an incomplete cached search",
        value=False,
        help=(
            "Leave this off to open recent results immediately. Turn it on and submit the same "
            "search to verify the next unchecked candidates without repeating completed work."
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
        if not setting("BRAVE_SEARCH_API_KEY").strip():
            st.warning(
                "Limited public-web coverage: BRAVE_SEARCH_API_KEY is not configured, so this "
                "local scan uses the slower DuckDuckGo fallback. OpenAlex research discovery "
                "still works, but hiring-page coverage will be less reliable."
            )
        if results_wanted >= 50:
            st.info(
                f"{results_wanted} is a deep-search goal. This pass is time-bounded so the page "
                "does not appear frozen; repeat the search with continuation enabled until the "
                "goal is reached or the candidate pool is exhausted."
            )
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
                continue_partial=continue_incomplete,
            )
            radar_professors = scan["professors"]
            if position != "All":
                wanted_signal_role = "Intern" if position == "Internship" else position
                radar_professors = [
                    professor
                    for professor in radar_professors
                    if professor["result_category"] not in {
                        "confirmed_opening", "public_hiring_signal"
                    }
                    or professor.get("position_type") == position
                    or wanted_signal_role in extract_roles_and_funding(
                        str(professor.get("hiring_evidence") or "")
                    )[0]
                ]
            run = scan["run"]
            progress_text.caption(
                f"Goal: {int(run.get('target_professors') or results_wanted)} verified · "
                f"{int(run.get('candidates_ranked') or 0)} candidates ranked · "
                f"{int(run.get('faculty_identities_checked') or 0)} identities checked · "
                f"{int(run.get('professors_found') or len(radar_professors))} verified"
            )
            remaining_candidates = max(
                0,
                int(run.get("candidates_ranked") or 0)
                - int(run.get("faculty_identities_checked") or 0),
            )
            if run["status"] == "running":
                status.update(
                    label="This topic is already being scanned; refresh shortly",
                    state="running",
                    expanded=False,
                )
            else:
                status.update(
                    label=(
                        "Radar continuation complete"
                        if scan.get("continued")
                        else "Radar scan complete" + (
                            " (recent cache reused)" if scan["cached"] else ""
                        )
                    ),
                    state="complete",
                    expanded=False,
                )
                progress_bar.progress(100)
                if (
                    remaining_candidates > 0
                    and int(run.get("professors_found") or 0) < results_wanted
                ):
                    st.info(
                        f"This bounded pass still has {remaining_candidates} ranked candidates "
                        "whose faculty identities have not been checked. Enable “Continue checking "
                        "an incomplete cached search” and press Search to continue; prior decisions "
                        "are reused."
                    )
        except Exception as error:
            status.update(label="Radar scan could not complete", state="error", expanded=True)
            st.error(str(error))

st.subheader(f"Confirmed ScholarRadar openings ({len(opportunities)})")
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
        "Results are ordered by evidence strength: confirmed ScholarRadar openings first, then "
        "current public hiring evidence, early-career/funding indicators, and finally research fit. "
        "A title or grant never proves that a professor is hiring."
    )
    if gpa_policy != "All":
        st.info(
            "The professor results below have GPA policy not stated. They are broader options, "
            "not matches to your selected GPA policy. Verify both lab and graduate-school rules."
        )
    # Keep the six evidence lanes internally for accurate badges and ordering,
    # but present only three concepts to students. The previous six headings
    # made closely related opportunity indicators look like different claims.
    category_sections = [
        (
            {"confirmed_opening", "public_hiring_signal"},
            "Hiring now — current evidence",
            "Confirmed ScholarRadar openings appear first, followed by current public recruiting evidence.",
        ),
        (
            {"early_career_funded", "early_career", "funded_lab"},
            "Likely opportunities — hiring not confirmed",
            "Early-career faculty and/or relevant active funding can make outreach promising, but neither proves an opening.",
        ),
        (
            {"research_match"},
            "Other verified faculty matches — hiring unknown",
            "These are verified faculty with matching recent research and no current hiring, early-career, or relevant-funding indicator.",
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
elif radar_professors and not include_unknown_gpa:
    st.info(
        f"The radar found {len(radar_professors)} research-matched professors, but their GPA "
        "policies are not verified. Enable the broader-professor option to see them."
    )
elif search_submitted and include_live_radar and area.strip() and ready:
    st.info(
        "No faculty-verified professors were found in this bounded pass. Research authors without "
        "an official university faculty page are intentionally hidden. Try a related phrase or a "
        "larger result count."
    )

st.divider()
cta, action = st.columns([4, 1], vertical_alignment="center")
cta.subheader("Are you faculty or a university recruiter?")
cta.write("Complete role verification, then publish an opening with a clear expiration date.")
if action.button("Start verification →", width="stretch"):
    st.switch_page("pages/1_Post_an_opening.py")
