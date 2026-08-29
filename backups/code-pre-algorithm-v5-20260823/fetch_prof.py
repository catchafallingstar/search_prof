from datetime import datetime, timezone
import re
import math
import time
from typing import Any

import requests

from db import get_db_connection
from ingestion.taxonomy import phrase_covers_query
from settings import setting

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _request_openalex_works(params: dict[str, object]) -> list[dict[str, Any]]:
    """Fetch one OpenAlex page with a short, bounded rate-limit retry.

    A radar query can fan out into several related research phrases.  OpenAlex
    occasionally responds with 429 while those phrases are being checked.  A
    single throttled phrase must not erase pages already collected from the
    other phrases, so callers can skip this page after the bounded retries.
    """
    for attempt in range(3):
        response = requests.get(OPENALEX_WORKS_URL, params=params, timeout=20)
        if response.status_code != 429:
            response.raise_for_status()
            return list(response.json().get("results", []))
        if attempt < 2:
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait_seconds = min(3.0, max(0.5, float(retry_after)))
            except ValueError:
                wait_seconds = 0.75 * (attempt + 1)
            time.sleep(wait_seconds)
    return []


def _matches_ai_security_intent(phrase: str) -> bool:
    """Distinguish security of/with AI from unrelated phrases such as food security."""
    words = re.findall(r"[a-z0-9]+", phrase.casefold())
    ai_positions = [
        index
        for index, word in enumerate(words)
        if word == "ai"
        or (word == "artificial" and index + 1 < len(words) and words[index + 1] == "intelligence")
        or (word == "machine" and index + 1 < len(words) and words[index + 1] == "learning")
        or (word == "generative" and index + 1 < len(words) and words[index + 1] == "ai")
    ]
    risk_positions: list[int] = []
    off_target_security_modifiers = {
        "agricultural", "banking", "food", "homeland", "national", "physical", "supply"
    }
    for index, word in enumerate(words):
        is_risk_term = (
            word in {"adversarial", "attack", "attacks", "privacy", "robustness", "secure", "threat", "threats"}
            or word.startswith("vulnerab")
            or (word == "red" and index + 1 < len(words) and words[index + 1].startswith("team"))
        )
        if word in {"security", "cybersecurity"}:
            previous = words[index - 1] if index else ""
            is_risk_term = previous not in off_target_security_modifiers
        if is_risk_term:
            risk_positions.append(index)
    return bool(
        ai_positions
        and risk_positions
        and min(abs(ai - risk) for ai in ai_positions for risk in risk_positions) <= 6
    )


def _work_matches_query(work: dict[str, Any], raw_query: str) -> bool:
    """Keep direct-search works only when one descriptive phrase covers the query."""
    phrases = [str(work.get("title") or "")]
    for key in ("topics", "keywords"):
        for value in work.get(key) or []:
            phrases.append(
                str(value.get("display_name") or value.get("keyword") or "")
                if isinstance(value, dict)
                else str(value)
            )
    query_tokens = set(re.findall(r"[a-z0-9]+", raw_query.casefold()))
    is_ai_security_query = "security" in query_tokens and (
        "ai" in query_tokens or {"artificial", "intelligence"}.issubset(query_tokens)
    )
    if is_ai_security_query:
        return any(_matches_ai_security_intent(phrase) for phrase in phrases)
    return any(phrase_covers_query(raw_query, phrase) for phrase in phrases)


