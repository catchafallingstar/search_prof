import pytest

from radar_store import normalize_topic_query


def test_topic_query_keeps_existing_ascii_behavior():
    assert normalize_topic_query("  AI---security ") == "ai security"
    assert normalize_topic_query("Asian Study") == "asian studies"


def test_topic_query_preserves_unicode_letters():
    assert normalize_topic_query("Café security") == "café security"
    assert normalize_topic_query("机器学习") == "机器学习"


def test_topic_query_rejects_too_short_input():
    with pytest.raises(ValueError):
        normalize_topic_query("AI")
