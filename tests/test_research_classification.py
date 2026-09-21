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


def test_broad_single_word_education_is_not_auto_accepted_from_incidental_word() -> None:
    category = {
        "category_key": "education",
        "canonical_name": "Education",
        "description": "Research focused on Education.",
        "aliases": ["Education"],
        "positive_terms": [],
        "exclusion_terms": [],
        "breadth": "BROAD",
    }
    result = classify_text(
        category,
        "Empirical: A scientific software library for research, education, and public engagement",
        "A software library supporting reproducible computational research and public engagement.",
    )
    assert result["decision"] != "AUTO_ACCEPTED"
    assert result["combined_score"] <= 54


def _seed_category(name: str) -> dict:
    from ingestion.research_classification import category_definitions
    definition = next(item for item in category_definitions() if item.name == name)
    return {
        "category_key": definition.key,
        "canonical_name": definition.name,
        "description": definition.description,
        "aliases": list(definition.aliases),
        "positive_terms": list(definition.positive_terms),
        "exclusion_terms": list(definition.exclusions),
        "breadth": definition.breadth,
    }


def test_separate_ai_and_security_interests_do_not_fuse_into_ai_security() -> None:
    from ingestion.research_classification import classify_interest_units
    result = classify_interest_units(
        _category("ai-security"),
        ["Artificial Intelligence", "Security", "Data Science"],
    )
    assert result["decision"] != "AUTO_ACCEPTED"


def test_single_combined_ai_security_paper_is_accepted() -> None:
    result = classify_text(
        _category("ai-security"),
        "An Empirical Study Using Auto Machine Learning to Detect Zero-Day Attacks",
        "Machine learning methods detect previously unseen cyber attacks.",
    )
    assert result["decision"] == "AUTO_ACCEPTED"


def test_cybersecurity_accepts_zero_day_attacks_and_literal_field() -> None:
    category = _seed_category("Cybersecurity")
    literal = classify_text(category, "Introducing Zero Trust in a Cybersecurity Course")
    zero_day = classify_text(category, "Machine Learning to Detect Zero-Day Attacks")
    assert literal["decision"] == "AUTO_ACCEPTED"
    assert zero_day["decision"] == "AUTO_ACCEPTED"


def test_robotic_process_automation_is_not_robotics() -> None:
    category = _seed_category("Robotics")
    result = classify_text(
        category,
        "Robotic Process Automation Implementation Case Studies in Accounting",
        "We automate repetitive business accounting processes using RPA.",
    )
    assert result["decision"] == "AUTO_REJECTED"


def test_physical_robot_is_robotics() -> None:
    category = _seed_category("Robotics")
    result = classify_text(
        category,
        "Developing a Robotic Dog for Run-Time Software Engineering Research",
        "A physical autonomous robot is used for experiments.",
    )
    assert result["decision"] == "AUTO_ACCEPTED"


def test_explicit_subfields_map_to_broad_parent_fields() -> None:
    from ingestion.research_classification import classify_interest_units
    cases = [
        ("Physics", "Condensed Matter Physics"),
        ("Psychology", "Educational Psychology"),
        ("Neuroscience", "Computational Neuroscience"),
        ("Education", "Science Education"),
        ("History", "Modern European History"),
    ]
    for field, interest in cases:
        result = classify_interest_units(_seed_category(field), [interest])
        assert result["decision"] == "AUTO_ACCEPTED", (field, interest, result)


def test_organization_name_does_not_become_education_research() -> None:
    from ingestion.research_classification import classify_interest_units
    result = classify_interest_units(_seed_category("Education"), ["American Council on Education"])
    assert result["decision"] != "AUTO_ACCEPTED"


def test_broad_multiword_fields_do_not_form_from_separate_words() -> None:
    social_work = classify_text(
        _seed_category("Social work"),
        "Tangible Privacy for Computer Supported Cooperative Work and Social Computing",
        "We study privacy in social computing systems and how people work with devices.",
    )
    mental_health = classify_text(
        _seed_category("Mental health"),
        "False balance in public health reporting and mental disability language",
        "A communication study about public health reporting.",
    )
    assert social_work["decision"] != "AUTO_ACCEPTED"
    assert mental_health["decision"] != "AUTO_ACCEPTED"
