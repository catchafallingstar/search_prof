import sys
import os
import streamlit as st
import pandas as pd
import uuid

from db import get_db_connection
from db import auto_migrate_db
from db import fetch_radar_dataframe, get_sqlalchemy_engine
from ingestion.i18n import TEXT, t, set_radar_context
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy
from ingestion.check_grants import check_and_save_grants  # Renamed/updated function
st.set_page_config(
    page_title="ScholarRadar | Academic Hiring Radar", page_icon="🎯", layout="wide"
)

# ----------------------------------------------------------------------
# 0. Session Initialization (NO os.environ!)
# ----------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

# Pre-check if form submit button was pressed in Streamlit's event queue
if st.session_state.get("mine_btn_submit"):
    st.session_state["is_mining"] = True

# ----------------------------------------------------------------------
# 1. Sidebar: Language Selection & Context Binding
# ----------------------------------------------------------------------
is_mining = st.session_state.get("is_mining", False)
st.sidebar.header("⚙️ Settings")
lang_choice = st.sidebar.radio("🌐 Language / 语言", ["English", "中文"], index=0, disabled=is_mining)
lang_code = "en" if lang_choice == "English" else "cn"

# ✅ BIND THREAD-LOCAL CONTEXT FOR THIS USER
set_radar_context(st.session_state["session_id"], lang_code)

# ----------------------------------------------------------------------
# 1.5. GLOBAL SESSION-AWARE STDOUT ROUTER
# ----------------------------------------------------------------------
class SessionAwareStreamCapture:
    def __init__(self, original_stdout):
        self._stdout = original_stdout
        self.buffers = {} 
        self.encoding = getattr(original_stdout, "encoding", "utf-8")
        self.errors = getattr(original_stdout, "errors", "strict")

    def register_ui(self, session_id, st_empty):
        self.buffers[session_id] = {"empty": st_empty, "text": ""}

    def write(self, text):
        # 1. Find out WHICH user triggered this print statement
        from ingestion.i18n import get_radar_session
        session_id = get_radar_session()
        
        # 2. Route the text directly to their unique UI container
        if session_id in self.buffers:
            buf = self.buffers[session_id]
            buf["text"] += text
            try:
                buf["empty"].code(buf["text"], language="shell")
            except Exception:
                pass
                
        # 3. Always pass through to the main terminal
        try:
            self._stdout.write(text)
        except Exception:
            pass
            
    def flush(self):
        try:
            self._stdout.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self._stdout, "isatty", lambda: False)()

# Only wrap sys.stdout ONCE per Python application lifecycle
if not hasattr(sys.stdout, "register_ui"):
    sys.stdout = SessionAwareStreamCapture(sys.stdout)

# ----------------------------------------------------------------------
# 2. Sidebar: Mining Form
# ----------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.header(t("mine_title"))
st.sidebar.caption(t("mine_caption"))

with st.sidebar.form(key="mining_form"):
    new_domain = st.text_input(t("mine_input"), placeholder=t("mine_placeholder"))
    max_papers_input = st.slider(
        t("mine_max_papers"), min_value=10, max_value=500, value=100, step=10
    )
    submit_btn = st.form_submit_button(t("mine_btn"), disabled=is_mining, key="mine_btn_submit")

# Header displayed on main page
st.title(t("title"))
st.caption(t("caption"))

# ----------------------------------------------------------------------
# 2.1 Main Page: Run Mining Task
# ----------------------------------------------------------------------

# 1. When Submit is clicked, save it to session state
if submit_btn:
    if not new_domain.strip():
        st.sidebar.error(t("mine_error"))
    else:
        st.session_state["is_mining"] = True
        st.session_state["stop_mining"] = False
        st.session_state["mining_domain"] = new_domain

