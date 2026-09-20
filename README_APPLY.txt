ScholarRadar fast professor research-profile fallback
Built from catchafallingstar/search_prof current main on 2026-09-20.

CHANGED PRODUCTION FILES
- db.sql
- radar_store.py
- pages/5_Radar_control.py
- ingestion/publication_discovery.py
- ingestion/ollama_evidence.py
- ingestion/roster_topic_index.py

CHANGED/NEW TESTS
- tests/test_ollama_evidence.py
- tests/test_scholar_workflow.py
- tests/test_research_profile_fallback.py (new)

NEW RESEARCH-PROFILE ORDER
1. Explicit research interests on verified faculty/personal/lab pages -> save directly.
2. Otherwise verified paper titles -> Qwen professor research profile.
3. Otherwise usable biography -> Qwen biography profile.
4. Otherwise -> MANUAL_REVIEW_REQUIRED.

Notes:
- Unsupported department-only AI suggestions are removed/disabled.
- If a Scholar candidate is still pending and there is no other evidence, the
  profile waits in AWAITING_PUBLICATIONS rather than creating a premature manual case.
- Paper-title summaries use up to 30 representative papers: 20 newest plus up
  to 10 spread across older work.
- Direct paper evidence still outranks professor-profile evidence in topic search.
- Professor profiles may now backstop topic search even when a professor already
  has papers, so weak per-paper categorization no longer blocks discovery.

EXISTING DATA MIGRATION
- Existing EXPLICIT_PROFILE_SECTION / legacy QWEN_VALIDATED_SECTION rows become
  OFFICIAL_INTERESTS profiles.
- Existing QWEN_PAPER_SUMMARY rows become PAPER_DERIVED profiles.
- Existing QWEN_BIO_SUMMARY rows become BIOGRAPHY_DERIVED profiles.
- Existing AI_SUGGESTION rows are deleted because they were unsupported
  department-only guesses.
- Publication discovery version is bumped to 14. Verified professors with no
  papers are eligible for a new bounded page/profile pass so the new fallback
  logic can run.
- Topic discovery version is bumped to 9 so research-profile-backed matching is
  reflected in rebuilt topic indexes.

APPLY
1. Replace the files above with these complete files.
2. Apply the idempotent schema:

   make schema

3. Run focused tests:

   .venv/bin/python -m pytest -q \
     tests/test_research_profile_fallback.py \
     tests/test_ollama_evidence.py \
     tests/test_scholar_workflow.py \
     tests/test_search_scheduling.py \
     tests/test_research_classification.py

4. Queue topic rebuilds for the new professor-profile fallback:

   make rebuild-topics

5. Start normally:

   make start

Do not run `make worker` at the same time as `make start` in the normal local setup.
