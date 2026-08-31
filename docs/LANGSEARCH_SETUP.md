# LangSearch setup

1. Obtain your own key from https://langsearch.com/api-keys .
2. In `/home/mel/search_prof/.env`, fill in `LANGSEARCH_API_KEY=`.
3. Keep `SEARCH_PROVIDER_STRATEGY=fallback`. LangSearch is a provider;
   Parallel.ai is another provider, not an instruction to run all APIs at once.
4. Restart any running worker with Ctrl+C, then from `~/search_prof` run:

```bash
source .venv/bin/activate
python -m scripts.run_worker
```

The configured chain starts with LangSearch, preserves existing fallback order,
and keeps DuckDuckGo last. Blank API keys are skipped. A successful result ends
the provider chain; empty results, unavailable slots, caps and errors allow a
fallback. Other APIs can consume their own credits within their existing caps.

## Pacing and budgets

```dotenv
LANGSEARCH_API_KEY=
LANGSEARCH_TIMEOUT_SECONDS=20
LANGSEARCH_MIN_INTERVAL_MS=2000
LANGSEARCH_DAILY_LIMIT=900
LANGSEARCH_MONTHLY_LIMIT=0
LANGSEARCH_TOTAL_LIMIT=0
LANGSEARCH_MAX_CONCURRENCY=1
```

One request at a time, at least two seconds between starts, across all workers
sharing this PostgreSQL database. No long sleep is added inside a worker job:
the existing durable limiter returns a retry time or uses the next provider.
The local daily cap resets at UTC midnight; this is not a claim about the
provider's own reset timezone. Requests from outside this database are not counted.
Zero monthly/total caps mean no additional operator cap, not unlimited API usage.

Published free tier: 1 request/second, 60/minute, 1000/day. Our defaults leave
some headroom; free-tier safety clamps prevent configuring >1000/day or <1s here.
See https://docs.langsearch.com/limits/api-limits .

No account-balance endpoint is assumed. Local request accounting and provider
error responses control fallback. HTTP errors and JSON-envelope error codes
are treated as failures, never as an empty result or a non-faculty verdict.
Rate-limit responses respect Retry-After and the existing shared cooldown.

## What it returns

One POST request returns up to 10 links. No hidden pagination or extra queries
are purchased. `freshness=noLimit` avoids excluding older faculty profiles;
`summary=false` retrieves snippets, then ScholarRadar reads eligible source
pages itself. A search snippet does not prove a current faculty/hiring role.
See https://docs.langsearch.com/api/web-search-api .

## Check configuration without spending search credits

```bash
python -c "from ingestion.websearch import configured_search_providers; print(configured_search_providers())"
```

After setting the key, an optional real diagnostic is:

```bash
python -m scripts.sample_faculty_verification --limit 3
```

This uses the full configured fallback chain and can consume provider credits.
It ends after the sample and saves a report, not faculty identity decisions.

Only `.env` is currently your local configuration. `.env.production.example`
is a template, not an automatically loaded production file; configure secrets
in the production environment when deploying. Never commit real keys.

Before mass indexing, confirm LangSearch permits your automated lookup and
stored-evidence use: https://langsearch.com/terms-of-service has broad restrictions
on scraping/mining/harvesting. Free access does not guarantee perpetual availability.
