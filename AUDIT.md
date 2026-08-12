# Complete audit of the original ScholarRadar project

> Historical reference: the findings below describe the originally submitted files, not the current refactored MVP in this folder. The replacement decisions were implemented here. Current architecture and remaining MVP limits are documented in `docs/ARCHITECTURE.md`.

This audit distinguishes a coding error from a design limitation. It also records which files should remain in the refactored project.

## File decisions

| Original file | Decision | Reason |
|---|---|---|
| `app.py` | Replace | It mixes public UI, ingestion, session isolation, database migration, cleanup, logging, and data administration in one rerun script. |
| `db.py` | Replace | Connection behavior is inconsistent and schema mutation happens during page rendering. |
| `db.sql` | Replace | It does not describe the schema expected by the Python code and has no marketplace or verification tables. |
| `ingestion/fetch_prof.py` | Keep and fix | OpenAlex discovery is useful, but the original cannot complete because of an undefined variable and schema drift. |
| `ingestion/check_grants.py` | Keep and fix | Funding evidence is useful, but it must be provider-specific and must not imply an opening. |
| `ingestion/parse_hiring_signals.py` | Keep and fix | This is a core feature, but attribution, freshness, networking, and execution need correction. |
| `ingestion/socialradar.py` | Keep and fix | Social discovery is useful as unverified evidence only. |
| `ingestion/homepagefinder.py` | Keep and fix | Homepage discovery is a separate responsibility and is not redundant with social search. |
| `ingestion/matchers.py` | Keep and fix | Centralized matching and deduplication are useful. |
| `ingestion/taxonomy.py` | Keep and fix | Normalization is useful, but unsupported fields must not silently become engineering/NSF. |
| `ingestion/i18n.py` | Optional; remove from English-only MVP | Keep it only if bilingual UI is a current requirement. The original has duplicate dictionary keys. |
| `ins-db.py` | Remove from project root | It is a debugging command, not application code. Its useful behavior belongs in `scripts/inspect_signals.py`. |
| `test.py` | Move/rewrite under `tests/` | It tests a mock implementation rather than the real pipeline. |
| `test_sig.py` | Move/rewrite or remove | It references an old function name and tests signatures instead of behavior. |

## `app.py`

1. `pandas` is imported but never used directly.
2. `normalize_taxonomy` is imported twice.
3. `scan_hiring_signals` is imported but never called, so the advertised third step does not happen.
4. `st.form_submit_button(..., key=...)` is incompatible with the pinned Streamlit 1.15.2.
5. `st.status` is incompatible with Streamlit 1.15.2.
6. `st.popover` is incompatible with Streamlit 1.15.2.
7. The global `sys.stdout` replacement is application-wide mutable state. UI containers can remain in memory after sessions disappear.
8. Background threads write into Streamlit UI objects, which makes correctness dependent on internal Streamlit execution context.
9. A random session UUID is used as ownership even though it is not authentication and is not stable identity.
10. Every visitor builds a private copy of radar data, so the site cannot be a shared directory.
11. Ingestion runs synchronously inside the web page and can block a session for minutes.
12. The stop button cannot reliably interrupt an active HTTP request or already-submitted worker.
13. Schema migration and cleanup execute during page rendering.
14. The migration connection returned by `get_db_connection()` is not explicitly closed in `app.py`.
15. `st.stop()` on an empty radar DataFrame prevents later homepage, authentication, or posting UI from rendering.
16. “Funded signals” are counted by searching words in hiring text instead of querying the `fundings` table.
17. Cards are grouped only by professor name, so two people with the same name at different institutions are merged.
18. “Extremely High Chance” presents an evidence score like an admission or hiring probability.
19. Grant evidence and explicit recruiting evidence are combined into one score without showing distinct semantics.
20. There is no pagination, so a large result set renders every card on every rerun.
21. Clear-data behavior is destructive and belongs in an authenticated administration interface.
22. The clear-data warning says it removes four data types, while the SQL explicitly deletes only two tables and relies on cascades for the rest.
23. Public URLs are rendered without application-level scheme validation.

