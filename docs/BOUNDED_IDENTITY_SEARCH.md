# Bounded faculty verification (version 10)

## What a new pass does

1. Reuse fresh saved identity decisions through the existing worker schedule.
2. Read stored affiliations; keep the bounded accessible-paper fallback when
   affiliation evidence needs resolving. Save that affiliation trail in the audit.
3. Check known profile URLs first. Otherwise search name + university. Field,
   official-domain, or supporting-paper queries can resolve the remaining ambiguity.
4. Collect at most 20 distinct search links, within two query calls by default.
   Providers can return fewer. This does not silently paginate or buy 20 queries.
5. Prioritize official university URLs; inspect at most ten result pages by default.
   Team/group pages can now be considered, but a current individual faculty role
   still requires person-specific attribution. News/events cannot prove a current
   faculty appointment. Personal/lab evidence still requires a matching paper.
6. Return VERIFIED, NOT_FACULTY, CONFLICT, or UNVERIFIED. Missing evidence is not a
   student verdict. Snippet-only student/postdoc clues are saved but not promoted
   into confirmed role decisions. This bounded pass does not invoke Gemini.
7. Save the completed decision using the existing refresh schedule. A provider
   outage is deferred separately and does not overwrite the identity decision.

The two-query and ten-page limits are per candidate pass, not a global worker
limit. Known-page, affiliation and related-publication checks have their existing
separate bounded limits. Timing still depends on source availability.

## Staff view

Open Radar control -> Recent automatic identity decisions -> select a person.
The existing identity editor now includes **Latest identity search** and
**Search results and page checks (first 10 links)**. The same evidence appears in
ambiguous identity reviews. It shows query text, the target university, source
links, snippets, unconfirmed student/postdoc hints, and page rejection reasons,
alongside the existing supporting papers. All diagnostics remain staff-only.

The database keeps the latest pass in `professors.identity_search_audit` (JSONB).
An older identity will have no trace until a new permitted check is performed.
This migration does not fabricate historical diagnostics or recheck everyone.
Existing public-version thresholds and staff overrides remain unchanged.

## Configuration

Defaults work without changing your API keys or existing .env:

```dotenv
FACULTY_IDENTITY_PASS_QUERIES=2
FACULTY_IDENTITY_PASS_PAGES=10
```

These supersede the older FACULTY_VERIFY_MAX_QUERIES/MAX_PAGES controls for this
identity pass. Other jobs and provider quotas are unchanged.

## Apply elsewhere

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.migrate_identity_audit
```

Restart a running worker and Streamlit process to load the new code. If the worker
is stopped, start it with `python -m scripts.run_worker`. Do not start an extra
worker merely to reload code. No worker was started by this change.

The diagnostic command `python -m scripts.sample_faculty_verification --limit 30`
now defaults to the same two-query completed pass and stores detailed search_audit
data in its report. It does not save identity decisions. Running it spends provider
requests; it was not rerun as a new paid 30-person sample during this implementation.

## Limits

This does not bypass university HTTP blocks, authentication, or CAPTCHA. A retrieved
page can still lack sufficient current-role evidence. It does not imply every
faculty member appears within the first 20 results. Lecturer/library-staff roles
are not automatically treated as PhD-supervising professors.
