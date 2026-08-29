"""Fail fast when launch-critical ScholarRadar settings or services are unsafe."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from db import get_db_connection
from settings import setting


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    message: str


def _looks_placeholder(value: str) -> bool:
    lowered = value.strip().casefold()
    return not lowered or any(
        marker in lowered
        for marker in ("replace-with", "example.com", "user:password", "your-")
    )


def check_configuration(values: dict[str, str] | None = None) -> list[Check]:
    """Return checks without exposing any secret values."""
    read = (lambda name: values.get(name, "")) if values is not None else setting
    checks: list[Check] = []

    app_env = read("APP_ENV").strip().casefold()
    checks.append(
        Check("PASS" if app_env == "production" else "FAIL", "Production mode",
              "APP_ENV is production." if app_env == "production" else "Set APP_ENV=production.")
    )
    bypass = read("DEV_AUTH_BYPASS").strip().casefold() in {"1", "true", "yes", "on"}
    checks.append(
        Check("FAIL" if bypass else "PASS", "Development login",
              "Development authentication is disabled." if not bypass else "Set DEV_AUTH_BYPASS=false.")
    )

    database_url = read("DATABASE_URL").strip()
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").casefold()
    local_database = host in {"", "localhost", "127.0.0.1", "::1"}
    query = parse_qs(parsed.query)
    tls_required = query.get("sslmode", [""])[0].casefold() in {"require", "verify-ca", "verify-full"}
    if _looks_placeholder(database_url) or local_database:
        checks.append(Check("FAIL", "Managed PostgreSQL", "Use a real managed PostgreSQL connection, not localhost or a placeholder."))
    elif not tls_required:
        checks.append(Check("FAIL", "Database TLS", "Require TLS in DATABASE_URL with sslmode=require or stronger."))
    else:
        checks.append(Check("PASS", "Managed PostgreSQL", "A non-local TLS PostgreSQL URL is configured."))

    openalex_key = read("OPENALEX_API_KEY")
    checks.append(
        Check(
            "FAIL" if _looks_placeholder(openalex_key) else "PASS",
            "OpenAlex API key",
            "Configure OPENALEX_API_KEY as a deployment secret."
            if _looks_placeholder(openalex_key)
            else "Configured without printing its value.",
        )
    )

    brave_ready = not _looks_placeholder(read("BRAVE_SEARCH_API_KEY"))
    searxng_url = read("SEARXNG_URL").strip()
    searxng_parsed = urlparse(searxng_url)
    searxng_ready = (
        not _looks_placeholder(searxng_url)
        and searxng_parsed.scheme in {"http", "https"}
        and bool(searxng_parsed.hostname)
    )
    checks.append(
        Check(
            "PASS" if brave_ready or searxng_ready else "FAIL",
            "Web-search provider",
            "At least one production provider is configured without exposing its secret."
            if brave_ready or searxng_ready
            else "Configure a private SEARXNG_URL or BRAVE_SEARCH_API_KEY.",
        )
    )

    email = read("CONTACT_EMAIL").strip()
    valid_email = "@" in email and "." in email.rsplit("@", 1)[-1] and not _looks_placeholder(email)
    checks.append(
        Check("PASS" if valid_email else "FAIL", "Correction contact",
              "A public correction contact is configured." if valid_email else "Set CONTACT_EMAIL to a monitored, non-placeholder inbox.")
    )
    return checks


def check_runtime() -> list[Check]:
    checks: list[Check] = []
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_regclass('public.users') AS users,
                        to_regclass('public.site_admins') AS site_admins,
                        to_regclass('public.radar_jobs') AS radar_jobs,
                        to_regclass('public.radar_worker_heartbeats') AS heartbeats,
                        to_regclass('public.radar_topic_professor_papers') AS exact_evidence,
                        to_regclass('public.web_search_cache') AS search_cache,
                        to_regclass('public.web_search_provider_health') AS search_health
                    """
                )
                schema = cursor.fetchone() or {}
                missing = [name for name, value in schema.items() if not value]
                checks.append(Check("FAIL" if missing else "PASS", "Database schema",
                                    f"Missing tables: {', '.join(missing)}." if missing else "Required launch tables exist."))
                if missing:
                    return checks

                cursor.execute(
                    "SELECT COUNT(*) AS total FROM site_admins WHERE admin_role = 'owner' AND revoked_at IS NULL"
                )
                owners = int((cursor.fetchone() or {}).get("total") or 0)
                checks.append(Check("PASS" if owners == 1 else "FAIL", "Owner account",
                                    "Exactly one active owner exists." if owners == 1 else f"Expected one active owner; found {owners}."))

                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM radar_worker_heartbeats
                    WHERE stopped_at IS NULL AND last_seen_at > NOW() - INTERVAL '10 minutes'
                    """
                )
                workers = int((cursor.fetchone() or {}).get("total") or 0)
                checks.append(Check("PASS" if workers >= 1 else "FAIL", "Background worker",
                                    f"{workers} healthy worker(s) reported recently." if workers else "No worker heartbeat in the last ten minutes."))

                cursor.execute("SELECT COUNT(*) AS total FROM radar_topics WHERE discovery_version < 3")
                old_topics = int((cursor.fetchone() or {}).get("total") or 0)
                checks.append(Check("WARN" if old_topics else "PASS", "Exact-evidence rebuild",
                                    f"{old_topics} topic(s) still need a version-3 rebuild." if old_topics else "All existing topics use discovery version 3."))

                cursor.execute("SELECT COUNT(*) AS total FROM radar_jobs WHERE status = 'failed'")
                failed_jobs = int((cursor.fetchone() or {}).get("total") or 0)
                checks.append(Check("WARN" if failed_jobs else "PASS", "Failed background jobs",
                                    f"{failed_jobs} failed job(s) need staff review." if failed_jobs else "No failed jobs are waiting."))
    except Exception as error:
        checks.append(Check("FAIL", "Database connection", f"Could not complete runtime checks: {type(error).__name__}."))
    return checks


def main() -> int:
    checks = check_configuration()
    if not any(check.level == "FAIL" for check in checks):
        checks.extend(check_runtime())

    for check in checks:
        print(f"{check.level:4}  {check.name}: {check.message}")
    failures = sum(check.level == "FAIL" for check in checks)
    warnings = sum(check.level == "WARN" for check in checks)
    print(f"\nProduction readiness: {failures} failure(s), {warnings} warning(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
