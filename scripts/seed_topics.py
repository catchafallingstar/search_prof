from __future__ import annotations

import argparse

from ingestion.research_seeds import RESEARCH_SEED_GROUPS, seed_topic_names
from radar_store import queue_seed_topics


def _print_catalog() -> None:
    for group, topics in RESEARCH_SEED_GROUPS.items():
        print(f"{group} ({len(topics)})")
        for topic in topics:
            print(f"  - {topic}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gradually seed ScholarRadar's controlled research catalog."
    )
    parser.add_argument(
        "--queue", action="store_true", help="Queue a low-priority seed batch."
    )
    parser.add_argument(
        "--group",
        action="append",
        choices=list(RESEARCH_SEED_GROUPS),
        help="Limit the batch to one or more catalog groups.",
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if not args.queue:
        _print_catalog()
        print("Use --queue to create low-priority background jobs.")
        return

    result = queue_seed_topics(
        seed_topic_names(args.group),
        new_job_limit=max(1, args.limit),
        priority=20,
    )
    print(
        f"Catalog: {result['catalog_topics']} topics; examined "
        f"{result['topics_examined']}; queued {result['new_jobs']} new jobs; "
        f"reused {result['reused_jobs']} active jobs."
    )
    for job in result["jobs"]:
        action = "reused" if job["reused"] else "created"
        print(
            f"- {job['requested_query']}: {job['job_type']} job "
            f"{job['job_id']} {action}, priority={job['priority']}"
        )
    print(
        "Seed jobs use low priority. A visitor searching the same topic "
        "automatically promotes its existing job."
    )


if __name__ == "__main__":
    main()
