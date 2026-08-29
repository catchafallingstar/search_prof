from __future__ import annotations

import time
from typing import Any, Callable

from db import (
    fail_radar_run,
    fetch_radar_prospects,
    fetch_radar_results,
    fetch_radar_run,
    finish_radar_run,
    mark_radar_prospects_checked,
    prioritize_professors_for_enrichment,
    save_radar_prospects,
    start_or_reuse_radar_run,
    update_radar_run,
)
from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.hiring_discovery import discover_hiring_first_leads
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy
from ingestion.verify_faculty import (
    get_cached_faculty_decisions,
    verify_faculty_candidates,
)
from settings import setting, setting_int

ProgressCallback = Callable[[str, int, dict[str, int]], None]


def execute_radar(
    query: str,
    target_professors: int = 25,
    web_check_limit: int | None = None,
    requested_by: int | None = None,
    progress_callback: ProgressCallback | None = None,
    continue_partial: bool = False,
) -> dict[str, Any]:
    """Run one bounded, targeted radar scan or reuse its recent cached result."""
    target_professors = int(target_professors)
    default_web_limits = {10: 10, 25: 12, 50: 15, 100: 20}
    web_check_limit = int(web_check_limit or default_web_limits.get(target_professors, 12))
    run, reused = start_or_reuse_radar_run(
        query, target_professors, web_check_limit, requested_by
    )
    continued_partial = False
    if (
        continue_partial
        and reused
        and run["status"] == "completed"
        and int(run.get("professors_found") or 0) < target_professors
        and int(run.get("faculty_identities_checked") or 0)
            < int(run.get("candidates_ranked") or 0)
    ):
        # A completed bounded pass is not the end of a large search. Repeating
        # the same search creates a continuation pass; current positive and
        # negative identity decisions are reused below, so work resumes at the
        # first unchecked candidate instead of returning the same partial list.
        run, reused = start_or_reuse_radar_run(
            query,
            target_professors,
            web_check_limit,
            requested_by,
            force_new=True,
        )
        continued_partial = True
    run_id = int(run["id"])
    if reused:
        if progress_callback:
            message = "Using recent cached radar results" if run["status"] == "completed" else "This radar scan is already running"
            progress_callback(message, int(run["progress"]), {})
        return {
            "run": run,
            "cached": True,
            "results": fetch_radar_results(run_id),
            "professors": fetch_radar_prospects(run_id),
        }

    counters = {
        "professors_found": 0,
        "papers_found": 0,
        "candidates_ranked": 0,
        "faculty_identities_checked": 0,
        "professors_checked": 0,
        "grants_added": 0,
        "signals_added": 0,
    }

    def progress(stage: str, percent: int, updates: dict[str, int] | None = None) -> None:
        if updates:
            counters.update(updates)
        update_radar_run(run_id, stage, percent, **counters)
        if progress_callback:
            progress_callback(stage, percent, dict(counters))

    try:
        if not setting("OPENALEX_API_KEY").strip():
            raise RuntimeError(
                "The live radar needs an OpenAlex API key. Create a free key at "
                "https://openalex.org/settings/api, add OPENALEX_API_KEY to .env "
                "(or Streamlit Secrets in production), then restart Streamlit."
            )
        progress("Understanding the research topic", 5)
        taxonomy = normalize_taxonomy(query)
        topic = str(taxonomy.get("topic_name") or query)

        progress("Finding recent relevant research", 15)
        discovery = fetch_professors_by_keywords(
            taxonomy, target_professors=target_professors
        )
        ranked_ids = list(discovery.get("professor_ids") or [])
        progress(
            "Searching first for explicit recruiting pages",
            22,
            {
                "papers_found": discovery["papers"],
                "candidates_ranked": len(ranked_ids),
            },
        )
        hiring_leads = discover_hiring_first_leads(query, ranked_ids)

        # Explicit hiring leads are verified first. Then continue through the
        # research ranking in small batches until the requested verified result
        # maximum is reached, candidates are exhausted, or the time budget ends.
        verification_order = [
            *[value for value in ranked_ids if int(value) in hiring_leads],
            *[value for value in ranked_ids if int(value) not in hiring_leads],
        ]
        cached_decisions = get_cached_faculty_decisions(ranked_ids)
        verified_set = {int(value) for value in cached_decisions["verified_ids"]}
        decided_set = {int(value) for value in cached_decisions["decided_ids"]}
        identities_evaluated = len(decided_set)
        pending_ids = [
            value for value in verification_order if int(value) not in decided_set
        ]
        batch_size = setting_int("FACULTY_VERIFY_BATCH_SIZE", 8, 3, 30)
        default_verify_budgets = {10: 60, 25: 90, 50: 105, 100: 120}
        verify_budget = setting_int(
            "FACULTY_VERIFY_BUDGET_SECONDS",
            default_verify_budgets.get(target_professors, 90),
            15,
            300,
        )
        verify_deadline = time.monotonic() + verify_budget

        for offset in range(0, len(pending_ids), batch_size):
            if len(verified_set) >= target_professors or time.monotonic() >= verify_deadline:
                break
            batch = pending_ids[offset: offset + batch_size]
            progress(
                f"Verifying faculty identities ({len(verified_set)}/{target_professors} found)",
                min(42, 26 + int(16 * identities_evaluated / max(1, len(ranked_ids)))),
                {"faculty_identities_checked": identities_evaluated},
            )
            verification = verify_faculty_candidates(batch)
            identities_evaluated += int(verification.get("evaluated") or len(batch))
            verified_set.update(int(value) for value in verification["verified_ids"])

        selected_ids = [
            *[value for value in ranked_ids if int(value) in hiring_leads and int(value) in verified_set],
            *[value for value in ranked_ids if int(value) not in hiring_leads and int(value) in verified_set],
        ][:target_professors]
        selected_set = {int(value) for value in selected_ids}
        verified_prospects = [
            prospect
            for prospect in discovery["prospects"]
            if int(prospect["professor_id"]) in selected_set
        ]
        save_radar_prospects(run_id, verified_prospects)
        progress(
            "Faculty identities verified",
            35,
            {
                "professors_found": len(verified_prospects),
                "faculty_identities_checked": identities_evaluated,
            },
        )

        # Search results help prioritize identity candidates, but they do not
        # establish that a page belongs to the professor. Only the trusted-page
        # scanner below may create a public hiring signal.
        lead_signals_added = 0

        progress("Checking current public grants", 45)
        professor_ids = [int(value) for value in selected_ids]
        enrichment_ids = prioritize_professors_for_enrichment([
            *[value for value in professor_ids if value in hiring_leads],
            *[value for value in professor_ids if value not in hiring_leads],
        ])[:web_check_limit]
        grants = check_and_save_grants(taxonomy, professor_ids=enrichment_ids)
        mark_radar_prospects_checked(run_id, enrichment_ids, "grants")
        progress(
            "Locating laboratory pages and hiring announcements",
            55,
            {
                "grants_added": grants["grants_added"],
                "signals_added": lead_signals_added,
            },
        )

        def signal_progress(stage: str, percent: int, updates: dict[str, int]) -> None:
            adjusted = dict(updates)
            adjusted["signals_added"] = lead_signals_added + int(updates.get("signals_added", 0))
            progress(stage, percent, adjusted)

        signals = scan_hiring_signals(
            domain_name=topic,
            professor_ids=enrichment_ids,
            progress_callback=signal_progress,
            radar_run_id=run_id,
        )
        mark_radar_prospects_checked(
            run_id, list(signals.get("checked_professor_ids") or []), "public"
        )
        final_stage = (
            "Time budget reached; saving partial results"
            if signals.get("timed_out")
            else "Saving attributed public evidence"
        )
        progress(
            final_stage,
            95,
            {
                "professors_checked": signals["professors_checked"],
                "signals_added": lead_signals_added + signals["signals_added"],
            },
        )
        finish_radar_run(run_id, topic, counters)
        progress("Complete", 100)
        return {
            "run": fetch_radar_run(run_id),
            "cached": False,
            "continued": continued_partial,
            "results": fetch_radar_results(run_id),
            "professors": fetch_radar_prospects(run_id),
        }
    except Exception as error:
        fail_radar_run(run_id, str(error))
        if progress_callback:
            progress_callback("Radar scan failed", max(1, int(fetch_radar_run(run_id)["progress"])), counters)
        raise
