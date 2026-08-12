import os
import re
from itertools import product
from typing import Any

import requests

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
        "agency_category": "UNSUPPORTED",
        "router_config": AGENCY_MATRIX["UNSUPPORTED"],
    }
    params: dict[str, object] = {"search": clean_query, "per_page": 25}
    contact_email = os.getenv("OPENALEX_EMAIL", "").strip()
    if contact_email:
        params["mailto"] = contact_email

    try:
        response = requests.get(OPENALEX_TOPICS_URL, params=params, timeout=10)
        response.raise_for_status()
        results = response.json().get("results", [])
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
            "agency_category": category,
            "router_config": AGENCY_MATRIX[category],
        }
    except requests.RequestException as error:
        print(f"OpenAlex taxonomy lookup failed: {error}")
        return fallback