def _probable_pi_authorships(
    authorships: list[dict[str, Any]],
    matching_work_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Return broad researcher candidates; faculty verification happens later."""
    matching_work_counts = matching_work_counts or {}
    candidates = [
        item
        for item in authorships
        if item.get("is_corresponding")
        or item.get("author_position") in {"first", "last"}
        or matching_work_counts.get(str((item.get("author") or {}).get("id") or ""), 0) >= 2
    ]
    distinct: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        author_id = str((item.get("author") or {}).get("id") or "")
        if author_id and author_id not in seen:
            seen.add(author_id)
            distinct.append(item)
    return distinct


def _us_education_institution(authorship: dict[str, Any]) -> dict[str, Any] | None:
    for institution in authorship.get("institutions") or []:
        country = str(institution.get("country_code") or "").upper()
        institution_type = str(institution.get("type") or "").casefold()
        if country == "US" and institution_type in {"", "education"}:
            return institution
    return None


def _fetch_works(tax_meta: dict[str, Any], target_professors: int) -> list[dict[str, Any]]:
    current_year = datetime.now(timezone.utc).year
    start_year = current_year - 2
    topic_id = str(tax_meta.get("topic_id") or "").rsplit("/", 1)[-1]
    domain_name = str(tax_meta.get("topic_name") or tax_meta["raw_query"])
    works: list[dict[str, Any]] = []
    seen_work_ids: set[str] = set()
    # A wider paper pool is necessary for new faculty whose relevant work is
    # not ranked at the very top of a broad keyword search.  The result count
    # still bounds how many verified faculty cards are returned.
    work_budget = min(800, max(200, target_professors * 8))
    search_queries = list(tax_meta.get("search_queries") or [domain_name])
    # A single OpenAlex topic is useful for precision, but it is not sufficient
    # coverage for broad fields or common shorthand such as "biomed". Keep the
    # matched topic and the expanded text searches, then deduplicate works.
    sources: list[tuple[str, str]] = []
    if topic_id:
        sources.append(("topic", topic_id))
    sources.extend(("search", query) for query in search_queries)
    if len(sources) == 1:
        source_budgets = [work_budget]
    else:
        primary_budget = min(100, math.ceil(work_budget / 2))
        remaining = max(0, work_budget - primary_budget)
        secondary_budget = max(10, math.ceil(remaining / (len(sources) - 1)))
        source_budgets = [primary_budget, *([secondary_budget] * (len(sources) - 1))]

    for (source_kind, source_value), source_budget in zip(sources, source_budgets):
        page = 1
        source_added = 0
        while len(works) < work_budget and source_added < source_budget and page <= 5:
            page_size = min(100, source_budget)
            filters = [f"publication_year:{start_year}-{current_year}", "institutions.country_code:us"]
            params: dict[str, object] = {"per_page": page_size, "page": page}
            if source_kind == "topic":
                filters.append(f"topics.id:{source_value}")
            else:
                params["search"] = source_value
            params["filter"] = ",".join(filters)
            contact_email = setting("OPENALEX_EMAIL").strip()
            if contact_email:
                params["mailto"] = contact_email
            api_key = setting("OPENALEX_API_KEY").strip()
            if api_key:
                params["api_key"] = api_key

            raw_batch = _request_openalex_works(params)
            if not raw_batch:
                break
            batch = raw_batch if source_kind == "topic" else [
                work for work in raw_batch if _work_matches_query(work, source_value)
            ]
            for work in batch:
                work_id = str(work.get("id") or "")
                if work_id and work_id not in seen_work_ids:
                    seen_work_ids.add(work_id)
                    works.append(work)
                    source_added += 1
                    if len(works) >= work_budget or source_added >= source_budget:
                        break
            if len(raw_batch) < page_size:
                break
            page += 1
    return works


def fetch_professors_by_keywords(
    tax_meta: dict[str, Any],
    target_professors: int = 25,
) -> dict[str, Any]:
    """Discover and rank probable PIs from recent, relevant OpenAlex works."""
    target_professors = max(1, min(100, int(target_professors)))
    research_domain = str(tax_meta.get("topic_name") or tax_meta["raw_query"]).strip()
    try:
        works = _fetch_works(tax_meta, target_professors)
    except requests.RequestException as error:
        raise RuntimeError(f"OpenAlex works request failed: {error}") from error
    if not works:
        raise RuntimeError(
            "OpenAlex returned no usable research results. Please wait a minute "
            "and retry, or use a more specific research phrase."
        )

    matching_work_ids: dict[str, set[str]] = {}
    for work in works:
        work_id = str(work.get("id") or "")
        for authorship in work.get("authorships") or []:
            if not _us_education_institution(authorship):
                continue
            author_id = str((authorship.get("author") or {}).get("id") or "")
            if author_id and work_id:
                matching_work_ids.setdefault(author_id, set()).add(work_id)
    matching_work_counts = {
        author_id: len(work_ids) for author_id, work_ids in matching_work_ids.items()
    }

    prospects: dict[int, dict[str, Any]] = {}
    # Faculty verification rejects students, industry authors, stale
    # affiliations, and ambiguous same-name people. A three-to-one pool could
    # therefore never reliably satisfy a 100-person goal. Deep searches keep
    # up to six candidates per requested verified faculty member.
    candidate_budget = min(600, max(120, target_professors * 6))
    saved_paper_ids: set[str] = set()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for work in works:
                probable_authors = [
                    (authorship, _us_education_institution(authorship))
                    for authorship in _probable_pi_authorships(
                        work.get("authorships") or [], matching_work_counts
                    )
                ]
                probable_authors = [item for item in probable_authors if item[1]]
                if not probable_authors:
                    continue
                openalex_work_id = str(work.get("id") or "").strip()
                if not openalex_work_id:
                    continue
                primary_location = work.get("primary_location") or {}
                source = primary_location.get("source") or {}
                cursor.execute(
                    """
                    INSERT INTO papers (
                        openalex_id, title, publication_year, venue, citation_count, doi
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (openalex_id) DO UPDATE
                    SET title = EXCLUDED.title,
                        venue = EXCLUDED.venue,
                        citation_count = EXCLUDED.citation_count,
                        doi = EXCLUDED.doi
                    RETURNING id
                    """,
                    (
                        openalex_work_id,
                        str(work.get("title") or "Untitled"),
                        work.get("publication_year"),
                        str(source.get("display_name") or ""),
                        int(work.get("cited_by_count") or 0),
                        work.get("doi"),
                    ),
                )
                paper_id = cursor.fetchone()["id"]
                linked_to_professor = False
                for authorship, institution in probable_authors:
                    author = authorship.get("author") or {}
                    professor_name = str(author.get("display_name") or "").strip()
                    openalex_author_id = str(author.get("id") or "").strip()
                    institution_name = str(institution.get("display_name") or "").strip()
                    if not professor_name or not openalex_author_id or not institution_name:
                        continue

                    cursor.execute(
                        """
                        SELECT id FROM professors
                        WHERE openalex_id = %s OR (name = %s AND institution_name = %s)
                        ORDER BY (openalex_id = %s) DESC LIMIT 1
                        """,
                        (openalex_author_id, professor_name, institution_name, openalex_author_id),
                    )
                    existing = cursor.fetchone()
                    if not existing and len(prospects) >= candidate_budget:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO institutions (name, country_code)
                        VALUES (%s, 'US')
                        ON CONFLICT (name) DO UPDATE
                        SET country_code = COALESCE(institutions.country_code, 'US')
                        RETURNING id
                        """,
                        (institution_name,),
                    )
                    institution_id = cursor.fetchone()["id"]
                    if existing:
                        professor_id = existing["id"]
                        if professor_id not in prospects and len(prospects) >= candidate_budget:
                            continue
                        cursor.execute(
                            """
                            UPDATE professors
                            SET openalex_id = COALESCE(openalex_id, %s),
                                institution_id = CASE
                                    WHEN faculty_status = 'VERIFIED'
                                         AND faculty_verification_version >= 2
                                        THEN institution_id
                                    WHEN EXISTS (
                                        SELECT 1 FROM professors other
                                        WHERE other.id <> professors.id
                                          AND other.name = professors.name
                                          AND other.institution_name = %s
                                    )
                                        THEN institution_id
                                    ELSE %s
                                END,
                                institution_name = CASE
                                    WHEN faculty_status = 'VERIFIED'
                                         AND faculty_verification_version >= 2
                                        THEN institution_name
                                    WHEN EXISTS (
                                        SELECT 1 FROM professors other
                                        WHERE other.id <> professors.id
                                          AND other.name = professors.name
                                          AND other.institution_name = %s
                                    )
                                        THEN institution_name
                                    ELSE %s
                                END,
                                research_domain = %s, updated_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                openalex_author_id,
                                institution_name, institution_id,
                                institution_name, institution_name,
                                research_domain, professor_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO professors (
                                openalex_id, name, institution_id, institution_name, research_domain
                            ) VALUES (%s, %s, %s, %s, %s) RETURNING id
                            """,
                            (openalex_author_id, professor_name, institution_id, institution_name, research_domain),
                        )
                        professor_id = cursor.fetchone()["id"]

                    cursor.execute(
                        """
                        INSERT INTO professor_papers (professor_id, paper_id, author_position)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (professor_id, paper_id) DO NOTHING
                        """,
                        (professor_id, paper_id, authorship.get("author_position")),
                    )
                    linked_to_professor = True
                    prospect = prospects.setdefault(
                        professor_id,
                        {
                            "professor_id": professor_id,
                            "matching_papers": 0,
                            "citation_count": 0,
                            "latest_paper_title": None,
                            "latest_paper_year": None,
                            "latest_paper_url": None,
                        },
                    )
                    prospect["matching_papers"] += 1
                    prospect["citation_count"] += int(work.get("cited_by_count") or 0)
                    year = int(work.get("publication_year") or 0)
                    if year >= int(prospect["latest_paper_year"] or 0):
                        prospect["latest_paper_title"] = str(work.get("title") or "Untitled")
                        prospect["latest_paper_year"] = year or None
                        prospect["latest_paper_url"] = str(work.get("doi") or openalex_work_id)
                if linked_to_professor:
                    saved_paper_ids.add(openalex_work_id)
                if len(prospects) >= candidate_budget:
                    break

    current_year = datetime.now(timezone.utc).year
    ranked_prospects = []
    for prospect in prospects.values():
        recency = 5 if prospect["latest_paper_year"] == current_year else 3
        score = min(
            40.0,
            18.0
            + min(12, prospect["matching_papers"] * 4)
            + min(5, prospect["citation_count"] / 20)
            + recency,
        )
        ranked_prospects.append({**prospect, "research_score": round(score, 2)})
    ranked_prospects.sort(
        key=lambda item: (item["research_score"], item["matching_papers"]),
        reverse=True,
    )
    # Faculty verification is deliberately stricter than author discovery.
    # Keep a deeper pool so the pipeline can continue beyond non-faculty and
    # same-name candidates until it reaches the requested verified maximum.
    candidate_pool_size = min(candidate_budget, max(60, target_professors * 6))
    ranked_prospects = ranked_prospects[:candidate_pool_size]
    return {
        "professors": min(target_professors, len(ranked_prospects)),
        "candidates_ranked": len(ranked_prospects),
        "papers": len(saved_paper_ids),
        "professor_ids": [item["professor_id"] for item in ranked_prospects],
        "prospects": ranked_prospects,
    }
