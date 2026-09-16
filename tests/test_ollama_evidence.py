from ingestion.ollama_evidence import validate_review


def test_ollama_quotes_must_exist_in_supplied_record() -> None:
    source = "Ruiting Wang | PhD Student | ruiting.wang@wsu.edu"
    valid = {
        "record_type": "STUDENT",
        "conflicts": [],
        "evidence": {"name": "Ruiting Wang", "role": "PhD Student"},
    }
    assert validate_review(valid, source) == ()
    invented = {**valid, "evidence": {"role": "Assistant Professor"}}
    assert "evidence.role is not in source" in validate_review(invented, source)


def test_ollama_schema_rejects_uncontrolled_record_types() -> None:
    errors = validate_review(
        {"record_type": "MAYBE_PROFESSOR", "conflicts": [], "evidence": {}},
        "Ada Lovelace",
    )
    assert "invalid record_type" in errors
