from __future__ import annotations

from typing import Any, Callable

from db import (
    fail_radar_run,
    fetch_radar_prospects,
    fetch_radar_results,
    fetch_radar_run,
    finish_radar_run,
    mark_radar_prospects_checked,
    save_radar_prospects,
    start_or_reuse_radar_run,
    update_radar_run,
)
from ingestion.check_grants import check_and_save_grants
from ingestion.fetch_prof import fetch_professors_by_keywords
from ingestion.parse_hiring_signals import scan_hiring_signals
from ingestion.taxonomy import normalize_taxonomy

ProgressCallback = Callable[[str, int, dict[str, int]], None]


def execute_radar(
    query: str,
    target_professors: int = 25,
    web_check_limit: int | None = None,
    requested_by: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run one bounded, targeted radar scan or reuse its recent cached result."""
    target_professors = int(target_professors)
    default_web_limits = {10: 10, 25: 12, 50: 15, 100: 20}
    web_check_limit = int(web_check_limit or default_web_limits.get(target_professors, 12))
    run, reused = start_or_reuse_radar_run(
        query, target_professors, web_check_limit, requested_by
    )
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
        progress("Understanding the research topic", 5)
        taxonomy = normalize_taxonomy(query)
        topic = str(taxonomy.get("topic_name") or query)

        progress("Finding recent relevant research", 15)
        discovery = fetch_professors_by_keywords(
            taxonomy, target_professors=target_professors
        )
        save_radar_prospects(run_id, discovery["prospects"])
        progress(
            "Identifying potential professors",
            35,
            {
                "professors_found": discovery["professors"],
                "papers_found": discovery["papers"],
            },
        )

        progress("Checking current public grants", 45)
        professor_ids = list(discovery.get("professor_ids") or [])
        enrichment_ids = professor_ids[:web_check_limit]
        grants = check_and_save_grants(taxonomy, professor_ids=enrichment_ids)
        mark_radar_prospects_checked(run_id, enrichment_ids, "grants")
        progress(
            "Locating laboratory pages and hiring announcements",
            55,
            {"grants_added": grants["grants_added"]},
        )

        signals = scan_hiring_signals(
            domain_name=topic,
            professor_ids=enrichment_ids,
            progress_callback=progress,
            radar_run_id=run_id,
        )
        mark_radar_prospects_checked(
            run_id, list(signals.get("checked_professor_ids") or []), "public"
        )
        final_stage = (
            "Time budget reached; saving partial results"
            if signals.get("timed_out")
            else "Saving evidence for moderation"
        )
        progress(
            final_stage,
            95,
            {
                "professors_checked": signals["professors_checked"],
                "signals_added": signals["signals_added"],
            },
        )
        finish_radar_run(run_id, topic, counters)
        progress("Complete", 100)
        return {
            "run": fetch_radar_run(run_id),
            "cached": False,
            "results": fetch_radar_results(run_id),
            "professors": fetch_radar_prospects(run_id),
        }
    except Exception as error:
        fail_radar_run(run_id, str(error))
        if progress_callback:
            progress_callback("Radar scan failed", max(1, int(fetch_radar_run(run_id)["progress"])), counters)
        raise
