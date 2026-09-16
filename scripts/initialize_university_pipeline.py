"""Queue the next university-first discovery batch."""
from __future__ import annotations

import argparse
import json

from radar_store import enqueue_due_maintenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    queued = enqueue_due_maintenance(limit=max(1, min(100, args.limit)))
    print(json.dumps({"jobs_queued": queued, "worker_command": "make worker"}, indent=2))


if __name__ == "__main__":
    main()
