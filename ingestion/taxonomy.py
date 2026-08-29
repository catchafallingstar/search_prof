import re
from itertools import product
from typing import Any

from ingestion.openalex_client import OpenAlexUnavailable, openalex_get_json
from settings import setting

AGENCY_MATRIX = {
    "CISE_NSF": {
        "fields": ["Computer Science", "Decision Sciences"],
        "primary_agency": "NSF",
        "programs": ["CRII", "CAREER"],
    },
    "NIH_BIOMED": {
        "fields": [
            "Medicine", "Biochemistry, Genetics and Molecular Biology", "Neuroscience",
            "Nursing", "Pharmacology, Toxicology and Pharmaceutics", "Health Professions",
            "Immunology and Microbiology", "Dentistry",
        ],
        "primary_agency": "NIH",
        "programs": ["K99/R00", "Early Stage Investigator", "R01"],
    },
    "NSF_ENG_MPS": {
        "fields": ["Engineering", "Chemical Engineering", "Materials Science", "Energy", "Physics and Astronomy", "Chemistry"],
        "primary_agency": "NSF",
        "programs": ["CAREER", "CRII"],
    },
    "NSF_SBE": {
        "fields": ["Economics, Econometrics and Finance", "Business, Management and Accounting", "Social Sciences", "Psychology"],
        "primary_agency": "NSF",
        "programs": ["CAREER"],
    },
    "NSF_GEO_BIO": {
        "fields": ["Earth and Planetary Sciences", "Environmental Science", "Agricultural and Biological Sciences"],
        "primary_agency": "NSF",
        "programs": ["CAREER"],
    },
    "UNSUPPORTED": {
        "fields": [],
        "primary_agency": "UNSUPPORTED",
        "programs": [],
    },
}

OPENALEX_TOPICS_URL = "https://api.openalex.org/topics"
STOPWORDS = {"and", "for", "from", "into", "of", "research", "systems", "the", "using", "with"}

# Query expansion is data-driven and shared by every field.  OpenAlex topics
# supply most expansions.  Compound entries cover phrases whose everyday name
# is broader than the titles used in papers (for example, "AI security").
COMPOUND_QUERY_EXPANSIONS: dict[frozenset[str], list[str]] = {
    frozenset({"ai", "security"}): [
        "network intrusion detection",
        "adversarial machine learning",
        "privacy preserving machine learning",
        "trustworthy artificial intelligence",
    ],
    frozenset({"machine", "learning", "security"}): [
        "adversarial machine learning",
        "network intrusion detection",
        "privacy preserving machine learning",
    ],
    # Common degree/program shorthand is often absent from paper titles and
    # OpenAlex topic names. Expand it into the vocabulary researchers use.
    frozenset({"biomed"}): [
        "biomedical engineering",
        "biomedical sciences",
        "biomedical research",
        "biomedicine",
    ],
    frozenset({"bio", "med"}): [
        "biomedical engineering",
        "biomedical sciences",
        "biomedical research",
        "biomedicine",
    ],
    frozenset({"biomedical"}): [
        "biomedical engineering",
        "biomedical sciences",
        "biomedical research",
        "biomedicine",
    ],
    # Broad and interdisciplinary fields need the vocabulary that normally
    # appears in paper titles and abstracts. The paper-level relevance gate
    # still decides whether each returned work is kept.
    frozenset({"political", "science"}): [
        "political behavior",
        "political institutions",
        "comparative politics",
        "international relations",
        "public policy",
    ],
    frozenset({"asian", "studies"}): [
        "East Asian studies",
        "Asian history",
        "Asian politics",
        "Asian culture",
    ],
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 or token == "ai"
    }


def _query_concepts(value: str) -> list[set[str]]:
    """Return concepts without treating AI and 'artificial intelligence' as different."""
    tokens = _tokens(value) - STOPWORDS
    concepts: list[set[str]] = []
    if "ai" in tokens or {"artificial", "intelligence"}.issubset(tokens):
        concepts.append({"ai", "artificial+intelligence"})
        tokens -= {"ai", "artificial", "intelligence"}
    concepts.extend({token} for token in sorted(tokens))
    return concepts


def _phrase_concepts(value: str) -> set[str]:
    tokens = _tokens(value)
    concepts = set(tokens)
    if "ai" in tokens or {"artificial", "intelligence"}.issubset(tokens):
        concepts.update({"ai", "artificial+intelligence"})
    return concepts


