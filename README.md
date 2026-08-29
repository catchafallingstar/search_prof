# ScholarRadar MVP

ScholarRadar combines five evidence levels:

1. Openings submitted by role-verified faculty or university staff and approved by a site administrator.
2. Explicit public hiring statements found on official or attributable sources.
3. Assistant professors with active public funding, then other assistant professors.
4. Established faculty with active public funding.
5. Other research-matched, officially verified faculty. Titles and grants are
   opportunity indicators rather than proof of hiring.

Public search is targeted, not a preload of every academic field. It reads a
shared, persistent professor index from PostgreSQL and returns immediately. A
separate worker discovers researchers, verifies current faculty identities,
checks grants, and looks for hiring evidence. New searches create one deduplicated
background job; later visitors reuse the same topic index instead of repeating
the network work.

The app is built with Streamlit and PostgreSQL. The included Docker Compose configuration is for local Ubuntu development; use managed PostgreSQL when deploying to Streamlit Community Cloud.

## Fast local setup on Ubuntu

Install Python, Make, Docker Engine, and the Docker Compose v2 plugin. Confirm that these commands work:

```bash
python3 --version
docker --version
docker compose version
```

Then open this project directory and edit `.env`:

```dotenv
DEV_USER_EMAIL=your-real-email@example.com
DEV_USER_NAME="Your Name"
OPENALEX_EMAIL=your-real-email@example.com
OPENALEX_API_KEY=paste-your-free-openalex-key-here
```

The provided local password works for testing. If you change `POSTGRES_PASSWORD`, change the password inside `DATABASE_URL` to the same value. URL-encode special characters used inside the URL.

Run:

```bash
make setup
make start
```

`make start` starts PostgreSQL, a private local SearXNG container, the background
index worker, and Streamlit. Keep
that terminal open while testing. `make worker` is only for running the worker
separately; do not run it at the same time as `make start` during local testing.

Open `http://localhost:8501`. The local `.env` identity is also the sole site owner, so it can open `/Admin_review` and `/Admin_accounts`.

Useful commands:

```bash
make test       # unit tests, PostgreSQL checks, and headless Streamlit page tests
make schema     # safely reapply the idempotent schema
make db-logs    # inspect PostgreSQL logs
make db-down    # stop PostgreSQL without deleting data
make search-test # require SearXNG and an upstream engine to return useful results
make search-logs # inspect upstream-engine blocks or SearXNG errors
make backup     # private timestamped local PostgreSQL snapshot in backups/
make rebuild-topics  # safely queue outdated research areas for version-3 rebuilding
make topic-status    # show exact supporting-paper rebuild progress
make seed-catalog    # list the controlled major-field and subfield catalog
make seed-topics     # queue the next 20 low-priority catalog jobs
```

`make rebuild-topics` does not delete professors or fake a version number. It
queues one real OpenAlex reindex per outdated topic, reuses an existing rebuild
job when present, and raises a reused queued job to rebuild priority. Keep the
worker running and use `make topic-status` until every row says `ready`.

The seed catalog covers broad computing, engineering, life science, health,
physical science, social science, humanities, business/law, agriculture, and
environmental areas. `make seed-topics` adds at most 20 new low-priority jobs per
run. It reuses OpenAlex professor identities and active jobs. If a visitor searches
one of those topics, the existing job is promoted above background seeding.

## Radar search and ranking

The visitor supplies a research area, desired position, and optional university.
GPA evidence appears on each result instead of acting as a usually empty filter.
Search returns up to 25 indexed records at once; **Load 25
more** paginates to a public maximum of 100. There are no continuation or live-scan
controls. If coverage is still growing, the page says that indexing is happening
and offers a simple refresh button.

The worker runs a strict staged pipeline. It first expands recent OpenAlex papers
into corresponding, last, repeated, and selected first-author candidates. It
then verifies current faculty identities until the topic goal is reached or the
candidate pool is exhausted. Only after that identity stage finishes does it
check grants and public hiring pages; those two enrichment checks run together.
It uses the OpenAlex author ID as the external
identity, reuses fresh positive and negative decisions, and checks unknown or stale
people against official university pages. A person is public only after current
faculty status is verified. The worker then checks NSF and public hiring sources
in one resumable enrichment stage with two parallel branches. Finished and failed
jobs survive application and worker restarts.

Faculty searches check stored OpenAlex affiliation metadata first, then look for a
current role. Before an unresolved identity is saved or sent to staff, the worker checks
up to three recent accessible papers. It extracts text normally and uses bounded OCR only
when a PDF has no usable text. Install `tesseract-ocr` on the worker host; `packages.txt`
installs it on Streamlit Community Cloud. Search snippets may suggest a new institution,
but they are never stored as proof.
A career move is accepted only when a current official faculty page is connected to
the OpenAlex candidate by an earlier institution mentioned on the page, a matching
paper/DOI, or a rare-name OpenAlex affiliation fragment at both institutions. A
single unrelated same-name result stays `UNVERIFIED`; only multiple plausible
official identities become a staff-review `CONFLICT`.

