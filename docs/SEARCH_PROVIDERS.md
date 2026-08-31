# Search providers and quiet queue waiting

No new account was purchased and no API credits were used to install this change.

## Start after an update

Stop the old worker with Ctrl+C, then:

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.migrate_affiliation_quota
python -m scripts.run_worker
```

The migration is safe to repeat. Restart Streamlit separately if necessary. Use
one worker process; it can check two distinct candidates concurrently.

## Add a search key

Edit `/home/mel/search_prof/.env` locally. Do not paste secrets into chat or Git.

```dotenv
SEARCH_PROVIDERS=searchapi,tavily,brave,ddgs
SEARCH_PROVIDER_STRATEGY=fallback
TAVILY_API_KEY=
TAVILY_ALLOW_PAID_USAGE=false
BRAVE_SEARCH_API_KEY=
BRAVE_STORAGE_ALLOWED=false
```

1. Tavily: https://app.tavily.com/ — copy an API key into TAVILY_API_KEY.
   Published free allowance: 1,000 credits/month. Our adapter explicitly uses basic
   search (one credit), not advanced/automatic depth, generated answers or extracts.
   Leave pay-as-you-go disabled to stay inside the account's free allowance.
2. Brave: https://brave.com/search/api/ — an optional alternate key. Because the
   site stores search results/evidence, confirm your plan permits that storage,
   then set BRAVE_STORAGE_ALLOWED=true. Without it, Brave is skipped. Follow the
   plan's attribution requirements when enabling free credits.
3. DuckDuckGo needs no key. It remains a slow fallback, not a guaranteed service.

Missing-key providers are skipped. No LangSearch integration is enabled. Its
"invalid state" sign-in error belongs to its login flow, not this site's worker.

The same settings belong in the deployment environment or Streamlit Secrets when
hosting. Editing an example file alone does not configure a running deployment.

## Caps and $20 budget

Each provider has independent limits, for example:

```dotenv
TAVILY_MIN_INTERVAL_MS=2000
TAVILY_DAILY_LIMIT=200
TAVILY_MONTHLY_LIMIT=1000
TAVILY_TOTAL_LIMIT=1000
BRAVE_MIN_INTERVAL_MS=2000
BRAVE_DAILY_LIMIT=200
BRAVE_MONTHLY_LIMIT=1000
BRAVE_TOTAL_LIMIT=1000
```

TOTAL_LIMIT is a non-resetting ceiling on locally tracked outbound attempts.
Increasing it explicitly permits more requests; it is not an additional allowance.
Daily/monthly counters reset by UTC calendar boundaries. Keys changed for the same
provider do not reset these counters. Unknown old usage before this migration and
usage in other applications is not counted. The provider dashboard is authoritative
for billing: configure its spending limit too.

Published prices checked August 30, 2026:
- Tavily basic PAYG: $0.008/credit, so $20 buys approximately 2,500 basic requests
  before taxes, independently of any remaining free allowance.
- Brave Search: $5/1,000 requests, so $20 buys approximately 4,000 requests before
  taxes. Confirm storage rights and the conditions for any free monthly credits.

Do not increase caps until you choose a plan and budget. Hitting a cap, HTTP 402,
or Tavily 432/433 makes this provider wait; another configured provider can run.
No account creation, payment, quota circumvention, or same-provider key rotation
is performed. Adding a completely different API later requires an adapter, not
just a made-up environment variable.

## Parallelism without duplicating every query

Two candidates can run at once. With fallback routing, a candidate uses the first
available provider; another candidate can use another provider when the first is
busy. Each provider admits only one in-flight request through a PostgreSQL lock,
and all processes share its quota and spacing. No 60-second sleep holds a job open.
When only DuckDuckGo is configured, search itself remains serial.

`SEARCH_PROVIDER_STRATEGY=parallel` also exists, but sends the SAME query to multiple
providers and consumes multiple allowances. It is not the recommended budget mode.

## What waits, what is reused

- The scheduler does not claim search-only identity jobs while all providers are
  waiting. Jobs remain queued without increasing failure attempts.
- Candidates with untried stored faculty/homepage/ORCID locators can still receive
  a direct check. A direct-only pass never calls a search engine.
- A candidate whose direct evidence was tried but still needs search gets a
  persistent search-pending flag. It is not rejected or repeatedly fetched during
  the same pause. PDFs remain an unresolved-affiliation fallback in normal passes.
- A completed identity decision is reused for at least 30 rolling days, even after
  another field discovers the same author or the algorithm version changes.
  Existing 45/75/90-day refresh intervals are retained where applicable.
- A deferred/failed search is not a completed check and remains retryable. Staff
  can explicitly request a retry sooner; that action overrides the reuse period.
- Old-version decisions are not automatically upgraded or made public.
- Hiring and grant refresh schedules are separate from faculty identity checks.

Staff → Live indexing activity → Search API capacity shows the actual wait reason.
One terminal `search_waiting` event replaces repeated no-progress search-slot
messages during a continuous pause. Successful batches can still be rescheduled
when other candidates remain; that is normal progress, not failure.

Sources: https://docs.tavily.com/documentation/api-credits
https://docs.tavily.com/documentation/api-reference/endpoint/search
https://brave.com/search/api/

## Provider-reported allowances

Tavily's authenticated `/usage` endpoint is checked before searches and cached
for five minutes. The allowance is the smaller of the account and key limits;
successful reservations decrement the local estimate between checks. Unknown or
zero allowance skips Tavily and tries the next provider. Paid allowance is excluded
unless `TAVILY_ALLOW_PAID_USAGE=true`. Keep a hard spending cap on the provider's
dashboard: usage outside ScholarRadar can race with this cached estimate.

Brave supplies allowance/reset headers with search responses. Subsequent requests
respect those windows as well as our local caps. There is no separate preflight
balance request here: the first call needs a valid key and uses the local budget.
Missing headers do not establish unlimited credit. HTTP quota/auth failures pause
that provider, then routing tries another configured service.

DuckDuckGo is the final fallback. If it is blocked or rate-limited too, work waits;
we do not override the engine's limits. Missing API keys are skipped, not purchased.

API references: https://docs.tavily.com/documentation/api-reference/endpoint/usage
https://api-dashboard.search.brave.com/documentation/guides/rate-limiting

## Faculty affiliation rule

Use the candidate's authorship metadata first, not a coauthor's institution. If
metadata is missing or ambiguous, inspect at most three accessible recent papers.
When no PDF URL is stored, read the DOI landing page's declared PDF link first.
Paywalls, access-denied pages, private redirects and unreadable files are not bypassed.
Match the author to an affiliation in the PDF header using shared, separate, or
numbered author blocks. If the layout cannot be linked safely, leave it unresolved.
An affiliation is a search clue, not proof of a current faculty appointment.

Search the person's name with the supported university. A supporting research
query can disambiguate short/common names. Open the actual profile and check the
person-specific faculty role; a search snippet or `.edu` suffix is not enough.
Personal/lab pages must also contain a matching paper or DOI.

A different observed university is `CONFLICT`, even if it might be a real move.
It stays hidden and is not automatically retried. Staff can resolve or explicitly
retry it. Known unrelated identities, unreadable PDFs, missing pages and blocked
engines still exist: the algorithm cannot honestly guarantee zero unresolved cases.
Completed non-conflict decisions are reused for at least 30 rolling days; hiring
and funding checks remain independent.

## Small live diagnostic

```bash
python -m scripts.sample_faculty_verification --limit 30 --max-searches-per-candidate 1
```

This selects up to 30 due US candidates across fields, uses only DuckDuckGo and
direct page/PDF checks, and keeps existing pacing/cooldowns. It does not save
identity decisions or use OpenAlex/Gemini API credits. Reports go under `reports/`.
`SAMPLE_LIMIT` means the one-query budget was insufficient; `SOURCE_WAIT` means
the provider could not serve the request. Neither means the person is not faculty.
This is a low-cost diagnostic, not a complete multi-query verification of everyone.
