from news_digest.collector import collect_feeds

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test Wire</title>
<item><title>First story</title><link>https://example.com/1</link><guid>id-1</guid>
<pubDate>Tue, 20 Oct 2026 08:00:00 GMT</pubDate><description>A short summary</description></item>
<item><title>Invalid story</title><link>javascript:alert(1)</link></item>
</channel></rss>'''


def test_collects_valid_entries_and_skips_unsafe_links():
    def fake_fetch(url, timeout):
        assert timeout > 0
        return RSS

    articles, failures = collect_feeds(
        [{"name": "Test", "url": "https://example.com/feed.xml", "enabled": True}],
        fetcher=fake_fetch,
    )
    assert failures == []
    assert len(articles) == 1
    assert articles[0].title == "First story"
    assert articles[0].source == "Test Wire"
    assert articles[0].url == "https://example.com/1"
    assert articles[0].summary == "A short summary"
    assert articles[0].published_at is not None


def test_one_feed_failure_does_not_stop_other_feeds():
    def fake_fetch(url, timeout):
        if "bad" in url:
            raise TimeoutError("simulated timeout")
        return RSS

    articles, failures = collect_feeds(
        [
            {"name": "Bad", "url": "https://bad.example/feed", "enabled": True},
            {"name": "Good", "url": "https://good.example/feed", "enabled": True},
            {"name": "Disabled", "url": "https://disabled.example/feed", "enabled": False},
        ],
        fetcher=fake_fetch,
    )
    assert len(articles) == 1
    assert len(failures) == 1
    assert failures[0].source == "Bad"


def test_rejects_non_http_urls_without_fetching():
    articles, failures = collect_feeds([{"name": "Bad URL", "url": "file:///etc/passwd"}])
    assert articles == []
    assert "HTTP or HTTPS" in failures[0].error
