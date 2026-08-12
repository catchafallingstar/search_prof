# ScholarRadar architecture and file guide

## 1. The whole system in one picture

ScholarRadar has four layers:

```text
Browser
  -> Streamlit pages (app.py and pages/)
       -> shared UI and authentication (ui.py and auth.py)
            -> database functions (db.py)
                 -> PostgreSQL tables (db.sql)

Scheduled/manual radar command (scripts/run_radar.py)
  -> ingestion modules
       -> public APIs and public web pages
       -> PostgreSQL
```

The public homepage reads approved openings. A signed-in submitter requests proof of a faculty or university role. An administrator manually checks the official evidence. Only after role approval can the user submit an opening, and the opening has its own second moderation step.

## 2. The important distinction: professor is not moderator

Two independent role systems are stored in different places.

### Academic/business role

`users.account_role` describes what a user may do as a participant:

- `applicant`: the default user.
- `professor`: faculty role was approved.
- `institution_admin`: university recruiter/administrator role was approved.

These roles control whether a person may submit an opening. They do not grant power over other users.

### Site authority

`site_admins.admin_role` describes authority over the website:

- `owner`: the single top-level administrator. Can moderate and manage moderators.
- `moderator`: can approve/reject role requests and openings, but cannot create another moderator.

This is where `moderator` is written and called:

1. `db.sql` defines `site_admins` and permits the value `moderator`.
2. `scripts/bootstrap_owner.py` creates the one `owner`; it does not create a moderator.
3. `pages/4_Admin_accounts.py` calls `grant_site_moderator(...)` when the owner presses the grant button.
4. `db.py` verifies that the caller is the owner, then writes `admin_role = 'moderator'` into `site_admins`.
5. `pages/3_Admin_review.py` calls `require_site_admin()` before displaying the moderation queue.
6. `auth.py` calls `get_site_admin(...)`. A row for either an active owner or active moderator passes.
7. When a decision is saved, `db.review_item(...)` performs the administrator check again. This second database-layer check prevents someone from bypassing the page and calling the write function directly.

The owner account is created outside the UI so a visitor cannot make themselves owner. Later, the owner can add a moderator only after that person has signed in once and has a `users` row.

## 3. Main user flows

### Browse flow

```text
app.py
  -> database_is_ready()
  -> fetch_active_opportunities()
  -> ui.render_opportunity()
```

Only active, unexpired database opportunities are returned. When PostgreSQL is unavailable, the homepage intentionally shows preview data so the design can still be viewed.

### Professor verification and opening submission

```text
pages/1_Post_an_opening.py
  -> auth.require_user()
       -> local development identity OR real OIDC identity
       -> db.upsert_authenticated_user()
  -> submit_professor_profile() or submit_institution_membership()
       -> pending verification row
  -> administrator reviews it
  -> verified user submits an opportunity
       -> pending opportunity row
  -> administrator reviews it
       -> active public opportunity, expiring after 90 days
```

A school email alone is not accepted as proof. The request includes name, title, department, institution, and an official university directory/profile URL. A human administrator opens that official evidence and compares it with the authenticated identity. Rejected or expired credentials can be corrected and resubmitted. Verification lasts one year in the current policy.

### Moderation flow

```text
pages/3_Admin_review.py
  -> auth.require_site_admin()
       -> db.get_site_admin()
  -> db.fetch_pending_reviews()
  -> owner/moderator presses Approve or Reject
  -> db.review_item()
       -> db._require_active_admin() checks authority again
       -> updates the requested record
       -> writes admin_audit_log
```

Profile approval creates or links a `professors` record and changes the user's academic role to `professor`. Institution membership approval changes it to `institution_admin`. Opportunity approval changes the opening to `active` and gives it a 90-day expiration.

### Owner manages moderators

```text
pages/4_Admin_accounts.py
  -> auth.require_site_admin(owner_only=True)
  -> db.list_users_for_admin()
  -> db.grant_site_moderator() or db.revoke_site_moderator()
       -> owner check inside db.py
       -> site_admins change
       -> audit-log entry
```

The page is owner-only. Hiding a link is not the security control; the checks in `auth.py` and `db.py` are the controls.

### Radar ingestion flow