def phrase_covers_query(raw_query: str, phrase: str, max_span: int = 6) -> bool:
    """Require all query concepts to occur together, not far apart by coincidence."""
    required = _query_concepts(raw_query)
    available = _phrase_concepts(phrase)
    if not required or not all(bool(concept & available) for concept in required):
        return False
    if len(required) == 1:
        return True

    words = re.findall(r"[a-z0-9]+", phrase.casefold())
    positions: list[list[int]] = []
    for concept in required:
        concept_positions: list[int] = []
        for index, word in enumerate(words):
            if word in concept:
                concept_positions.append(index)
            if (
                "artificial+intelligence" in concept
                and word == "artificial"
                and index + 1 < len(words)
                and words[index + 1] == "intelligence"
            ):
                concept_positions.append(index)
        if not concept_positions:
            return False
        positions.append(concept_positions)

    return any(
        max(chosen) - min(chosen) <= max_span
        for chosen in product(*positions)
    )


def _keyword_text(topic: dict[str, Any]) -> str:
    values = topic.get("keywords") or []
    return " ".join(
        str(value.get("display_name") or value.get("keyword") or "")
        if isinstance(value, dict)
        else str(value)
        for value in values
    )


def _keyword_phrases(topic: dict[str, Any]) -> list[str]:
    values = topic.get("keywords") or []
    return [
        str(value.get("display_name") or value.get("keyword") or "")
        if isinstance(value, dict)
        else str(value)
        for value in values
    ]


def _topic_relevance(raw_query: str, topic: dict[str, Any]) -> float:
    """Reject fuzzy topics that scatter query words across unrelated keywords."""
    display_name = str(topic.get("display_name") or "")
    phrases = [display_name, *_keyword_phrases(topic)]
    if not any(phrase_covers_query(raw_query, phrase) for phrase in phrases):
        return 0.0
    return 2.0 if phrase_covers_query(raw_query, display_name) else 1.0


def build_search_queries(raw_query: str, topic: dict[str, Any] | None = None) -> list[str]:
    """Return a small, deduplicated set of research searches for any field."""
    raw_tokens = _tokens(raw_query) - STOPWORDS
    queries: list[str] = []
    for required, expansions in COMPOUND_QUERY_EXPANSIONS.items():
        if required.issubset(raw_tokens):
            queries.extend(expansions)
    if topic:
        display_name = str(topic.get("display_name") or "").strip()
        if display_name:
            queries.append(display_name)
        queries.extend(value for value in _keyword_phrases(topic)[:3] if value.strip())
    queries.append(raw_query.strip())

    distinct: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(query.casefold().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            distinct.append(" ".join(query.split()))
    return distinct[:6]


def normalize_taxonomy(raw_query: str) -> dict[str, Any]:
    clean_query = raw_query.strip()
    if not clean_query:
        raise ValueError("Research area cannot be empty.")

    fallback = {
        "raw_query": clean_query,
        "topic_id": None,
        "topic_name": clean_query,
        "field_name": "Unknown",
        "domain_name": "Unknown",
        "keywords": [clean_query],
        "search_queries": build_search_queries(clean_query),
        "agency_category": "UNSUPPORTED",
        "router_config": AGENCY_MATRIX["UNSUPPORTED"],
    }
    params: dict[str, object] = {"search": clean_query, "per_page": 25}
    contact_email = setting("OPENALEX_EMAIL").strip()
    if contact_email:
        params["mailto"] = contact_email
    api_key = setting("OPENALEX_API_KEY").strip()
    if api_key:
        params["api_key"] = api_key

    try:
        results = openalex_get_json(
            OPENALEX_TOPICS_URL,
            params=params,
            timeout=10,
        ).get("results", [])
        if not results:
            return fallback
        ranked = sorted(
            ((_topic_relevance(clean_query, topic), topic) for topic in results),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            print(f"OpenAlex returned no relevant topic for {clean_query!r}; using direct works search.")
            return fallback
        topic = ranked[0][1]
        field_name = str((topic.get("field") or {}).get("display_name") or "Unknown")
        domain_name = str((topic.get("domain") or {}).get("display_name") or "Unknown")
        category = "UNSUPPORTED"
        for candidate, config in AGENCY_MATRIX.items():
            if field_name in config["fields"]:
                category = candidate
                break
        return {
            "raw_query": clean_query,
            "topic_id": topic.get("id"),
            "topic_name": topic.get("display_name") or clean_query,
            "field_name": field_name,
            "domain_name": domain_name,
            "keywords": topic.get("keywords") or [clean_query],
            "search_queries": build_search_queries(clean_query, topic),
            "agency_category": category,
            "router_config": AGENCY_MATRIX[category],
        }
    except OpenAlexUnavailable:
        raise
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"OpenAlex taxonomy lookup failed: {type(error).__name__}")
        return fallback
