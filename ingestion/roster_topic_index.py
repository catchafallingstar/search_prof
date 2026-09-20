from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from db import get_db_connection
from ingestion.research_classification import (
    CLASSIFICATION_VERSION,
    classify_text,
    rebuild_professor_profiles,
    resolve_category,
)


ROSTER_DISCOVERY_VERSION = 9
_STOP_WORDS = {
    "a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with",
    "technique", "techniques", "method", "methods", "study", "studies",
}


def _tokens(value: str) -> list[str]:
    return [
        token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in _STOP_WORDS and len(token) > 1
    ]


def score_paper(query: str, title: str, abstract: str = "") -> tuple[float, str]:
    """Return a transparent lexical score and the exact matching text.

    A paper must contain direct query evidence. Broad provider labels are not
    accepted as proof of relevance.
    """
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0, ""
    title_tokens = set(_tokens(title))
    abstract_tokens = set(_tokens(abstract))
    title_overlap = len(query_tokens & title_tokens) / len(query_tokens)
    abstract_overlap = len(query_tokens & abstract_tokens) / len(query_tokens)
    normalized_query = " ".join(_tokens(query))
    normalized_title = " ".join(_tokens(title))
    normalized_abstract = " ".join(_tokens(abstract))
    if normalized_query and normalized_query in normalized_title:
        return 100.0, title
    if normalized_query and normalized_query in normalized_abstract:
        return 85.0, abstract[:500]
    if title_overlap >= 0.75:
        return round(60.0 + 30.0 * title_overlap, 2), title
    if abstract_overlap >= 0.75:
        return round(45.0 + 25.0 * abstract_overlap, 2), abstract[:500]
    if len(query_tokens) == 1 and (title_overlap == 1.0 or abstract_overlap == 1.0):
        return (70.0, title) if title_overlap else (55.0, abstract[:500])
    return 0.0, ""