## `db.py`

1. `from builtins import Exception` is unnecessary.
2. `DATABASE_URL` is read once at import time, while the SQLAlchemy function reads it again later; configuration behavior can differ between connection methods.
3. The project uses both psycopg2 and SQLAlchemy for the same database without a clear reason.
4. SSL behavior is inferred from `DB_HOST` even when a full `DATABASE_URL` is used.
5. Connection and cursor lifetime is manually managed and several error paths can leak resources.
6. Runtime DDL is performed whenever the Streamlit script reruns.
7. Unique constraints are dropped and recreated repeatedly, taking unnecessary table locks.
8. `professors.openalex_id` is referenced by migrations but never created.
9. The migration deletes global records older than two hours, which is incompatible with profiles or advertisements.
10. Migration errors are printed and swallowed, allowing the app to continue against an unknown schema.
11. There is no transaction boundary shared by related marketplace writes because no marketplace model exists.

## `db.sql`

1. `professors.openalex_id` is missing.
2. `professors.session_id`, `papers.session_id`, and `professors.score_breakdown` are missing even though runtime code expects them.
3. `hiring_signals.raw_text_hash` is missing even though inserts use it for conflict handling.
4. `fundings.funding_hash` is missing from the source schema and is created inside an ingestion function.
5. The source schema and runtime-migrated schema are therefore different products.
6. There are no authenticated users, institutions, claimed profiles, role-verification requests, opportunities, sponsors, reports, or moderation records.
7. `updated_at` has a default but is not updated automatically.
8. Confidence is stored as arbitrary text without a constraint.
9. A funding row has no expiration date or source URL, so active funding cannot be distinguished from old evidence.
10. Hiring signals have no observed, last-checked, or expiration lifecycle.

## `ingestion/fetch_prof.py`

1. `raw_domain` is undefined; the first eligible record raises `NameError`.
2. The code later uses `professors.openalex_id`, which is absent from `db.sql` and its migration.
3. It assumes an OpenAlex author embedded in a work contains `homepage_url`; this field is generally not dependable in that object.
4. It stores ORCID as a homepage, even though an ORCID page is not a laboratory recruiting page.
5. When no corresponding author is found, it assumes the final author is a professor/PI.
6. It selects the first institution rather than an eligible US educational affiliation.
7. Names, institutions, domains, titles, and venues are truncated far below the database column capacity.
8. One database error can roll back the whole batch and resource closure is not protected by context managers.
9. The OpenAlex request does not supply an optional contact email.
10. Data is duplicated per anonymous session instead of upserted into a shared professor graph.

## `ingestion/check_grants.py`

1. It sends NIH, DoD, USDA, NEH, and other program names to the NSF API.
2. It labels every returned early-career-program keyword result as a new assistant professor without validating career stage.
3. It executes DDL inside the ingestion function.
4. Professor-name matching is a loose keyword query.
5. Institution verification uses one directional substring comparison and fails on common abbreviations.
6. It does not check the grant expiration date before increasing the score.
7. It inserts a fabricated `01/01/2024` award date when the API does not supply a date.
8. Amount parsing catches `ValueError` but not `TypeError`.
9. New-professor insertion has no conflict handling.
10. Funding is treated as hiring evidence even though it does not confirm recruitment.
11. The same function performs discovery, enrichment, schema migration, scoring, and reporting.

## `ingestion/parse_hiring_signals.py`

1. The function is never called from the original app.
2. `breakdown_str` is assigned twice consecutively.
3. Network exceptions are broadly swallowed, hiding operational failures.
4. Discovered URLs are fetched without blocking localhost, private IPs, or unsafe redirects; this creates server-side request-forgery risk.
5. Content type is not checked before HTML parsing.
6. Signals have no expiration or scheduled recheck.
7. A thread-local session plus internal Streamlit context is required only because ingestion is incorrectly running in the UI process.
8. The stop callback does not cancel requests already running; leaving the executor context waits for running work.
9. The score combines funding, new-faculty wording, and recruiting language as if it were a calibrated probability.
10. All errors become console text rather than structured outcomes suitable for monitoring.

