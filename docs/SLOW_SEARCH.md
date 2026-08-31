# Slow free indexing

For the newer optional API providers and quiet queue scheduling, see
[Search providers](SEARCH_PROVIDERS.md). The DuckDuckGo-only configuration below
remains supported; the default provider list now skips unconfigured Tavily/Brave
keys and falls through to DuckDuckGo. Run `scripts.migrate_search_queue` on upgrades.

The website continues to display saved verified results. One background worker
checks known pages before using DuckDuckGo. No extra IP addresses or paid search
API are required. Coverage grows gradually; successful verification is not guaranteed.

## Start

```bash
cd ~/search_prof
source .venv/bin/activate
python -m scripts.migrate_slow_search  # once on each existing database
python -m scripts.run_worker
```

Alternatively `make start` starts the site and a worker together. Do not run both
launch methods simultaneously. Stop using the keyboard shortcut Ctrl+C; do not
type those characters as a Bash command. An active bounded job may finish first.

## What happens for a candidate

1. Reuse fresh positive/negative identity decisions.
2. Read stored OpenAlex paper affiliations (no discovery request).
3. Open a known official profile URL, or a personal/lab page with matching papers.
4. Optionally read an already-linked ORCID record. Its dates/role are clues, not a
   current faculty verdict. Mismatched ORCID names do not supply profile locators.
5. If affiliation is missing or ambiguous, inspect up to three accessible papers.
6. If needed, search DuckDuckGo for the name and supported university, then inspect
   source pages under the existing role, identity, institution and publication rules.
7. Save a decision, or schedule a retry without changing the identity decision.

Existing candidates without a stored ORCID ID skip ORCID. New OpenAlex discovery
records known ORCID IDs; no re-extraction is launched merely to fill them in.
PDF retrieval uses stored PDF URLs by default. Optional OpenAlex PDF locator
requests require PAPER_AFFILIATION_OPENALEX_LOOKUP_ENABLED=true.

## Local settings

```dotenv
SEARCH_PROVIDERS=ddgs
SEARCH_PROVIDER_STRATEGY=fallback
SEARCH_MIN_INTERVAL_MS=60000
SEARCH_DAILY_LIMIT=200
SEARCH_PROVIDER_BACKOFF_SECONDS=3600
SEARCH_RATE_LIMIT_BACKOFF_SECONDS=3600
SEARCH_CACHE_ENABLED=true
SEARCH_CACHE_HOURS=168
SEARCH_EMPTY_CACHE_HOURS=24
INDEX_VERIFY_BATCH_SIZE=1
FACULTY_VERIFY_MAX_WORKERS=1
ORCID_ENABLED=true
ORCID_ACCESS_TOKEN=
PAPER_AFFILIATION_OPENALEX_LOOKUP_ENABLED=false
```

DDGS explicitly uses its DuckDuckGo backend, not the auto/multi-engine backend.
The daily budget counts outbound attempts, not professors; it resets at midnight
UTC. A PostgreSQL reservation and session lock enforce pacing/concurrency across
worker processes. A cached query consumes no slot. Empty successful queries are
cached too, so a multi-query identity can resume without restarting the same search.
HTTP errors, CAPTCHA responses and provider failures are not cached as empty matches.

These limits are conservative operating choices, not a provider-approved allowance.
Respect provider restrictions; do not rotate IPs to evade blocks. Longer Retry-After
headers are honored. Jobs defer rather than sleeping inside a five-minute deadline.
Direct-page checks may continue during search pauses; waiting identities show in
Staff → Live indexing activity → Next web-search retries.

ORCID is optional and cached for 30 days (failed lookups one day). Keep its Public
API use non-commercial and within its terms. If monetizing later, reassess ORCID
licensing; production example files leave it disabled by default. A new ORCID paper
does not imply the employment information was updated.

`make search-test` uses the configured provider and respects its cache, budget and
cooldown. It does not reset blocked providers or guarantee future availability.
SearXNG stays installed but is not used or started by `make start` in DDGS-only mode.

No topic rebuild, full dataset download, directory crawler or Common Crawl integration
is required for this mode. Existing positive decisions retain their normal refresh dates.
