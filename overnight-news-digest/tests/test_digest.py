from news_digest.digest import build_digest


def test_digest_includes_source_link_and_escapes_untrusted_text():
    payload = {
        "generated_at": "2026-10-20T12:00:00+00:00",
        "articles": [{"title": "<script>alert(1)</script>", "url": "https://news.example/story?a=1&b=2", "source": "Example & Co", "summary": "Feed excerpt", "published_at": None}],
        "ai_summaries": [{"url": "https://news.example/story?a=1&b=2", "summary": "A grounded summary.", "uncertainty": "Still developing."}],
    }
    subject, html, text = build_digest(payload)
    assert "October 20, 2026" in subject
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "https://news.example/story?a=1&amp;b=2" in html
    assert "A grounded summary." in html and "A grounded summary." in text
    assert "Still developing." in text


def test_empty_digest_has_helpful_message():
    subject, html, text = build_digest({"articles": [], "ai_summaries": []})
    assert "Morning News Digest" in subject
    assert "No stories matched" in html
    assert "No stories matched" in text