# 2. Run the mining process based on the session state
# In app.py - inside the mining runner block
if st.session_state.get("is_mining", False):
    domain = st.session_state["mining_domain"]

    # ------------------------------------------------------------------
    # RUN LAYER 1 & 2: TAXONOMY NORMALIZATION & AGENCY ROUTING
    # ------------------------------------------------------------------
    from ingestion.taxonomy import normalize_taxonomy
    taxonomy_info = normalize_taxonomy(domain)
    
    st.subheader(f"🎯 Target Normalized: {taxonomy_info['field_name']} ({taxonomy_info['domain_name']})")
    st.info(
        f"**Routed Agency**: {taxonomy_info['router_config']['primary_agency']} "
        f"| **Target Programs**: {', '.join(taxonomy_info['router_config']['programs'])}"
    )

    stop_col, _ = st.columns([1, 4])
    with stop_col:
        if st.button(t("stop_btn"), type="primary"):
            st.session_state["stop_mining"] = True
            st.session_state["is_mining"] = False 
            st.warning(t("stop_requested"))
            st.rerun()

   

    def is_stop_requested():
        return st.session_state.get("stop_mining", False)

    try:
        
# 1. STEP 0: Layer 1 Taxonomy Normalization
        tax_meta = normalize_taxonomy(domain)
        st.success(t(
                "taxonomy_success",
                topic=tax_meta.get("topic_name"),
                field=tax_meta.get("field_name"),
                strategy=tax_meta.get("agency_category")
            ))

# 2. STEP 1: OpenAlex Topic-Filtered Search 
        with st.status(t("step1_lbl"), expanded=True) as status1:
            log1 = st.empty()
            sys.stdout.register_ui(st.session_state["session_id"], log1)
            fetch_professors_by_keywords(
                tax_meta=tax_meta,
                max_papers=max_papers_input,
            )
            status1.update(label="✅ " + t("step1_lbl"), state="complete", expanded=False)

# 3. STEP 2: Multi-Agency Grants Engine
        if not is_stop_requested():
            with st.status(t("step2_lbl"), expanded=True) as status2:
                log2 = st.empty()
                sys.stdout.register_ui(st.session_state["session_id"], log2)
                check_and_save_grants(tax_meta=tax_meta)
                status2.update(label="✅ " + t("step2_lbl"), state="complete", expanded=False)

        if is_stop_requested():
            st.warning(t("stop_success"))
        else:
            st.success(t("mine_success", domain=domain))

    except Exception as e:
        st.error(f"❌ Execution error: {e}")
    finally:
        st.session_state["is_mining"] = False 
        st.cache_data.clear()

# ----------------------------------------------------------------------
# 3. Database Migration & Loading
# ----------------------------------------------------------------------
try:
    auto_migrate_db(conn=get_db_connection())
except Exception as e:
    st.error(f"❌ Database migration error: {e}")

@st.cache_data(ttl=30)
def load_radar_data(session_id):
    engine = get_sqlalchemy_engine()
    return fetch_radar_dataframe(engine, session_id)

try:
    df = load_radar_data(st.session_state["session_id"])
except Exception as e:
    st.error(f"❌ Database error: {e}")
    st.stop()

# ----------------------------------------------------------------------
# 4. Dashboard Metrics
# ----------------------------------------------------------------------
st.info(t("tip_refresh"))
if df.empty:
    st.warning(t("no_data"))
    st.stop()

unique_profs = df["name"].nunique()
total_signals = len(df[df["raw_text"].str.strip() != ""])
funded_signals = len(
    df[
        df["raw_text"].str.contains(
            "Funding|CAREER|CRII|Schmidt|NSF", case=False, na=False
        )
    ]
)

col1, col2, col3 = st.columns(3)
col1.metric(t("stat_profs"), f"{unique_profs}{t('unit_prof')}")
col2.metric(t("stat_signals"), f"{total_signals}{t('unit_signal')}")
col3.metric(t("stat_funded"), f"{funded_signals}{t('unit_funded')}")

# ----------------------------------------------------------------------
# 5. Sidebar Filter Console
# ----------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.header(t("sidebar_title"))

all_domains = sorted([d for d in df["research_domain"].unique().tolist() if d])
selected_domain = st.sidebar.selectbox(
    t("domain_selector"), options=["All Domains"] + all_domains
)
min_score = st.sidebar.slider(t("min_score"), 0, 200, 0, step=10)

all_institutions = sorted([i for i in df["institution"].unique().tolist() if i])
selected_insts = st.sidebar.multiselect(t("filter_inst"), options=all_institutions)
search_keyword = st.sidebar.text_input(
    t("filter_kw"), "", placeholder=t("kw_placeholder")
)

