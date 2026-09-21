import json
from pathlib import Path
import pytest
from ingestion.program_gpa import analyze_gpa, page_text, resolve_sources, fetch_official_text

@pytest.mark.parametrize('text,degree,status,value',[
 ('Applicants must have a minimum GPA of 3.0 for admission.','PhD','PUBLISHED_MINIMUM',3),
 ('There is no minimum GPA of 3.0 required for admission.','PhD','EXPLICIT_NO_MINIMUM',None),
 ('Applicants should have a GPA of 3.5, which is recommended.','PhD','NOT_FOUND',None),
 ('Undergraduate applicants must have a minimum GPA of 3.0.','PhD','NOT_FOUND',None),
 ('A minimum GPA of 3.0 is required for the MS program.','PhD','NEEDS_REVIEW',None),
 ('A minimum GPA of 3.0 is required for graduation.','PhD','NOT_FOUND',None),
 ('Applicants must have a GPA of 3.0.\nApplicants must have a GPA of 3.5.','PhD','NEEDS_REVIEW',None),
 ('We do not require a minimum GPA for admission.','MS','EXPLICIT_NO_MINIMUM',None),
 ('Is there a minimum GPA?\nThere is no minimum GPA required to apply.','MS','EXPLICIT_NO_MINIMUM',None),
 ('Applications are reviewed holistically.','PhD','NOT_FOUND',None),
 ('A minimum GPA of 8.0 on a 10.0 scale is required.','MS','PUBLISHED_MINIMUM',8),
 ('A minimum GPA of 4.5 on a 4.0 scale is required.','MS','NEEDS_REVIEW',None),
 ('A minimum GPA of 3.0 is required unless an exception is approved.','MS','NEEDS_REVIEW',None),
])
def test_parser(text,degree,status,value):
    r=analyze_gpa(text,degree)
    assert (r['status'],r['minimum_gpa'])==(status,value)

def test_inline_markup():
    r=analyze_gpa(page_text('<p>Applicants need a minimum <strong>GPA</strong> of <b>3.0</b> on a 4.0 scale.</p>'),'PhD')
    assert (r['minimum_gpa'],r['gpa_scale'])==(3,4)

def test_conflicting_sources():
    a=analyze_gpa('A minimum GPA of 3.0 is required.','PhD')
    b=analyze_gpa('There is no minimum GPA required.','PhD')
    assert resolve_sources([a,b])['status']=='NEEDS_REVIEW'

def test_redirect_off_domain(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('ingestion.program_gpa._fetch_with_safe_redirects',lambda _:SimpleNamespace(url='https://example.org/admissions',raise_for_status=lambda:None))
    with pytest.raises(ValueError,match='redirected'):fetch_official_text('https://example.edu/admissions','example.edu')

FIXTURES=Path(__file__).parent/'fixtures/gpa'
@pytest.mark.parametrize('case',json.loads((FIXTURES/'manifest.json').read_text()))
def test_official_admissions_pages(case):
    snapshot=FIXTURES/(case['key']+'.html')
    if not snapshot.exists():pytest.skip('Local downloaded snapshot absent; run scripts.validate_gpa_pages for fresh network validation.')
    r=analyze_gpa(page_text(snapshot.read_text()),case['degree'])
    assert (r['status'],r['minimum_gpa'])==(case['status'],case['minimum']),r

def test_negated_program_minimum_does_not_hide_university_minimum():
    r=analyze_gpa('There is no minimum GPA, but the graduate school requires a minimum GPA of 3.0.','PhD')
    assert r['status']=='NEEDS_REVIEW'

def test_credit_windows_preserve_or():
    r=analyze_gpa('A minimum GPA of 3.0 is required for the last two years OR last 90 quarter credits OR last 60 semester credits.','PhD')
    assert ' OR ' in r['gpa_basis']

def test_missing_scale_not_assumed():
    assert analyze_gpa('A minimum GPA of 3.0 is required.','PhD')['gpa_scale'] is None

def test_bot_page_not_saved_as_missing_requirement(monkeypatch):
    from types import SimpleNamespace
    response=SimpleNamespace(url='https://example.edu/admissions',headers={'Content-Type':'text/html'},
        text='<p>Verify you are human</p>'+'x'*150,raise_for_status=lambda:None)
    monkeypatch.setattr('ingestion.program_gpa._fetch_with_safe_redirects',lambda _:response)
    with pytest.raises(ValueError,match='blocked'):fetch_official_text(response.url,'example.edu')
