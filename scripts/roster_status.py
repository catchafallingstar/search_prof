from db import get_db_connection


def main() -> None:
    queries = [
        (
            "official_faculty_and_papers",
            """SELECT COUNT(DISTINCT p.id) AS official_faculty,
                      COUNT(DISTINCT pp.paper_id) AS linked_papers,
                      COUNT(DISTINCT p.id) FILTER (WHERE pp.paper_id IS NOT NULL)
                          AS faculty_with_papers
               FROM professors p
               LEFT JOIN professor_papers pp ON pp.professor_id = p.id
               WHERE p.data_origin IN
                   ('OFFICIAL_DIRECTORY', 'OFFICIAL_PROFILE', 'MANUAL_REVIEW')""",
        ),
        (
            "directories",
            """SELECT COUNT(*) AS registered,
                      COUNT(*) FILTER (WHERE active) AS active,
                      COUNT(*) FILTER (WHERE last_success_at IS NOT NULL) AS crawled
               FROM faculty_directories""",
        ),
        (
            "memberships",
            """SELECT COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE currently_listed) AS current
               FROM faculty_directory_memberships""",
        ),
        (
            "topics",
            """SELECT discovery_version, status, COUNT(*) AS total
               FROM radar_topics GROUP BY 1,2 ORDER BY 1,2""",
        ),
        (
            "active_jobs",
            """SELECT job_type, status, COUNT(*) AS total
               FROM radar_jobs WHERE status IN ('queued', 'running')
               GROUP BY 1,2 ORDER BY 1,2""",
        ),
        (
            "hiring_signals_by_origin",
            """SELECT p.data_origin, hs.check_status, COUNT(*) AS total
               FROM hiring_signals hs JOIN professors p ON p.id = hs.professor_id
               GROUP BY 1,2 ORDER BY 1,2""",
        ),
        (
            "gpa_evidence_by_origin",
            """SELECT data_origin,
                      COUNT(*) FILTER (WHERE gpa_last_checked_at IS NOT NULL) AS checked,
                      COUNT(*) FILTER (WHERE lab_gpa_evidence_text IS NOT NULL
                                        OR program_gpa_source_url IS NOT NULL) AS evidence_found
               FROM professors GROUP BY 1 ORDER BY 1""",
        ),
        (
            "reused_official_source_urls",
            """SELECT source_url, COUNT(DISTINCT professor_id) AS professors
               FROM faculty_verification_evidence
               WHERE source_type = 'OFFICIAL_UNIVERSITY_PAGE'
                 AND verification_status = 'VERIFIED'
                 AND supports_decision = TRUE
               GROUP BY source_url HAVING COUNT(DISTINCT professor_id) >= 2
               ORDER BY professors DESC, source_url LIMIT 30""",
        ),
    ]
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for label, query in queries:
                cursor.execute(query)
                print(label, list(cursor.fetchall()))


if __name__ == "__main__":
    main()
