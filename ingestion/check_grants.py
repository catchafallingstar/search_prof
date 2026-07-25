import sys
import os
# from duckdb import cursor
import requests
import hashlib
# 1. Ensure import of db.py from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db_connection
from ingestion.i18n import TEXT, t, get_radar_session





NSF_API_URL = "https://api.nsf.gov/services/v1/awards.json"

def get_funding_hash( prof_id, award_title):
    session_id = get_radar_session()
    raw = f"{session_id}_{prof_id}_{award_title.strip()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def check_and_save_nsf_grants(domain):
    session_id = get_radar_session()
    conn = get_db_connection()
    cursor = conn.cursor()
    

    # 1. Ensure database schema supports funding_hash and career_stage
    cursor.execute("""
        ALTER TABLE fundings ADD COLUMN IF NOT EXISTS funding_hash VARCHAR(64);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fundings_hash ON fundings(funding_hash);
        ALTER TABLE professors ADD COLUMN IF NOT EXISTS career_stage VARCHAR(50) DEFAULT 'ESTABLISHED_PI';
    """)
    conn.commit()

    # ----------------------------------------------------------------------
    # PHASE 1: DISCOVER NEW APs DIRECTLY FROM NSF CRII AWARDS (i18n integrated)
    # ----------------------------------------------------------------------
    print(t("crii_start"))
    try:
        crii_res = requests.get(
        NSF_API_URL, 
        params={
        # Combine CRII with the user's specific domain (e.g., "CRII AI Security")
            'keyword': f'CRII "{domain}"', 
        # Fetch up to 50 results to maximize opportunities
            'rpp': 50, 
            'printFields': 'id,title,piFirstName,piLastName,awardeeName'
        }, 
            timeout=10
        )
        if crii_res.status_code == 200:
            crii_data = crii_res.json()
            for award in crii_data.get('response', {}).get('award', []):
                first_name = award.get('piFirstName', '').strip()
                last_name = award.get('piLastName', '').strip()
                full_name = f"{first_name} {last_name}".strip()
                inst = award.get('awardeeName', '').strip()

                if full_name and inst:
                    # Insert professor if missing from DB and tag as NEW_AP
                    # Insert or update CRII APs scoped by session_id
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
                    else:
                        cursor.execute("""
        INSERT INTO professors (name, institution, hiring_score, career_stage, session_id)
        VALUES (%s, %s, 100, 'NEW_AP', %s);
    """, (full_name, inst, session_id))
            conn.commit()
    except Exception as e:
        conn.rollback()  # Add this to rescue the transaction!
        print(t("crii_skip", error=e))

    # ----------------------------------------------------------------------
    # PHASE 2: Check all professors in DB for all NSF grants
    # ----------------------------------------------------------------------
    # Only query professors belonging to this session
    cursor.execute("SELECT id, name, institution FROM professors WHERE session_id = %s;", (session_id,))

    professors = cursor.fetchall()

    print(t("start_query", count=len(professors)))

    for prof_id, name, institution in professors:
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
            award_title = award.get('title', '')
            amount_str = award.get('fundsAvailableAmt', '0')
            start_date = award.get('startDate') or '01/01/2024'
            end_date = award.get('expDate') or '01/01/2027'

            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            award_type = 'Standard Grant'
            grant_score = 15
            
            if 'CRII' in award_title.upper():
                award_type = 'CRII'
                grant_score = 40  
                is_crii_winner = True
            elif 'CAREER' in award_title.upper():
                award_type = 'CAREER'
                grant_score = 30  

            funding_hash = get_funding_hash(prof_id, award_title)

            # Insert only if new
            cursor.execute("""
                INSERT INTO fundings (professor_id, funder, grant_title, amount, award_date, funding_hash)
                VALUES (%s, 'NSF', %s, %s, TO_DATE(%s, 'MM/DD/YYYY'), %s)
                ON CONFLICT (funding_hash) DO NOTHING;
            """, (prof_id, award_title, amount, start_date, funding_hash))

            # Only add score boost if this grant was NOT already in the database
            if cursor.rowcount > 0:
                funding_added += 1
                total_score_boost += grant_score

        if total_score_boost > 0:
            # Update hiring score AND update career_stage if they won CRII
            if is_crii_winner:
                cursor.execute("""
                    UPDATE professors 
                    SET hiring_score = hiring_score + %s,
                        career_stage = 'NEW_AP'
                    WHERE id = %s;
                """, (total_score_boost, prof_id))
            else:
                cursor.execute("""
                    UPDATE professors 
                    SET hiring_score = hiring_score + %s 
                    WHERE id = %s;
                """, (total_score_boost, prof_id))

            print(t("hit_grant", name=name, inst=institution, count=funding_added, score=total_score_boost))

        conn.commit()

    cursor.close()
    conn.close()
    print(t("sync_complete"))

