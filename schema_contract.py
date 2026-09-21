"""Single source of truth for the minimum runtime database schema.

Keep this contract in sync with db.sql whenever code begins relying on a new
runtime table or column.  It is intentionally stricter than a basic database
connection check so an old PostgreSQL volume cannot be treated as application-
ready after the Python code has moved forward.
"""

from __future__ import annotations

from typing import Any


REQUIRED_TABLES = frozenset(
    {
        "admin_audit_log",
        "ai_usage_daily",
        "college_scorecard_institutions",
        "faculty_appointments",
        "faculty_directories",
        "faculty_directory_memberships",
        "faculty_page_candidates",
        "faculty_verification_evidence",
        "fundings",
        "hiring_signals",
        "institution_aliases",
        "institution_domains",
        "institution_memberships",
        "institution_units",
        "institutions",
        "ollama_extraction_runs",
        "opportunities",
        "opportunity_sources",
        "paper_abstract_evidence",
        "paper_research_categories",
        "papers",
        "professor_external_identities",
        "professor_identity_review_queue",
        "professor_name_aliases",
        "professor_papers",
        "professor_profiles",
        "professor_publication_sources",
        "professor_research_categories",
        "professor_research_interests",
        "professor_topic_grant_checks",
        "professors",
        "program_admission_requirements",
        "graduate_programs",
        "professor_graduate_programs",
        "program_admission_sources",
        "program_admission_evidence",
        "radar_jobs",
        "radar_run_professors",
        "radar_run_results",
        "radar_runs",
        "radar_topic_professor_papers",
        "radar_topic_professors",
        "radar_topics",
        "radar_worker_heartbeats",
        "reports",
        "research_categories",
        "role_verifications",
        "roster_member_candidates",
        "scholar_publication_review_queue",
        "site_admins",
        "sponsorships",
        "users",
        "web_search_cache",
        "web_search_provider_health",
    }
)

# These are migration markers for the newest code paths.  Checking every
# historical column would make this noisy without improving stale-schema
# detection; these fields specifically distinguish the current schema from the
# older volume that predates publication/research classification v2.
REQUIRED_COLUMNS = {
    "program_admission_requirements": frozenset({"program_id","gpa_scale","gpa_basis","manual_override","requirement_level"}),
    "graduate_programs": frozenset({"program_name","degree_type","verified_at"}),
    "papers": frozenset(
        {
            "abstract_checked_at",
            "abstract_source_url",
            "abstract_status",
            "classification_version",
            "classified_at",
            "metadata_status",
            "raw_citation",
            "source_evidence",
            "source_key",
            "source_type",
            "source_url",
        }
    ),
    "professors": frozenset(
        {
            "publication_checked_at",
            "publication_discovery_version",
            "publication_status",
            "research_profile_checked_at",
            "research_profile_confidence",
            "research_profile_primary_field",
            "research_profile_source_url",
            "research_profile_status",
            "research_profile_version",
        }
    ),
    "radar_jobs": frozenset({"program_id","paper_id"}),
    "radar_topic_professors": frozenset(
        {"evidence_basis", "interest_source_url", "interest_summary"}
    ),
    "radar_topics": frozenset({"research_category_id"}),
    "roster_member_candidates": frozenset({"staff_overrides", "validation_version"}),
}


def schema_gaps(cursor: Any) -> tuple[list[str], dict[str, list[str]]]:
    """Return missing runtime tables and columns for the connected database."""
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )
    existing_tables = {str(row["tablename"]) for row in cursor.fetchall()}
    missing_tables = sorted(REQUIRED_TABLES - existing_tables)

    cursor.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    )
    columns: dict[str, set[str]] = {}
    for row in cursor.fetchall():
        columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))

    missing_columns = {
        table: sorted(required - columns.get(table, set()))
        for table, required in REQUIRED_COLUMNS.items()
        if required - columns.get(table, set())
    }
    return missing_tables, missing_columns
