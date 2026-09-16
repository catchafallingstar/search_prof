"""Local, transparent research-query normalization and phrase matching."""
from __future__ import annotations

import re
from typing import Any

ALIASES = {
    "ai": ("ai", "artificial intelligence", "machine learning"),
    "security": ("security", "secure", "privacy", "adversarial", "robustness", "threat"),
    "nlp": ("nlp", "natural language processing", "language model"),
}
STOP = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with"}


def _tokens(value: str) -> list[str]:
    return [v for v in re.findall(r"[a-z0-9]+", str(value).casefold()) if v not in STOP]


def _concepts(query: str) -> list[set[str]]:
    concepts = []
    for token in _tokens(query):
        concepts.append(set(ALIASES.get(token, (token,))))
    return concepts


def phrase_covers_query(query: str, phrase: str) -> bool:
    text = str(phrase).casefold()
    concepts = _concepts(query)
    return bool(concepts) and all(any(alias in text for alias in choices) for choices in concepts)


def _topic_relevance(query: str, topic: dict[str, Any]) -> float:
    phrases = [str(topic.get("display_name") or ""), *[str(v) for v in topic.get("keywords") or []]]
    return 1.0 if any(phrase_covers_query(query, phrase) for phrase in phrases) else 0.0


def normalize_taxonomy(query: str) -> dict[str, Any]:
    clean = " ".join(str(query).split())
    variants = [clean]
    for token in _tokens(clean):
        variants.extend(ALIASES.get(token, ()))
    return {"raw_query": clean, "topic_name": clean, "normalized_topic": clean.casefold(),
            "search_queries": list(dict.fromkeys(v for v in variants if v))}
