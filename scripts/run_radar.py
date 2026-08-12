import argparse

from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ScholarRadar discovery pipeline.")
    parser.add_argument("research_area", help="Research area, such as robotics")
    parser.add_argument("--max-papers", type=int, default=100)
    parser.add_argument("--skip-web-signals", action="store_true")
    args = parser.parse_args()

    taxonomy = normalize_taxonomy(args.research_area)
    print(f"Taxonomy: {taxonomy['topic_name']} / {taxonomy['field_name']}")
    print(fetch_professors_by_keywords(taxonomy, max_papers=args.max_papers))
    print(check_and_save_grants(taxonomy))
    if not args.skip_web_signals:
        print(scan_hiring_signals(domain_name=taxonomy["topic_name"]))


if __name__ == "__main__":
    main()

