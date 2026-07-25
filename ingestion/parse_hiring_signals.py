import sys
import os
import re
import time
import requests
import random
import threading
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from ingestion.i18n import t, get_radar_session, get_radar_lang, set_radar_context
load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db_connection
from ingestion.homepagefinder import get_professor_homepage
from ingestion.socialradar import check_social_hiring
from ingestion.matchers import (
    get_text_hash,
    FLEXIBLE_HIRING_PATTERN,
    is_valid_signal_text,
    extract_roles_and_funding,
    clean_and_extract_hiring_quote 
)
NON_ACADEMIC_KEYWORDS = ['Google', 'Amazon', 'Microsoft', 'Nvidia', 'Deloitte', 'Clinic', 'Walmart', 'Cisco']

NEW_AP_PATTERN = re.compile(
    r'(joining|starting\s+(my\s+)?lab|new\s+assistant\s+professor|incoming\s+(faculty|professor)|prospective\s+students)', 
    re.IGNORECASE
)

def fetch_and_parse_homepage(homepage_url):
    """Scrapes homepage HTML safely for hiring sentences."""
    bad_domains = [
        "scholar.google.com", "f1000research.com", "theamericanjournals.com", 
        "scispace.com", "researchgate.net", "nytimes.com", "ratemyprofessors.com", 
        "academia.edu", "cambridge.org", "dokumen.pub", "amazon.com", "wikipedia.org"
    ]
    
    url_lower = homepage_url.lower().split('?')[0]
    if any(domain in url_lower for domain in bad_domains) or url_lower.endswith(('.pdf', '.docx', '.doc', '.ppt', '.pptx', '.zip', '.epub')):
        return []
        
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        time.sleep(random.uniform(0.3, 0.7))
        resp = requests.get(homepage_url, headers=headers, timeout=3.5)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for script in soup(["script", "style", "nav", "footer"]):
                script.extract()
                
            text = soup.get_text(separator=' ')
            matched = []
            
            for sentence in re.split(r'[;\n\r\t\.]', text):
                clean_s = sentence.strip()
                if FLEXIBLE_HIRING_PATTERN.search(clean_s) and is_valid_signal_text(clean_s):
                    if 15 < len(clean_s) < 300:
                        matched.append(clean_s)
            return matched
    except Exception:
        pass
    return []

def save_signal_to_db(prof_id, sig_type, raw_quote, source_url):
    """Persists extracted hiring signal to PostgreSQL and updates scores/career stage."""
    conn = get_db_connection()
    cur = conn.cursor()
    session_id = get_radar_session()
    
    # Initialize defaults in case of error
    score_boost, roles, has_funding = 0, [], False 
    
    try:
        quote_hash = get_text_hash(f"{session_id}_{raw_quote}")
        roles, has_funding = extract_roles_and_funding(raw_quote)
        is_new_ap = bool(NEW_AP_PATTERN.search(raw_quote))
        score_boost = 40 + (20 if has_funding else 0) + (30 if is_new_ap else 0)

        signal_category = 'HOMEPAGE_BANNER' if sig_type == 'HOMEPAGE' else 'SOCIAL_POST'
        confidence = 'HIGH' if has_funding else 'MEDIUM'

        cur.execute("""
            INSERT INTO hiring_signals (professor_id, signal_type, raw_text, confidence_score, source_url, raw_text_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (raw_text_hash) DO NOTHING;
        """, (prof_id, signal_category, raw_quote, confidence, source_url, quote_hash))

        if cur.rowcount > 0:
            if is_new_ap:
                cur.execute("""
                    UPDATE professors 
                    SET hiring_score = hiring_score + %s, career_stage = 'NEW_AP'
                    WHERE id = %s;
                """, (score_boost, prof_id))
            else:
                cur.execute("""
                    UPDATE professors 
                    SET hiring_score = hiring_score + %s
                    WHERE id = %s;
                """, (score_boost, prof_id))

        conn.commit()
        
        # ✅ Return the metrics so the print statement can use them
        return score_boost, roles, has_funding
        
    except Exception as e:
        conn.rollback()
        print(f"⚠️ Error in save_signal_to_db: {e}")
        return score_boost, roles, has_funding
    finally:
        cur.close()
        conn.close()

