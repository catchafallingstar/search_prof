"""Requeue recoverable historical model outages from their saved evidence."""
import argparse
import json
from db import get_db_connection
from radar_store import enqueue_radar_job


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT DISTINCT ON (p.id) p.id,p.name,p.institution_id,p.institution_name,p.department,
                r.source_record_key,r.input_excerpt,r.validation_status,r.created_at
                FROM professors p JOIN ollama_extraction_runs r
                ON split_part(r.source_record_key,':',1)=p.id::text
                WHERE p.faculty_status='VERIFIED' AND r.source_type='RESEARCH_INTEREST_SUMMARY'
                AND NOT EXISTS (SELECT 1 FROM professor_research_interests i WHERE i.professor_id=p.id AND i.checked_at>=r.created_at)
                ORDER BY p.id,r.created_at DESC""")
            rows=cursor.fetchall()
    queued=[]; skipped=[]
    for row in rows:
        if row['validation_status']!='MODEL_UNAVAILABLE':continue
        try:
            saved=json.loads(row['input_excerpt'])
            if saved['institution']!=row['institution_name']:raise ValueError('Institution changed')
            url=row['source_record_key'].split(':',1)[1]
            labels=saved.get('explicit_interests') or []
            text=saved.get('biography') or ''
            payload={'professor':{k:row[k] for k in ('name','institution_id','institution_name','department')},
                     'explicit':[(labels,url,text)] if labels else [],'biography_text':text,'biography_url':url}
            if args.execute:
                job=enqueue_radar_job('QWEN_REVIEW_INTERESTS',professor_id=row['id'],priority=60,max_attempts=3,
                                     initial_result={'interest_input':payload},delay_seconds=300)
                queued.append({'professor':row['name'],'job_id':job['id'],'reused':job.get('reused',False)})
            else:queued.append({'professor':row['name']})
        except (ValueError,KeyError,TypeError):skipped.append(row['id'])
    print(json.dumps({'execute':args.execute,'reviews':queued,'unrecoverable_inputs':skipped},indent=2))


if __name__=='__main__':main()
