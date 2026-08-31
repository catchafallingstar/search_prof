# Verification repairs — August 31, 2026

The verifier still requires an attributable faculty role. It does not turn a
name match, a LinkedIn snippet, a research paper, or a blocked faculty URL into
proof of a current appointment.

## What changed

- A successful identity-search response containing unrelated results is a
  query miss, not a provider outage. The verifier tries another query. Genuine
  quota, network and rate-limit failures still use the configured fallback.
- Staff diagnostics retain the provider, result count and up to three example
  rejected results for each retrieval event. Relevant sources remain separate.
- Fetched-page diagnostics include response status, size, title, headings and
  bounded text excerpts. Bot challenges are labelled `SOURCE_BLOCKED` even
  when the server returns HTTP 200. An empty JavaScript shell is not verified.
  The crawler does not bypass challenges or log into restricted sources.
- HTML downloads have an 8 MB limit and share the candidate's time budget.
- CV PDFs can be read (first three pages), including explicit website links.
  A CV is a locator, not proof that a historical employer is current.
- Team sections handle nested containers, quoted nicknames and pronouns.
  Individual-profile role subheadings remain readable. Mentors' and other team
  members' roles must not become the candidate's role.
- A name-bearing link from the matching university directory can connect a
  personal profile to the candidate. Its own role must still be explicit;
  the link alone does not establish faculty status.
- A positive personal/lab-page claim without an official directory link gets
  one additional `"Full Name" faculty profile` query to corroborate the current
  university. A different official university is `CONFLICT`. If corroboration
  is unavailable, the result is `CURRENT_AFFILIATION_UNCONFIRMED`, not a published
  current-university claim. Search snippets alone do not decide a conflict.
- University-domain hints work with subdomains such as `wagner.nyu.edu` and
  spaced Scholar email excerpts. `nyunews.com` is not treated as `nyu.edu`.
- Searches remove middle initials only, not compound family names.
- Fragments such as `#papers` share the existing page fetch; query parameters
  identifying different profiles are preserved. Redirect destinations are reused.
- A saved negative decision's source can be reopened on its next scheduled
  check rather than rediscovered. Existing monthly/recheck scheduling is unchanged.

## Run the same diagnostic again

From `/home/mel/search_prof`:

```bash
source .venv/bin/activate
python -m scripts.sample_faculty_verification --limit 10 \
  --replay-report reports/verification-sample-20260831T014623Z/results.jsonl
```

`--replay-report` selects the same database IDs, even if their due dates have
changed. It still reads the current stored metadata. It does not save identity
decisions, publish profiles, or launch discovery. It uses the providers in
`.env` and their existing quotas/pacing, then exits after the sample. Results go
into a new `reports/verification-sample-...` directory.

The ordinary worker loads Python code at startup. Stop its terminal with Ctrl+C
and restart `python -m scripts.run_worker` when you want it to use these changes.
This installation does not start a bulk worker or reset existing decisions.

## Limits that remain

Search APIs can miss real profiles. University sites can block requests. Personal
and university pages can remain online after someone moves. A completed search
therefore does not guarantee a verified person or an up-to-date appointment.
Unresolved identities remain hidden from public results, with source-specific
diagnostics for staff. No automatic browser challenge bypass was added.