def process_single_professor(prof, domain_name):
    prof_id, name, institution, homepage_url = prof
    
    if any(k in (institution or "") for k in NON_ACADEMIC_KEYWORDS):
        return None

    # Step 1: Clean & fetch homepage
    cleaned_homepage = get_professor_homepage(name, institution, openalex_homepage=homepage_url)
    
    if cleaned_homepage and "scholar.google.com" not in cleaned_homepage:
        matched_sentences = fetch_and_parse_homepage(cleaned_homepage)
        if matched_sentences:
        # Clean snippet before saving
            raw_quote = clean_and_extract_hiring_quote(" | ".join(matched_sentences))
            if raw_quote:
                return ("HOMEPAGE", prof_id, name, raw_quote, cleaned_homepage)

    # Step 2: Check Social / Web fallback
    social_text, social_url = check_social_hiring(prof_name=name, institution=institution)
    if social_text:
        # Clean the social snippet just like the homepage!
        cleaned_social = clean_and_extract_hiring_quote(social_text)
        if cleaned_social:
            return ("SOCIAL", prof_id, name, cleaned_social, social_url)

    return None

def worker_wrapper(prof, domain_name, session_id, lang_code, ctx):
    """Executes worker logic and binds Streamlit AND Radar context."""
    if ctx:
        add_script_run_ctx(threading.current_thread(), ctx)
    
    # Securely bind the TLS state to this background thread
    set_radar_context(session_id, lang_code)
    
    return process_single_professor(prof, domain_name)

def scan_hiring_signals(domain_name=None, stop_check_callback=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Grab context from the parent thread
    session_id = get_radar_session()
    current_lang = get_radar_lang()

    cursor.execute("SELECT id, name, institution, homepage_url FROM professors WHERE session_id = %s;", (session_id,))
    profs = cursor.fetchall()
    cursor.close()
    conn.close()

    print(t("start_radar", count=len(profs)))
    hits_count = 0
    ctx = get_script_run_ctx()

    workers_env = os.getenv("RADAR_MAX_WORKERS", "1")
    max_workers = int(workers_env) if workers_env.strip() else 1
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for prof in profs:
            if stop_check_callback and stop_check_callback():
                break
            # Pass session_id and current_lang down into the wrapper
            futures[executor.submit(worker_wrapper, prof, domain_name, session_id, current_lang, ctx)] = prof
        for future in as_completed(futures):
            if stop_check_callback and stop_check_callback():
                print(t("stop_requested"))
                break

            
            try:
                result = future.result()
                prof_tuple = futures[future] 
                prof_name = prof_tuple[1]
                prof_inst = prof_tuple[2]

                    # 👤 Print Analyzing status using i18n
                print(t("analyzing", name=prof_name, institution=prof_inst)) 

                if result:
                    sig_type, prof_id, name, text_signal, source_url = result
                        
                        # Grab the score and funding details returned from the DB save
                    score_boost, roles, has_funding = save_signal_to_db(prof_id, sig_type, text_signal, source_url)
                    hits_count += 1
                        
                        # Format the arguments for your i18n brackets
                    short_quote = (text_signal[:75] + '...') if len(text_signal) > 75 else text_signal
                    role_str = f" for {', '.join(roles)}" if roles else ""
                    fund_str = " 💰" if has_funding else ""
                        
                    if sig_type == "HOMEPAGE":
                        print(t("hit_homepage", roles=role_str, funding=fund_str, quote=short_quote, score=score_boost))
                    else:
                        print(t("hit_social", roles=role_str, funding=fund_str, quote=short_quote, score=score_boost))
                else:
                        # ⚪ Handle "No Hit" and "Skipped" scenarios
                    if any(k in (prof_inst or "") for k in NON_ACADEMIC_KEYWORDS):
                        print(t("skip_non_academic", name=prof_name, institution=prof_inst))
                    else:
                        print(t("no_hiring_verb"))
                            
            except Exception as e:
                print(f"⚠️ Error processing candidate: {e}")
    print(t("scan_complete", hits=hits_count))

if __name__ == "__main__":
    scan_hiring_signals()