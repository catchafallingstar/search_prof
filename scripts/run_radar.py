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
        help="Maximum professor prospects to return.",
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
    result = execute_radar(
        args.research_area,
        target_professors=target_professors,
        progress_callback=show,
    )
    run = result["run"]
    print(
        {
            "run_id": run["id"],
            "status": run["status"],
            "cached": result["cached"],
            "professors_found": run["professors_found"],
            "papers_found": run["papers_found"],
            "professors_checked": run["professors_checked"],
            "grants_added": run["grants_added"],
            "signals_added": run["signals_added"],
        }
    )


if __name__ == "__main__":
    main()
