# ScholarRadar MVP

ScholarRadar combines two kinds of evidence:

1. Public radar signals that suggest a laboratory may be hiring.
2. Openings submitted by role-verified faculty or university staff and approved by a site administrator.

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

## Learn the codebase

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete request flows, database roles, and a file-by-file explanation. Read [docs/UBUNTU_LOCAL_TESTING.md](docs/UBUNTU_LOCAL_TESTING.md) for setup, tests, troubleshooting, and the boundary between local and production configuration. Read [docs/RADAR_REVIEW.md](docs/RADAR_REVIEW.md) for the radar audit, corrected data flow, known limits, safe WSL update, cleanup, and retest procedure.
