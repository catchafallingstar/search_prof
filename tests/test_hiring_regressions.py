from ingestion.matchers import is_valid_signal_text
from ingestion.parse_hiring_signals import _scoped_professor_sentences


def test_soft_graduate_student_language_is_hiring_signal() -> None:
    assert is_valid_signal_text("I'd love to have a graduate student help me on this project.")
    assert is_valid_signal_text("It would be great to have a graduate student work with me on this research.")


def test_shared_page_scopes_recruiting_statement_to_named_professor() -> None:
    snapshot = {
        "blocks": [
            {"tag": "h2", "text": "Sara Sutton"},
            {"tag": "p", "text": "I am looking for a graduate student to work on machine learning and cybersecurity."},
            {"tag": "h2", "text": "Erik Fredericks"},
            {"tag": "p", "text": "It would be great to have a graduate student work with me on software engineering."},
            {"tag": "h2", "text": "Samah Mansour"},
            {"tag": "p", "text": "My research includes IoT security and machine learning."},
        ]
    }
    sara = _scoped_professor_sentences({"name": "Sara Sutton"}, snapshot)
    erik = _scoped_professor_sentences({"name": "Erik Fredericks"}, snapshot)
    samah = _scoped_professor_sentences({"name": "Samah Mansour"}, snapshot)
    assert sara == ["I am looking for a graduate student to work on machine learning and cybersecurity."]
    assert erik == ["It would be great to have a graduate student work with me on software engineering."]
    assert samah == []
