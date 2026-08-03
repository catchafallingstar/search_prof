# ingestion/check_grants.py

import sys
import os
import requests
import hashlib

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db_connection
from ingestion.i18n import t, get_radar_session

NSF_API_URL = "https://api.nsf.gov/services/v1/awards.json"

def get_funding_hash(prof_id, award_title):
    session_id = get_radar_session()
    raw = f"{session_id}_{prof_id}_{award_title.strip()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def check_and_save_grants(tax_meta: dict):
    session_id = get_radar_session()
    conn = get_db_connection()
    cursor = conn.cursor()

    router_config = tax_meta.get("router_config", {})
    allowed_programs = router_config.get("programs", [])
    domain = tax_meta.get("raw_query")

    # Ensure schema updates
    cursor.execute("""
        ALTER TABLE fundings ADD COLUMN IF NOT EXISTS funding_hash VARCHAR(64);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fundings_hash ON fundings(funding_hash);
        ALTER TABLE professors ADD COLUMN IF NOT EXISTS career_stage VARCHAR(50) DEFAULT 'ESTABLISHED_PI';
    """)
    conn.commit()

    # ----------------------------------------------------------------------
    # PHASE 1: DISCOVER NEW APs VIA CRII (ONLY FOR CISE / ENGINEERING FIELDS)
    # ----------------------------------------------------------------------
    # ----------------------------------------------------------------------
    # PHASE 1: DISCOVER NEW APs VIA ALL TAXONOMY-LISTED EARLY-CAREER GRANTS
    # ----------------------------------------------------------------------
    for prog in allowed_programs:
        # Clean program name (e.g., strip 'NSF ' prefix for clean keyword matching)
        prog_keyword = prog.replace("NSF ", "").strip()
        search_query = f'{prog_keyword} "{domain}"'
        print(t("crii_start", prog=prog, search_query=search_query))
        try:
            grant_res = requests.get(
                NSF_API_URL, 
                params={
                    'keyword': search_query, 
                    'rpp': 50, 
                    'printFields': 'id,title,piFirstName,piLastName,awardeeName'
                }, 
                timeout=10
            )
            if grant_res.status_code == 200:
                grant_data = grant_res.json()
                for award in grant_data.get('response', {}).get('award', []):
                    first_name = award.get('piFirstName', '').strip()
                    last_name = award.get('piLastName', '').strip()
                    full_name = f"{first_name} {last_name}".strip()
                    inst = award.get('awardeeName', '').strip()

                    if full_name and inst:
                        cursor.execute("""
                            SELECT id FROM professors 
                            WHERE name = %s AND institution = %s AND session_id = %s;
                        """, (full_name, inst, session_id))

                        existing_prof = cursor.fetchone()
                        if existing_prof:
                            cursor.execute("""
                                UPDATE professors 
                                SET career_stage = 'NEW_AP' 
                                WHERE id = %s;
                            """, (existing_prof[0],))
                            print(t("crii_updated", name=full_name, inst=inst))
                        else:
                            cursor.execute("""
                                INSERT INTO professors (name, institution, hiring_score, career_stage, session_id, score_breakdown)
                                VALUES (%s, %s, 100, 'NEW_AP', %s, %s);
                            """, (full_name, inst, session_id, f' +100 ({prog} Base)'))
                            print(t("crii_inserted", name=full_name, inst=inst))
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️ Discovery skipped for grant '{prog}': {e}")
        

    # ----------------------------------------------------------------------
    # PHASE 2: CHECK EXISTING PROFESSORS FOR GRANTS WITH INST MATCHER
    # ----------------------------------------------------------------------
    cursor.execute("SELECT id, name, institution FROM professors WHERE session_id = %s;", (session_id,))
    professors = cursor.fetchall()

    print(t("start_query", count=len(professors)))

    for prof_id, name, institution in professors:
        # Include institution in query to reduce false-positive grant hits
        clean_inst = (institution or "").split('(')[0].strip()
        params = {
            'keyword': f'"{name}"',
            'printFields': 'id,title,fundsAvailableAmt,startDate,expDate,awardeeName'
        }

        try:
            res = requests.get(NSF_API_URL, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(t("query_failed", name=name, error=e))
            continue

        awards = data.get('response', {}).get('award', [])
        if not awards:
            continue

        funding_added = 0
        total_score_boost = 0
        is_crii_winner = False

        for award in awards:
            awardee = award.get('awardeeName', '').lower()
            
            # Simple verification: Ensure grant awardee institution aligns with professor institution
            if clean_inst and len(clean_inst) > 3 and clean_inst.lower() not in awardee:
                continue

            award_title = award.get('title', '')
            amount_str = award.get('fundsAvailableAmt', '0')
            start_date = award.get('startDate') or '01/01/2024'

            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            grant_score = 15
            if 'CRII' in award_title.upper():
                grant_score = 40  
                is_crii_winner = True
            elif 'CAREER' in award_title.upper():
                grant_score = 30  

            funding_hash = get_funding_hash(prof_id, award_title)

            cursor.execute("""
                INSERT INTO fundings (professor_id, funder, grant_title, amount, award_date, funding_hash)
                VALUES (%s, 'NSF', %s, %s, TO_DATE(%s, 'MM/DD/YYYY'), %s)
                ON CONFLICT (funding_hash) DO NOTHING;
            """, (prof_id, award_title, amount, start_date, funding_hash))

            if cursor.rowcount > 0:
                funding_added += 1
                total_score_boost += grant_score

        if total_score_boost > 0:
            breakdown_str = f" +{total_score_boost} (Grants x{funding_added})"
            cursor.execute("""
                UPDATE professors 
                SET hiring_score = hiring_score + %s,
                    career_stage = CASE WHEN %s THEN 'NEW_AP' ELSE career_stage END,
                    score_breakdown = COALESCE(score_breakdown, '') || %s
                WHERE id = %s;
            """, (total_score_boost, is_crii_winner, breakdown_str, prof_id))
            print(t("hit_grant", name=name, inst=institution, count=funding_added, score=total_score_boost))
        conn.commit()

    cursor.close()
    conn.close()
    print(t("sync_complete"))