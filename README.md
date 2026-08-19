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

`make start` starts both the background index worker and Streamlit. Keep that
terminal open while testing. To run them separately, use `make worker` in one
terminal and `streamlit run app.py` in another.

Open `http://localhost:8501`. The local `.env` identity is also the sole site owner, so it can open `/Admin_review` and `/Admin_accounts`.

Useful commands:

```bash
make test       # unit tests, PostgreSQL checks, and headless Streamlit page tests
make schema     # safely reapply the idempotent schema
make db-logs    # inspect PostgreSQL logs
make db-down    # stop PostgreSQL without deleting data
make backup     # private timestamped local PostgreSQL snapshot in backups/
```

## Radar search and ranking

The visitor supplies a research area, desired position, GPA preference, and
optional university. Search returns up to 25 indexed records at once; **Load 25
more** paginates to a public maximum of 100. There are no continuation or live-scan
controls. If coverage is still growing, the page says that indexing is happening
and offers a simple refresh button.

The worker expands recent OpenAlex papers into corresponding, last, repeated, and
selected first-author candidates. It uses the OpenAlex author ID as the external
identity, reuses fresh positive and negative decisions, and checks unknown or stale
people against official university pages. A person is public only after current
faculty status is verified. The worker then checks NSF and public hiring sources in
separate cached jobs. Finished and failed jobs survive application and worker
restarts.

Faculty decisions have explicit next-check dates: verified roles are normally
refreshed after 90 days; non-faculty after 75 days; conflicts after 45 days; and
unverified records after 30 days. Funding and public-hiring checks use their own
timestamps because neither one proves the other.

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

The owner can inspect topic coverage, pending and failed jobs, worker health, and
faculty conflicts on the Staff **Radar operations** page. Only the owner can retry
or cancel indexing jobs.

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

The browser-facing Streamlit process never calls DuckDuckGo, OpenAlex, university
sites, or NSF while answering a search. All slow or failure-prone network work is
owned by the durable worker queue.

## Learn the codebase

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete request flows, database roles, and a file-by-file explanation. Read [docs/UBUNTU_LOCAL_TESTING.md](docs/UBUNTU_LOCAL_TESTING.md) for setup, tests, troubleshooting, and the boundary between local and production configuration. Read [docs/RADAR_REVIEW.md](docs/RADAR_REVIEW.md) for the radar audit, corrected data flow, known limits, safe WSL update, cleanup, and retest procedure.
