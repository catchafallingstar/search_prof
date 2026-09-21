"""GPA extraction from reviewed program-specific official admissions sources."""
from __future__ import annotations
import json
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from db import get_db_connection
from ingestion.parse_hiring_signals import _fetch_with_safe_redirects, _host_matches_domain

GPA = r'(?:G\.?P\.?A\.?|grade[ -]point average)'
NUMBER = r'(?<![\d.])(?:[0-9]|10)(?:\.\d{1,2})?(?![\d.])'
STATUSES = {'PUBLISHED_MINIMUM','EXPLICIT_NO_MINIMUM','NOT_FOUND','NEEDS_REVIEW'}

def page_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for el in soup(['script','style','nav','footer','header','noscript']):
        el.decompose()
    for el in soup.find_all(['p','li','h1','h2','h3','h4','tr','br']):
        el.insert_before('\n'); el.insert_after('\n')
    return '\n'.join(' '.join(x.split()) for x in soup.get_text(' ').splitlines() if x.strip())

def _basis(text):
    # Preserve AND/OR wording; do not turn alternative credit windows into a list.
    m = re.search(r'last\s+(?:60|90|two|2)\b|upper[ -]division|\b(?:overall|cumulative)\b',text,re.I)
    return re.split(r'(?<=[.!?])\s+',text[m.start():],maxsplit=1)[0] if m else None

def analyze_gpa(text, degree_type):
    candidates, ignored = [], []
    section = ''
    for line in [' '.join(x.split()) for x in text.splitlines() if x.strip()]:
        if len(line)<120 and not re.search(GPA,line,re.I): section=line
        if line.endswith('?'): continue
        if not re.search(GPA+r'|\b[‘\'\"]?B[’\'\"]? average',line,re.I): continue
        context = section+' '+line
        if re.search(r'(?:undergraduate|freshman|transfer)\s+(?:admission|applicant)|scholarship|to graduate|for graduation|maintain|academic standing|receive your .*degree',context,re.I):
            ignored.append(line); continue
        other = (r'\bM\.?S\.?\b|master[’\']?s? (?:program|admission|applicant)' if degree_type.casefold()=='phd' else r'\bPh\.?D\.?\b|doctoral (?:program|admission|applicant)')
        if re.search(other,context,re.I):
            candidates.append(dict(ambiguous=True,evidence=line)); continue
        no_min = re.search(r'(?:no|without|not (?:have|require|specify|set|establish))\s+(?:(?:a|any|formal|specific|strict|set)\s+)*(?:minimum\s+'+GPA+r'|'+GPA+r'\s+(?:minimum|requirement))|'+GPA+r'\s+(?:is\s+)?not\s+required',line,re.I)
        if no_min and re.search(r'\b(?:but|however|although)\b.*(?:require|minimum|must)',line[no_min.end():],re.I):
            candidates.append(dict(ambiguous=True,evidence=line)); continue
        if no_min:
            candidates.append(dict(status='EXPLICIT_NO_MINIMUM',minimum_gpa=None,gpa_scale=None,gpa_basis=None,evidence=line)); continue
        if re.search(r'recommend|prefer|competitive|average (?:admitted|accepted)|typically|normally',line,re.I) and not re.search(r'minimum|required|must|at least|eligible',line,re.I):
            ignored.append(line); continue
        if not re.search(r'minimum|required|must|at least|or (?:higher|better)|need|requirement',line,re.I): continue
        patterns = [GPA+r'\s*(?:of|is|:|=)?\s*('+NUMBER+r')',
                    r'('+NUMBER+r')\s*(?:cumulative\s+|overall\s+|undergraduate\s+)?'+GPA,
                    GPA+r'\s+(?:of\s+)?(?:at least|minimum(?:\s+of)?|must be)\s*('+NUMBER+r')',
                    r'B[’\'\"]?\s+average\s*\(('+NUMBER+r')']
        nums = {float(m.group(1)) for pat in patterns for m in re.finditer(pat,line,re.I)}
        sm = re.search(r'(?:on (?:a |the )?)(\d+(?:\.\d+)?)\s*(?:[ -]point\s*)?scale|out of\s+(\d+(?:\.\d+)?)',line,re.I)
        scale = float(next(x for x in sm.groups() if x)) if sm else None
        if len(nums)!=1 or re.search(r'\b(?:unless|alternatively|exception|waiv\w*|conditional)\b',line,re.I):
            candidates.append(dict(ambiguous=True,evidence=line)); continue
        minimum = next(iter(nums))
        if scale is not None and (scale<=0 or minimum>scale):
            candidates.append(dict(ambiguous=True,evidence=line)); continue
        candidates.append(dict(status='PUBLISHED_MINIMUM',minimum_gpa=minimum,gpa_scale=scale,gpa_basis=_basis(line),evidence=line))
    identities = {(x.get('status'),x.get('minimum_gpa')) for x in candidates}
    bases = {x['gpa_basis'] for x in candidates if x.get('gpa_basis')}
    scales = {x['gpa_scale'] for x in candidates if x.get('gpa_scale')}
    if any(x.get('ambiguous') for x in candidates) or len(identities)>1 or len(bases)>1 or len(scales)>1:
        return dict(status='NEEDS_REVIEW',minimum_gpa=None,gpa_scale=None,gpa_basis=None,evidence_text='\n'.join(x['evidence'] for x in candidates),candidates=candidates)
    if candidates:
        r=max(candidates,key=lambda x:bool(x.get('gpa_basis'))+bool(x.get('gpa_scale')))
        return {**{k:v for k,v in r.items() if k!='evidence'},'evidence_text':r['evidence'],'candidates':candidates}
    return dict(status='NOT_FOUND',minimum_gpa=None,gpa_scale=None,gpa_basis=None,evidence_text=None,candidates=[],ignored_statements=ignored)

