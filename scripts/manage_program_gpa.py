"""Trusted-terminal administration of verified graduate programs and GPA evidence."""
import argparse
import json
from urllib.parse import urlparse
from db import get_db_connection
from ingestion.program_gpa import check_program_gpa, STATUSES
from ingestion.parse_hiring_signals import _host_matches_domain
from radar_store import enqueue_radar_job


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    add=sub.add_parser('register')
    add.add_argument('--institution-id',type=int,required=True)
    add.add_argument('--name',required=True)
    add.add_argument('--degree',choices=['MS','MA','PhD','EdD','MBA','Other'],required=True)
    add.add_argument('--department')
    add.add_argument('--confirm-verified-program',action='store_true',required=True)
    source=sub.add_parser('source')
    source.add_argument('--program-id',type=int,required=True)
    source.add_argument('--url',required=True)
    source.add_argument('--official-domain',required=True)
    source.add_argument('--level',choices=['PROGRAM','GRADUATE_SCHOOL'],required=True)
    source.add_argument('--applicability-note',required=True)
    source.add_argument('--confirm-applicability',action='store_true',required=True)
    link=sub.add_parser('link')
    link.add_argument('--program-id',type=int,required=True)
    link.add_argument('--professor-id',type=int,required=True)
    link.add_argument('--evidence-url',required=True)
    link.add_argument('--confirm-membership',action='store_true',required=True)
    for action in ['check','queue']:
        q=sub.add_parser(action);q.add_argument('--program-id',type=int,required=True)
        if action=='queue':q.add_argument('--priority',type=int,default=95)
    sub.add_parser('list')
    edit=sub.add_parser('manual')
    edit.add_argument('--program-id',type=int,required=True)
    edit.add_argument('--status',choices=sorted(STATUSES),required=True)
    edit.add_argument('--minimum',type=float)
    edit.add_argument('--scale',type=float)
    edit.add_argument('--basis')
    edit.add_argument('--url',required=True)
    edit.add_argument('--evidence',required=True)
    edit.add_argument('--level',choices=['PROGRAM','GRADUATE_SCHOOL'],required=True)
    a=p.parse_args()
    if a.action=='check': print(json.dumps(check_program_gpa(a.program_id),default=str,indent=2));return
    if a.action=='queue':
        with get_db_connection() as c:
            with c.cursor() as cur:
                cur.execute('SELECT id FROM graduate_programs WHERE id=%s AND verified_at IS NOT NULL',(a.program_id,))
                if not cur.fetchone():p.error('Program must be registered and verified.')
        print(json.dumps(enqueue_radar_job('CHECK_PROGRAM_GPA',program_id=a.program_id,priority=a.priority),default=str));return
    with get_db_connection() as c:
        with c.cursor() as cur:
            if a.action=='register':
                if not a.name.strip():p.error('Program name cannot be empty.')
                cur.execute('''INSERT INTO graduate_programs(institution_id,program_name,department,degree_type,verified_at)
                    VALUES(%s,%s,%s,%s,NOW()) ON CONFLICT(institution_id,program_name,degree_type)
                    DO UPDATE SET department=EXCLUDED.department,verified_at=NOW() RETURNING id''',
                    (a.institution_id,a.name.strip(),a.department,a.degree))
            elif a.action=='source':
                parsed=urlparse(a.url)
                if parsed.scheme not in ('https','http') or not _host_matches_domain(parsed.hostname or '',a.official_domain):p.error('Source must be on its approved official domain.')
                if not a.applicability_note.strip():p.error('Applicability evidence is required.')
                cur.execute('''INSERT INTO program_admission_sources(program_id,source_url,official_domain,requirement_level,applicability_verified,applicability_note)
                    VALUES(%s,%s,%s,%s,TRUE,%s) ON CONFLICT(program_id,source_url)
                    DO UPDATE SET official_domain=EXCLUDED.official_domain,requirement_level=EXCLUDED.requirement_level,
                    applicability_verified=TRUE,applicability_note=EXCLUDED.applicability_note RETURNING id''',
                    (a.program_id,a.url,a.official_domain,a.level,a.applicability_note))
            elif a.action=='link':
                if urlparse(a.evidence_url).scheme not in ('https','http'):p.error('HTTP evidence URL required.')
                cur.execute('''SELECT p.id FROM professors p JOIN graduate_programs gp ON gp.institution_id=p.institution_id
                    WHERE p.id=%s AND gp.id=%s AND gp.verified_at IS NOT NULL''',(a.professor_id,a.program_id))
                if not cur.fetchone():p.error('Professor and verified program must belong to the same institution.')
                cur.execute('''INSERT INTO professor_graduate_programs(professor_id,program_id,evidence_url)
                    VALUES(%s,%s,%s) ON CONFLICT(professor_id,program_id) DO UPDATE
                    SET evidence_url=EXCLUDED.evidence_url,verified_at=NOW() RETURNING program_id''',
                    (a.professor_id,a.program_id,a.evidence_url))
            elif a.action=='manual':
                if (a.status=='PUBLISHED_MINIMUM') != (a.minimum is not None):p.error('Only a published minimum may have a numeric cutoff.')
                if a.minimum is not None and (a.minimum<0 or (a.scale is not None and a.minimum>a.scale)):p.error('Invalid GPA value.')
                if a.scale is not None and a.scale<=0:p.error('Scale must be positive.')
                if urlparse(a.url).scheme not in ('https','http') or not a.evidence.strip():p.error('Source URL and evidence are required.')
                cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(f'program-gpa:{a.program_id}',))
                cur.execute('SELECT * FROM graduate_programs WHERE id=%s AND verified_at IS NOT NULL',(a.program_id,));gp=cur.fetchone()
                if not gp:p.error('Verified program required.')
                policy={'PUBLISHED_MINIMUM':'HARD_MINIMUM','EXPLICIT_NO_MINIMUM':'NO_FORMAL_MINIMUM','NOT_FOUND':'NOT_STATED','NEEDS_REVIEW':'NEEDS_REVIEW'}[a.status]
                cur.execute('INSERT INTO program_admission_evidence(program_id,results_json) VALUES(%s,%s::jsonb)',(a.program_id,json.dumps({'manual':True,**vars(a)})))
                cur.execute('''INSERT INTO program_admission_requirements(program_id,institution_id,department_key,degree_type,policy,check_status,minimum_gpa,gpa_scale,gpa_basis,source_url,evidence_text,requirement_level,manual_override,checked_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW())
                    ON CONFLICT(program_id) WHERE program_id IS NOT NULL DO UPDATE SET
                    policy=EXCLUDED.policy,check_status=EXCLUDED.check_status,minimum_gpa=EXCLUDED.minimum_gpa,
                    gpa_scale=EXCLUDED.gpa_scale,gpa_basis=EXCLUDED.gpa_basis,source_url=EXCLUDED.source_url,
                    evidence_text=EXCLUDED.evidence_text,requirement_level=EXCLUDED.requirement_level,
                    manual_override=TRUE,checked_at=NOW() RETURNING id''',
                    (a.program_id,gp['institution_id'],f'program:{a.program_id}',gp['degree_type'],policy,a.status,a.minimum,a.scale,a.basis,a.url,a.evidence,a.level))
            else:
                cur.execute('''SELECT gp.id,gp.program_name,gp.degree_type,i.name AS university,r.check_status,
                    r.minimum_gpa,r.gpa_scale,r.gpa_basis,r.source_url,r.checked_at,r.manual_override
                    FROM graduate_programs gp JOIN institutions i ON i.id=gp.institution_id
                    LEFT JOIN program_admission_requirements r ON r.program_id=gp.id ORDER BY gp.id''')
                print(json.dumps(cur.fetchall(),default=str,indent=2));return
            print(json.dumps(cur.fetchone(),default=str))

if __name__=='__main__': main()
