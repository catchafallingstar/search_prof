from datetime import datetime, timezone
import re
import math
from typing import Any

from db import get_db_connection
from ingestion.openalex_client import openalex_get_json
from ingestion.taxonomy import phrase_covers_query
from ingestion.institution_domains import canonical_institution
from settings import setting

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _work_pdf_url(work: dict[str, Any]) -> str:
    """Return an OpenAlex-provided open PDF URL without guessing publisher paths."""
    locations = [
        work.get("best_oa_location") or {},
        work.get("primary_location") or {},
        *(work.get("locations") or []),
    ]
    for location in locations:
        pdf_url = str((location or {}).get("pdf_url") or "").strip()
        if pdf_url:
            return pdf_url
    return ""


def _raw_affiliation_text(authorship: dict[str, Any]) -> str:
    values = [
        " ".join(str(value or "").split())
        for value in authorship.get("raw_affiliation_strings") or []
        if str(value or "").strip()
    ]
    if not values:
        values = [str(item.get("display_name") or "").strip()
                  for item in authorship.get("institutions") or [] if item.get("display_name")]
    return " | ".join(dict.fromkeys(values))


def _request_openalex_works(params: dict[str, object]) -> list[dict[str, Any]]:
    """Fetch one globally paced OpenAlex page.

    A 429 pauses every discovery job through the durable provider-health row.
    The next queued topic therefore waits instead of repeating the same failed
    request and making the rate limit worse.
    """
    return list(
        openalex_get_json(
            OPENALEX_WORKS_URL,
            params=params,
            timeout=20,
        ).get("results", [])
    )


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


