"""Exact institution aliases and domain locators; these never prove a person's role.

This deliberately small seed catalog complements institutions.primary_domain.
Add full names/aliases, never substring matches (Central Missouri != Missouri).
No external requests or OpenAlex credits are needed to use these locators.
"""
import re
import unicodedata
from urllib.parse import urlparse

# Read-side safeguard for older saved records whose country is still the US
# parent's. Parameterize this expression in SQL; never interpolate user URLs.
OFFSHORE_SOURCE_PATTERN = (r'^https?://([a-z0-9-]+\.)*('
    r'rit\.edu/(dubai|croatia)(/|$)|nyuad\.nyu\.edu/|shanghai\.nyu\.edu/|'
    r'qatar\.cmu\.edu/|qatar-weill\.cornell\.edu/)')

# canonical institution, official domain, supported exact aliases
INSTITUTIONS = (
    ('Morgan State University', 'morgan.edu', ()),
    ('Stanford University', 'stanford.edu', ()),
    ('Michigan State University', 'msu.edu', ()),
    ('University of Southern Mississippi', 'usm.edu', ('The University of Southern Mississippi',)),
    ('Arizona State University', 'asu.edu', ()),
    ('New York University', 'nyu.edu', ()),
    ('University of Nebraska Omaha', 'unomaha.edu', ('University of Nebraska at Omaha',)),
    ('George Washington University', 'gwu.edu', ('The George Washington University',)),
    ('Seattle University', 'seattleu.edu', ()),
    ('City College of New York', 'ccny.cuny.edu', ('The City College of New York',)),
    ('University of Georgia', 'uga.edu', ()),
    ('Ohio University', 'ohio.edu', ()),
    ('Carnegie Mellon University', 'cmu.edu', ()),
    ('The University of Texas at Austin', 'utexas.edu', ('University of Texas at Austin', 'UT Austin')),
    ('The University of Texas Health Science Center at Houston', 'uth.edu',
     ('University of Texas Health Science Center at Houston', 'UTHealth Houston')),
    ('Southern Methodist University', 'smu.edu', ()),
    ('University of Arizona', 'arizona.edu', ('The University of Arizona',)),
    ('Northwestern University', 'northwestern.edu', ()),
    ('University at Buffalo', 'buffalo.edu', ('University at Buffalo, State University of New York', 'SUNY Buffalo')),
    ('University of Central Missouri', 'ucmo.edu', ()),
    ('University of Missouri', 'missouri.edu', ('University of Missouri-Columbia',)),
    ('Colorado School of Mines', 'mines.edu', ()),
    ('Northeastern University', 'northeastern.edu', ()),
    ('Rochester Institute of Technology', 'rit.edu', ()),
    ('Lehigh University', 'lehigh.edu', ()),
    ('Western Illinois University', 'wiu.edu', ()),
    ('Massachusetts Institute of Technology', 'mit.edu', ()),
    ('Georgia Institute of Technology', 'gatech.edu', ('Georgia Tech',)),
    ('American University', 'american.edu', ()),
    ('University of Colorado Boulder', 'colorado.edu', ('University of Colorado at Boulder',)),
)


def key(value):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold()
                   if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def record_for_name(name):
    normalized = key(name)
    return next((r for r in INSTITUTIONS if normalized in {key(v) for v in (r[0], *r[2])}), None)


def record_for_host(host):
    host = str(host).lower().rstrip('.')
    return next((r for r in sorted(INSTITUTIONS, key=lambda r: -len(r[1]))
                 if host == r[1] or host.endswith('.' + r[1])), None)


def canonical_institution(name):
    record = record_for_name(name)
    return record[0] if record else name


def academic_domain_hint(value):
    """Only academic domains may become site: queries from email/PDF clues."""
    domain = str(value).strip().lower().rstrip('.')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', domain):
        return ''
    return domain if (record_for_host(domain) or domain.endswith('.edu') or
                      re.search(r'\.(?:ac|edu)\.[a-z]{2}$', domain)) else ''


def offshore_appointment(url):
    """Recognize explicit overseas campus URL namespaces, not footer mentions.

    Return a separate campus institution so saving AE/CN/QA never changes the
    parent US institution's country for other professors.
    """
    parsed = urlparse(url)
    host, path = (parsed.hostname or '').lower(), parsed.path.lower()
    campuses = (
        ('rit.edu', '/dubai/', 'Rochester Institute of Technology — Dubai', 'AE'),
        ('rit.edu', '/croatia/', 'Rochester Institute of Technology — Croatia', 'HR'),
        ('nyuad.nyu.edu', '/', 'New York University — Abu Dhabi', 'AE'),
        ('shanghai.nyu.edu', '/', 'New York University — Shanghai', 'CN'),
        ('qatar.cmu.edu', '/', 'Carnegie Mellon University — Qatar', 'QA'),
        ('qatar-weill.cornell.edu', '/', 'Weill Cornell Medicine — Qatar', 'QA'),
    )
    return next(((name, country) for domain, prefix, name, country in campuses
                 if (host == domain or host.endswith('.' + domain)) and path.startswith(prefix)), None)
