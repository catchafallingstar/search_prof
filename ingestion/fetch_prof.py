import sys
import os
import requests
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db_connection
from ingestion.i18n import t, get_radar_session

OPENALEX_URL = "https://api.openalex.org/works"

def fetch_professors_by_keywords(tax_meta: dict, max_papers=100):
    session_id = get_radar_session() 
    
    current_year = datetime.now().year
    start_year = current_year - 2
    year_range = f"{start_year}-{current_year}"
    
    topic_id = tax_meta.get("topic_id")
    domain_name = tax_meta.get("topic_name") or tax_meta.get("raw_query")

    print(t("start_search", domain=domain_name, year_range=year_range))
    
    works = []
    page = 1
    remaining = max_papers

    # Clean topic ID format (e.g., "https://openalex.org/T10234" -> "T10234")
    clean_topic_id = topic_id.split("/")[-1] if topic_id else None

    while remaining > 0:
        fetch_count = min(remaining, 100)
        
        # FIX: Filter explicitly by topic ID if Layer 1 found one
        if clean_topic_id:
            filter_str = f'publication_year:{year_range},institutions.country_code:us,topics.id:{clean_topic_id}'
            params = {
                'filter': filter_str,
                'per_page': fetch_count,
                'page': page
            }
        else:
            params = {
                'filter': f'publication_year:{year_range},institutions.country_code:us',
                'search': domain_name,
                'per_page': fetch_count,
                'page': page
            }

        try:
            res = requests.get(OPENALEX_URL, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            results = data.get('results', [])
            
            if not results:
                break
                
            works.extend(results)
            remaining -= len(results)
            page += 1
            
            if len(results) < fetch_count:
                break
                
        except Exception as e:
            print(t("api_failed", error=e))
            break

    print(t("papers_found", count=len(works)))
    if not works:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    saved_count = 0

    for work in works:
        authorships = work.get('authorships', [])
        if not authorships:
            continue

        pi_author = None
        for a in reversed(authorships):
            if a.get('is_corresponding') and a.get('author_position') != 'first':
                pi_author = a
                break
                
        if not pi_author:
            pi_author = authorships[-1]

        author_info = pi_author.get('author', {})
        prof_name = author_info.get('display_name')
        openalex_id = author_info.get('id')
        native_homepage = author_info.get('homepage_url') or author_info.get('orcid') or ''

        if not prof_name or not openalex_id:
            continue

        institutions = pi_author.get('institutions', [])
        institution_name = "Unknown Institution"
        country_code, inst_type = "", ""

        if institutions:
            inst_obj = institutions[0]
            institution_name = inst_obj.get('display_name') or "Unknown Institution"
            country_code = (inst_obj.get('country_code') or "").upper()
            inst_type = (inst_obj.get('type') or "").lower()

        if country_code != "US" or (inst_type and inst_type != "education"):
            continue

        safe_name = prof_name[:99]
        safe_inst = institution_name[:99]
        safe_domain = raw_domain[:99]

        cursor.execute("""
            SELECT id FROM professors 
            WHERE name = %s AND institution = %s AND session_id = %s;
        """, (safe_name, safe_inst, session_id))
        
        existing_prof = cursor.fetchone()

        if existing_prof:
            prof_id = existing_prof[0]
            cursor.execute("""
                UPDATE professors 
                SET openalex_id = %s,
                    homepage_url = COALESCE(NULLIF(professors.homepage_url, ''), %s),
                    research_domain = COALESCE(professors.research_domain, %s)
                WHERE id = %s;
            """, (openalex_id, native_homepage, safe_domain, prof_id))
        else:
            cursor.execute("""
                INSERT INTO professors (name, institution, openalex_id, research_domain, homepage_url, session_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name, institution, session_id) DO UPDATE 
                SET homepage_url = COALESCE(NULLIF(professors.homepage_url, ''), EXCLUDED.homepage_url),
                    research_domain = COALESCE(professors.research_domain, EXCLUDED.research_domain)
                RETURNING id;
            """, (safe_name, safe_inst, openalex_id, safe_domain, native_homepage, session_id))
            
            prof_id = cursor.fetchone()[0]

        paper_title = (work.get('title') or 'Untitled')[:250]
        publication_year = work.get('publication_year')
        openalex_paper_id = work.get('id')

        primary_loc = work.get('primary_location') or {}
        source_info = primary_loc.get('source') or {}
        venue = (source_info.get('display_name') or 'Conference/Journal')[:99]

        cursor.execute("""
            INSERT INTO papers (title, venue, publication_year, openalex_id, session_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (openalex_id, session_id) DO UPDATE
            SET title = EXCLUDED.title, venue = EXCLUDED.venue
            RETURNING id;
        """, (paper_title, venue, publication_year, openalex_paper_id, session_id))

        paper_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO professor_papers (professor_id, paper_id, author_position)
            VALUES (%s, %s, 'corresponding')
            ON CONFLICT DO NOTHING;
        """, (prof_id, paper_id))

        saved_count += 1
        print(t("saved_prof", name=safe_name, inst=safe_inst))

    conn.commit()
    cursor.close()
    conn.close()

    print(t("process_complete", count=saved_count))