filtered_df = df[df["hiring_score"] >= min_score]
if selected_domain != "All Domains":
    filtered_df = filtered_df[filtered_df["research_domain"] == selected_domain]
if selected_insts:
    filtered_df = filtered_df[filtered_df["institution"].isin(selected_insts)]
if search_keyword:
    filtered_df = filtered_df[
        filtered_df["raw_text"].str.contains(search_keyword, case=False, na=False)
        | filtered_df["name"].str.contains(search_keyword, case=False, na=False)
    ]

# ----------------------------------------------------------------------
# 5.1 Sidebar: Data Management
# ----------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.subheader(t("data_mgmt_title"))

if "df" in locals() and not df.empty:
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.sidebar.download_button(
        label=t("export_btn"),
        data=csv_bytes,
        file_name="scholar_radar_results.csv",
        mime="text/csv",
        use_container_width=True,
    )
else:
    st.sidebar.info(t("export_no_data"))
    
with st.sidebar.popover(t("clear_popover"), use_container_width=True):
    st.warning(t("clear_warning"))
    if st.button(t("clear_confirm"), type="primary", use_container_width=True):
        try:
            st.session_state["is_mining"] = False
            st.session_state["stop_mining"] = True
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Delete records matching THIS user's session
            current_session = st.session_state["session_id"]
            
            cur.execute("""
                DELETE FROM professors WHERE session_id = %s;
                DELETE FROM papers WHERE session_id = %s;
            """, (current_session, current_session))
            
            conn.commit()
            cur.close()
            conn.close()

            st.cache_data.clear()
            st.success(t("clear_success"))
            st.rerun()

        except Exception as e:
            st.error(t("clear_error", error=e))

# ----------------------------------------------------------------------
# 6. Professor Card Display
# ----------------------------------------------------------------------
st.markdown("---")
st.subheader(t("list_title", count=filtered_df["name"].nunique()))

for name, group in filtered_df.groupby("name", sort=False):
    first_row = group.iloc[0]
    score = first_row["hiring_score"]
    inst = first_row["institution"]
    homepage = str(first_row["homepage_url"]).strip()
    
    # Get the paper count
    paper_count = first_row.get("paper_count", 0)
    no_paper_tag = t("no_paper_tag") if paper_count == 0 else ""
    
    career_stage = first_row.get("career_stage", "ESTABLISHED_PI")
    ap_tag = "🎓 [1st/2nd Yr AP]" if career_stage == "NEW_AP" else ""
    
    has_signals = group["raw_text"].astype(str).str.strip().ne("").any()
    signal_tag = "📢 [Hiring Signal]" if has_signals else ""

    badge = (
        t("badge_high") if score >= 100
        else (t("badge_medium") if score >= 60 else t("badge_low"))
    )
    
    breakdown = str(first_row.get("score_breakdown", "")).strip()
    breakdown_display = f" [{breakdown}]" if breakdown else ""

    # Add the no_paper_tag to the title
    with st.expander(f"{badge} **{name}** — {inst} {ap_tag} {signal_tag} {no_paper_tag} | Score: `{score}`{breakdown_display}"):
        
        # Display the localized explanation ONLY if they have 0 papers
        if paper_count == 0:
            st.info(t("no_paper_info"))
        
        if homepage and homepage.lower() != "nan":
            st.markdown(f"{t('homepage')}: [{homepage}]({homepage})")
        else:
            st.markdown(t("no_homepage"))
        valid_signals = group[
            group["raw_text"].astype(str).str.strip().ne("") & 
            group["raw_text"].astype(str).str.lower().ne("nan")
        ]

        if not valid_signals.empty:
            st.markdown(t("signals_section"))
            for _, row in valid_signals.iterrows():
                sig_type = row['signal_type'] or "SIGNAL"
                conf = row['confidence_score'] if row['confidence_score'] != "" else "N/A"
                raw_text = row['raw_text']
                src_url = row['source_url'] if (row['source_url'] and str(row['source_url']).lower() != "nan") else homepage
                
                st.info(
                    f"**[{sig_type}]** (Confidence: `{conf}`)\n\n\"{raw_text}\"\n\n{t('source_link')}({src_url})"
                )