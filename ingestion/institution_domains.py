"""Exact institution aliases and domain locators; these never prove a person's role.

This deliberately small seed catalog complements institutions.primary_domain.
Add full names/aliases, never substring matches (Central Missouri != Missouri).
No external search requests are needed to use these locators.
"""
import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlparse

# Read-side safeguard for older saved records whose country is still the US
# parent's. Parameterize this expression in SQL; never interpolate user URLs.
OFFSHORE_SOURCE_PATTERN = (r'^https?://([a-z0-9-]+\.)*('
    r'rit\.edu/(dubai|croatia)(/|$)|nyuad\.nyu\.edu/|shanghai\.nyu\.edu/|'
    r'qatar\.cmu\.edu/|qatar-weill\.cornell\.edu/)')

# canonical institution, official domain, supported exact aliases
INSTITUTIONS = (
    ('Columbia University', 'columbia.edu', ('Columbia University in the City of New York',)),
    ('Virginia Tech', 'vt.edu', ('Virginia Polytechnic Institute and State University',)),
    ('University of South Carolina-Columbia', 'sc.edu', ('University of South Carolina',)),
    ('Morgan State University', 'morgan.edu', ()),
    ('Stanford University', 'stanford.edu', ()),
    ('Michigan State University', 'msu.edu', ()),
    ('University of Michigan', 'umich.edu',
     ('University of Michigan-Ann Arbor', 'University of Michigan Ann Arbor',
      'UMich', 'U-M')),
    ('University of Washington', 'washington.edu',
     ('University of Washington, Seattle', 'UW Seattle')),
    ('Washington University in St. Louis', 'wustl.edu',
     ('Washington University at St. Louis', 'WashU', 'WUSTL')),
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
    ('Massachusetts Institute of Technology', 'mit.edu', ('MIT - Massachusetts Institute of Technology', 'MIT')),
    ('University at Albany', 'albany.edu', ('University at Albany, State University of New York', 'SUNY Albany')),
    ('Florida International University', 'fiu.edu', ('Florida International University in Miami, FL',)),
    ('Rutgers University', 'rutgers.edu', ('Rutgers, The State University of New Jersey', 'Rutgers University, The State University of New Jersey')),
    ('University of Pittsburgh', 'pitt.edu', ('University of Pittsburgh-Pittsburgh Campus',)),
    ('University of Alabama', 'ua.edu', ('The University of Alabama',)),
    ('North Carolina State University', 'ncsu.edu', ('North Carolina State University at Raleigh',)),
    ('University of Oklahoma', 'ou.edu', ('University of Oklahoma-Norman Campus',)),
    ('Purdue University', 'purdue.edu', ('Purdue University-Main Campus', 'Purdue University West Lafayette',)),
    ('Wichita State University', 'wichita.edu', ('Wichita State University - Kansas',)),
    ('State University of New York at New Paltz', 'newpaltz.edu', ('The State University of New York at New Paltz', 'SUNY New Paltz')),
    ('University of Maryland, College Park', 'umd.edu', ('University of Maryland', 'University of Maryland-College Park')),
    ('Georgia Institute of Technology', 'gatech.edu', ('Georgia Tech',)),
    ('American University', 'american.edu', ()),
    ('University of Colorado Boulder', 'colorado.edu', ('University of Colorado at Boulder',)),
    ('Drexel University', 'drexel.edu', ()),
    ('Texas A&M University', 'tamu.edu',
     ('Texas A&M University-College Station',
      'Texas A&M University, College Station')),
)

# Explicit parent/campus continuity used only for affiliation matching. These
# names remain distinct canonical display names.
AFFILIATION_EQUIVALENTS = (
    ('Pennsylvania State University', 'Pennsylvania State University-Penn State Hazleton'),
    ('University of Hawaiʻi at Mānoa', 'University of Hawaii System'),
)


def key(value):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold()
                   if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


@lru_cache(maxsize=8192)
def _directory_record_for_name(normalized):
    if not normalized:
        return None
    try:
        from db import get_db_connection
        with get_db_connection() as connection:
            row = connection.execute(
                """SELECT institution_name, primary_domain
                   FROM college_scorecard_institutions
                   WHERE normalized_name = %s
                   ORDER BY CASE WHEN is_currently_operating IS TRUE THEN 0 ELSE 1 END,
                            CASE WHEN is_main_campus IS TRUE THEN 0 ELSE 1 END
                   LIMIT 1""",
                (normalized,),
            ).fetchone()
        if row:
            return (str(row['institution_name']), str(row['primary_domain'] or ''), ())
    except Exception:
        return None
    return None


@lru_cache(maxsize=8192)
def _directory_record_for_host(host):
    if not host:
        return None
    try:
        from db import get_db_connection
        with get_db_connection() as connection:
            row = connection.execute(
                """SELECT institution_name, primary_domain
                   FROM college_scorecard_institutions
                   WHERE primary_domain = %s
                   ORDER BY CASE WHEN is_currently_operating IS TRUE THEN 0 ELSE 1 END,
                            CASE WHEN is_main_campus IS TRUE THEN 0 ELSE 1 END
                   LIMIT 1""",
                (host,),
            ).fetchone()
        if row:
            return (str(row['institution_name']), str(row['primary_domain'] or ''), ())
    except Exception:
        return None
    return None


def record_for_name(name):
    normalized = key(name)
    seeded = next((r for r in INSTITUTIONS if normalized in {key(v) for v in (r[0], *r[2])}), None)
    return seeded or _directory_record_for_name(normalized)


def record_for_host(host):
    host = str(host).lower().rstrip('.')
    seeded = next((r for r in sorted(INSTITUTIONS, key=lambda r: -len(r[1]))
                   if host == r[1] or host.endswith('.' + r[1])), None)
    if seeded:
        return seeded
    candidates = [host]
    labels = host.split('.')
    if len(labels) > 2 and labels[-1] == 'edu':
        candidates.append('.'.join(labels[-2:]))
    for candidate in candidates:
        record = _directory_record_for_host(candidate)
        if record:
            return record
    return None


def canonical_institution(name):
    record = record_for_name(name)
    return record[0] if record else name


def institutions_equivalent(left, right):
    """Match canonical identities without guessing ambiguous abbreviations."""
    if key(left) and key(left) == key(right):
        return True
    if frozenset((key(left), key(right))) in {
        frozenset((key(a), key(b))) for a, b in AFFILIATION_EQUIVALENTS
    }:
        return True
    left_record, right_record = record_for_name(left), record_for_name(right)
    if left_record or right_record:
        return bool(left_record and right_record and key(left_record[0]) == key(right_record[0]))
    left_key, right_key = key(left), key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if frozenset((left_key, right_key)) in {
        frozenset((key(a), key(b))) for a, b in AFFILIATION_EQUIVALENTS
    }:
        return True
    # Similar words are not an institution identity. Unknown aliases need an
    # explicit registry entry; campuses sharing a parent must remain distinct.
    return False


def academic_domain_hint(value):
    """Only academic domains may become site: queries from email/PDF clues."""
    domain = str(value).strip().lower().rstrip('.')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', domain):
        return ''
    public_email_domains = {
        'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com',
        'live.com', 'yahoo.com', 'icloud.com', 'proton.me', 'protonmail.com',
    }
    if domain in public_email_domains:
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
