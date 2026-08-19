# Ubuntu local setup and testing

## Prerequisites

You need:

- Ubuntu with `python3`, `python3-venv`, and `make`.
- Docker Engine running for your user.
- Docker Compose v2, invoked as `docker compose`.

If Docker says permission denied, configure your Ubuntu user for Docker or temporarily run the Docker commands with appropriate system privileges. Do not run the Streamlit app itself as root.

## Configure `.env`

The project includes a local `.env` with placeholders. Replace:

```dotenv
DEV_USER_EMAIL=REPLACE_WITH_YOUR_EMAIL@example.com
DEV_USER_NAME="Replace With Your Name"
OPENALEX_EMAIL=REPLACE_WITH_YOUR_EMAIL@example.com
```

Local SQL values mean:

- `POSTGRES_DB`: database Docker creates.
- `POSTGRES_USER`: dedicated application/database user Docker creates.
- `POSTGRES_PASSWORD`: that user's local password.
- `POSTGRES_PORT`: Ubuntu host port exposed by Docker.
- `DATABASE_URL`: the same four values assembled for Python/psycopg.

There is no unknown SQL account you must obtain. Docker creates it on the first start. If port 5432 is occupied, set `POSTGRES_PORT=5433` and change the URL port to `5433`.

If you change the database/user/password after the Docker volume has already been initialized, PostgreSQL does not recreate them automatically. For a disposable local database only, you may run `docker compose down -v` and rerun setup; `-v` permanently deletes the local ScholarRadar database volume.

## First run

```bash
make setup
make start
```

`make setup` performs these operations in order:

1. Checks Python, Docker, and Compose.
2. Rejects unedited identity placeholders.
3. Creates `.venv`.
4. Installs `requirements.txt`.
5. Starts PostgreSQL and waits for health.
6. applies `db.sql` with `ON_ERROR_STOP`.
7. Creates the local development user and makes it the one owner.
8. Runs all test layers.

`make start` starts the durable radar worker in the background and keeps Streamlit
in the foreground. Press Ctrl+C to stop both processes; PostgreSQL remains in
Docker until `make db-down`. Worker logs are written to
`/tmp/scholarradar-worker.log`.

## Test layers

Run all tests:

```bash
make test
```

The layers are:

- Unit tests: text matchers, safe URL rules, expiry behavior, and schema contracts.
- Database smoke test: required tables exist and exactly one owner is active.
- Streamlit smoke test: every page executes in a headless runner with no uncaught exception.

Useful diagnosis:

```bash
docker compose ps
make db-logs
make schema
make backup
.venv/bin/python -m scripts.smoke_test_db
.venv/bin/python -m scripts.smoke_test_streamlit
```

## Test the moderation workflow manually

With local development auth, your `.env` user is the owner.

1. Open `http://localhost:8501/1_Post_an_opening`.
2. Submit a faculty verification request using a real-looking official public university URL.
3. Open `http://localhost:8501/3_Admin_review` and approve it.
4. Return to the post page and submit an opening.
5. Return to the moderation queue and approve the opening.
6. Open the homepage and confirm that the opening is searchable.
7. Open `http://localhost:8501/4_Admin_accounts` to see the audit entries.

To test a second local identity, change `DEV_USER_EMAIL` and `DEV_USER_NAME`, restart Streamlit, and visit a protected/user page once. That creates the second `users` row. Restore the owner's email, restart, open Admin accounts, and grant the second user moderator access. Change back to the second identity to confirm that it can use Admin review but cannot use Admin accounts.

## Test the shared radar index

Start the site with `make start`, enter a research area, and press Search. Existing
verified matches should appear immediately. A new topic creates a background job;
use the homepage refresh button later to see new records. The owner can inspect
coverage and failures from Staff → Radar operations.

Worker commands for diagnosis:

```bash
make worker-once  # process one queued job
make worker       # keep processing jobs
tail -f /tmp/scholarradar-worker.log
```

The older foreground radar CLI remains useful for ingestion diagnostics, but the
website does not call it:

```bash
.venv/bin/python -m scripts.run_radar "robotics" --max-papers 20 --skip-web-signals
```

Public APIs can rate-limit, change, or return no matches; that is separate from whether the local web and database stack is healthy.

## Local login versus production login

Local development uses:

```dotenv
APP_ENV=development
DEV_AUTH_BYPASS=true
```

`auth.py` requires both switches, displays a visible warning, and builds a local subject from `DEV_USER_EMAIL`.

Production must use:

```dotenv
APP_ENV=production
DEV_AUTH_BYPASS=false
```

and a real Streamlit OIDC `[auth]` configuration. Do not upload `.env`. Put production values into the hosting service's secret manager. The deployed `DATABASE_URL` must point to a reachable managed PostgreSQL host and normally include `sslmode=require`.

## Production owner bootstrap

After applying `db.sql` to production:

1. Sign in to the deployed app once using the email that should be owner.
2. From a trusted terminal with the production `DATABASE_URL`, run:

```bash
APP_ENV=production DATABASE_URL='postgresql://...' \
  .venv/bin/python -m scripts.bootstrap_owner \
  --email your-email@university.edu --no-create-local-user
```

The command only grants the first owner or confirms the same owner. It refuses to replace a different active owner. There is intentionally no public "create admin" button.

## Production worker requirement

The Streamlit process and the worker must share the same managed `DATABASE_URL`.
Run `python -m scripts.run_worker` as an always-on process on a worker-capable host.
Streamlit Community Cloud alone does not keep a second background process alive.
If the worker is absent, indexed results remain readable but new jobs stay queued;
the owner dashboard will show that no healthy worker is connected.