The optional Gemini identity assistant is a fallback, not the primary verifier.
Rules and cached decisions run first. Gemini receives only already-fetched public
page text, and its URL, title, and verbatim evidence must pass deterministic checks
before a decision is stored. `GEMINI_IDENTITY_DAILY_LIMIT` is enforced in
PostgreSQL across every worker. Leave `GEMINI_IDENTITY_ENABLED=false` to run the
complete rule-based system without Gemini.

Faculty identity and research relevance are separate decisions. An official
faculty page can confirm a person even when a short directory biography omits the
visitor's research keywords. `CONFLICT` is reserved for identity evidence that
contains multiple plausible official faculty matches for the same name. Each
network job runs in an isolated process with a configurable hard
deadline (`INDEX_JOB_TIMEOUT_SECONDS`, default 300); the Staff page labels an
overdue job as stalled instead of presenting it as normal progress.
Faculty verification uses small batches of at most eight people and saves each
decision immediately. Per-person query and page limits keep normal batches below
the hard deadline; a retry resumes with unfinished identities instead of
repeating completed checks.

Faculty decisions have explicit next-check dates: verified roles are normally
refreshed after 90 days; non-faculty after 75 days; conflicts after 45 days; and
unverified records after 30 days. Funding and public-hiring checks use their own
timestamps because neither one proves the other.

Professor-prospect ranking keeps six evidence lanes internally:

- confirmed opening: approved on-site post;
- attributed online signal: explicit recruiting language found through a verified faculty page;
- assistant professor with active funding: early-career rank plus an active grant;
- assistant professor: verified early-career rank, hiring unknown;
- established faculty with active funding: funding indicator, hiring unknown;
- other verified faculty match: relevant recent work, hiring and funding unknown.

The public page shows four understandable sections: **Posted on ScholarRadar**,
**Hiring signals found online**, **Possible opportunities — hiring not confirmed**, and
**Other verified faculty matches — hiring unknown**. The detailed card badge explains whether the
reason is a site opening, public statement, assistant-professor title, active relevant
funding, or research fit.

OpenAlex does not prove employment title. The discovery step deliberately gathers
first, corresponding, last, and repeated matching authors, but a candidate is not
put in a public result until an official current `.edu` page identifies that same
person as faculty. This is why a last author who is actually a student or data
scientist is hidden, while a new assistant professor can be found even when the
paper still carries an older affiliation. Verification evidence is retained for
auditing and periodically refreshed. Publication affiliations never overwrite a
versioned, officially verified current appointment.

Web discovery uses a bounded provider pool. Local development sends one paced
request stream through private SearXNG, which may aggregate multiple upstream
engines. Direct DDGS is not enabled at the same time by default because both
paths normally leave through the same public IP and therefore do not provide
independent rate-limit capacity. The worker limits concurrency, persists
provider backoff in PostgreSQL across isolated jobs, and caches successful query
results for 24 hours.

An upstream outage is not an identity decision. When every provider is blocked,
the worker leaves professors unchanged and retries the durable job after the
shared backoff period. `make search-test` fails when SearXNG returns HTTP 200 but
all upstream engines return zero results, a CAPTCHA, or a suspension.

`SEARCH_PROVIDER_STRATEGY=balanced` is the recommended default: concurrent
candidate workers are distributed among providers. `parallel` asks up to
`SEARCH_PARALLEL_MAX_PROVIDERS` providers the same query simultaneously; reserve
it for controlled reindexing because it multiplies upstream traffic. Search
results are discovery clues only—an official university page and the existing
identity rules are still required before a professor becomes public.

For production, run SearXNG privately beside the always-on indexing worker and
set `SEARXNG_URL` to that private service. It cannot run as a durable second
process inside Streamlit Community Cloud. A Brave Search API key remains an
optional reliable provider; store it as `BRAVE_SEARCH_API_KEY` in the worker's
environment and Streamlit Secrets if used.
Also create a free OpenAlex key and store it as `OPENALEX_API_KEY`; production API
searches are rate-limited without it. Never commit either key.
`OPENALEX_EMAIL` should contain a real contact email.

ScholarRadar submissions and indexed web evidence remain separate:

- role-verified professor post: 100
- verified university post: 95
- attributed online hiring signal ranks after submissions but is not an opportunity post

Funding alone never creates an opening. The search form does not filter on usually
missing GPA claims. Each professor card instead reports sourced lab/program GPA
information or clearly says that it was not stated on the pages checked. Stale hiring
claims are hidden while a 24-hour trusted-page refresh runs in the background.
Sponsored placement remains separately labeled.

The owner can inspect topic coverage, pending and failed jobs, worker health,
faculty conflicts, recent automatic identity decisions, Gemini fallback usage,
hiring-source failures, and stale hiring checks on the Staff **Professor database**
page. Automatic decisions do not require routine approval, but the owner can
override any visible identity decision. Only the owner can change identity
decisions or manage indexing jobs.

If shell execution bits were lost while copying the project, the Make targets still invoke the scripts with `bash`. You can also run `chmod +x scripts/*.sh`.

## What to configure

