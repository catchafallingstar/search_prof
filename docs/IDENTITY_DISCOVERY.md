# Faculty discovery: LangSearch-first, bounded and visible

## What changed

The verifier still starts from stored OpenAlex authorship metadata. It uses the
existing accessible-paper fallback when affiliation is missing or ambiguous,
then checks saved faculty/homepage URLs before doing a general web search.
This update does not re-extract every topic or erase existing identity decisions.

When a search is needed:

1. Search the person's name and supported university using the configured pool.
2. Classify the returned links. Prioritize university profiles, then useful
   personal/lab pages and CVs. Retain LinkedIn snippets as clues, not confirmed roles.
3. Read promising pages. An individual profile must identify the candidate and
   attribute the role to them. A news article, speaker biography, or directory
   is not itself proof of an appointment.
4. Follow a few useful links: news/directory to a name-bearing profile, personal
   site to About/Team, or a personal GitHub/user-directory CV to its homepage.
   Followed pages must independently pass the identity checks. Personal/lab
   verification still needs university continuity and a matching paper.
5. If unresolved, try the supported university domain (`"Name" site:university.edu`)
   before adding long paper titles. Domain clues come from stored data and returned
   university URLs; we do not invent a university domain. Additional variants can
   use a shorter name, research field, faculty/profile, CV and supporting papers.
6. Stop on a supported faculty/student decision or attributable university
   mismatch. Also stop when query, link, page or time limits are reached.

Finding a matching name in a result is retrieval success, **not identity success**.
The verifier now continues with better queries on the same configured provider
pool when the links have not produced sufficient identity evidence.

### Early exit (save searches)

The 100-link ceiling is never a target to fill. After each promising source is
inspected, stop immediately if it establishes faculty status, an attributable
student/postdoc role, or a university conflict. Do not read the rest of the batch
or send a second query after that decision. This also applies when a personal/lab
page or a link followed from a CV supplies the decisive evidence. Logs and staff
audits explain that the check stopped early.

A name on a school news page or an unconfirmed LinkedIn snippet is only a clue;
it is not an automatic early-exit verdict. Continue looking for attributable
evidence if the promising source cannot establish a decision. Stop collecting new
search results at 100 unique links, but inspect useful links in that final batch
within the remaining page/time budget. Duplicates do not count again. Independent
query/time/page limits still prevent expensive loops when providers repeat links.

## Limits and cost

LangSearch documents at most **10 results per request** and no pagination parameter:
[Web Search API](https://docs.langsearch.com/api/web-search-api).
Thus “100 results” means **up to 100 unique returned links across distinct queries**,
not guaranteed access to Google's first 100 hits. Duplicates, small result sets,
an early decision or the time/page budget can make the total smaller.

```dotenv
FACULTY_IDENTITY_PASS_QUERIES=10
FACULTY_IDENTITY_PASS_RESULTS=100
FACULTY_IDENTITY_PASS_PAGES=20
FACULTY_IDENTITY_PASS_SECONDS=90
FACULTY_IDENTITY_VERBOSE_LOG=true
```

Queries stop early. Ten queries per candidate would use a 1,000-request daily
allowance in roughly 100 candidates, before other API use. Existing global
provider limits, minimum intervals, quotas and fallback ordering remain in force.
Normal first-provider pacing delays of up to five seconds are waited out within
the pass, rather than immediately triggering a fallback. Long waits still defer
work or use a configured fallback. No new account rotation was added.

The 90-second budget is cooperative: checks stop between operations; existing
network/PDF timeouts and the worker's hard job deadline are still the final guards.
Do not raise batch sizes to compensate for slow sources.

## What staff sees

New identity audits retain useful university, personal, lab, LinkedIn and CV
sources. If no such sources were found, they retain the first ten results.
Full HTML and discarded raw search lists are not written to the identity audit.
The generic search cache is not populated with expanded identity search results;
existing cache entries can still be reused. Other search callers keep caching.
This does not delete old reports, caches or audit records.

The staff page shows the selected sources, snippets, queries, inspected-page
reasons and linked-from URLs. Failure explanations distinguish:

- Source blocked/challenge or source unavailable.
- No readable HTML / unsupported content.
- No useful profile found.
- Incomplete identity evidence (for example, no attributable role or matching paper).
- Missing university affiliation.
- Check limit reached.
- Search provider unavailable / waiting for a request slot (no identity verdict saved).

`VERIFIED`, `NOT_FACULTY`, `CONFLICT` and `UNVERIFIED` remain database statuses.
The detailed failure code is separate; “unverified” never means “definitely a student.”
Previously verified URLs are still saved and opened directly on later checks.
Existing monthly freshness rules and manual conflict review remain unchanged.

## Terminal

Stop the old worker with Ctrl+C in its terminal, then run:

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.run_worker
```

With verbose logging enabled, JSON events show candidate/field, university evidence,
each query, returned URLs/titles/snippets, skipped results, page fetches, followed
links and the final decision/source. Logs describe actual returned links, not
imaginary extra pages. Snippets are JSON-escaped so source text cannot inject
fake log lines. Run only one worker unless your deployment is configured for more.

Set `FACULTY_IDENTITY_VERBOSE_LOG=false` for quieter output. Restart the worker
after editing `.env`. These are staff-terminal logs, not public browser messages.
If you redirect them to a file, the file contains the printed raw results; use
log rotation/retention appropriate to your disk space.

## Small diagnostic

```bash
python -m scripts.sample_faculty_verification --limit 3
```

It reads the configured providers and pass limit from `.env`, stops after the
requested sample, and does not publish identity decisions. Reports retain the
selected sources, not every discarded result. It does consume provider quota.
For a cheaper, explicitly narrower test:

```bash
python -m scripts.sample_faculty_verification --limit 3 --max-searches-per-candidate 2
```

Do not interpret this two-query diagnostic as the full ten-query verifier's success rate.
Old monthly decisions are not automatically invalidated by installing this code.
