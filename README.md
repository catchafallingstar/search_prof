# ScholarRadar MVP

ScholarRadar combines five evidence levels:

1. Openings submitted by role-verified faculty or university staff and approved by a site administrator.
2. Explicit public hiring statements found on official or attributable sources.
3. Assistant professors with active public funding, then other assistant professors.
4. Established faculty with active public funding.
5. Other research-matched, officially verified faculty. Titles and grants are
   opportunity indicators rather than proof of hiring.

Public search is targeted, not a preload of every academic field. A visitor first
searches active approved records. With **Include a live public-web radar** enabled,
ScholarRadar can then run a bounded scan for that exact research area, show its
progress, and return both explicit public recruiting evidence and relevant
professor prospects. Public hiring candidates are clearly labeled unreviewed
and are also added to the staff moderation queue.

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

Open `http://localhost:8501`. The local `.env` identity is also the sole site owner, so it can open `/Admin_review` and `/Admin_accounts`.

Useful commands:

```bash
make test       # unit tests, PostgreSQL checks, and headless Streamlit page tests
make schema     # safely reapply the idempotent schema
make db-logs    # inspect PostgreSQL logs
make db-down    # stop PostgreSQL without deleting data
```

## Radar search and ranking

The live radar progresses through topic interpretation, expanded recent-paper
discovery, a small hiring-first public-web search, broad researcher-candidate
identification, progressive current faculty-role verification, public grant checks,
official faculty/lab-page discovery, web/social hiring-language checks, and
moderation storage. Recent identical scans are cached,
concurrent duplicate scans are collapsed, and an hourly global limit prevents a
public deployment from repeatedly hitting external services.

Visitors can choose a goal of 10, 25, 50, or 100 verified faculty results. A goal is
not a promise: authors whose faculty identity cannot be verified are intentionally
hidden. Discovery keeps a candidate pool up to six times larger (capped at 600), prioritizes explicit
hiring leads, includes safely cached faculty decisions at any research rank, and
verifies additional candidates in batches until the goal, candidate pool, or
verification time budget is reached. Fewer results are valid when identities cannot
be verified safely. Live NSF and detailed homepage enrichment remains bounded to
the top 10, 12, 15, or 20 respectively. Every card says whether each source class was checked.
The website opens recent results immediately by default. Select **Continue checking an
incomplete cached search** and submit the same query to advance through unchecked
candidates. The CLI continues when the same command is rerun, or can build a deep
cache in several bounded passes with:

```bash
python -m scripts.run_radar "AI security" --professors 100 --passes 5
```

Current positive decisions
are cached for 90 days and current negative decisions for 30 days, so continuation passes
do not repeat completed identity checks.
Grant and public-hiring timestamps are stored per professor, so later passes prioritize
people whose enrichment sources have not yet been checked instead of repeatedly checking
the same top 20.
The cache identity includes the query, requested professor count, enrichment size,
and algorithm version, so a previous 10-result scan cannot satisfy a 100-result
request.

Professor-prospect ranking keeps six evidence lanes internally:

- confirmed opening: approved on-site post;
- current public signal: explicit recruiting language, still moderated;
- assistant professor with active funding: early-career rank plus an active grant;
- assistant professor: verified early-career rank, hiring unknown;
- established faculty with active funding: funding indicator, hiring unknown;
- other verified faculty match: relevant recent work, hiring and funding unknown.

The public page combines those lanes into three understandable sections: **Hiring now —
current evidence**, **Likely opportunities — hiring not confirmed**, and **Other verified
faculty matches — hiring unknown**. The detailed card badge still explains whether the
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

For production reliability, create a Brave Search API key and store it as
`BRAVE_SEARCH_API_KEY` in Streamlit Secrets. The code uses Brave's official Web
Search API when that key exists and falls back to DDGS for local development.
Also create a free OpenAlex key and store it as `OPENALEX_API_KEY`; production API
searches are rate-limited without it. Never commit either key.
`OPENALEX_EMAIL` should contain a real contact email.

Organic opportunity ranking is source-based:

- role-verified professor post: 100
- verified university post: 95
- official homepage hiring signal: 75
- other moderated public web/social signal: 60

Funding alone never creates an opening. Public web evidence uses `GPA policy not
stated`; only a verified submitter may claim GPA flexibility. Sponsored placement
remains separately labeled.

Owners and moderators can run larger scans and inspect run history on the Staff
**Radar operations** page.

If shell execution bits were lost while copying the project, the Make targets still invoke the scripts with `bash`. You can also run `chmod +x scripts/*.sh`.

## What to configure

- `.env` is local-only and is ignored by Git.
- `.env.example` documents local settings without personal secrets.
- `.env.production.example` documents production environment variables.
- `.streamlit/secrets.example.toml` documents production OIDC and database secrets. Copy it to `.streamlit/secrets.toml` only for local testing with real login; never commit that file.

`DEV_AUTH_BYPASS=true` is intentionally accepted only when `APP_ENV=development`. Never put either development identity setting in Streamlit Cloud secrets.

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
`.streamlit/secrets.example.toml` into the Secrets editor.

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

For a single Streamlit instance, the bounded scan can execute during the request as
implemented here. For significant public traffic, move `execute_radar` to a durable
worker/queue so scans continue across app restarts. PostgreSQL already stores run
status, counters, cache identity, and candidate links for that migration.

## Learn the codebase

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete request flows, database roles, and a file-by-file explanation. Read [docs/UBUNTU_LOCAL_TESTING.md](docs/UBUNTU_LOCAL_TESTING.md) for setup, tests, troubleshooting, and the boundary between local and production configuration. Read [docs/RADAR_REVIEW.md](docs/RADAR_REVIEW.md) for the radar audit, corrected data flow, known limits, safe WSL update, cleanup, and retest procedure.
