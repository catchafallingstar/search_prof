"""Versioned paper metadata enrichment and explainable research classification."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from db import get_db_connection
from ingestion.research_seeds import RESEARCH_SEED_GROUPS


CLASSIFICATION_VERSION = 3
USER_AGENT = "ScholarRadar/2.0 publication-metadata-enricher"
STOP_WORDS = {
    "a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with",
    "analysis", "method", "methods", "study", "using",
}


@dataclass(frozen=True)
class CategoryDefinition:
    key: str
    name: str
    description: str
    aliases: tuple[str, ...] = ()
    positive_terms: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    breadth: str = "SPECIALIZED"
    parent_key: str | None = None
    concept_groups: tuple[tuple[str, ...], ...] = ()


SPECIALIZED_CATEGORIES = (
    CategoryDefinition(
        "ai-security", "AI Security",
        "Attacks, vulnerabilities, privacy risks, and defenses involving artificial intelligence or machine-learning systems.",
        aliases=("ai security", "machine learning security", "ml security", "llm security",
                 "adversarial machine learning", "model security", "secure machine learning"),
        positive_terms=("prompt injection", "model poisoning", "data poisoning", "model extraction",
                        "membership inference", "evasion attack", "adversarial example",
                        "backdoor attack", "jailbreak attack", "model vulnerability",
                        "zero-day", "zero day", "intrusion detection", "malware detection",
                        "network intrusion", "impersonation attack"),
        exclusions=("physical security", "border security", "food security", "energy security"),
        parent_key="artificial-intelligence",
        concept_groups=(
            ("ai", "artificial intelligence", "machine learning", "ml", "language model", "llm",
             "neural network", "foundation model", "agentic"),
            ("security", "secure", "cybersecurity", "attack", "attacks", "cyber attack",
             "cyber attacks", "adversarial", "vulnerability", "vulnerabilities",
             "privacy", "poisoning", "inference", "extraction", "jailbreak",
             "backdoor", "evasion", "zero-day", "zero day", "intrusion detection",
             "malware", "threat", "threats"),
        ),
    ),
    CategoryDefinition(
        "cybersecurity", "Cybersecurity",
        "Security of computers, networks, software, data, and cyber-physical systems.",
        aliases=("cybersecurity", "cyber security", "computer security",
                 "information security", "network security"),
        positive_terms=("malware", "intrusion detection", "network intrusion",
                        "zero-day", "zero day", "cyber attack", "cyber attacks",
                        "security vulnerability", "security vulnerabilities",
                        "secure systems", "IoT security", "internet of things security"),
        exclusions=("physical security", "border security", "food security",
                    "energy security", "national security policy"),
        breadth="BROAD", parent_key="computing",
    ),
    CategoryDefinition(
        "robotics", "Robotics",
        "Design, control, perception, learning, and operation of physical robots and robotic systems.",
        aliases=("robotics", "robot", "robots", "robotic system", "robotic systems",
                 "autonomous robot", "autonomous robots"),
        positive_terms=("robotic arm", "mobile robot", "humanoid robot", "robot navigation",
                        "robot manipulation", "robot learning", "robot motion planning"),
        exclusions=("robotic process automation", "business process automation",
                    "process automation", "rpa implementation"),
        breadth="BROAD", parent_key="computing",
    ),
    CategoryDefinition(
        "ai-safety", "AI Safety",
        "Reliability, alignment, controllability, risk, and safe behavior of artificial-intelligence systems.",
        aliases=("ai safety", "artificial intelligence safety", "machine learning safety",
                 "alignment", "safe ai"),
        positive_terms=("robust alignment", "catastrophic risk", "safe reinforcement learning",
                        "value alignment", "reward hacking"),
        parent_key="artificial-intelligence",
        concept_groups=(
            ("ai", "artificial intelligence", "machine learning", "language model", "llm", "agent"),
            ("safety", "safe", "alignment", "risk", "controllability", "reward hacking"),
        ),
    ),
    CategoryDefinition(
        "large-language-models", "Large Language Models",
        "Methods, evaluation, applications, and behavior of large language and foundation models.",
        aliases=("large language model", "large language models", "llm", "llms",
                 "foundation language model"),
        positive_terms=("transformer language model", "instruction tuning", "prompt engineering"),
        parent_key="natural-language-processing",
    ),
)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in STOP_WORDS and len(token) > 1
    }


_ORGANIZATION_LIKE_INTEREST = re.compile(
    r"\b(?:council|center|centre|institute|journal|society|association|college|"
    r"university|department|school|office|committee)\b",
    re.I,
)


def _normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _contains_phrase(text: str, phrase: str) -> bool:
    haystack = f" {_normalized_text(text)} "
    needle = _normalized_text(phrase)
    return bool(needle and f" {needle} " in haystack)


def _explicit_interest_supports_broad_field(definition: CategoryDefinition, value: str) -> bool:
    """Allow genuine subfield labels without letting organization names become fields."""
    if _ORGANIZATION_LIKE_INTEREST.search(value):
        return False
    words = _normalized_text(value).split()
    if not 1 <= len(words) <= 8:
        return False
    return _contains_phrase(value, definition.name) or any(
        _contains_phrase(value, alias) for alias in definition.aliases
    )


def category_definitions() -> list[CategoryDefinition]:
    definitions: dict[str, CategoryDefinition] = {}
    for group, names in RESEARCH_SEED_GROUPS.items():
        for name in names:
            key = _key(name)
            definitions[key] = CategoryDefinition(
                key, name, f"Research focused on {name}.", aliases=(name,),
                positive_terms=(), breadth="BROAD", parent_key=_key(group),
            )
    for definition in SPECIALIZED_CATEGORIES:
        definitions[definition.key] = definition
    return list(definitions.values())


def sync_research_categories() -> dict[str, int]:
    """Idempotently seed the controlled catalog without overwriting staff edits."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for definition in category_definitions():
                cursor.execute(
                    """INSERT INTO research_categories
                       (category_key,canonical_name,description,parent_key,aliases,
                        positive_terms,exclusion_terms,breadth,classification_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (category_key) DO UPDATE SET
                         canonical_name=EXCLUDED.canonical_name,
                         description=CASE
                           WHEN research_categories.classification_version < EXCLUDED.classification_version
                           THEN EXCLUDED.description ELSE research_categories.description END,
                         aliases=CASE
                           WHEN research_categories.classification_version < EXCLUDED.classification_version
                           THEN EXCLUDED.aliases ELSE research_categories.aliases END,
                         positive_terms=CASE
                           WHEN research_categories.classification_version < EXCLUDED.classification_version
                           THEN EXCLUDED.positive_terms ELSE research_categories.positive_terms END,
                         exclusion_terms=CASE
                           WHEN research_categories.classification_version < EXCLUDED.classification_version
                           THEN EXCLUDED.exclusion_terms ELSE research_categories.exclusion_terms END,
                         classification_version=GREATEST(
                           research_categories.classification_version,
                           EXCLUDED.classification_version), updated_at=NOW()""",
                    (definition.key, definition.name, definition.description,
                     definition.parent_key, list(definition.aliases),
                     list(definition.positive_terms), list(definition.exclusions),
                     definition.breadth, CLASSIFICATION_VERSION),
                )
            cursor.execute("SELECT category_key,id FROM research_categories WHERE active=TRUE")
            return {str(row["category_key"]): int(row["id"]) for row in cursor.fetchall()}


