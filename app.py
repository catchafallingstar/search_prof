import streamlit as st

from auth import account_controls
from db import count_active_opportunities, database_is_ready, fetch_active_opportunities
from ui import (
    GPA_LABELS,
    configure_page,
    demo_opportunities,
    filter_demo,
    navigation,
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
    area_col, type_col, gpa_col, button_col = st.columns([2.2, 1.1, 1.4, 0.8], vertical_alignment="bottom")
    area = area_col.text_input("Research area", placeholder="Machine learning, robotics, neuroscience…")
    position = type_col.selectbox("Looking for", ["All", "PhD", "Postdoc", "Research Assistant", "Masters", "Internship"])
    gpa_policy = gpa_col.selectbox("GPA policy", ["All", *GPA_LABELS.keys()], format_func=lambda key: "All policies" if key == "All" else GPA_LABELS[key])
    button_col.form_submit_button("Search", type="primary", width="stretch")

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

st.subheader(f"Active opportunities ({len(opportunities)})")
st.caption("Every result identifies its source, verification level, and evidence. Funding signals are not treated as confirmed openings.")

if not opportunities:
    indexed_count = count_active_opportunities() if ready else 0
    if ready and indexed_count == 0:
        st.warning(
            "No active opportunities have been indexed yet. Search checks the local ScholarRadar "
            "database; it does not launch a live internet scan. Run the radar or submit and approve "
            "an opening to create searchable records."
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

st.divider()
cta, action = st.columns([4, 1], vertical_alignment="center")
cta.subheader("Are you faculty or a university recruiter?")
cta.write("Complete role verification, then publish an opening with a clear expiration date.")
action.link_button("Start verification →", "/Post_an_opening", width="stretch")