- `.env` is local-only and is ignored by Git.
- `.env.example` documents local settings without personal secrets.
- `.env.production.example` documents production environment variables.
- `.streamlit/secrets.example.toml` documents production OIDC and database secrets. Copy it to `.streamlit/secrets.toml` only for local testing with real login; never commit that file.

`DEV_AUTH_BYPASS=true` is intentionally accepted only when `APP_ENV=development`. Never put either development identity setting in Streamlit Cloud secrets.

To enable the optional identity fallback, set `GEMINI_API_KEY`, change
`GEMINI_IDENTITY_ENABLED=true`, and start with the default limit of 25 calls per
day. The worker never enables Google Search grounding for these requests.

## Owner and moderator model

The project separates a person's site authority from their academic role:

- `users.account_role`: applicant, professor, or institution administrator.
- `site_admins.admin_role`: owner or moderator.

There is exactly one active owner. Only that owner can grant or revoke moderator access from `pages/4_Admin_accounts.py`. Both owners and moderators can review verification requests and openings in `pages/3_Admin_review.py`. Every decision is recorded in `admin_audit_log`.

For production, sign in once with the intended owner email so a `users` row exists, then run the owner command against the production `DATABASE_URL` from a trusted machine:

```bash
APP_ENV=production DATABASE_URL='postgresql://...' \
  .venv/bin/python -m scripts.bootstrap_owner \
  --email you@your-domain.edu --no-create-local-user
```

The bootstrap refuses to replace a different active owner.

## Production note

Do not deploy the local PostgreSQL container with Streamlit Community Cloud, and do not use `localhost` in its `DATABASE_URL`. Create a managed PostgreSQL database, apply `db.sql`, and place the managed connection string plus OIDC settings in the Streamlit Secrets editor. Set the OIDC redirect URI to your deployed app's `/oauth2callback` address.

From a trusted terminal with the production connection string, apply or upgrade
the managed database safely with:

```bash
APP_ENV=production DATABASE_URL='postgresql://...' \
  .venv/bin/python -m scripts.apply_schema
```

In Streamlit Community Cloud, select `app.py` as the entrypoint and Python 3.12
in Advanced settings, then paste the completed values from
`.streamlit/secrets.example.toml` into the Secrets editor. Streamlit Community
Cloud does not provide a durable second process, so run
`python -m scripts.run_worker` on a separate always-on worker service connected to
the same managed PostgreSQL database. Without that worker, existing indexed
results still display, but new topics will remain queued.

Before making the GitHub repository public:

1. Confirm `git status --ignored` shows `.env`, `.streamlit/secrets.toml`, and
   database snapshots as ignored.
2. Do not upload `before-*.sql` files. They are local recovery snapshots and can
   contain email addresses or moderation records.
3. If a snapshot was committed in an earlier Git revision, removing it from the
   latest revision is not enough. Publish from a clean repository or remove the
   file from Git history before changing repository visibility.
4. Apply `db.sql` to the managed database, add production secrets, sign in once,
   and bootstrap the owner before inviting test users.
5. Keep `DEV_AUTH_BYPASS`, `DEV_USER_EMAIL`, and `DEV_USER_NAME` out of production
   secrets.
6. Enable automated managed-database backups and test restoration. `make backup`
   is a local Docker safety tool; production backups should be scheduled by the
   PostgreSQL provider with retention and off-site storage.
7. Set `CONTACT_EMAIL`, then open **About data** and verify that the correction
   link reaches the monitored inbox.
8. Run `make production-check` from the production environment. Fix every FAIL
   before inviting users; review WARN items before the wider launch.

Role-verification requests and opening submissions are limited per account per
hour at the database boundary. Configure `ROLE_VERIFICATION_HOURLY_LIMIT` and
`OPPORTUNITY_SUBMISSION_HOURLY_LIMIT` to match the moderation capacity. See
`docs/PRODUCTION_CHECKLIST.md` for the launch and recurring operations checklist.

The browser-facing Streamlit process never calls DuckDuckGo, OpenAlex, university
sites, or NSF while answering a search. All slow or failure-prone network work is
owned by the durable worker queue.

## Learn the codebase

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete request flows, database roles, and a file-by-file explanation. Read [docs/UBUNTU_LOCAL_TESTING.md](docs/UBUNTU_LOCAL_TESTING.md) for setup, tests, troubleshooting, and the boundary between local and production configuration. Read [docs/RADAR_REVIEW.md](docs/RADAR_REVIEW.md) for the radar audit, corrected data flow, known limits, safe WSL update, cleanup, and retest procedure.

OpenAlex requests are paced across all worker processes through PostgreSQL. If
OpenAlex returns HTTP 429, every discovery job pauses until the shared cooldown
expires instead of retrying the service topic by topic. Configure the behavior
with `OPENALEX_MIN_INTERVAL_MS`, `OPENALEX_RATE_LIMIT_BACKOFF_SECONDS`, and
`OPENALEX_NETWORK_BACKOFF_SECONDS`. Do not put Proton VPN credentials in `.env`;
a system VPN is not an application search API, and ScholarRadar does not rotate
addresses to bypass source limits.