## `ingestion/socialradar.py`

1. The Bluesky comment says author metadata is checked, but the original implementation does not match the author to the target professor.
2. A first-person snippet such as “my lab” is accepted without proving whose lab it is.
3. Search-result snippets can therefore be attributed to the wrong professor.
4. Bluesky results have no age cutoff.
5. The institution variable is calculated but not used to verify Bluesky identity.
6. Broad exception swallowing makes rate limits, timeouts, parsing changes, and programming errors indistinguishable.
7. Search-engine snippets are treated as evidence without an explicit refresh lifecycle.

## `ingestion/homepagefinder.py`

1. URL validation uses a blocklist only and does not restrict schemes or private-network targets.
2. The Google Scholar fallback is returned as a homepage, but the next stage explicitly refuses to scan Google Scholar; the fallback cannot produce a signal.
3. Random websites can pass validation even when they are unrelated to the professor.
4. Search failures are silently ignored.
5. The `backend="api"` dependency on a particular DDGS implementation is unnecessarily brittle.

## `ingestion/matchers.py`

1. `re` is imported twice.
2. `datetime` is imported but unused.
3. Stale years are hard-coded only through 2023, so the definition becomes wrong each year.
4. Searching for a generic `RA` token is prone to false positives.
5. Role results are converted through a set, which makes their display order unstable.
6. When no hiring sentence is found, the function can return a shortened unrelated snippet instead of rejecting it.
7. Negative patterns omit common phrases such as “no openings” and “position filled.”

## `ingestion/taxonomy.py`

1. Every failed or unknown lookup defaults to Engineering and an NSF/DoD strategy.
2. Field matching uses substring comparisons, which can route ambiguous names incorrectly.
3. The router claims six multi-agency strategies, but only an NSF connector exists.
4. The OpenAlex request has no optional contact email.
5. API failures are printed and silently converted into a potentially wrong scientific classification.

## `ingestion/i18n.py`

1. The English dictionary defines `no_homepage` twice; Python keeps only the later value.
2. The Chinese dictionary has the same duplicate-key problem.
3. The English OpenAlex start message hard-codes `2024-2026`, while the crawler calculates a dynamic year range.
4. Several UI messages still describe scores as probabilities rather than evidence.
5. Language identifiers mix `cn`, `zh`, a Chinese display string, and English display strings.
6. The module also stores session identity, even though localization and user identity are separate concerns.

## `ins-db.py`

1. This is an inspection script, not application code, and should not sit beside `app.py`.
2. It uses manual connection cleanup without a `finally` block.
3. It prints raw evidence that may eventually contain personal data.
4. Its functionality is retained as `scripts/inspect_signals.py` with the new schema.

## Tests

### `test.py`

1. It tests a locally recreated mock architecture, not the imported production code.
2. Passing the test therefore does not demonstrate that the Streamlit application or database isolation works.
3. It writes to process-global `sys.stdout`, which can affect other tests.

### `test_sig.py`

1. It references `check_and_save_nsf_grants`, but the implementation is named `check_and_save_grants`.
2. It fails during import when undeclared direct dependencies are unavailable.
3. Signature-only tests do not verify database writes, attribution, freshness, matching, cancellation, or moderation behavior.

## `requirements.txt`

1. Streamlit 1.15.2 is incompatible with multiple APIs used by the app.
2. Core direct dependencies such as SQLAlchemy, Beautiful Soup, DDGS, requests, and pandas are not all declared explicitly.
3. The file includes many unrelated packages for notebooks, PDFs, Qt, grading, Docker, Google APIs, and other projects.
4. Exact pins across that unrelated dependency set make security updates and Python-version compatibility harder.
5. The corrected MVP declares only direct runtime dependencies and constrains their major versions.