def extract_program_gpa(text):
    """Compatibility helper; recommendations are not persisted as minimums."""
    r=analyze_gpa(text,'PhD')
    if r['status'] in {'PUBLISHED_MINIMUM','EXPLICIT_NO_MINIMUM'}:
        return dict(policy='HARD_MINIMUM' if r['status']=='PUBLISHED_MINIMUM' else 'NO_FORMAL_MINIMUM',minimum=r['minimum_gpa'],evidence=r['evidence_text'])
    m=re.search(GPA+r'\s+(?:of\s+)?(\d(?:\.\d+)?)\s+.*(?:recommend|prefer)',text,re.I)
    if m: return dict(policy='RECOMMENDED',minimum=float(m.group(1)),evidence=text)
    return None

def fetch_official_text(url,domain):
    if not domain or not _host_matches_domain((urlparse(url).hostname or '').lower(),domain.lower()):
        raise ValueError('Admissions URL is outside the approved official domain.')
    response=_fetch_with_safe_redirects(url)
    response.raise_for_status()
    if not _host_matches_domain((urlparse(response.url).hostname or '').lower(),domain.lower()):
        raise ValueError('Admissions URL redirected outside the approved official domain.')
    if 'html' not in response.headers.get('Content-Type','').lower():
        raise ValueError('Only HTML admissions sources are supported; review PDF sources manually.')
    text=page_text(response.text)
    if len(text)<100 or re.search(r'verify you are human|just a moment|access denied|captcha',text[:1000],re.I):
        raise ValueError('Admissions source is empty or blocked; existing evidence is preserved.')
    return text

def resolve_sources(results):
    found=[r for r in results if r['status']!='NOT_FOUND']
    if not found: return {**results[0],'status':'NOT_FOUND'}
    keys={(r['status'],r['minimum_gpa'],r['gpa_scale'],r['gpa_basis']) for r in found}
    if len(keys)>1 or any(r['status']=='NEEDS_REVIEW' for r in found):
        return dict(status='NEEDS_REVIEW',minimum_gpa=None,gpa_scale=None,gpa_basis=None,evidence_text='\n'.join(r.get('evidence_text') or '' for r in found),source_url=None,requirement_level=None)
    return found[0]

def check_program_gpa(program_id):
    with get_db_connection() as c:
        with c.cursor() as cur:
            cur.execute('SELECT * FROM graduate_programs WHERE id=%s AND verified_at IS NOT NULL',(program_id,))
            program=cur.fetchone()
            if not program: raise ValueError('Register and verify this program before checking GPA.')
            cur.execute('SELECT * FROM program_admission_sources WHERE program_id=%s AND applicability_verified=TRUE ORDER BY id',(program_id,))
            sources=list(cur.fetchall())
    if not sources: raise ValueError('No official admissions source has verified applicability.')
    results=[]
    # Fetch errors propagate to worker retry without replacing earlier evidence.
    for s in sources:
        r=analyze_gpa(fetch_official_text(s['source_url'],s['official_domain']),program['degree_type'])
        results.append({**r,'source_url':s['source_url'],'requirement_level':s['requirement_level']})
    resolved=resolve_sources(results)
    with get_db_connection() as c:
        with c.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(f'program-gpa:{program_id}',))
            cur.execute('INSERT INTO program_admission_evidence (program_id,results_json) VALUES (%s,%s::jsonb)',(program_id,json.dumps(results)))
            policy={'PUBLISHED_MINIMUM':'HARD_MINIMUM','EXPLICIT_NO_MINIMUM':'NO_FORMAL_MINIMUM','NOT_FOUND':'NOT_STATED','NEEDS_REVIEW':'NEEDS_REVIEW'}[resolved['status']]
            cur.execute('''INSERT INTO program_admission_requirements
                (program_id,institution_id,department_key,department,degree_type,policy,minimum_gpa,
                 gpa_scale,gpa_basis,requirement_level,evidence_text,source_url,check_status,checked_at,next_check_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()+INTERVAL '90 days')
                ON CONFLICT (program_id) WHERE program_id IS NOT NULL DO UPDATE SET
                policy=EXCLUDED.policy,minimum_gpa=EXCLUDED.minimum_gpa,gpa_scale=EXCLUDED.gpa_scale,
                gpa_basis=EXCLUDED.gpa_basis,requirement_level=EXCLUDED.requirement_level,
                evidence_text=EXCLUDED.evidence_text,source_url=EXCLUDED.source_url,
                check_status=EXCLUDED.check_status,checked_at=NOW(),next_check_at=EXCLUDED.next_check_at,last_error=NULL
                WHERE program_admission_requirements.manual_override=FALSE RETURNING id''',
                (program_id,program['institution_id'],f'program:{program_id}',program['department'],program['degree_type'],policy,
                 resolved['minimum_gpa'],resolved['gpa_scale'],resolved['gpa_basis'],resolved.get('requirement_level'),
                 resolved['evidence_text'],resolved.get('source_url'),resolved['status']))
            preserved=cur.fetchone() is None
    return {**resolved,'program_id':program_id,'manual_override_preserved':preserved,
            'requirements_found':int(not preserved and resolved['status'] in {'PUBLISHED_MINIMUM','EXPLICIT_NO_MINIMUM'})}

def check_program_gpa_for_professor(professor_id):
    raise ValueError('Legacy professor-based GPA job: register and verify a graduate program instead.')
