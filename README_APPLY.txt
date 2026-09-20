ScholarRadar research-profile v2 fix — 2026-09-20
=================================================

What this fixes
---------------
1. Old v1 professor research profiles can no longer block a new v2 rebuild.
2. Old/stale profile rows are excluded from professor-profile search until a
   successful v2 replacement is written.
3. `make schema` no longer promotes old automatic EXPLICIT_PROFILE_SECTION,
   QWEN_PAPER_SUMMARY, or QWEN_BIO_SUMMARY rows to the current version.
4. Manual staff-reviewed profiles are trusted and promoted to v2.
5. Maintenance automatically queues MATCH_FACULTY_PUBLICATIONS for verified
   professors whose research_profile_version is older than v2.
6. The explicit-interest parser rejects narrative prose, demographic fragments,
   contact information, addresses, phone numbers, and advising text.
7. Existing paper-research-v4 partial acceptance remains included: valid research
   areas survive even if another proposed area has invalid exact-title support.

Files to replace
----------------
db.sql
radar_store.py
ingestion/publication_discovery.py
ingestion/ollama_evidence.py
ingestion/roster_topic_index.py
tests/test_faculty_publications.py
tests/test_research_profile_fallback.py
tests/test_ollama_evidence.py

Apply
-----
1. Stop `make start` with Ctrl+C if it is running.
2. Replace the files above, preserving their project paths.
3. From the project root run:

   make schema

4. Verify syntax:

   .venv/bin/python -m py_compile \
     ingestion/publication_discovery.py \
     ingestion/ollama_evidence.py \
     ingestion/roster_topic_index.py \
     radar_store.py

5. Run focused tests:

   .venv/bin/python -m pytest -q \
     tests/test_faculty_publications.py \
     tests/test_research_profile_fallback.py \
     tests/test_ollama_evidence.py \
     tests/test_scholar_workflow.py \
     tests/test_search_scheduling.py \
     tests/test_research_classification.py

Expected on the packaged source: 93 focused tests pass.

Fast canary check
-----------------
After `make schema`, old automatic v1 profiles are intentionally stale. Search
will not use them until rebuilt. You do not need to wait for thousands of jobs.
Queue Erica, Anis, and Haluk at high priority and process only three jobs:

.venv/bin/python - <<'PY'
from db import get_db_connection
from radar_store import enqueue_radar_job

names = ["Erica Austin", "Haluk Beyenal", "Anis Allagui"]
with get_db_connection() as connection:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id, name
            FROM professors
            WHERE name = ANY(%s)
            ORDER BY name
        """, (names,))
        professors = cursor.fetchall()

for professor in professors:
    job = enqueue_radar_job(
        "MATCH_FACULTY_PUBLICATIONS",
        professor_id=int(professor["id"]),
        priority=300,
        max_attempts=5,
    )
    print(professor["id"], professor["name"], job["id"])
PY

.venv/bin/python -m scripts.run_worker --max-jobs 3 --poll-seconds 0.25

Expected canary behavior
------------------------
- Erica: old `adolescents` / `adults and families` v1 explicit rows do not block
  rebuilding. If the corrected parser finds no compact explicit list, her stored
  papers can drive QWEN_PAPER_SUMMARY.
- Anis: address/phone/advising text must not appear as research interests.
- Haluk: paper-research-v4 keeps independently valid areas and records warnings
  for bad supporting-title proposals rather than rejecting the whole profile.

Normal operation
----------------
After the canaries look good:

   make start

Maintenance will automatically prioritize stale research-profile rebuilds via
MATCH_FACULTY_PUBLICATIONS. Existing ENRICH_CLASSIFY_PAPER jobs can stay queued.
