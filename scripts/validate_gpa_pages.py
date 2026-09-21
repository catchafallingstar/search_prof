"""Download official admissions pages and compare GPA results with reviewed expectations.

No database access or writes. Raw page snapshots stay in the selected cache directory.
Run: python -m scripts.validate_gpa_pages --cache-dir work/gpa-pages
"""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from ingestion.program_gpa import analyze_gpa, fetch_official_text
CASES=[
 dict(key='ut_cs',url='https://www.cs.utexas.edu/graduate/apply',degree='PhD',status='PUBLISHED_MINIMUM',minimum=3.0),
 dict(key='uw_psych',url='https://psych.uw.edu/graduate/prospective-students/faq',degree='PhD',status='PUBLISHED_MINIMUM',minimum=3.0),
 dict(key='fiu_cs',url='https://www.cis.fiu.edu/degree/master-of-science-in-computer-science/',degree='MS',status='PUBLISHED_MINIMUM',minimum=3.0),
 dict(key='stanford_chpr',url='https://prevention.stanford.edu/education/chpr/faq.html',degree='MS',status='EXPLICIT_NO_MINIMUM',minimum=None),
 dict(key='stanford_graduation_negative',url='https://www.cs.stanford.edu/ms-requirements-overall',degree='MS',status='NOT_FOUND',minimum=None),
]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache-dir',type=Path,required=True);a=p.parse_args()
    a.cache_dir.mkdir(parents=True,exist_ok=True)
    results=[]
    for case in CASES:
        try:
            text=fetch_official_text(case['url'],urlparse(case['url']).hostname)
            (a.cache_dir/(case['key']+'.txt')).write_text(text,encoding='utf-8')
            r=analyze_gpa(text,case['degree'])
            ok=(r['status'],r['minimum_gpa'])==(case['status'],case['minimum'])
            results.append(dict(case=case,result=r,passed=ok))
            print(case['key'], 'PASS' if ok else 'FAIL',r['status'],r['minimum_gpa'])
        except Exception as e:
            results.append(dict(case=case,passed=False,error=f'{type(e).__name__}: {e}'))
            print(case['key'],'FETCH/EXTRACTION FAILED',type(e).__name__)
    (a.cache_dir/'report.json').write_text(json.dumps(dict(checked_at=datetime.now(timezone.utc).isoformat(),results=results),indent=2),encoding='utf-8')
    raise SystemExit(0 if all(x['passed'] for x in results) else 1)
if __name__=='__main__':main()