def resolve_category(query: str) -> dict[str, Any]:
    """Map a user query to a catalog category; create an explicit dynamic one if new."""
    normalized = " ".join(str(query).split()).casefold()
    sync_research_categories()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM research_categories
                   WHERE active=TRUE AND (
                     LOWER(canonical_name)=%s OR %s=ANY(
                       SELECT LOWER(value) FROM UNNEST(aliases) value
                     )
                   )
                   ORDER BY (LOWER(canonical_name)=%s) DESC, id LIMIT 1""",
                (normalized, normalized, normalized),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            dynamic_key = "query-" + hashlib.sha256(normalized.encode()).hexdigest()[:16]
            cursor.execute(
                """INSERT INTO research_categories
                   (category_key,canonical_name,description,aliases,positive_terms,
                    breadth,classification_version)
                   VALUES (%s,%s,%s,%s,%s,'NARROW',%s)
                   ON CONFLICT (category_key) DO UPDATE SET updated_at=NOW()
                   RETURNING *""",
                (dynamic_key, " ".join(str(query).split()),
                 f"User-requested research area: {' '.join(str(query).split())}.",
                 [" ".join(str(query).split())], sorted(_tokens(query)),
                 CLASSIFICATION_VERSION),
            )
            return dict(cursor.fetchone())


def _definition_for(category: dict[str, Any]) -> CategoryDefinition:
    seeded = {item.key: item for item in category_definitions()}
    return seeded.get(str(category["category_key"]), CategoryDefinition(
        str(category["category_key"]), str(category["canonical_name"]),
        str(category["description"]), tuple(category.get("aliases") or ()),
        tuple(category.get("positive_terms") or ()),
        tuple(category.get("exclusion_terms") or ()),
        str(category.get("breadth") or "NARROW"),
    ))


def classify_text(
    category: dict[str, Any], title: str, abstract: str = "", *,
    evidence_kind: str = "paper",
) -> dict[str, Any]:
    """Classify one evidence unit without combining unrelated research labels.

    ``evidence_kind='explicit_interest'`` is intentionally more permissive for
    short official labels such as "Condensed Matter Physics". Paper text remains
    conservative for broad one-word fields so incidental words do not create a
    research area.
    """
    definition = _definition_for(category)
    title_text, abstract_text = title.casefold(), abstract.casefold()
    whole = f"{title_text} {abstract_text}".strip()
    exclusions = [term for term in definition.exclusions if _contains_phrase(whole, term)]
    phrases = list(dict.fromkeys((definition.name, *definition.aliases, *definition.positive_terms)))
    title_matches = [phrase for phrase in phrases if _contains_phrase(title_text, phrase)]
    abstract_matches = [phrase for phrase in phrases if _contains_phrase(abstract_text, phrase)]
    direct_names = {definition.name.casefold(), *map(str.casefold, definition.aliases)}

    lexical = 0.0
    if title_matches:
        lexical = 96.0 if any(p.casefold() in direct_names for p in title_matches) else 86.0
    elif abstract_matches:
        lexical = 88.0 if any(p.casefold() in direct_names for p in abstract_matches) else 76.0
    else:
        category_tokens = _tokens(" ".join((definition.name, *definition.aliases)))
        title_overlap = len(category_tokens & _tokens(title)) / max(1, len(category_tokens))
        abstract_overlap = len(category_tokens & _tokens(abstract)) / max(1, len(category_tokens))
        if title_overlap >= 0.6:
            lexical = 55.0 + 35.0 * title_overlap
        elif abstract_overlap >= 0.6:
            lexical = 45.0 + 30.0 * abstract_overlap

    concept_hits: list[str] = []
    concept_score = 0.0
    concepts_satisfied = True
    if definition.concept_groups:
        for group in definition.concept_groups:
            hit = next((term for term in group if _contains_phrase(whole, term)), "")
            if not hit:
                concepts_satisfied = False
            else:
                concept_hits.append(hit)
        concept_score = 100.0 * len(concept_hits) / len(definition.concept_groups)
    elif lexical:
        concept_score = min(100.0, lexical + 5.0)

    combined = max(lexical, 0.65 * lexical + 0.35 * concept_score)
    if exclusions:
        combined = min(combined, 20.0)
        concepts_satisfied = False
    if definition.concept_groups and not concepts_satisfied:
        combined = min(combined, 54.0)

    broad_tokens = _tokens(definition.name)
    broad_single_word = (
        definition.breadth == "BROAD"
        and len(broad_tokens) == 1
        and not definition.concept_groups
        and not definition.positive_terms
    )
    if broad_single_word:
        if evidence_kind == "explicit_interest" and _explicit_interest_supports_broad_field(definition, title):
            combined = max(combined, 92.0)
            lexical = max(lexical, 92.0)
        else:
            # An incidental generic word in a paper or biography is not enough
            # to auto-create a broad field. It can still be surfaced for review.
            combined = min(combined, 54.0)

    # Seeded broad multi-word fields must also be present as an actual phrase.
    # Token overlap alone can otherwise turn "social computing" + "work" into
    # Social Work, or "public health" + "mental" into Mental Health.
    if (
        definition.breadth == "BROAD"
        and not definition.concept_groups
        and not definition.positive_terms
        and not title_matches
        and not abstract_matches
    ):
        combined = min(combined, 54.0)

    combined = round(combined, 2)
    decision = (
        "AUTO_ACCEPTED" if combined >= 65 and concepts_satisfied
        else "REVIEW_REQUIRED" if combined >= 45
        else "AUTO_REJECTED"
    )
    matched_terms = list(dict.fromkeys([*title_matches, *abstract_matches, *concept_hits]))
    evidence = title if title_matches or (lexical and not abstract_matches) else abstract[:700]
    return {
        "lexical_score": round(lexical, 2), "concept_score": round(concept_score, 2),
        "combined_score": combined, "decision": decision,
        "matched_terms": matched_terms, "evidence_text": evidence,
        "exclusions": exclusions,
    }


def classify_interest_units(category: dict[str, Any], interests: list[str]) -> dict[str, Any]:
    """Classify explicit interests independently so separate labels cannot fuse.

    Example: ["Artificial Intelligence", "Security"] must not become
    "AI Security" unless one evidence unit independently supports that field.
    """
    best: dict[str, Any] | None = None
    best_interest = ""
    for interest in interests:
        result = classify_text(category, interest, "", evidence_kind="explicit_interest")
        if best is None or float(result["combined_score"]) > float(best["combined_score"]):
            best = result
            best_interest = interest
    if best is None:
        best = classify_text(category, "", "", evidence_kind="explicit_interest")
    return {**best, "matched_interest": best_interest}


def _title_similarity(expected: str, observed: str) -> float:
    left, right = _tokens(expected), _tokens(observed)
    return len(left & right) / max(1, len(left | right))


def extract_page_metadata(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    def meta(*selectors: str) -> str:
        for selector in selectors:
            node = soup.select_one(selector)
            if node and node.get("content"):
                return " ".join(str(node["content"]).split())
        return ""
    title = meta('meta[name="citation_title"]', 'meta[property="og:title"]')
    abstract = meta('meta[name="citation_abstract"]', 'meta[name="dc.description"]',
                    'meta[name="description"]', 'meta[property="og:description"]')
    doi = meta('meta[name="citation_doi"]', 'meta[name="dc.identifier"]')
    if not title and soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if not isinstance(value, dict):
                continue
            title = title or str(value.get("headline") or value.get("name") or "")
            abstract = abstract or str(value.get("abstract") or value.get("description") or "")
            doi = doi or str(value.get("doi") or "")
    return {"title": title[:1000], "abstract": abstract[:20000], "doi": doi[:300]}


# These source types identify the page where a publication list was discovered,
# not an individual paper landing page. They remain useful provenance, but they
# should never be fetched as though they were paper metadata sources.
PROVENANCE_ONLY_PUBLICATION_SOURCES = {
    "GOOGLE_SCHOLAR",
    "OFFICIAL_PROFILE",
    "OFFICIAL_ALTERNATE_PROFILE",
    "PERSONAL_SITE",
    "LAB_SITE",
    "INSTITUTIONAL_RESEARCH_PORTAL",
}


def _metadata_url(paper: dict[str, Any]) -> str:
    """Return a direct metadata URL only when the stored source is paper-specific."""
    doi = str(paper.get("doi") or "").strip()
    if doi:
        return "https://doi.org/" + re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)

    source_type = str(paper.get("source_type") or "").strip().upper()
    if source_type in PROVENANCE_ONLY_PUBLICATION_SOURCES:
        return ""

    source = str(paper.get("source_url") or "").strip()
    host = (urlparse(source).hostname or "").casefold()
    if not source or "scholar.google." in host:
        return ""
    return source


def enrich_paper_metadata(paper_id: int) -> dict[str, Any]:
    """Use DOI/direct paper URLs only; provenance/list pages are not metadata pages."""
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM papers WHERE id=%s", (paper_id,))
            paper = cursor.fetchone()
    if not paper:
        raise RuntimeError("Paper no longer exists.")

    paper_data = dict(paper)
    url = _metadata_url(paper_data)
    status = "NOT_FOUND" if url else "NO_DIRECT_METADATA_SOURCE"
    abstract, metadata = "", {}
    title_score = 0.0
    if url:
        try:
            response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            metadata = extract_page_metadata(response.text)
            title_score = _title_similarity(str(paper["title"]), metadata.get("title", ""))
            if title_score >= 0.55 and metadata.get("abstract"):
                abstract, status = metadata["abstract"], (
                    "VERIFIED_DOI" if paper.get("doi") else "VERIFIED_TITLE"
                )
            elif metadata.get("title") and title_score < 0.55:
                status = "TITLE_CONFLICT"
        except requests.RequestException:
            status = "SOURCE_UNAVAILABLE"

    content_hash = hashlib.sha256(abstract.encode("utf-8", "replace")).hexdigest() if abstract else None
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            if url:
                cursor.execute(
                    """INSERT INTO paper_abstract_evidence
                       (paper_id,abstract_text,source_url,source_type,title_match_score,
                        doi_match,verification_status,content_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (paper_id,source_url) DO UPDATE SET
                         abstract_text=EXCLUDED.abstract_text,
                         title_match_score=EXCLUDED.title_match_score,
                         doi_match=EXCLUDED.doi_match,
                         verification_status=EXCLUDED.verification_status,
                         content_hash=EXCLUDED.content_hash,fetched_at=NOW()""",
                    (paper_id, abstract or None, url, "DOI_LANDING" if paper.get("doi") else "KNOWN_URL",
                     round(100 * title_score, 2), bool(paper.get("doi")), status, content_hash),
                )
            cursor.execute(
                """UPDATE papers SET
                     abstract_text=CASE WHEN %s IN ('VERIFIED_DOI','VERIFIED_TITLE')
                                        THEN %s ELSE abstract_text END,
                     abstract_status=%s, abstract_source_url=%s,
                     abstract_checked_at=NOW(),
                     metadata_status=CASE WHEN %s IN ('VERIFIED_DOI','VERIFIED_TITLE')
                                          THEN 'RESOLVED' ELSE metadata_status END
                   WHERE id=%s""",
                (status, abstract or None, status, url or None, status, paper_id),
            )
    return {"paper_id": paper_id, "abstract_status": status,
            "abstract_found": bool(abstract), "source_url": url or None}


def classify_paper(paper_id: int) -> dict[str, Any]:
    sync_research_categories()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,title,COALESCE(abstract_text,'') AS abstract_text FROM papers WHERE id=%s", (paper_id,))
            paper = cursor.fetchone()
            if not paper:
                raise RuntimeError("Paper no longer exists.")
            cursor.execute("SELECT * FROM research_categories WHERE active=TRUE ORDER BY id")
            categories = list(cursor.fetchall())
            accepted = 0
            for category in categories:
                result = classify_text(dict(category), str(paper["title"]), str(paper["abstract_text"]))
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
                    (paper_id, category["id"], result["lexical_score"], result["concept_score"],
                     result["combined_score"], result["decision"], result["evidence_text"] or None,
                     result["matched_terms"], CLASSIFICATION_VERSION),
                )
                accepted += int(result["decision"] in {"AUTO_ACCEPTED", "QWEN_ACCEPTED"})
            cursor.execute(
                """UPDATE papers SET classification_version=%s,
                   classified_at=NOW() WHERE id=%s""",
                (CLASSIFICATION_VERSION, paper_id),
            )
    return {"paper_id": paper_id, "categories_accepted": accepted,
            "categories_checked": len(categories)}


def rebuild_professor_profiles(professor_ids: list[int] | None = None) -> int:
    """Aggregate accepted paper evidence into durable current/historical profiles."""
    current_year = datetime.now(timezone.utc).year
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            params: list[Any] = []
            where = ""
            if professor_ids:
                where = "WHERE pp.professor_id=ANY(%s)"
                params.append(professor_ids)
                cursor.execute("DELETE FROM professor_research_categories WHERE professor_id=ANY(%s)", (professor_ids,))
            else:
                cursor.execute("TRUNCATE professor_research_categories")
            cursor.execute(
                f"""SELECT pp.professor_id,classification.category_id,paper.id AS paper_id,
                           paper.publication_year,classification.combined_score
                    FROM professor_papers pp
                    JOIN papers paper ON paper.id=pp.paper_id
                    JOIN paper_research_categories classification ON classification.paper_id=paper.id
                    {where}
                      {'AND' if where else 'WHERE'} classification.decision IN ('AUTO_ACCEPTED','QWEN_ACCEPTED')
                    ORDER BY pp.professor_id,classification.category_id,
                             classification.combined_score DESC""",
                params,
            )
            grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
            for row in cursor.fetchall():
                grouped.setdefault((int(row["professor_id"]), int(row["category_id"])), []).append(dict(row))
            written = 0
            for (professor_id, category_id), papers in grouped.items():
                recent = [row for row in papers if row.get("publication_year") and current_year - int(row["publication_year"]) <= 6]
                top = float(papers[0]["combined_score"])
                expertise = min(100.0, top + min(15.0, 3.0 * (len(papers) - 1)))
                weighted = []
                for row in papers:
                    age = current_year - int(row.get("publication_year") or current_year - 11)
                    weight = 1.0 if age <= 3 else 0.85 if age <= 6 else 0.65 if age <= 10 else 0.4
                    weighted.append(float(row["combined_score"]) * weight)
                activity = min(100.0, max(weighted) + min(12.0, 2.0 * (len(recent) - 1)))
                if recent and (max(float(row["combined_score"]) for row in recent) >= 85 or len(recent) >= 2):
                    status = "CURRENTLY_ACTIVE"
                elif recent:
                    status = "EMERGING_AREA"
                elif len(papers) >= 2:
                    status = "ESTABLISHED_EXPERTISE"
                else:
                    status = "HISTORICAL_ONLY"
                cursor.execute(
                    """INSERT INTO professor_research_categories
                       (professor_id,category_id,expertise_score,current_activity_score,
                        matching_paper_count,recent_matching_paper_count,strongest_paper_id,
                        latest_matching_year,status,classification_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (professor_id, category_id, round(expertise, 2), round(activity, 2),
                     len(papers), len(recent), papers[0]["paper_id"],
                     max((row.get("publication_year") or 0) for row in papers) or None,
                     status, CLASSIFICATION_VERSION),
                )
                written += 1
    return written


def enrich_classify_paper(paper_id: int) -> dict[str, Any]:
    metadata = enrich_paper_metadata(paper_id)
    classification = classify_paper(paper_id)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT professor_id FROM professor_papers WHERE paper_id=%s", (paper_id,))
            professor_ids = [int(row["professor_id"]) for row in cursor.fetchall()]
    profiles = rebuild_professor_profiles(professor_ids) if professor_ids else 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE radar_topics SET next_refresh_at=NOW(),updated_at=NOW()")
    return {**metadata, **classification, "professor_profiles_updated": profiles}
