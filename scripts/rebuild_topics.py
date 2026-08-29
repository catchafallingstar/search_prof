from __future__ import annotations

import argparse

from radar_store import fetch_topic_rebuild_status, queue_outdated_topic_rebuilds


def _print_status() -> None:
    rows = fetch_topic_rebuild_status()
    if not rows:
        print("No research topics have been indexed yet.")
        return
    print(
        "Research area | Version | Current professors | With exact evidence | "
        "Missing | Rebuild"
    )
    for row in rows:
        print(
            f"{row['requested_query']} | {row['discovery_version']} | "
            f"{row['current_professors']} | {row['professors_with_evidence']} | "
            f"{row['missing_professor_evidence']} | {row['rebuild_state']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue or inspect safe versioned research-topic rebuilds."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Only show rebuild and exact-evidence status.",
    )
    parser.add_argument("--limit", type=int, default=250)
    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    result = queue_outdated_topic_rebuilds(limit=max(1, args.limit))
    if not result["outdated_topics"]:
        print("All existing topics already use the current discovery version and evidence model.")
        _print_status()
        return
    print(
        f"Prepared {result['outdated_topics']} topic rebuilds: "
        f"{result['new_jobs']} new, {result['reused_jobs']} already queued/running."
    )
    for job in result["jobs"]:
        action = "reused" if job["reused"] else "created"
        print(
            f"- {job['requested_query']}: job {job['job_id']} {action}; "
            f"status={job['job_status']}, priority={job['priority']}"
        )
    print("Keep the ScholarRadar worker running, then use --status to check completion.")


if __name__ == "__main__":
    main()