```text
scripts/run_radar.py
  -> ingestion/taxonomy.py
  -> ingestion/fetch_prof.py
  -> ingestion/check_grants.py
  -> ingestion/parse_hiring_signals.py
       -> homepagefinder.py
       -> socialradar.py
       -> matchers.py
  -> PostgreSQL professors, papers, fundings, and hiring_signals
```

Radar data is evidence, not a guaranteed vacancy. A qualifying public hiring quote creates a `pending` public-signal opportunity. An owner or moderator must review it before it becomes `active` and searchable. Papers and grants alone never create a public opening, and the radar never invents a GPA policy.

## 4. Database tables

- `institutions`: normalized university names and optional domain/country information.
- `users`: authenticated identities and academic/business roles.
- `site_admins`: owner/moderator authority, separate from the user role.
- `admin_audit_log`: immutable-style history of owner and moderator actions.
- `professors`: radar's professor entities and scores.
- `professor_profiles`: a user's claim to be a faculty member and its review status.
- `institution_memberships`: a user's claim to represent a university.
- `role_verifications`: each evidence/review attempt and reviewer notes.
- `opportunities`: submitted or discovered openings and public status.
- `opportunity_sources`: evidence and provenance for an opportunity.
- `sponsorships`: future paid-placement records. Paid records must remain labeled and must not buy verification.
- `reports`: user reports about inaccurate opportunities.
- `papers`: OpenAlex paper records.
- `professor_papers`: many-to-many link between professors and papers.
- `fundings`: public grant evidence linked to professors.
- `hiring_signals`: time-bounded public web/social hiring text.

`db.sql` contains constraints and indexes as well as tables. Those constraints reject invalid status values even if a future programming error sends one.

## 5. File-by-file guide

### Root application files

- `app.py`: public homepage. Builds search filters, obtains active opportunities, and renders cards. Falls back to sample records if the database is not ready.
- `auth.py`: identity and authorization boundary. Uses an explicit fake identity for local development, real Streamlit OIDC in production, and checks `site_admins` for protected pages.
- `db.py`: the only general database access layer. Opens PostgreSQL connections, parameterizes SQL inputs, reads public data, saves verification/opening submissions, enforces admin authority, and logs privileged actions.
- `db.sql`: declarative PostgreSQL schema. Run it before the app. It is idempotent for a fresh/current schema and deliberately is not executed automatically by the web request process.
- `ui.py`: shared page configuration, navigation, styling, safe URL checks, preview records, filters, and opportunity-card rendering.
- `requirements.txt`: pinned major-version ranges for Streamlit, PostgreSQL, dotenv, HTTP, HTML parsing, and search dependencies.
- `compose.yaml`: local PostgreSQL 16 service, persistent Docker volume, port mapping, health check, and first-start schema mount.
- `Makefile`: short names for setup, start, test, database, schema, owner, and radar commands.
- `.env`: actual local development settings. Ignored by Git. Edit the placeholders.
- `.env.example`: safe template for another developer's local `.env`.
- `.env.production.example`: documents production environment keys without real secrets.
- `.gitignore`: prevents local secrets, virtual environments/caches, logs, and editor files from being committed.
- `AUDIT.md`: earlier detailed code/security review and design rationale. It is documentation, not runtime code.

### Streamlit pages

- `pages/1_Post_an_opening.py`: authenticated two-stage submitter workflow: role verification, then opportunity submission.
- `pages/2_Verification.py`: public explanation of why email alone is insufficient and what evidence/review is used.
- `pages/3_Admin_review.py`: protected queue for owners and moderators. Approves/rejects profiles, memberships, and openings.
- `pages/4_Admin_accounts.py`: protected owner-only moderator management and audit-log view.

The numeric prefixes control Streamlit's page order. The custom navigation in `ui.py` supplies the public-facing links, including a Staff link; protected URLs still enforce authorization if opened directly. The owner sees a moderator-management link from the review page.

### Ingestion modules

- `ingestion/__init__.py`: marks the folder as an importable Python package.
- `ingestion/taxonomy.py`: maps a research query to OpenAlex topic/field metadata.
- `ingestion/fetch_prof.py`: downloads relevant OpenAlex works, selects probable principal investigators, and saves professor/paper relationships.
- `ingestion/check_grants.py`: checks NSF award data, matches researchers/institutions, de-duplicates grants, and saves funding evidence.
- `ingestion/homepagefinder.py`: finds likely faculty homepages and blocks unsafe/private-network URLs before fetching them.
- `ingestion/socialradar.py`: checks public social/search sources for possible hiring text, with throttling and identity matching.
- `ingestion/matchers.py`: pure text helpers: signal phrases, roles, funding terms, stale dates, cleaned quotes, and hashes.
- `ingestion/parse_hiring_signals.py`: orchestrates homepage/social checks, validates evidence, computes confidence, and saves time-limited hiring signals.

