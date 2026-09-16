from __future__ import annotations

import argparse

from ingestion.roster_topic_index import index_rostered_topic
from radar_store import ensure_radar_topic


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one topic from confirmed roster faculty.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    topic = ensure_radar_topic(args.query, args.limit, enforce_hourly_limit=False)
    print(index_rostered_topic(int(topic["id"])))


if __name__ == "__main__":
    main()
