import os
from datetime import datetime, timezone
from typing import Any

import requests

from db import get_db_connection
from ingestion.taxonomy import phrase_covers_query

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


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
    return any(phrase_covers_query(raw_query, phrase) for phrase in phrases)


def _choose_probable_pi(authorships: list[dict[str, Any]]) -> dict[str, Any] | None:
    corresponding = [item for item in authorships if item.get("is_corresponding")]
    candidates = corresponding or [item for item in authorships if item.get("author_position") == "last"]
    return candidates[0] if candidates else None


def _us_education_institution(authorship: dict[str, Any]) -> dict[str, Any] | None:
    for institution in authorship.get("institutions") or []:
        country = str(institution.get("country_code") or "").upper()
        institution_type = str(institution.get("type") or "").casefold()
        if country == "US" and institution_type in {"", "education"}:
            return institution
    return None


def _fetch_works(tax_meta: dict[str, Any], max_papers: int) -> list[dict[str, Any]]:
    current_year = datetime.now(timezone.utc).year
    start_year = current_year - 2
    topic_id = str(tax_meta.get("topic_id") or "").rsplit("/", 1)[-1]
    domain_name = str(tax_meta.get("topic_name") or tax_meta["raw_query"])
    works: list[dict[str, Any]] = []
    page = 1

    while len(works) < max_papers and page <= 10:
        page_size = min(100, max_papers - len(works))
        filters = [f"publication_year:{start_year}-{current_year}", "institutions.country_code:us"]
        params: dict[str, object] = {"per_page": page_size, "page": page}
        if topic_id:
            filters.append(f"topics.id:{topic_id}")
        else:
            params["search"] = domain_name
        params["filter"] = ",".join(filters)
        contact_email = os.getenv("OPENALEX_EMAIL", "").strip()
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
        works.extend(batch)
        if len(raw_batch) < page_size:
            break
        page += 1
    return works


def fetch_professors_by_keywords(tax_meta: dict[str, Any], max_papers: int = 100) -> dict[str, int]:
    """Discover probable PIs from recent OpenAlex works and upsert global records."""
    research_domain = str(tax_meta.get("topic_name") or tax_meta["raw_query"]).strip()
    try:
        works = _fetch_works(tax_meta, max_papers)
    except requests.RequestException as error:
        raise RuntimeError(f"OpenAlex works request failed: {error}") from error

    saved_professors: set[int] = set()
    saved_papers = 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for work in works:
                authorship = _choose_probable_pi(work.get("authorships") or [])
                if not authorship:
                    continue
                institution = _us_education_institution(authorship)
                if not institution:
                    continue
                author = authorship.get("author") or {}
                professor_name = str(author.get("display_name") or "").strip()
                openalex_author_id = str(author.get("id") or "").strip()
                institution_name = str(institution.get("display_name") or "").strip()
                if not professor_name or not openalex_author_id or not institution_name:
                    continue

                cursor.execute(
                    """
                    INSERT INTO institutions (name, country_code)
                    VALUES (%s, 'US')
                    ON CONFLICT (name) DO UPDATE SET country_code = COALESCE(institutions.country_code, 'US')
                    RETURNING id
                    """,
                    (institution_name,),
                )
                institution_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    SELECT id FROM professors
                    WHERE openalex_id = %s OR (name = %s AND institution_name = %s)
                    ORDER BY (openalex_id = %s) DESC
                    LIMIT 1
                    """,
                    (openalex_author_id, professor_name, institution_name, openalex_author_id),
                )
                existing = cursor.fetchone()
                if existing:
                    professor_id = existing["id"]
                    cursor.execute(
                        """
                        UPDATE professors
                        SET openalex_id = COALESCE(openalex_id, %s),
                            institution_id = %s,
                            research_domain = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (openalex_author_id, institution_id, research_domain, professor_id),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO professors (
                            openalex_id, name, institution_id, institution_name, research_domain
                        ) VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (openalex_author_id, professor_name, institution_id, institution_name, research_domain),
                    )
                    professor_id = cursor.fetchone()["id"]

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
                cursor.execute(
                    """
                    INSERT INTO professor_papers (professor_id, paper_id, author_position)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (professor_id, paper_id) DO NOTHING
                    """,
                    (professor_id, paper_id, authorship.get("author_position")),
                )
                saved_professors.add(professor_id)
                saved_papers += 1
    return {"professors": len(saved_professors), "papers": saved_papers}
