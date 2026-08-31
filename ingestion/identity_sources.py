"""Source selection, not faculty verification. A useful link is only a lead."""
import re
from urllib.parse import urljoin, urlparse, urlunparse

from ingestion.verification_audit import safe_source_link


def canonical_source_url(url):
    """Fragments are page locations, not new documents. Preserve query identities."""
    parsed = urlparse(str(url))
    # Keep the actual host (www can be a distinct endpoint). Redirect aliases
    # are deduplicated by the fetcher after observing a real redirect.
    return urlunparse(parsed._replace(fragment='', scheme=parsed.scheme.lower(),
                                     netloc=parsed.netloc.lower()))


def text_url_leads(text):
    """Read explicit URLs, including PDF extraction's spaces around dots/slashes."""
    # Never join lines/columns in extracted PDF text. A truncated URL is a
    # locator at most; guessing its continuation invents requests.
    found = []
    for line in str(text).splitlines():
        compact = re.sub(r'[ \t]*([./])[ \t]*', r'\1', line)
        found.extend(re.findall(r'https?://[^\s<>"\)]+|\b(?:[a-z0-9-]+\.)+(?:edu|com|org|io)(?:/[^\s<>"\)]*)?', compact, re.I))
    return list(dict.fromkeys(canonical_source_url(
        u.rstrip('.,;') if u.startswith(('http://', 'https://')) else 'https://' + u.rstrip('.,;'))
        for u in found))[:8]


NON_PROFILE_HOSTS = (
    'researchgate.net', 'semanticscholar.org', 'openalex.org', 'dblp.org', 'dblp.uni-trier.de',
    'acm.org', 'ieee.org', 'springer.com', 'sciencedirect.com', 'wikipedia.org',
    'youtube.com', 'facebook.com', 'x.com', 'twitter.com', 'amazon.com', 'rsc.org',
    'ratemyprofessors.com', 'myprofessorreviews.com', 'myprofreviews.com', 'bokus.com', 'bnf.fr',
    'grokipedia.com', 'grantome.com', 'acadomeet.com', 'gohighhorse.com',
    'govsalaries.com', 'openpayrolls.com', 'opengovpay.com', 'gradnova.com',
    'capneteq.com', 'sciprofiles.com', 'tableau.com', 'linkedin.com',
)


def excluded_profile_source(url):
    parsed = urlparse(url)
    host, path = (parsed.hostname or '').lower(), parsed.path.lower()
    return (any(host == h or host.endswith('.' + h) for h in NON_PROFILE_HOSTS)
            or host.startswith('scholar.google.')
            or bool(re.search(r'/(?:doi|fac_pubs|publications?|papers?|articles?|books?|theses|dissertations?)/', path)))


def source_kind(url, summary, *, name_matches, official=False, profile_title=False):
    if not safe_source_link(url):
        return None
    parsed = urlparse(url)
    host, path = (parsed.hostname or '').lower(), parsed.path.lower()
    if not name_matches:
        return None
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        return 'linkedin'
    if excluded_profile_source(url):
        return None
    if re.search(r'(?:^|[/_-])(?:cv|vita|resume)(?:[./_-]|$)', path) or (
            path.endswith('.pdf') and re.search(r'curriculum vitae|\bCV\b|\bresume\b', summary, re.I)):
        return 'cv'
    if path.endswith('.pdf'):
        return None  # A research article is not a CV or a current profile.
    if official:
        return 'university'
    if re.search(r'/(?:news|events?|articles?|publications?|books?|doi)/', path):
        return None
    if re.search(r'\b(?:lab|laboratory|research group|research team)\b', summary, re.I) and (
            re.search(r'lab|research', host) or re.search(r'/team|/people|/about', path)):
        return 'lab'
    if host.endswith('.github.io') or re.search(r'/~|/people/|/team|/about|/profile|/authors/', path) or (
            profile_title and re.search(r'personal|homepage|home page|researcher|professor|student', summary, re.I)):
        return 'personal'
    return None


def cv_homepage(url):
    """Infer only unambiguous personal-site roots, not a university home page."""
    parsed = urlparse(url)
    if not parsed.path.lower().endswith('.pdf'):
        return None
    if (parsed.hostname or '').endswith('.github.io'):
        return urlunparse((parsed.scheme, parsed.netloc, '/', '', '', ''))
    match = re.match(r'(/~[^/]+/)', parsed.path)
    if match:
        return urlunparse((parsed.scheme, parsed.netloc, match.group(1), '', '', ''))
    return None


def linked_profile_leads(url, anchors, name_matches, *, personal=False, limit=2):
    """Name-bearing profile links, or an already attributed site's About/Team link."""
    leads, seen = [], set()
    for anchor in anchors:
        target = canonical_source_url(urljoin(url, str(anchor.get('href') or '')))
        label = str(anchor.get('label') or '')
        path = urlparse(target).path
        same_host = urlparse(target).hostname == urlparse(url).hostname
        named = name_matches(label)
        if not named and not (personal and same_host and re.fullmatch(
            r'\s*(?:about(?: me| us)?|home|team|people|biography|profile)\s*', label, re.I)):
            continue
        if not safe_source_link(target) or target == canonical_source_url(url) or target in seen or path.lower().endswith('.pdf'):
            continue
        if re.search(r'/(?:news|events?|publications?|papers?)/', path, re.I):
            continue
        seen.add(target)
        leads.append({'href': target, 'title': label, 'body': '', 'discovered_from': url,
                      'named_anchor': named})
        if len(leads) >= limit:
            break
    return leads