def _abstract_text(work: dict[str, Any]) -> str:
    """Rebuild the OpenAlex abstract without depending on dictionary order."""
    inverted = work.get("abstract_inverted_index") or {}
    if not isinstance(inverted, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                positioned.append((int(position), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(positioned))


def _work_relevance_score(work: dict[str, Any], raw_query: str) -> float:
    """Score direct textual evidence, not a broad OpenAlex field label.

    OpenAlex topics are valuable for retrieving a candidate pool, but a broad
    topic such as ``Law and Political Science`` must not by itself make an LLM
    security paper a political-science result. A retained work therefore needs
    evidence in its title, abstract, or author/editor keywords.
    """
    weighted_phrases: list[tuple[float, str]] = [
        (8.0, str(work.get("title") or "")),
        (6.0, _abstract_text(work)),
    ]
    for value in work.get("keywords") or []:
        phrase = (
            str(value.get("display_name") or value.get("keyword") or "")
            if isinstance(value, dict)
            else str(value)
        )
        weighted_phrases.append((5.0, phrase))

    query_tokens = set(re.findall(r"[a-z0-9]+", raw_query.casefold()))
    is_ai_security_query = "security" in query_tokens and (
        "ai" in query_tokens or {"artificial", "intelligence"}.issubset(query_tokens)
    )
    matches: list[float] = []
    for weight, phrase in weighted_phrases:
        if not phrase:
            continue
        covered = (
            _matches_ai_security_intent(phrase)
            if is_ai_security_query
            else phrase_covers_query(raw_query, phrase)
        )
        if covered:
            matches.append(weight)
    if not matches:
        return 0.0
    return max(matches) + min(2.0, max(0, len(matches) - 1) * 0.75)


def _work_matches_query(work: dict[str, Any], raw_query: str) -> bool:
    """Keep a work only when its descriptive metadata supports the query."""
    return _work_relevance_score(work, raw_query) >= 5.0


def _taxonomy_work_match(
    work: dict[str, Any], mappings: list[dict[str, Any]]
) -> tuple[float, str, str]:
    best = (0.0, "", "")
    for assigned in work.get("topics") or []:
        hierarchy = {
            "topic": _short_id(assigned.get("id")),
            "subfield": _short_id((assigned.get("subfield") or {}).get("id")),
            "field": _short_id((assigned.get("field") or {}).get("id")),
            "domain": _short_id((assigned.get("domain") or {}).get("id")),
        }
        assignment_score = float(assigned.get("score") or 0)
        for mapping in mappings:
            node_type = str(mapping.get("node_type") or "")
            node_id = _short_id(mapping.get("openalex_id"))
            if node_id and hierarchy.get(node_type) == node_id:
                score = 5.0 + (3.0 * float(mapping.get("weight") or 0)) + assignment_score
                candidate = (
                    score,
                    f"OpenAlex {node_type}: {mapping.get('display_name') or node_id}",
                    node_id,
                )
                if candidate[0] > best[0]:
                    best = candidate
    return best


def _short_id(value: object) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def _best_work_evidence(
    work: dict[str, Any],
    search_queries: list[str],
    mappings: list[dict[str, Any]] | None = None,
) -> tuple[float, str, str]:
    """Return the strongest direct paper match and the query that produced it."""
    scored = [
        (_work_relevance_score(work, query), query)
        for query in search_queries
        if query
    ]
    if not scored:
        text_match = (0.0, "")
    else:
        text_match = max(scored, key=lambda item: item[0])
    taxonomy_match = _taxonomy_work_match(work, mappings or [])
    if taxonomy_match[0] and text_match[0]:
        if text_match[0] >= taxonomy_match[0]:
            return text_match[0] + 1.0, text_match[1], taxonomy_match[2]
        return taxonomy_match[0] + 1.0, taxonomy_match[1], taxonomy_match[2]
    if taxonomy_match[0]:
        return taxonomy_match
    return text_match[0], text_match[1], ""


def _best_work_match(
    work: dict[str, Any], search_queries: list[str]
) -> tuple[float, str]:
    """Compatibility wrapper for callers that only need text evidence."""
    score, matched_query, _ = _best_work_evidence(work, search_queries)
    return score, matched_query


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


def _education_institution(authorship: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer an educational affiliation in any country.

    Faculty verification later establishes the person's current role. This
    stage should not silently remove international researchers.
    """
    for institution in authorship.get("institutions") or []:
        institution_type = str(institution.get("type") or "").casefold()
        if institution_type in {"", "education"}:
            return institution
    return None


def _fetch_works(tax_meta: dict[str, Any], target_professors: int) -> list[dict[str, Any]]:
    current_year = datetime.now(timezone.utc).year
    start_year = current_year - 2
    domain_name = str(tax_meta.get("topic_name") or tax_meta["raw_query"])
    works: list[dict[str, Any]] = []
    seen_work_ids: set[str] = set()
    # A wider paper pool is necessary for new faculty whose relevant work is
    # not ranked at the very top of a broad keyword search.  The result count
    # still bounds how many verified faculty cards are returned.
    work_budget = min(800, max(200, target_professors * 8))
    search_queries = list(tax_meta.get("search_queries") or [domain_name])
    mappings = list(tax_meta.get("openalex_mappings") or [])
    # A single OpenAlex topic is useful for precision, but it is not sufficient
    # coverage for broad fields or common shorthand such as "biomed". Keep the
    # matched topic and the expanded text searches, then deduplicate works.
    sources: list[tuple[str, object]] = [
        ("taxonomy", mapping) for mapping in mappings
    ]
    if not mappings and tax_meta.get("topic_id"):
        sources.append(("legacy_topic", _short_id(tax_meta.get("topic_id"))))
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
            filters = [f"publication_year:{start_year}-{current_year}"]
            params: dict[str, object] = {"per_page": page_size, "page": page}
            if source_kind == "taxonomy":
                mapping = dict(source_value) if isinstance(source_value, dict) else {}
                filters.append(
                    f"{mapping.get('filter_field') or 'topics.id'}:"
                    f"{_short_id(mapping.get('openalex_id'))}"
                )
            elif source_kind == "legacy_topic":
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
            # Topic retrieval and text retrieval now use the same evidence
            # gate. Previously, broad topic labels bypassed paper relevance.
            batch: list[dict[str, Any]] = []
            for work in raw_batch:
                relevance, matched_query, matched_node_id = _best_work_evidence(
                    work, search_queries, mappings
                )
                if relevance < 5.0:
                    continue
                work["_scholarradar_relevance"] = relevance
                work["_scholarradar_matched_query"] = matched_query
                work["_scholarradar_matched_node_id"] = matched_node_id
                batch.append(work)
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
    target_country = setting("TARGET_COUNTRY_CODE").strip().upper() or "US"
    research_domain = str(tax_meta.get("topic_name") or tax_meta["raw_query"]).strip()
    works = _fetch_works(tax_meta, target_professors)
    if not works:
        raise RuntimeError(
            "OpenAlex returned no usable research results. Please wait a minute "
            "and retry, or use a more specific research phrase."
        )

    matching_work_ids: dict[str, set[str]] = {}
    for work in works:
        work_id = str(work.get("id") or "")
        for authorship in work.get("authorships") or []:
            institution = _education_institution(authorship)
            if not institution or str(institution.get("country_code") or "").upper() != target_country:
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
                    (authorship, _education_institution(authorship))
                    for authorship in _probable_pi_authorships(
                        work.get("authorships") or [], matching_work_counts
                    )
                ]
                probable_authors = [item for item in probable_authors if item[1]]
                probable_authors = [
                    item
                    for item in probable_authors
                    if str(item[1].get("country_code") or "").upper()
                    == target_country
                ]
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
                        openalex_id, title, publication_year, venue,
                        citation_count, doi, pdf_url
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (openalex_id) DO UPDATE
                    SET title = EXCLUDED.title,
                        venue = EXCLUDED.venue,
                        citation_count = EXCLUDED.citation_count,
                        doi = EXCLUDED.doi,
                        pdf_url = COALESCE(EXCLUDED.pdf_url, papers.pdf_url)
                    RETURNING id
                    """,
                    (
                        openalex_work_id,
                        str(work.get("title") or "Untitled"),
                        work.get("publication_year"),
                        str(source.get("display_name") or ""),
                        int(work.get("cited_by_count") or 0),
                        work.get("doi"),
                        _work_pdf_url(work) or None,
                    ),
                )
                paper_id = cursor.fetchone()["id"]
                linked_to_professor = False
                for authorship, institution in probable_authors:
                    author = authorship.get("author") or {}
                    professor_name = str(author.get("display_name") or "").strip()
                    openalex_author_id = str(author.get("id") or "").strip()
                    institution_name = canonical_institution(
                        str(institution.get("display_name") or "").strip()
                    )
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
                        VALUES (%s, %s)
                        ON CONFLICT (name) DO UPDATE
                        SET country_code = COALESCE(
                            EXCLUDED.country_code, institutions.country_code
                        )
                        RETURNING id
                        """,
                        (
                            institution_name,
                            str(institution.get("country_code") or "").upper() or None,
                        ),
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

                    if author.get("orcid"):
                        cursor.execute(
                            "UPDATE professors SET orcid_id = %s WHERE id = %s AND orcid_id IS NULL",
                            (str(author["orcid"]), professor_id),
                        )
                    cursor.execute(
                        """
                        INSERT INTO professor_papers (
                            professor_id, paper_id, author_position,
                            raw_affiliation_text
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (professor_id, paper_id) DO UPDATE
                        SET author_position = EXCLUDED.author_position,
                            raw_affiliation_text = COALESCE(
                                NULLIF(EXCLUDED.raw_affiliation_text, ''),
                                professor_papers.raw_affiliation_text
                            )
                        """,
                        (
                            professor_id,
                            paper_id,
                            authorship.get("author_position"),
                            _raw_affiliation_text(authorship),
                        ),
                    )
                    linked_to_professor = True
                    prospect = prospects.setdefault(
                        professor_id,
                        {
                            "professor_id": professor_id,
                            "matching_papers": 0,
                            "citation_count": 0,
                            "relevance_total": 0.0,
                            "latest_paper_title": None,
                            "latest_paper_year": None,
                            "latest_paper_url": None,
                            "supporting_papers": [],
                        },
                    )
                    prospect["matching_papers"] += 1
                    prospect["citation_count"] += int(work.get("cited_by_count") or 0)
                    prospect["relevance_total"] += float(
                        work.get("_scholarradar_relevance") or 0
                    )
                    prospect["supporting_papers"].append(
                        {
                            "paper_id": int(paper_id),
                            "title": str(work.get("title") or "Untitled"),
                            "publication_year": int(work.get("publication_year") or 0) or None,
                            "url": str(work.get("doi") or openalex_work_id),
                            "relevance_score": float(
                                work.get("_scholarradar_relevance") or 0
                            ),
                            "matched_query": str(
                                work.get("_scholarradar_matched_query")
                                or tax_meta["raw_query"]
                            ),
                            "matched_openalex_node_id": str(
                                work.get("_scholarradar_matched_node_id") or ""
                            ) or None,
                        }
                    )
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
        average_relevance = prospect["relevance_total"] / max(
            1, prospect["matching_papers"]
        )
        score = min(
            40.0,
            12.0
            + min(12, prospect["matching_papers"] * 4)
            + min(5, prospect["citation_count"] / 20)
            + recency
            + min(8, average_relevance),
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
