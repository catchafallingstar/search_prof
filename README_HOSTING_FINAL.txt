ScholarRadar final pre-hosting hardening patch — 2026-09-20

WHAT THIS FIXES
1. Publication-list parser now prefers linked paper-title text instead of storing
   the entire bibliography citation as the paper title.
2. Broken HTML such as href="papers/mascots03.pdf">Real title is recovered as
   the real title. File paths/URLs are never persisted as paper titles.
3. When a professor publication page is refreshed, obvious legacy file-path and
   whole-citation rows from provenance-only sources are detached and orphan rows
   are removed conservatively.
4. PUBLICATION_DISCOVERY_VERSION is now 15.
5. Paper CLASSIFICATION_VERSION is now 2. Existing papers are gradually
   reclassified by normal maintenance because classification_version < 2.
6. Broad single-word seeded fields such as Education can no longer be
   AUTO_ACCEPTED solely because that generic word appears in a paper. Such cases
   can require review instead of creating false professor expertise.
7. Recent Activity now separates a paper's accepted category from the linked
   professor's durable research profile. It no longer displays the professor's
   area as though it were the classification result of that individual paper.
8. Verified professor publication matching is refreshed every 30 days online,
   rather than remaining indefinitely unchanged after the first successful run.
9. Research-profile version 2 fixes from the previous package remain included.

VALIDATION IN THIS PACKAGE
Focused regression suite: 96 passed.
This includes the earlier 93 tests plus regressions for:
- linked Kurmas-style full citations -> clean title
- malformed literal href/PDF line -> clean title
- incidental "education" -> not automatically accepted as Education research

APPLY
1. Stop the local worker/server (Ctrl+C if make start is running).
2. Replace the files from this ZIP using the same relative paths.
3. Run:

   make schema

4. Syntax check:

   .venv/bin/python -m py_compile \
     ingestion/publication_discovery.py \
     ingestion/research_classification.py \
     ingestion/ollama_evidence.py \
     ingestion/roster_topic_index.py \
     radar_store.py \
     pages/5_Radar_control.py

5. Run focused tests:

   .venv/bin/python -m pytest -q \
     tests/test_faculty_publications.py \
     tests/test_research_profile_fallback.py \
     tests/test_ollama_evidence.py \
     tests/test_scholar_workflow.py \
     tests/test_search_scheduling.py \
     tests/test_research_classification.py

   Expected: 96 passed

6. Queue only professors who currently have obvious legacy malformed publication
   titles. This script is idempotent:

   .venv/bin/python scripts/queue_publication_cleanup.py

7. Start normal operation:

   make start

WHAT HAPPENS AFTER START
- Targeted malformed-title cleanup jobs run at priority 95.
- Normal faculty directory crawls continue at priority 90.
- Publication refreshes run automatically when due, including every 30 days.
- Existing classification_version < 2 papers are reclassified gradually in
  bounded maintenance batches.
- The queue does not need to reach zero. make start is a continuous service.

QUICK DATABASE CHECK AFTER THE TARGETED CLEANUP JOBS FINISH

.venv/bin/python - <<'PY'
from db import get_db_connection
with get_db_connection() as connection:
    with connection.cursor() as cursor:
        cursor.execute(r"""
            SELECT COUNT(*) AS n
            FROM professor_papers link
            JOIN papers paper ON paper.id=link.paper_id
            WHERE paper.source_type IN (
                'OFFICIAL_PROFILE','OFFICIAL_ALTERNATE_PROFILE','PERSONAL_SITE',
                'LAB_SITE','INSTITUTIONAL_RESEARCH_PORTAL'
            )
              AND (
                paper.title ~* '^(https?://\S+|([^/[:space:]]+/)+[^/[:space:]]+\.(pdf|docx?|pptx?))$'
                OR paper.title ~* '^[A-Z]\.\s+[^.]{1,220}\.\s+.+\m(Proceedings|Conference|Workshop|Journal)\M.*\m(19|20)[0-9]{2}\M'
              )
        """)
        print("Obvious malformed linked publication titles remaining:", cursor.fetchone()["n"])
PY

Ideally this moves toward 0 as targeted refreshes succeed.
