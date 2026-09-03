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
OPENALEX_NODE_TYPES = ("domain", "field", "subfield", "topic")
MAX_OPENALEX_MAPPINGS = 12
STOPWORDS = {"and", "for", "from", "into", "of", "research", "systems", "the", "using", "with"}

# Query expansion is data-driven and shared by every field.  OpenAlex topics
# supply most expansions.  Compound entries cover phrases whose everyday name
# is broader than the titles used in papers (for example, "AI security").
COMPOUND_QUERY_EXPANSIONS: dict[frozenset[str], list[str]] = {
    frozenset({"math"}): [
        "mathematics", "applied mathematics", "pure mathematics",
        "mathematical analysis", "algebra", "geometry", "number theory",
        "statistics", "probability",
    ],
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

BROAD_QUERY_EXPANSIONS: dict[str, list[str]] = {
    "artificial+intelligence": [
        "machine learning",
        "natural language processing",
        "computer vision",
        "reinforcement learning",
        "AI security and robustness",
        "knowledge representation and reasoning",
        "AI planning",
        "speech recognition",
        "robotics and intelligent systems",
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


def _short_openalex_id(value: object) -> str:
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def _canonical_name(value: str) -> str:
    tokens = _tokens(value) - STOPWORDS
    if "ai" in tokens or {"artificial", "intelligence"}.issubset(tokens):
        tokens -= {"ai", "artificial", "intelligence"}
        tokens.add("artificial+intelligence")
    return " ".join(sorted(tokens))


def _node_mapping(
    node: dict[str, Any],
    node_type: str,
    *,
    weight: float,
    mapping_method: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    node_id = _short_openalex_id(node.get("id"))
    filter_field = "topics.id" if node_type == "topic" else f"topics.{node_type}.id"
    return {
        "openalex_id": node_id,
        "node_type": node_type,
        "display_name": str(node.get("display_name") or node_id),
        "description": str(node.get("description") or ""),
        "parent_id": _short_openalex_id(parent_id) or None,
        "filter_field": filter_field,
        "weight": round(float(weight), 3),
        "mapping_method": mapping_method,
    }


def _hierarchy_nodes(topic: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    nodes: list[tuple[str, dict[str, Any]]] = []
    for node_type in ("domain", "field", "subfield"):
        node = topic.get(node_type) or {}
        if node.get("id") and node.get("display_name"):
            nodes.append((node_type, node))
    if topic.get("id") and topic.get("display_name"):
        nodes.append(("topic", topic))
    return nodes


def _exact_hierarchy_match(
    raw_query: str, topics: list[dict[str, Any]]
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    wanted = _canonical_name(raw_query)
    if not wanted:
        return None
    # Prefer the broadest exact node. This makes "Computer Science" a field
    # and "Artificial Intelligence" a subfield rather than one narrow topic.
    for wanted_type in OPENALEX_NODE_TYPES:
        for topic in topics:
            for node_type, node in _hierarchy_nodes(topic):
                if node_type == wanted_type and _canonical_name(
                    str(node.get("display_name") or "")
                ) == wanted:
                    return node_type, node, topic
    return None


def _topic_lookup(search: str) -> list[dict[str, Any]]:
    params: dict[str, object] = {"search": search, "per_page": 25}
    contact_email = setting("OPENALEX_EMAIL").strip()
    if contact_email:
        params["mailto"] = contact_email
    api_key = setting("OPENALEX_API_KEY").strip()
    if api_key:
        params["api_key"] = api_key
    return list(
        openalex_get_json(OPENALEX_TOPICS_URL, params=params, timeout=10).get(
            "results", []
        )
    )


def _child_topic_mappings(
    node_type: str, node: dict[str, Any]
) -> list[dict[str, Any]]:
    if node_type == "topic":
        return []
    node_id = _short_openalex_id(node.get("id"))
    if not node_id:
        return []
    params: dict[str, object] = {
        "filter": f"{node_type}.id:{node_id}",
        "per_page": 100,
    }
    contact_email = setting("OPENALEX_EMAIL").strip()
    if contact_email:
        params["mailto"] = contact_email
    api_key = setting("OPENALEX_API_KEY").strip()
    if api_key:
        params["api_key"] = api_key
    children = list(
        openalex_get_json(OPENALEX_TOPICS_URL, params=params, timeout=10).get(
            "results", []
        )
    )
    # A bounded, deterministic sample gives each broad field several lanes
    # without turning one discovery pass into thousands of API requests.
    if len(children) > MAX_OPENALEX_MAPPINGS - 1:
        step = len(children) / float(MAX_OPENALEX_MAPPINGS - 1)
        children = [children[int(index * step)] for index in range(MAX_OPENALEX_MAPPINGS - 1)]
    return [
        _node_mapping(
            child,
            "topic",
            weight=0.75,
            mapping_method="inherited_child",
            parent_id=node_id,
        )
        for child in children
        if child.get("id")
    ]


def _dedupe_mappings(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    distinct: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = (str(value.get("node_type")), str(value.get("openalex_id")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        distinct.append(value)
    return distinct[:MAX_OPENALEX_MAPPINGS]


def build_search_queries(raw_query: str, topic: dict[str, Any] | None = None) -> list[str]:
    """Return a small, deduplicated set of research searches for any field."""
    raw_tokens = _tokens(raw_query) - STOPWORDS
    queries: list[str] = []
    for required, expansions in COMPOUND_QUERY_EXPANSIONS.items():
        if required.issubset(raw_tokens):
            queries.extend(expansions)
    queries.extend(BROAD_QUERY_EXPANSIONS.get(_canonical_name(raw_query), []))
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
    return distinct[:10]


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
        "openalex_mappings": [],
        "query_level": "text",
        "agency_category": "UNSUPPORTED",
        "router_config": AGENCY_MATRIX["UNSUPPORTED"],
    }
    try:
        results = _topic_lookup(clean_query)
        if not results:
            return fallback
        exact = _exact_hierarchy_match(clean_query, results)
        clean_tokens = _tokens(clean_query) - STOPWORDS
        is_compound_query = any(
            required.issubset(clean_tokens)
            for required in COMPOUND_QUERY_EXPANSIONS
        )
        # A compound area such as AI security crosses several OpenAlex topics.
        # An exact detailed-topic label is evidence, but must not collapse the
        # whole user request back to that one topic.
        if exact and not (exact[0] == "topic" and is_compound_query):
            node_type, node, representative_topic = exact
            parent = {
                "topic": representative_topic.get("subfield"),
                "subfield": representative_topic.get("field"),
                "field": representative_topic.get("domain"),
                "domain": None,
            }.get(node_type) or {}
            mappings = [
                _node_mapping(
                    node,
                    node_type,
                    weight=1.0,
                    mapping_method="exact_hierarchy",
                    parent_id=parent.get("id"),
                ),
            ]
            topic = representative_topic
            topic_name = str(node.get("display_name") or clean_query)
            query_level = node_type
        else:
            lookup_terms = [clean_query, *build_search_queries(clean_query)]
            lookup_terms = list(dict.fromkeys(lookup_terms))[:4]
            scored_topics: list[tuple[float, dict[str, Any]]] = [
                (_topic_relevance(clean_query, topic), topic) for topic in results
            ]
            for term in lookup_terms[1:]:
                scored_topics.extend(
                    (_topic_relevance(term, topic) * 0.85, topic)
                    for topic in _topic_lookup(term)
                )
            ranked = sorted(scored_topics, key=lambda pair: pair[0], reverse=True)
            relevant = [topic for score, topic in ranked if score > 0]
            if not relevant:
                print(
                    f"OpenAlex returned no relevant taxonomy node for {clean_query!r}; "
                    "using direct works search."
                )
                return fallback
            topic = relevant[0]
            mappings = [
                _node_mapping(
                    value,
                    "topic",
                    weight=1.0 if index == 0 else 0.85,
                    mapping_method="topic_search",
                    parent_id=(value.get("subfield") or {}).get("id"),
                )
                for index, value in enumerate(relevant[:MAX_OPENALEX_MAPPINGS])
            ]
            topic_name = clean_query
            query_level = "cross_topic" if len(mappings) > 1 else "topic"
        mappings = _dedupe_mappings(mappings)
        field_name = str((topic.get("field") or {}).get("display_name") or "Unknown")
        domain_name = str((topic.get("domain") or {}).get("display_name") or "Unknown")
        category = "UNSUPPORTED"
        for candidate, config in AGENCY_MATRIX.items():
            if field_name in config["fields"]:
                category = candidate
                break
        return {
            "raw_query": clean_query,
            "topic_id": next(
                (
                    value["openalex_id"]
                    for value in mappings
                    if value["node_type"] == "topic"
                ),
                None,
            ),
            "topic_name": topic_name,
            "field_name": field_name,
            "domain_name": domain_name,
            "keywords": topic.get("keywords") or [clean_query],
            "search_queries": build_search_queries(
                clean_query,
                None if query_level in {"domain", "field", "subfield"} else topic,
            ),
            "openalex_mappings": mappings,
            "query_level": query_level,
            "agency_category": category,
            "router_config": AGENCY_MATRIX[category],
        }
    except OpenAlexUnavailable:
        raise
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"OpenAlex taxonomy lookup failed: {type(error).__name__}")
        return fallback