def index_rostered_topic(radar_topic_id: int) -> dict[str, int]:
    """Build one topic entirely from confirmed roster faculty and their papers."""
    category: dict[str, Any] | None = None
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM radar_topics WHERE id = %s", (radar_topic_id,))
            topic = cursor.fetchone()
            if not topic:
                raise RuntimeError("The radar topic no longer exists.")
            query = str(topic.get("requested_query") or topic.get("normalized_query") or "")
            category = resolve_category(query)
            cursor.execute(
                """
                SELECT p.id AS professor_id, paper.id AS paper_id, paper.title,
                       COALESCE(paper.abstract_text, '') AS abstract_text,
                       paper.publication_year, paper.doi, paper.source_url
                FROM professors p
                JOIN professor_papers pp ON pp.professor_id = p.id
                JOIN papers paper ON paper.id = pp.paper_id
                JOIN institutions i ON i.id = p.institution_id
                WHERE p.data_origin = 'OFFICIAL_DIRECTORY'
                  AND p.employment_status IN ('ACTIVE_CONFIRMED', 'EMERITUS_CONFIRMED')
                  AND p.faculty_status = 'VERIFIED'
                  AND EXISTS (
                          SELECT 1 FROM roster_member_candidates candidate
                          WHERE candidate.professor_id = p.id
                            AND candidate.validation_status IN ('PROFILE_VERIFIED', 'ROSTER_VERIFIED')
                      )
                  AND EXISTS (
                          SELECT 1 FROM faculty_directory_memberships membership
                          JOIN faculty_directories directory ON directory.id = membership.directory_id
                          WHERE membership.professor_id = p.id
                            AND membership.currently_listed = TRUE
                            AND directory.active = TRUE
                            AND directory.validation_status = 'APPROVED'
                      )
                  AND i.country_code = 'US'
                """
            )
            rows = list(cursor.fetchall())

            matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                classification = classify_text(
                    category, str(row.get("title") or ""),
                    str(row.get("abstract_text") or ""),
                )
                cursor.execute(
                    """INSERT INTO paper_research_categories
                       (paper_id,category_id,lexical_score,concept_score,combined_score,
                        decision,evidence_text,matched_terms,classification_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (paper_id,category_id) DO UPDATE SET
                         lexical_score=EXCLUDED.lexical_score,
                         concept_score=EXCLUDED.concept_score,
                         combined_score=EXCLUDED.combined_score,
                         decision=EXCLUDED.decision,evidence_text=EXCLUDED.evidence_text,
                         matched_terms=EXCLUDED.matched_terms,
                         classification_version=EXCLUDED.classification_version,
                         classified_at=NOW()""",
                    (row["paper_id"], category["id"], classification["lexical_score"],
                     classification["concept_score"], classification["combined_score"],
                     classification["decision"], classification["evidence_text"] or None,
                     classification["matched_terms"], CLASSIFICATION_VERSION),
                )
                if classification["decision"] in {"AUTO_ACCEPTED", "QWEN_ACCEPTED"}:
                    matches[int(row["professor_id"])].append(
                        {**row, "score": classification["combined_score"],
                         "matched_text": classification["evidence_text"]}
                    )

            ranked: list[tuple[float, int, list[dict[str, Any]], str, str, str]] = []
            for professor_id, evidence in matches.items():
                evidence.sort(
                    key=lambda item: (item["score"], item.get("publication_year") or 0),
                    reverse=True,
                )
                # A professor-topic relationship needs one strong paper or at
                # least two independently moderate papers. Identity matching
                # never turns a broad or adjacent paper into topic evidence.
                recent = [item for item in evidence
                          if item.get("publication_year") and
                          datetime.now().year - int(item["publication_year"]) <= 6]
                if not (
                    any(item["score"] >= 85 for item in recent)
                    or sum(1 for item in evidence if item["score"] >= 65) >= 2
                    or evidence[0]["score"] >= 92
                ):
                    continue
                if evidence[0]["score"] < 65 and sum(
                    1 for item in evidence if item["score"] >= 65
                ) < 2:
                    continue
                combined = min(100.0, evidence[0]["score"] + min(15, 3 * (len(evidence) - 1)))
                ranked.append((combined, professor_id, evidence, "PAPER", "", ""))

            # Professor-level research profiles are the fast discovery layer.
            # They may backstop any verified professor, including professors who
            # already have papers, but they never outrank strong direct-paper
            # evidence. This lets explicit interests and Qwen paper-title
            # summaries make a professor searchable before every paper has been
            # individually categorized.
            paper_ranked_ids = {int(item[1]) for item in ranked}
            cursor.execute(
                """SELECT p.id AS professor_id,
                          p.research_profile_status,
                          p.research_profile_confidence,
                          ARRAY_AGG(interest.display_interest
                                    ORDER BY interest.confidence DESC,
                                             interest.display_interest) AS interests,
                          (ARRAY_AGG(interest.source_url
                                     ORDER BY interest.confidence DESC,
                                              interest.checked_at DESC))[1] AS source_url
                   FROM professors p
                   JOIN professor_research_interests interest
                     ON interest.professor_id=p.id
                   JOIN institutions institution ON institution.id=p.institution_id
                   WHERE p.data_origin='OFFICIAL_DIRECTORY'
                     AND p.faculty_status='VERIFIED'
                     AND p.employment_status IN ('ACTIVE_CONFIRMED','EMERITUS_CONFIRMED')
                     AND institution.country_code='US'
                     AND p.research_profile_status IN (
                         'OFFICIAL_INTERESTS','PAPER_DERIVED',
                         'BIOGRAPHY_DERIVED','MANUAL_REVIEWED'
                     )
                     AND EXISTS (
                         SELECT 1 FROM faculty_directory_memberships membership
                         JOIN faculty_directories directory
                           ON directory.id=membership.directory_id
                         WHERE membership.professor_id=p.id
                           AND membership.currently_listed=TRUE
                           AND directory.active=TRUE
                           AND directory.validation_status='APPROVED'
                     )
                   GROUP BY p.id"""
            )
            for interest_row in cursor.fetchall():
                professor_id = int(interest_row["professor_id"])
                if professor_id in paper_ranked_ids:
                    continue
                labels = [str(value) for value in (interest_row.get("interests") or [])]
                summary = " • ".join(dict.fromkeys(labels))
                classification = classify_text(category, summary, "")
                if classification["decision"] != "AUTO_ACCEPTED":
                    continue
                status = str(interest_row.get("research_profile_status") or "")
                cap = {
                    "OFFICIAL_INTERESTS": 62.0,
                    "MANUAL_REVIEWED": 62.0,
                    "PAPER_DERIVED": 58.0,
                    "BIOGRAPHY_DERIVED": 52.0,
                }.get(status, 45.0)
                confidence = float(interest_row.get("research_profile_confidence") or 0)
                interest_score = min(
                    cap,
                    round(float(classification["combined_score"]) * (0.55 + 0.15 * confidence), 2),
                )
                ranked.append((
                    interest_score, professor_id, [],
                    "RESEARCH_INTEREST", summary,
                    str(interest_row.get("source_url") or ""),
                ))
            ranked.sort(key=lambda item: item[0], reverse=True)
            ranked = ranked[: int(topic.get("desired_results") or 100)]

            cursor.execute(
                "UPDATE radar_topic_professors SET is_current_match = FALSE WHERE radar_topic_id = %s",
                (radar_topic_id,),
            )
            cursor.execute(
                "UPDATE radar_topic_professor_papers SET is_current_match = FALSE WHERE radar_topic_id = %s",
                (radar_topic_id,),
            )
            paper_count = 0
            for rank, (combined, professor_id, evidence, evidence_basis,
                       interest_summary, interest_source_url) in enumerate(ranked, start=1):
                latest = (max(evidence, key=lambda item: item.get("publication_year") or 0)
                          if evidence else {})
                latest_url = ((f"https://doi.org/{latest['doi']}" if latest.get("doi")
                               else str(latest.get("source_url") or ""))
                              if latest else "")
                cursor.execute(
                    """
                    INSERT INTO radar_topic_professors (
                        radar_topic_id, professor_id, result_rank, research_score,
                        matching_papers, latest_paper_title, latest_paper_year,
                        latest_paper_url, evidence_basis, interest_summary,
                        interest_source_url, is_current_match
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (radar_topic_id, professor_id) DO UPDATE SET
                        result_rank = EXCLUDED.result_rank,
                        research_score = EXCLUDED.research_score,
                        matching_papers = EXCLUDED.matching_papers,
                        latest_paper_title = EXCLUDED.latest_paper_title,
                        latest_paper_year = EXCLUDED.latest_paper_year,
                        latest_paper_url = EXCLUDED.latest_paper_url,
                        evidence_basis = EXCLUDED.evidence_basis,
                        interest_summary = EXCLUDED.interest_summary,
                        interest_source_url = EXCLUDED.interest_source_url,
                        is_current_match = TRUE, last_matched_at = NOW()
                    """,
                    (radar_topic_id, professor_id, rank, combined, len(evidence),
                     latest.get("title"), latest.get("publication_year"), latest_url,
                     evidence_basis, interest_summary or None,
                     interest_source_url or None),
                )
                for item in evidence:
                    cursor.execute(
                        """
                        INSERT INTO radar_topic_professor_papers (
                            radar_topic_id, professor_id, paper_id, relevance_score,
                            matched_query, evidence_method, matched_text, is_current_match
                        ) VALUES (%s, %s, %s, %s, %s, 'DIRECT_PAPER_TEXT', %s, TRUE)
                        ON CONFLICT (radar_topic_id, professor_id, paper_id) DO UPDATE SET
                            relevance_score = EXCLUDED.relevance_score,
                            matched_query = EXCLUDED.matched_query,
                            evidence_method = EXCLUDED.evidence_method,
                            matched_text = EXCLUDED.matched_text,
                            is_current_match = TRUE, last_matched_at = NOW()
                        """,
                        (radar_topic_id, professor_id, item["paper_id"], item["score"],
                         query, item["matched_text"]),
                    )
                    paper_count += 1

            match_count = len(ranked)
            cursor.execute(
                """
                UPDATE radar_topics
                SET normalized_topic = %s, research_category_id = %s,
                    discovery_version = %s, candidates_seen = %s,
                    verified_count = %s, papers_found = %s,
                    sources_exhausted = TRUE,
                    status = CASE WHEN %s > 0 THEN 'ready' ELSE 'partial' END,
                    last_error = NULL, last_indexed_at = NOW(),
                    next_refresh_at = NOW() + INTERVAL '30 days', updated_at = NOW()
                WHERE id = %s
                """,
                (category["canonical_name"], category["id"], ROSTER_DISCOVERY_VERSION,
                 match_count, match_count, paper_count,
                 match_count, radar_topic_id),
            )
    rebuild_professor_profiles(list(matches))
    return {"professors_matched": len(ranked), "papers_matched": paper_count}
