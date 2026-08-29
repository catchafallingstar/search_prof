# ScholarRadar production checklist

Use this checklist for the private beta and again before a wider public launch.

## Application and secrets

- [ ] `APP_ENV=production` and `DEV_AUTH_BYPASS=false`.
- [ ] OIDC redirect URI, client ID, client secret, and a long random cookie secret are configured.
- [ ] `DATABASE_URL` points to managed PostgreSQL, not localhost, and requires TLS.
- [ ] OpenAlex, optional Brave Search, and optional Gemini credentials are deployment secrets, not Git files.
- [ ] SearXNG runs privately beside the indexing worker; its JSON API is not exposed as an unrestricted public service.
- [ ] `SEARCH_PROVIDERS`, per-provider concurrency, pacing, backoff, and the 24-hour PostgreSQL query cache are configured.
- [ ] Run a provider health query and confirm 403/429/CAPTCHA events pause only the affected provider.
- [ ] `CONTACT_EMAIL` reaches an inbox that is monitored for corrections and removals.
- [ ] Run `make production-check` and resolve every FAIL.

## Database and worker

- [ ] Apply the current schema with `python -m scripts.apply_schema`.
- [ ] Bootstrap exactly one owner account.
- [ ] Run one durable worker service separately from Streamlit.
- [ ] Run SearXNG on an always-on service reachable from that worker; Streamlit Community Cloud cannot host it as a second durable process.
- [ ] Configure automatic worker restart and alert when no heartbeat is recorded for ten minutes.
- [ ] Confirm user searches receive higher priority than catalog seeding.
- [ ] Confirm old topics were rebuilt with discovery version 3 and exact supporting papers.

## Abuse and moderation

- [ ] Keep per-account submission limits enabled.
- [ ] Keep identical public indexing requests deduplicated and topic creation rate-limited.
- [ ] Keep manual moderation for openings submitted to ScholarRadar.
- [ ] Validate links and length-limit text at both the form and database boundary.
- [ ] Add CAPTCHA at the identity provider, proxy, or form layer if real abuse appears.

## Backups, logs, and response

- [ ] Enable daily managed-database backups with at least seven daily restore points.
- [ ] Keep weekly backups for one month and monthly backups for the chosen retention period.
- [ ] Perform and record a restore test before launch and at least quarterly.
- [ ] Retain structured worker logs and alert on repeated timeouts, failed jobs, queue growth, and database errors.
- [ ] Check the staff dashboard for identity conflicts, source failures, and stale coverage.

## Public information

- [ ] Review the **About data** page on desktop and mobile.
- [ ] Confirm every online hiring signal links to its original source and does not claim the opening is current.
- [ ] Confirm faculty, funding, hiring, and GPA evidence remain separate labels.
- [ ] Test the correction/removal email link.
- [ ] Publish the final privacy and acceptable-use wording after legal review appropriate to the launch location.
