import re
import time
import random
import os
import threading
import requests
from ddgs import DDGS
from ingestion.matchers import FLEXIBLE_HIRING_PATTERN, is_valid_signal_text

# Sites that are pure noise (jobs boards, generic wikis, etc.)
EXCLUDED_DOMAINS = [
    'wikipedia.org', 'roberthalf.com', 'indeed.com', 'glassdoor.com', 
    'ziprecruiter.com', 'scholar.google.com', 'researchgate.net',
    'ratemyprofessors.com', 'dokumen.pub'
]

ddg_lock = threading.Lock()

def check_bluesky_hiring(prof_name: str, institution: str):
    """Directly queries Bluesky's open, public REST API (No Auth/API Key needed)."""
    if not prof_name:
        return None, None

    clean_inst = (institution or "").split('(')[0].strip()
    # Query Bluesky for posts matching the professor's name and hiring intent
    q = f'"{prof_name}" (hiring OR recruiting OR "PhD position" OR "prospective students")'

    try:
        url = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
        params = {"q": q, "limit": 5}
        resp = requests.get(url, params=params, timeout=3.5)
        
        if resp.status_code == 200:
            posts = resp.json().get('posts', [])
            for p in posts:
                record = p.get('record', {})
                text = record.get('text', '')
                
                # Check snippet against strict NLP matchers
                if FLEXIBLE_HIRING_PATTERN.search(text) and is_valid_signal_text(text):
                    author_handle = p.get('author', {}).get('handle', '')
                    post_uri = p.get('uri', '')
                    rkey = post_uri.split('/')[-1] if post_uri else ''
                    
                    web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}" if author_handle and rkey else "https://bsky.app"
                    return " ".join(text.split()), web_url
    except Exception:
        pass
        
    return None, None
FIRST_PERSON_PATTERNS = re.compile(r'\b(my lab|i am|i\'m|our group|our lab|my group)\b', re.IGNORECASE)

def check_social_hiring(prof_name: str = None, institution: str = None, query: str = None, **kwargs):
    if not prof_name:
        return None, None

    name_parts = prof_name.strip().split()
    last_name = name_parts[-1].lower() if name_parts else ""

    # Phase 1: Bluesky API (Match against AUTHOR metadata, not post body!)
    try:
        bsky_text, bsky_url = check_bluesky_hiring(prof_name, institution)
        if bsky_text:
            return bsky_text, bsky_url
    except Exception:
        pass

    # Phase 2: Web / Social Search via DuckDuckGo
    clean_inst = (institution or "").split('(')[0].strip()
    candidate_queries = [
        f'"{prof_name}" "{clean_inst}" hiring PhD students',
        f'"{prof_name}" recruiting prospective students lab',
        f'site:linkedin.com/posts "{prof_name}" hiring'
    ]

    proxy_url = os.getenv("ROTATING_PROXY_URL")

    for q in candidate_queries:
        try:
            with ddg_lock:
                time.sleep(random.uniform(0.4, 0.8))

            ddgs_kwargs = {"timeout": 5}
            if proxy_url:
                ddgs_kwargs["proxy"] = proxy_url

            ddgs = DDGS(**ddgs_kwargs)
            results = list(ddgs.text(q, max_results=5, backend="api"))
            
            if not results:
                continue

            for res in results:
                snippet = res.get('body', '')
                url = res.get('href', '')
                snippet_lower = snippet.lower()
                
                # 1. Skip noisy/excluded domains
                if any(domain in url.lower() for domain in EXCLUDED_DOMAINS):
                    continue

                # 2. Skip LinkedIn boilerplate metadata
                if "professional community of 1 billion" in snippet_lower or ("view" in snippet_lower and "profile on linkedin" in snippet_lower):
                    continue

                # 3. IDENTITY CHECK:
                # If it's written in 1st person ("my lab", "I'm hiring"), accept it immediately!
                # If it's written in 3rd person, ensure the target professor's last name is mentioned.
                is_first_person = bool(FIRST_PERSON_PATTERNS.search(snippet_lower))
                if not is_first_person and last_name and (last_name not in snippet_lower):
                    continue # Skip 3rd-party posts mentioning a different professor
                
                # 4. Strict NLP Matcher
                if FLEXIBLE_HIRING_PATTERN.search(snippet) and is_valid_signal_text(snippet):
                    return " ".join(snippet.split()), url

        except Exception:
            continue

    return None, None