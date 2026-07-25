# ingestion/matchers.py
import re
import hashlib

def get_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()

ACTION_VERBS = r"(?:look(?:ing)?\s+for|plan[s]?\s+to\s+recruit|recruit(?:ing)?|seek(?:ing)?|accept(?:ing)?|welcom(?:e|ing)|invit(?:e|ing)|sponsor(?:ing)?|open(?:ing)?[s]?\s+for|looking\s+to\s+hire|join\s+(?:my|our)\s+(?:lab|group|team))"
TARGET_ROLES = r"(?:phd[s]?|doctoral|graduate[s]?|ph\.d\.|postdoc[s]?|post-doc[s]?|research\s+assistant[s]?|ra[s]?|intern[s]?|student[s]?)"

FLEXIBLE_HIRING_PATTERN = re.compile(rf"\b{ACTION_VERBS}\s+(?:\w+\W+){{0,8}}?{TARGET_ROLES}\b", re.IGNORECASE)
FUNDING_PATTERNS = re.compile(r"(?:sponsored\s+by|funded\s+by|grants?\s+from|schmidt\s+sciences|nsf\s+career|open\s+philanthropy|darpa|nih|dod|onr)", re.IGNORECASE)
NEGATIVE_PATTERNS = re.compile(r"(?:do\s+not\s+have|not\s+accepting|not\s+recruiting|lab\s+is\s+full)", re.IGNORECASE)

def is_valid_signal_text(text: str) -> bool:
    if not text or NEGATIVE_PATTERNS.search(text):
        return False
    return True

def extract_roles_and_funding(text: str):
    roles = []
    text_lower = text.lower()
    if 'postdoc' in text_lower or 'post-doc' in text_lower: roles.append('Postdoc')
    if 'phd' in text_lower or 'doctoral' in text_lower or 'ph.d.' in text_lower: roles.append('PhD')
    if 'intern' in text_lower: roles.append('Intern')
    if 'research assistant' in text_lower or ' ra ' in text_lower: roles.append('RA')

    has_funding = bool(FUNDING_PATTERNS.search(text))
    return list(set(roles)), has_funding

import re
from datetime import datetime

# Matches leading search engine dates: "Oct 7, 2024 ... ", "Jun 12, 2023 ..."
SEARCH_DATE_PREFIX = re.compile(r'^(?:[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})\s*\.{2,3}\s*', re.IGNORECASE)

# Matches explicit past years (e.g., 2017, 2018, 2021) to detect stale posts
OLD_YEAR_PATTERN = re.compile(r'\b(201[0-9]|202[0-3])\b')

def clean_and_extract_hiring_quote(raw_snippet: str) -> str:
    """
    Strips search engine metadata noise, checks for staleness, 
    and isolates the pure hiring sentence.
    """
    if not raw_snippet:
        return ""

    # 1. Strip leading search engine dates & ellipsis junk
    cleaned = SEARCH_DATE_PREFIX.sub('', raw_snippet.strip())
    
    # 2. Reject if the snippet explicitly references an old year (e.g., 2017, 2022)
    if OLD_YEAR_PATTERN.search(cleaned):
        return ""  # Stale signal

    # 3. Isolate sentences and pick only the sentence with actual hiring intent
    sentences = re.split(r'(?<=[.!?])\s+', cleaned)
    hiring_sentences = []
    
    for s in sentences:
        s_clean = s.strip()
        if FLEXIBLE_HIRING_PATTERN.search(s_clean) and len(s_clean) > 15:
            hiring_sentences.append(s_clean)

    if hiring_sentences:
        return " ".join(hiring_sentences[:2]) # Return at most 2 relevant sentences

    return cleaned if len(cleaned) < 180 else cleaned[:180] + "..."