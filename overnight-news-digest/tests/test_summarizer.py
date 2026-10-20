import json

import pytest

from news_digest.models import Article
from news_digest.summarizer import summarize_article


def sample_article(summary="The city council approved a new public transit plan on Tuesday."):
    return Article(
        title="Council approves transit plan",
        url="https://news.example/transit",
        source="Example News",
        summary=summary,
    )


def fake_transport_for(result, captured=None):
    def transport(url, payload, timeout):
        if captured is not None:
            captured.update(url=url, payload=json.loads(payload), timeout=timeout)
        return json.dumps({"response": json.dumps(result)}).encode()
    return transport


def test_summarize_article_parses_structured_local_model_response():
    result = summarize_article(
        sample_article(),
        model="test-model",
        transport=fake_transport_for({
            "summary": "The council approved a transit plan on Tuesday.",
            "key_points": ["The council approved the plan."],
            "uncertainty": "The excerpt gives no budget details.",
        }),
    )
    assert result.summary == "The council approved a transit plan on Tuesday."
    assert result.key_points == ("The council approved the plan.",)
    assert "budget" in result.uncertainty
    assert result.model == "test-model"


def test_sends_request_to_local_ollama_endpoint_and_uses_json_mode():
    captured = {}
    summarize_article(sample_article(), model="test-model", transport=fake_transport_for(
        {"summary": "Summary", "key_points": [], "uncertainty": ""}, captured
    ))
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["format"] == "json"
    assert "untrusted article text" in captured["payload"]["prompt"]


def test_empty_feed_excerpt_does_not_call_model_or_invent_summary():
    def should_not_run(*args):
        raise AssertionError("Model should not be called without article text")

    result = summarize_article(sample_article(""), model="test-model", transport=should_not_run)
    assert "not provide enough article text" in result.summary
    assert result.key_points == ()


def test_invalid_model_json_is_rejected():
    def bad_transport(url, payload, timeout):
        return json.dumps({"response": "not json"}).encode()

    with pytest.raises(ValueError, match="invalid JSON"):
        summarize_article(sample_article(), model="test-model", transport=bad_transport)


def test_missing_summary_is_rejected():
    with pytest.raises(ValueError, match="usable summary"):
        summarize_article(sample_article(), model="test-model", transport=fake_transport_for({"summary": "  "}))


def test_model_error_is_reported_as_local_runtime_problem():
    def unavailable(url, payload, timeout):
        raise OSError("connection refused")

    with pytest.raises(RuntimeError, match="local Ollama"):
        summarize_article(sample_article(), model="test-model", transport=unavailable)
