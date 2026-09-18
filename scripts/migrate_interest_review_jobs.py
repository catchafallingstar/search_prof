"""Allow the dedicated durable research-interest review job (no data rewrite)."""
from db import get_db_connection


def main():
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE radar_jobs DROP CONSTRAINT IF EXISTS radar_jobs_job_type_check')
            cursor.execute("""ALTER TABLE radar_jobs ADD CONSTRAINT radar_jobs_job_type_check CHECK (job_type IN (
                'DISCOVER_FACULTY_DIRECTORIES','CRAWL_FACULTY_DIRECTORY',
                'MATCH_FACULTY_PUBLICATIONS','QWEN_REVIEW_PUBLICATION','QWEN_REVIEW_INTERESTS',
                'INDEX_ROSTER_TOPIC','ENRICH_CLASSIFY_PAPER','CHECK_HIRING','CHECK_GRANTS','CHECK_PROGRAM_GPA'))""")
    print('Research-interest review jobs enabled.')


if __name__ == '__main__':
    main()
