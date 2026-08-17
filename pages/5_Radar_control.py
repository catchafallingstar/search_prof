import streamlit as st

from auth import account_controls, require_site_admin
from db import database_is_ready, list_recent_radar_runs
from ingestion.radar_pipeline import execute_radar
from ui import configure_page, navigation, render_professor_prospect

configure_page("Radar operations")
navigation()
account_controls()

st.title("Radar operations")
st.write(
    "Run a targeted discovery scan and watch each stage. This searches one research "
    "area; it does not build a database of every professor."
)
if not database_is_ready():
    st.error("The database is not ready.")
    st.stop()

user, admin = require_site_admin()
st.caption(f"Signed in with {admin['admin_role']} authority")

with st.form("staff_radar"):
    research_area = st.text_input(
        "Research area", placeholder="Adversarial machine learning, robotics, neuroscience…"
    )
    target_professors = st.selectbox("Professor prospects to return", [10, 25, 50, 100], index=1)
    run_scan = st.form_submit_button("Run targeted radar", type="primary")

if run_scan:
    status = st.status("Starting radar…", expanded=True)
    bar = st.progress(0)
    details = st.empty()

    def show(stage: str, percent: int, counters: dict[str, int]) -> None:
        status.update(label=stage, state="running", expanded=True)
        bar.progress(percent)
        details.write(counters)

    try:
        result = execute_radar(
            research_area,
            target_professors=target_professors,
            requested_by=user["id"],
            progress_callback=show,
        )
        status.update(label="Radar complete", state="complete", expanded=False)
        if result["professors"]:
            st.subheader("Professor prospects")
            for professor in result["professors"]:
                render_professor_prospect(professor)
        else:
            st.info("No relevant professor prospects were found for this exact query.")
    except Exception as error:
        status.update(label="Radar failed", state="error", expanded=True)
        st.error(str(error))

st.subheader("Recent radar runs")
runs = list_recent_radar_runs(user["id"])
if runs:
    st.dataframe(runs, width="stretch", hide_index=True)
else:
    st.info("No radar runs have been recorded yet.")
