from ingestion.program_gpa import extract_program_gpa


def test_extracts_hard_minimum_without_confusing_recommendation() -> None:
    result = extract_program_gpa("Applicants must have a minimum GPA of 3.0 for admission.")
    assert result == {
        "policy": "HARD_MINIMUM",
        "minimum": 3.0,
        "evidence": "Applicants must have a minimum GPA of 3.0 for admission.",
    }


def test_extracts_recommended_gpa_separately() -> None:
    result = extract_program_gpa("A GPA of 3.5 is normally recommended for competitive applicants.")
    assert result and result["policy"] == "RECOMMENDED"
    assert result["minimum"] == 3.5


def test_silence_does_not_mean_no_cutoff() -> None:
    assert extract_program_gpa("Applications are reviewed by the department.") is None


def test_no_minimum_requires_explicit_language() -> None:
    result = extract_program_gpa("There is no formal minimum GPA for this doctoral program.")
    assert result and result["policy"] == "NO_FORMAL_MINIMUM"
