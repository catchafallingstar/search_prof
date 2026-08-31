# SearchAPI, Parallel.ai, then DuckDuckGo

Parallel.ai is a search service. It is NOT the setting for simultaneous queries.
The corrected configuration is sequential fallback, with no simultaneous calls:

```dotenv
SEARCHAPI_API_KEY=your_searchapi_key
PARALLEL_API_KEY=your_parallel_ai_key
SEARCH_PROVIDERS=searchapi,parallel,ddgs
SEARCH_PROVIDER_STRATEGY=fallback
```

Edit `/home/mel/search_prof/.env`. Keep each provider's own key in its own field.
Restart the worker or diagnostic to load changes. Missing-key providers are
skipped. Do not use `SEARCH_PROVIDER_STRATEGY=parallel` for this workflow.

## What causes fallback?

SearchAPI is tried first. If it is out of quota, unavailable, rate-limited, or
returns no useful results, try Parallel. If Parallel cannot help, try DuckDuckGo.
Once a provider supplies useful results, no later provider is called for that
query. Existing positive search cache entries may avoid external calls entirely.

SearchAPI reports remaining credits through its account endpoint. This check is
cached for five minutes and local reservations decrement the allowance between
checks. Search responses still enforce the provider's actual limits.

No ordinary API-key balance endpoint for Parallel was identified in the reviewed
documentation. We do NOT display an invented dollar balance. A Parallel HTTP 402
means insufficient available credit, triggers fallback immediately, and puts that
provider into the existing 24-hour cooldown. A 429 triggers rate-limit backoff.
The next query skips a provider during its cooldown rather than repeatedly
sending requests to it. Local caps are enforced independently for each provider.

```dotenv
PARALLEL_MIN_INTERVAL_MS=2000
PARALLEL_DAILY_LIMIT=100
PARALLEL_MONTHLY_LIMIT=100
PARALLEL_TOTAL_LIMIT=100
```

These are conservative request-count limits, NOT dollar balances or promises of
free usage. TOTAL_LIMIT is cumulative and never resets automatically. Parallel
uses the `fast` Search mode; no tasks, automated purchases, or top-ups are made
by this integration. Check your provider dashboard's billing, auto-recharge,
and spend settings as well: local request caps do not control charges incurred
by other apps using the same account.

The 24-hour cooldown remains after a top-up until it expires; adding credit does
not automatically clear our stored cooldown. Keep the working fallback enabled.

## Adding another service

Already supported: add its own key and insert its provider name before `ddgs`.
For example, `searchapi,parallel,tavily,ddgs`. Use a Tavily key for Tavily, never a
SearchAPI or Parallel key. Brave also requires a plan permitting result storage.

A completely unsupported service needs a provider adapter, error handling and
quota support where documented, then configuration and regression tests.
See `docs/SEARCHAPI_SETUP.md` for the developer checklist. Its earlier example
using simultaneous mode is superseded by the sequential settings in this guide.

## Testing

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.check_search
python -m scripts.sample_faculty_verification --limit 30
```

The diagnostic exits after up to 30 candidates. It does not save identity
decisions. Normal provider request counters and search caches update. A one-query
sample is not a full faculty-verification evaluation.

References:
- https://docs.parallel.ai/api-reference/search/search
- https://docs.parallel.ai/resources/warnings-and-errors
- https://www.searchapi.io/docs/account-api