These modules are not unnecessary duplicates. `matchers.py` contains reusable pure logic; `homepagefinder.py` owns URL discovery/safety; `socialradar.py` owns social-source collection; and `parse_hiring_signals.py` coordinates them.

### Operational scripts

- `scripts/__init__.py`: lets scripts run with `python -m scripts.name` and keeps imports consistent.
- `scripts/setup_ubuntu.sh`: creates `.venv`, installs packages, starts PostgreSQL, applies schema, creates the owner, and runs tests.
- `scripts/start_local.sh`: loads `.env`, starts PostgreSQL, activates `.venv`, and runs Streamlit.
- `scripts/apply_schema.sh`: waits for the database and applies `db.sql` with stop-on-error behavior.
- `scripts/test_local.sh`: runs unit, database, and headless page smoke tests.
- `scripts/bootstrap_owner.py`: creates/confirms the one owner. It refuses to replace a different owner.
- `scripts/smoke_test_db.py`: verifies important tables and exactly one active owner.
- `scripts/smoke_test_streamlit.py`: executes every Streamlit page without opening a browser and fails on uncaught page exceptions.
- `scripts/run_radar.py`: command-line entry point for a full research-area radar scan.
- `scripts/inspect_signals.py`: developer/admin command for reading recently stored signals; it is useful for diagnosis but is not part of a web request.

The old standalone `ins-db.py` from the original submission is not needed in this corrected project. Its responsibilities are covered by `db.sql`, the controlled submission functions in `db.py`, and the explicit ingestion scripts. Keeping an ad-hoc insert script would create a second, easier-to-misuse path into the same tables.

The original `ingestion/i18n.py` is also omitted from this English-only MVP. It was not called by the new UI and contained duplicate translation keys. Add a tested localization layer later if multilingual UI becomes a real requirement; an unused translation dictionary currently adds maintenance cost without changing the site.

### Tests and Streamlit configuration

- `tests/test_matchers.py`: unit tests for signal extraction and stale-text rejection.
- `tests/test_urls.py`: unit tests for safe public HTTP URL handling.
- `tests/test_taxonomy.py`: regression tests for coherent topic matching.
- `tests/test_grants.py`: full-name, institution, and scan-scope grant checks.
- `tests/test_identity.py`: full-name checks for homepage and social evidence.
- `tests/test_homepage_parser.py`: HTML block parsing and redirect safety checks.
- `tests/test_radar_contract.py`: direct-work relevance and pending-moderation contract.
- `tests/test_authorization_contract.py`: tests verification expiry and confirms the two-role schema contract.
- `.streamlit/secrets.example.toml`: template for real OIDC login and production database secrets.

## 6. Which files run when?

For ordinary browsing, Python loads `app.py`, which imports `auth.py`, `db.py`, and `ui.py`. It does not load the ingestion pipeline.

For a Streamlit subpage, Streamlit executes that page as the entry file; the page imports the shared modules it needs. The numbered pages do not call one another.

For radar collection, you explicitly run `python -m scripts.run_radar ...`. It imports ingestion modules and `db.py`; it does not run Streamlit.

For setup/testing, the shell scripts orchestrate Docker and Python. They are operations tooling, not part of the deployed web UI.

## 7. Security boundaries to preserve

- Never enable development auth in production.
- Never infer professor status from an `.edu` address alone.
- Keep privileged checks inside `db.py`, even when the UI already checked.
- Never let an owner role be self-registered from a public page.
- Do not publish pending opportunities.
- Keep source URLs, evidence text, observed dates, expirations, and confidence labels for radar records.
- Keep sponsorship visually separate from verification and organic ranking.
- Treat web ingestion as untrusted input and retain private-network URL blocking.

## 8. Current MVP limits

This is a working MVP foundation, not the complete commercial service. Before a large public launch, add email/domain policy checks, rate limits, CAPTCHA or abuse controls, report-handling UI, automated expiry jobs, backups, observability, privacy/terms pages, and payment handling. ORCID can be added as supporting evidence but should not replace official-role review.
