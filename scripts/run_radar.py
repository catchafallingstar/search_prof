import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import TextIO

from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.radar_pipeline import execute_radar
from ingestion.taxonomy import normalize_taxonomy


class _Tee:
    """Write terminal output to the console and a durable run report."""

    def __init__(self, terminal: TextIO, report: TextIO) -> None:
        self.terminal = terminal
        self.report = report

    def write(self, value: str) -> int:
        self.terminal.write(value)
        self.report.write(value)
        self.report.flush()
        return len(value)

    def flush(self) -> None:
        self.terminal.flush()
        self.report.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.terminal, "isatty", lambda: False)())


def _default_log_path(research_area: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", research_area.casefold()).strip("-") or "research"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("reports") / f"radar-run-{timestamp}-{slug}.md"


def _enable_run_report(research_area: str, requested_path: str | None) -> Path:
    path = Path(requested_path).expanduser() if requested_path else _default_log_path(research_area)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.stdout, report)
    sys.stderr = _Tee(sys.stderr, report)
    print(f"Radar process and results report: {path.resolve()}")
    return path


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _print_detailed_results(result: dict, summary: dict) -> None:
    """Append a human-readable evidence report after the live process log."""
    details = dict(result.get("report_details") or {})
    print("\n# ScholarRadar run report")
    print(f"\nResearch area: {_one_line(details.get('topic'))}")
    print(f"Run ID: {summary['run_id']} · Status: {summary['status']}")

    professors = list(result.get("professors") or [])
    prospects = {
        int(item["professor_id"]): item
        for item in details.get("prospects") or []
    }
    grant_checks = {
        int(item["professor_id"]): item
        for item in details.get("grant_checks") or []
    }
    hiring_checks = {
        int(item["professor_id"]): item
        for item in details.get("hiring_checks") or []
    }

    print(f"\n## Verified professors ({len(professors)})")
    if not professors:
        print(
            "\nNo professor passed identity verification in this bounded pass. "
            "Grant and hiring checks therefore had nobody to inspect."
        )
    for index, professor in enumerate(professors, start=1):
        professor_id = int(professor["professor_id"])
        name = _one_line(professor.get("professor_name"))
        title = _one_line(professor.get("faculty_title")) or "Faculty title not stated"
        institution = _one_line(professor.get("institution_name"))
        print(f"\n### {index}. {name}")
        print(f"\n- Faculty role: {title} · {institution}")
        if professor.get("faculty_source_url"):
            print(f"- Faculty source: {professor['faculty_source_url']}")

        print("- Supporting papers:")
        supporting = list(prospects.get(professor_id, {}).get("supporting_papers") or [])
        if supporting:
            for paper in supporting:
                year = f" ({paper['publication_year']})" if paper.get("publication_year") else ""
                url = f" — {paper['url']}" if paper.get("url") else ""
                print(f"  - {_one_line(paper.get('title'))}{year}{url}")
        elif professor.get("latest_paper_title"):
            year = f" ({professor['latest_paper_year']})" if professor.get("latest_paper_year") else ""
            print(f"  - {_one_line(professor['latest_paper_title'])}{year}")
        else:
            print("  - No exact supporting-paper title was available in this run report.")

        grant = grant_checks.get(professor_id)
        print("- Grant check:")
        if not grant:
            print("  - Not run in this pass.")
        elif grant.get("status") == "SOURCE_UNAVAILABLE":
            print(f"  - Grant sources unavailable: {_one_line(grant.get('error'))}")
        elif grant.get("grants"):
            for award in grant["grants"]:
                amount = f" · ${float(award['amount']):,.0f}" if award.get("amount") else ""
                expires = f" · expires {award['expiration_date']}" if award.get("expiration_date") else ""
                source = f" · {award['source_url']}" if award.get("source_url") else ""
                print(f"  - {_one_line(award.get('title'))}{amount}{expires}{source}")
        else:
            sources = ", ".join(grant.get("sources_checked") or ["NSF", "NIH RePORTER", "ORCID"])
            print(f"  - Checked {sources}; no active topic-compatible award found.")

        hiring = hiring_checks.get(professor_id)
        print("- Hiring-signal check:")
        if not hiring:
            print("  - Not completed in this pass.")
        else:
            print(f"  - Result: {hiring.get('check_status', 'UNKNOWN')}")
            for source in hiring.get("sources_checked") or []:
                label = _one_line(source.get("source_type")).replace("_", " ")
                source_url = source.get("url") or "no page URL"
                print(f"  - {label}: {source.get('result', 'UNKNOWN')} · {source_url}")
            signal = hiring.get("signal")
            if signal:
                print(f"  - Evidence: “{_one_line(signal.get('quote'))}”")
                print(f"  - Source: {signal.get('source_url')}")

    print("\n## Summary")
    print(f"\n- Candidate papers found: {summary['papers_found']}")
    print(f"- Candidates ranked: {summary['candidates_ranked']}")
    print(f"- Faculty identities checked: {summary['faculty_identities_checked']}")
    print(f"- Verified professors found: {summary['professors_found']} / {summary['verified_goal']}")
    print(f"- Verified professors with completed hiring checks: {summary['hiring_profiles_checked']}")
    print(f"- New matching grants saved: {summary['grants_added']}")
    print(f"- New hiring signals saved: {summary['signals_added']}")


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
    parser.add_argument(
        "--log-file",
        help=(
            "Write the complete stage output and any traceback to this file. "
            "Defaults to reports/radar-run-<timestamp>-<research-area>.log."
        ),
    )
    args = parser.parse_args()
    _enable_run_report(args.research_area, args.log_file)
    requested_count = args.max_papers or args.professors

    if args.skip_web_signals:
        taxonomy = normalize_taxonomy(args.research_area)
        print(f"Taxonomy: {taxonomy['topic_name']} / {taxonomy['field_name']}")
        discovery = fetch_professors_by_keywords(
            taxonomy, target_professors=requested_count
        )
        print(discovery)
        print(check_and_save_grants(taxonomy, professor_ids=discovery["professor_ids"]))
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
            "hiring_profiles_checked": run["professors_checked"],
            "grants_added": run["grants_added"],
            "signals_added": run["signals_added"],
        }
        _print_detailed_results(result, summary)
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
