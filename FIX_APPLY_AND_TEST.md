# ScholarRadar pipeline fixes — 2026-09-22

This branch contains the corrected files:

- ingestion/research_classification.py
- ingestion/parse_hiring_signals.py
- tests/test_pipeline_full.py

## Apply to local repo

From /home/mel/search_prof after extracting this branch ZIP:

```bash
cp /path/to/extracted/search_prof-chatgpt-pipeline-fixes-20260922/ingestion/research_classification.py ingestion/
cp /path/to/extracted/search_prof-chatgpt-pipeline-fixes-20260922/ingestion/parse_hiring_signals.py ingestion/
cp /path/to/extracted/search_prof-chatgpt-pipeline-fixes-20260922/tests/test_pipeline_full.py tests/
```

## Run deterministic regression tests

```bash
.venv/bin/python -m pytest -vv -s tests/test_pipeline_full.py -k "stage12 or stage15 or stage99"
```

## Run current official-page regression tests

```bash
RUN_LIVE_PIPELINE_TESTS=1 \
.venv/bin/python -m pytest -vv -s tests/test_pipeline_full.py -k stage19
```

## Run all deterministic tests

```bash
.venv/bin/python -m pytest -vv -s tests/test_pipeline_full.py
```

## Sync classification v4 and reprocess

```bash
.venv/bin/python - <<'PY'
from ingestion.research_classification import sync_research_categories
result = sync_research_categories()
print("Categories synced:", len(result))
PY

make backfill-classification
make rebuild-topics
```

## Check stale roster review versions

```bash
docker exec -i scholarradar-local-postgres-1 \
  psql -U scholarradar_app -d scholarradar_pipeline_test -c "
SELECT validation_version, validation_status, validation_reason, COUNT(*) AS count
FROM roster_member_candidates
WHERE validation_status NOT IN (
    'PENDING','PROFILE_VERIFIED','ROSTER_VERIFIED','REJECTED','NOT_A_PERSON',
    'HISTORICAL_PROFILE','NOT_GROUP_LEADING_FACULTY',
    'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE'
)
GROUP BY validation_version, validation_status, validation_reason
ORDER BY validation_version, count DESC;
"
```

Current roster validation writer is v4. Versions <4 are stale candidates for reprocessing.

## Requeue pre-v4 unresolved roster rows

Back up first:

```bash
make backup
```

Then:

```bash
set -a
source .env
set +a

.venv/bin/python - <<'PY'
from db import get_db_connection
from radar_store import enqueue_radar_job

CURRENT_ROSTER_VERSION = 4
directory_ids = set()

with get_db_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT directory_id
            FROM roster_member_candidates
            WHERE validation_version < %s
              AND validation_status NOT IN (
                  'PENDING','PROFILE_VERIFIED','ROSTER_VERIFIED','REJECTED',
                  'NOT_A_PERSON','HISTORICAL_PROFILE',
                  'NOT_GROUP_LEADING_FACULTY',
                  'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE'
              )
        """, (CURRENT_ROSTER_VERSION,))
        directory_ids = {int(row["directory_id"]) for row in cur.fetchall()}

        cur.execute("""
            UPDATE roster_member_candidates
            SET validation_status='PENDING',
                validation_reason='REQUEUED_AFTER_ROSTER_VALIDATOR_V4',
                checked_at=NULL
            WHERE validation_version < %s
              AND validation_status NOT IN (
                  'PENDING','PROFILE_VERIFIED','ROSTER_VERIFIED','REJECTED',
                  'NOT_A_PERSON','HISTORICAL_PROFILE',
                  'NOT_GROUP_LEADING_FACULTY',
                  'ROSTER_CONFIRMED_PROFILE_UNAVAILABLE'
              )
        """, (CURRENT_ROSTER_VERSION,))
        print("Historical unresolved rows reset:", cur.rowcount)

        if directory_ids:
            cur.execute("""
                UPDATE faculty_directories
                SET last_success_at=NULL, updated_at=NOW()
                WHERE id = ANY(%s)
            """, (list(directory_ids),))

for directory_id in sorted(directory_ids):
    enqueue_radar_job(
        "CRAWL_FACULTY_DIRECTORY",
        faculty_directory_id=directory_id,
        priority=95,
        max_attempts=5,
    )

print("Directories queued:", len(directory_ids))
PY
```

Run the worker:

```bash
make worker
```

## Final DB diagnostic

```bash
RUN_DB_PIPELINE_DIAGNOSTICS=1 \
.venv/bin/python -m pytest -vv -s tests/test_pipeline_full.py -k stage18
```

## All-in-one verification

```bash
RUN_LIVE_PIPELINE_TESTS=1 \
RUN_DB_PIPELINE_DIAGNOSTICS=1 \
.venv/bin/python -m pytest -vv -s tests/test_pipeline_full.py
```
