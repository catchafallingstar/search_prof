from datetime import datetime, timezone
import re
from typing import Any

import requests

from db import get_db_connection
from ingestion.taxonomy import phrase_covers_query
from settings import setting

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


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


def _probable_pi_authorships(authorships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return distinct corresponding and senior authors, not every paper author."""
    candidates = [
        item
        for item in authorships
        if item.get("is_corresponding") or item.get("author_position") == "last"
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
    work_budget = min(400, max(40, target_professors * 4))
    page = 1

    while len(works) < work_budget and page <= 10:
        page_size = min(100, work_budget)
        filters = [f"publication_year:{start_year}-{current_year}", "institutions.country_code:us"]
        params: dict[str, object] = {"per_page": page_size, "page": page}
        if topic_id:
            filters.append(f"topics.id:{topic_id}")
        else:
            params["search"] = domain_name
        params["filter"] = ",".join(filters)
        contact_email = setting("OPENALEX_EMAIL").strip()
        if contact_email:
            params["mailto"] = contact_email

        response = requests.get(OPENALEX_WORKS_URL, params=params, timeout=20)
        response.raise_for_status()
        raw_batch = response.json().get("results", [])
        if not raw_batch:
            break
        batch = raw_batch if topic_id else [
            work for work in raw_batch if _work_matches_query(work, domain_name)
        ]
        for work in batch:
            work_id = str(work.get("id") or "")
            if work_id and work_id not in seen_work_ids:
                seen_work_ids.add(work_id)
                works.append(work)
                if len(works) >= work_budget:
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

    prospects: dict[int, dict[str, Any]] = {}
    saved_paper_ids: set[str] = set()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for work in works:
                probable_authors = [
                    (authorship, _us_education_institution(authorship))
                    for authorship in _probable_pi_authorships(work.get("authorships") or [])
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
                    if not existing and len(prospects) >= target_professors:
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
                        if professor_id not in prospects and len(prospects) >= target_professors:
                            continue
                        cursor.execute(
                            """
                            UPDATE professors
                            SET openalex_id = COALESCE(openalex_id, %s),
                                institution_id = %s, institution_name = %s,
                                research_domain = %s, updated_at = NOW()
                            WHERE id = %s
                            """,
                            (openalex_author_id, institution_id, institution_name, research_domain, professor_id),
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
                if len(prospects) >= target_professors:
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
    return {
        "professors": len(ranked_prospects),
        "papers": len(saved_paper_ids),
        "professor_ids": [item["professor_id"] for item in ranked_prospects],
        "prospects": ranked_prospects,
    }
