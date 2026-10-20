from datetime import datetime, timezone

from news_digest.dedup import deduplicate, filter_by_window, normalize_url
from news_digest.models import Article


def article(url, title="Title", published_at=None):
    return Article(title=title, url=url, source="Test", published_at=published_at)


def test_normalize_url_removes_tracking_parameters_and_fragment():
    assert normalize_url("HTTPS://News.Example/a/?utm_source=x&story=1#top") == "https://news.example/a?story=1"


def test_deduplicate_keeps_first_article_by_normalized_url():
    items = [
        article("https://example.com/story?utm_source=a", "First"),
        article("https://example.com/story#comments", "Duplicate"),
        article("https://example.com/another", "Different"),
    ]
    result = deduplicate(items)
    assert [item.title for item in result] == ["First", "Different"]


def test_filter_by_window_keeps_recent_and_undated_but_drops_old_and_future():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    items = [
        article("https://example.com/recent", published_at=datetime(2026, 10, 20, 8, tzinfo=timezone.utc)),
        article("https://example.com/old", published_at=datetime(2026, 10, 18, 8, tzinfo=timezone.utc)),
        article("https://example.com/future", published_at=datetime(2026, 10, 21, 8, tzinfo=timezone.utc)),
        article("https://example.com/undated"),
    ]
    result = filter_by_window(items, lookback_hours=24, now=now)
    assert [item.url for item in result] == ["https://example.com/recent", "https://example.com/undated"]
