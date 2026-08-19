import argparse

from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.radar_pipeline import execute_radar
from ingestion.taxonomy import normalize_taxonomy


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ScholarRadar discovery pipeline.")
    parser.add_argument("research_area", help="Research area, such as robotics")
    parser.add_argument(
        "--professors",
        type=int,
        choices=[10, 25, 50, 100],
        default=25,
        help="Verified-faculty goal for this search.",
    )
    parser.add_argument(
        "--passes",
        type=int,
        choices=range(1, 21),
        default=1,
        metavar="1-20",
        help=(
            "Run multiple bounded continuation passes. Useful for building a "
            "50- or 100-person cache without manually repeating the command."
        ),
    )
    parser.add_argument("--max-papers", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--skip-web-signals",
        action="store_true",
        help="Only test topic, paper, professor, and grant discovery.",
    )
    args = parser.parse_args()
    requested_count = args.max_papers or args.professors

    if args.skip_web_signals:
        taxonomy = normalize_taxonomy(args.research_area)
        print(f"Taxonomy: {taxonomy['topic_name']} / {taxonomy['field_name']}")
        discovery = fetch_professors_by_keywords(
            taxonomy, target_professors=requested_count
        )
        print(discovery)
        print(check_and_save_grants(taxonomy, professor_ids=discovery["professor_ids"][:20]))
        return

    def show(stage: str, percent: int, counters: dict[str, int]) -> None:
        details = " ".join(f"{key}={value}" for key, value in counters.items() if value)
        print(f"[{percent:3d}%] {stage}{' - ' + details if details else ''}")

    allowed_counts = [10, 25, 50, 100]
    target_professors = next(
        (count for count in allowed_counts if count >= requested_count), 100
    )
    previous_progress: tuple[int, int] | None = None
    for pass_number in range(1, args.passes + 1):
        if args.passes > 1:
            print(f"\n=== bounded pass {pass_number}/{args.passes} ===")
        result = execute_radar(
            args.research_area,
            target_professors=target_professors,
            progress_callback=show,
            continue_partial=True,
        )
        run = result["run"]
        summary = {
            "run_id": run["id"],
            "status": run["status"],
            "cached": result["cached"],
            "verified_goal": target_professors,
            "professors_found": run["professors_found"],
            "candidates_ranked": run["candidates_ranked"],
            "faculty_identities_checked": run["faculty_identities_checked"],
            "papers_found": run["papers_found"],
            "professors_checked": run["professors_checked"],
            "grants_added": run["grants_added"],
            "signals_added": run["signals_added"],
        }
        print(summary)
        progress_state = (
            int(run["professors_found"] or 0),
            int(run["faculty_identities_checked"] or 0),
        )
        if (
            progress_state[0] >= target_professors
            or progress_state[1] >= int(run["candidates_ranked"] or 0)
        ):
            break
        if previous_progress == progress_state:
            print("No additional candidates were checked; stopping continuation loop.")
            break
        previous_progress = progress_state


if __name__ == "__main__":
    main()
