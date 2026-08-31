# Verification repairs: August 31, 2026

These repairs address bugs exposed by the 30-person report, not a promise that
every unresolved candidate is faculty. Verification version is now 11. Existing
monthly identity scheduling, search quotas and faculty/student safeguards remain.

## What changed

- Decode HTML bytes before matching accented names. Normalize compatibility
  characters and degree suffixes. Extra leading names need corroboration.
- Recognize multiple first-person roles while retaining adviser, student and
  historical-role exclusions. Tie a role to its own institution.
- A `/faculty/` path cannot override a fetched profile identifying another name.
- Use an exact-name university/domain seed catalog when a stored domain is
  missing. This is a small locator catalog, not a universal faculty database.
  Existing institution domains and page evidence still matter.
- Use new academic-domain clues from a read CV in the next query. Never turn
  Gmail or another consumer email address into a university `site:` query.
- Keep split-name search excerpts as retrieval leads; they do not verify roles.
- Preserve relevant target-directory results even when the snippet omits the
  name; inspect them only as bounded locators.
- Do not crawl salary sites, DBLP navigation, unclaimed researcher aggregators,
  social login pages or publication repositories as personal faculty profiles.
- Prefer embedded PDF links; never concatenate PDF lines into guessed URLs.
- Distinguish removed profiles, blocked pages and JavaScript/empty pages. A
  CAPTCHA widget in an otherwise readable profile is not a blocked response.
- Reuse previously discovered profile URLs; read known profiles before PDF
  fallback when the university is already known.
- Check an attributed homepage before trusting a subordinate student biography
  when the homepage explicitly records a completed doctorate.
- Save recognized overseas campuses as separate campus institutions, retaining
  faculty status without classifying a Dubai appointment as US. Filter recognized
  overseas source URLs before public SQL counts/pagination, including old records.

## Files

- `ingestion/verify_faculty.py`: fetch, parse, attribute, query, and save decisions.
- `ingestion/identity_sources.py`: source screening, link extraction and following.
- `ingestion/institution_domains.py`: exact institution locators and known overseas
  campus URL namespaces. Add reviewed full-name aliases/domains here when needed.
- `ingestion/websearch.py`: retrieval-only name normalization and directory leads.
- `ingestion/verification_audit.py`: retain failure, encoding and scope details.
- `radar_store.py`: version consistency and public overseas-campus exclusion.
- `tests/test_verification_repairs_v2.py`: offline regression examples derived from
  the report. Fixtures are representative snippets, not fresh/live site copies.
- `tests/test_faculty_verification.py`: removes an unsafe automatic extra-first-name
  match assumption; keeps student, guest-speaker and name-conflict tests.

## Testing completed

250 related tests passed with outbound socket/DNS access blocked. These include
34 new regressions plus existing identity, worker, affiliation, quota and provider
tests. This validates the coded cases, not the live success rate of 30 people.
Test output deliberately includes mocked quota errors and malformed-PDF warnings.

No search API calls, identity rewrites, reindexing jobs or environment-key changes
were performed as part of installing this repair. No dependency or schema migration
is needed. Restart any already-running Streamlit/worker processes to load the code.

## Repeat the same 30-person diagnostic

In Ubuntu:

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.sample_faculty_verification \
  --limit 30 \
  --max-searches-per-candidate 10 \
  --replay-report reports/verification-sample-20260831T044049Z/results.jsonl
```

This reads your configured `.env` provider order and exits after the selected
candidates finish or receive a bounded outcome. It creates a new timestamped report;
it does not overwrite the earlier report or publish/save faculty identity decisions.
Search caches, provider counters and quota checks may be updated. It can send up to
300 logical queries; provider fallback can affect actual outgoing request totals.
Use `--limit 5` first if conserving credits. Existing cached decisions in the public
database do not become new decisions simply because this diagnostic was run.

After reviewing that comparison, use a separate randomized sample to test people
not represented in these fixtures. Do not call a bigger verified count a success
unless the sources actually support the identities and US appointments.

## Remaining limits

- Real access challenges, inaccessible PDFs and missing/JavaScript-only profiles
  can still leave a candidate unresolved. No CAPTCHA bypass or browser-rendering
  service was added.
- LangSearch may not return the profile even when a manual Google search does.
  More permissive role rules are not a safe solution to missing retrieval.
- Domain and overseas-campus catalogs are deliberately limited. They do not
  establish worldwide coverage or guarantee every institution/campus mapping.
- Source freshness cannot be guaranteed. Dates and conflicting evidence remain
  important, especially on personal pages and old university pages.
- Nothing automatically rechecks thousands of recently checked candidates or
  overrides staff conflicts. The existing schedule remains; the diagnostic can
  test the repairs immediately without changing stored decisions.
