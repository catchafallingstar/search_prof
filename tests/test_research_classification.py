from ingestion.research_classification import (
    SPECIALIZED_CATEGORIES,
    classify_text,
    extract_page_metadata,
)


def _category(key: str) -> dict:
    definition = next(item for item in SPECIALIZED_CATEGORIES if item.key == key)
    return {
        "category_key": definition.key,
        "canonical_name": definition.name,
        "description": definition.description,
        "aliases": list(definition.aliases),
        "positive_terms": list(definition.positive_terms),
        "exclusion_terms": list(definition.exclusions),
        "breadth": definition.breadth,
    }


def test_ai_security_accepts_semantic_alias_without_exact_query_words() -> None:
    result = classify_text(
        _category("ai-security"),
        "Adversarial Machine Learning Through Data Poisoning Attacks",
        "We study attacks and defenses for neural network classifiers.",
    )
    assert result["decision"] == "AUTO_ACCEPTED"
    assert result["combined_score"] >= 65


def test_ai_security_rejects_unrelated_security_domain() -> None:
    result = classify_text(
        _category("ai-security"),
        "Artificial Intelligence for Border Security Operations",
        "A planning system for border security personnel.",
    )
    assert result["decision"] == "AUTO_REJECTED"
    assert "border security" in result["exclusions"]


def test_ai_security_does_not_accept_general_machine_learning() -> None:
    result = classify_text(
        _category("ai-security"),
        "Deep Learning for Image Classification",
        "A neural network architecture for recognizing images.",
    )
    assert result["decision"] != "AUTO_ACCEPTED"


def test_publication_metadata_reads_citation_meta_tags() -> None:
    metadata = extract_page_metadata("""
      <html><head>
      <meta name="citation_title" content="A Reliable Paper">
      <meta name="citation_abstract" content="This is the verified abstract.">
      <meta name="citation_doi" content="10.1000/example">
      </head></html>
    """)
    assert metadata == {
        "title": "A Reliable Paper",
        "abstract": "This is the verified abstract.",
        "doi": "10.1000/example",
    }


def test_description_without_matching_title_cannot_supply_an_abstract() -> None:
    metadata = extract_page_metadata("""
      <html><head><title>Unrelated university home page</title>
      <meta name="description" content="General university description"></head></html>
    """)
    assert metadata["title"] == "Unrelated university home page"
    assert metadata["abstract"] == "General university description"
