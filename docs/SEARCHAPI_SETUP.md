# SearchAPI and adding another search provider

Current requested behavior is **sequential fallback**, with Parallel.ai as a
separate search service. See `SEARCH_FALLBACKS.md`. The optional simultaneous
strategy described below is NOT enabled for this workflow.

## Current setup

The active Ubuntu project is `/home/mel/search_prof`, and local configuration is
`/home/mel/search_prof/.env`. Editing Downloads or a production example does not
change this running project. Restart a diagnostic/worker after changing `.env`.
Do not put a SearchAPI key in `TAVILY_API_KEY`: keys are provider-specific.

```dotenv
SEARCHAPI_API_KEY=your_searchapi_io_key
PARALLEL_API_KEY=your_parallel_ai_key
SEARCH_PROVIDERS=searchapi,parallel,ddgs
SEARCH_PROVIDER_STRATEGY=fallback
SEARCH_PARALLEL_MAX_PROVIDERS=2
SEARCHAPI_MIN_INTERVAL_MS=2000
SEARCHAPI_DAILY_LIMIT=100
SEARCHAPI_MONTHLY_LIMIT=100
SEARCHAPI_TOTAL_LIMIT=100
```

The default SearchAPI caps protect a 100-request trial. They are local safety
limits, not a claim about the provider's plans. `TOTAL_LIMIT` never resets. Raise
it deliberately only after checking your actual allowance; do not reset counters
to bypass a cap. Provider account quotas are checked separately and cached for
five minutes. An exhausted hourly allowance also prevents search requests.
An unknown quota fails safely to another provider, with a specific error.

SearchAPI uses Google organic results. Auth is sent in a header, never in URLs.
Faculty identity checks, evidence requirements, and moderation are unchanged.

## Parallel, balanced, and fallback

- `fallback`: try providers in order; stop on useful results.
- `parallel`: send the same query to up to `SEARCH_PARALLEL_MAX_PROVIDERS` API
  providers simultaneously, merge/deduplicate the results, and try remaining
  providers if necessary. This can spend one request at EACH participating API.
  With only SearchAPI enabled, it uses one API, not two calls to that service.
- `balanced`: distribute different queries across configured API providers,
  then fall back if needed. This does not by itself start concurrent candidates;
  candidate concurrency is controlled separately by the verification worker.

DuckDuckGo stays last and is not included in the initial parallel API group.
All modes retain provider-specific pacing, durable budgets, and cooldowns.
Two keys for different APIs do not automatically share one account or one quota.

## Add a supported provider later

For Tavily, add a key from Tavily itself:

```dotenv
TAVILY_API_KEY=your_actual_tavily_key
SEARCH_PROVIDERS=searchapi,parallel,tavily,ddgs
SEARCH_PROVIDER_STRATEGY=fallback
SEARCH_PARALLEL_MAX_PROVIDERS=2
TAVILY_DAILY_LIMIT=100
TAVILY_MONTHLY_LIMIT=1000
TAVILY_TOTAL_LIMIT=1000
```

For Brave, use `BRAVE_SEARCH_API_KEY` and enable `BRAVE_STORAGE_ALLOWED=true`
only if your plan permits storing search results/evidence. SearXNG uses
`SEARXNG_URL`; its upstream engines can still block requests.
Missing-key providers are skipped. Never paste keys into logs or chat.

## Add a provider that is not supported yet

An arbitrary key or provider name in `.env` is not enough. A developer must:

1. Read that provider's API, pricing, storage rules, and error documentation.
2. Add its adapter to `ingestion/websearch.py`: use its own endpoint and auth;
   return a list of `{href, title, body}` records. Never substitute a failed
   response with an empty successful result. Add the name to
   `SUPPORTED_PROVIDERS`, key detection in `_provider_names`, and the adapter
   in `_run_provider`.
3. Add its quota parser/check in `ingestion/provider_quota.py` when supported.
   Register it in `_run_provider`. Do not pretend a provider reports remaining
   credits if it has no account endpoint. Keep local caps regardless.
4. Add API pacing defaults in `ingestion/search_budget.py` and settings for
   `NAME_MIN_INTERVAL_MS`, `NAME_DAILY_LIMIT`, `NAME_MONTHLY_LIMIT`, and
   `NAME_TOTAL_LIMIT`. The health/usage table accepts new provider names; no
   per-provider database migration is required.
5. Update both env examples and Streamlit's secrets example, without real keys.
6. Test successful, empty, malformed, 401/403, 429, zero-credit, parallel,
   exhausted-primary fallback, and secret-safe logging behavior.
7. Check account access, run one search, then the 30-person diagnostic.

## Diagnostics

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.check_search
python -m scripts.sample_faculty_verification --limit 30
```

The sample honors `.env` and prints its provider pool at startup. It is NOT 30
unit tests: it evaluates up to 30 due US candidates using at most one query per
candidate by default. Reports go to `reports/verification-sample-<timestamp>/`.
It exits after completion and does not save faculty identity decisions. Normal
search cache and budget counters do update. A `SAMPLE_LIMIT` is inconclusive,
not a failed faculty identity. Up to three queries per candidate can be enabled
with `--max-searches-per-candidate 3`, at a higher possible API cost.

## Reference documentation

- Search: https://www.searchapi.io/docs/google
- Account/credits: https://www.searchapi.io/docs/account-api
