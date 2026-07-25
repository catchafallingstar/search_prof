import random
import time
import urllib.parse
from ddgs import DDGS
from ingestion.i18n import t
from ingestion.socialradar import ddg_lock
import os

EXCLUDED_DOMAINS = [
    'wikipedia.org', 'linkedin.com', 'twitter.com', 'x.com', 
    'facebook.com', 'youtube.com', 'instagram.com', 'reddit.com',
    'scholar.google', 'researchgate.net', 'semanticscholar.org', 
    'arxiv.org', 'nature.com', 'sciencedirect.com', 'ieee.org', 'acm.org',
    'orcid.org', 'scispace.com', 'zenodo.org', 'f1000research.com', 
    'theamericanjournals.com', 'mdpi.com', 'springer.com', 'wiley.com'
]

def is_valid_homepage(url: str) -> bool:
    """Helper to check if a URL is a valid lab/personal homepage."""
    if not url or not isinstance(url, str):
        return False
        
    url_lower = url.strip().lower()
    
    # NEW FIX: Reject relative tracking links returned by DDG
    if not url_lower.startswith("http"):
        return False
    
    if url_lower.endswith(('.pdf', '.doc', '.docx', '.ppt', '.pptx', '.zip')):
        return False
        
    if any(domain in url_lower for domain in EXCLUDED_DOMAINS):
        return False
        
    return True

def get_professor_homepage(prof_name: str, institution: str, openalex_homepage: str = None, **kwargs) -> str:
    # ------------------------------------------------------------------
    # TIER 1: OpenAlex Native URL (Instant hit, no web call required)
    # ------------------------------------------------------------------
    if openalex_homepage and str(openalex_homepage).startswith("http"):
        if is_valid_homepage(openalex_homepage):
            return openalex_homepage.strip()

    # ------------------------------------------------------------------
    # TIER 2: Fast DDG Search (Clean queries without OR syntax)
    # ------------------------------------------------------------------
    clean_inst = (institution or "").split('(')[0].strip()
    proxy_url = os.getenv("ROTATING_PROXY_URL")

    # CLEAN QUERIES: No parentheses, no "OR" keywords
    candidate_queries = [
        f'"{prof_name}" "{clean_inst}" faculty homepage',
        f'{prof_name} {clean_inst} lab website'
    ]

    for query in candidate_queries:
        try:
            # 1. ONLY hold the lock to stagger the requests (anti-bot protection)
            with ddg_lock:
                time.sleep(random.uniform(0.5, 1.5))

            # 2. Configure arguments OUTSIDE the lock
            ddgs_kwargs = {"timeout": 6} # You can safely keep 6 seconds now!
            if proxy_url:
                ddgs_kwargs["proxy"] = proxy_url

            # 3. Execute Network I/O OUTSIDE the lock so threads run concurrently
            ddgs = DDGS(**ddgs_kwargs)
            results = list(ddgs.text(query, max_results=4, backend="api"))
            
            if not results:
                continue

            for res in results:
                url = res.get('href', '')
                if is_valid_homepage(url):
                    return url
                    
            if results:
                break # Got valid search results, no need for second query
        except Exception:
            continue

    # ------------------------------------------------------------------
    # TIER 3: Guaranteed Fallback URL
    # ------------------------------------------------------------------
    encoded_query = urllib.parse.quote(f"{prof_name} {clean_inst}")
    return f"https://scholar.google.com/citations?view_op=search_authors&mauthors={encoded_query}"