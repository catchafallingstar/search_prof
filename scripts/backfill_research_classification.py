"""Queue existing papers for versioned metadata enrichment and classification."""
from __future__ import annotations

import argparse

from db import get_db_connection
from ingestion.research_classification import CLASSIFICATION_VERSION, sync_research_categories
from radar_store import enqueue_radar_job


def queue_backfill(limit: int | None = None) -> dict[str, int]:
    sync_research_categories()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT paper.id
                   FROM papers paper
                   WHERE paper.classification_version < %s
                     AND NOT EXISTS (
                       SELECT 1 FROM radar_jobs job
                       WHERE job.paper_id=paper.id
                         AND job.job_type='ENRICH_CLASSIFY_PAPER'
                         AND job.status IN ('queued','running')
                     )
                   ORDER BY paper.id
                   LIMIT %s""",
                (CLASSIFICATION_VERSION, limit if limit is not None else 2147483647),
            )
            paper_ids = [int(row["id"]) for row in cursor.fetchall()]
    new_jobs = 0
    for paper_id in paper_ids:
        job = enqueue_radar_job(
            "ENRICH_CLASSIFY_PAPER", paper_id=paper_id,
            priority=45, max_attempts=3,
        )
        new_jobs += int(not job.get("reused"))
    return {"papers_found": len(paper_ids), "jobs_queued": new_jobs}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(queue_backfill(max(1, args.limit) if args.limit else None))


if __name__ == "__main__":
    main()
