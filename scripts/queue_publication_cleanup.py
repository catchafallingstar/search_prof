"""Queue targeted publication refreshes for legacy malformed profile-page rows.

Safe to run repeatedly. Active MATCH_FACULTY_PUBLICATIONS jobs are reused by
radar_store.enqueue_radar_job, and the refreshed parser removes only obvious
legacy file-path/full-citation titles from provenance-only publication sources.
"""
from db import get_db_connection
from radar_store import enqueue_radar_job

PROVENANCE_TYPES = (
    "OFFICIAL_PROFILE",
    "OFFICIAL_ALTERNATE_PROFILE",
    "PERSONAL_SITE",
    "LAB_SITE",
    "INSTITUTIONAL_RESEARCH_PORTAL",
)


def main() -> None:
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                r"""
                SELECT DISTINCT p.id, p.name
                FROM professors p
                JOIN professor_papers link ON link.professor_id=p.id
                JOIN papers paper ON paper.id=link.paper_id
                WHERE paper.source_type = ANY(%s)
                  AND (
                      paper.title ~* '^(https?://\S+|([^/[:space:]]+/)+[^/[:space:]]+\.(pdf|docx?|pptx?))$'
                      OR paper.title ~* '^[A-Z]\.\s+[^.]{1,220}\.\s+.+\m(Proceedings|Conference|Workshop|Journal)\M.*\m(19|20)[0-9]{2}\M'
                  )
                ORDER BY p.id
                """,
                (list(PROVENANCE_TYPES),),
            )
            rows = list(cursor.fetchall())

    print(f"Professors with obvious legacy publication-title problems: {len(rows)}")
    queued = 0
    reused = 0
    for row in rows:
        job = enqueue_radar_job(
            "MATCH_FACULTY_PUBLICATIONS",
            professor_id=int(row["id"]),
            priority=95,
            max_attempts=8,
        )
        if job.get("reused"):
            reused += 1
        else:
            queued += 1
        print(row["id"], row["name"], job["id"], job.get("status"))

    print(f"New cleanup jobs queued: {queued}")
    print(f"Existing active jobs reused: {reused}")


if __name__ == "__main__":
    main()
