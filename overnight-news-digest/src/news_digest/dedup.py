"""Deterministic article deduplication and publication-window filtering."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article

_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    """Normalize a URL for matching while preserving the original article URL."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def _duplicate_key(article: Article) -> str:
    normalized_url = normalize_url(article.url)
    if normalized_url and normalized_url != "/":
        return normalized_url
    if article.article_id:
        return article.article_id.strip()
    return hashlib.sha256(article.title.casefold().encode("utf-8")).hexdigest()


def deduplicate(articles: list[Article]) -> list[Article]:
    """Keep the first occurrence of each normalized URL or fallback identifier."""
    seen: set[str] = set()
    unique_articles: list[Article] = []
    for article in articles:
        key = _duplicate_key(article)
        if key in seen:
            continue
        seen.add(key)
        unique_articles.append(article)
    return unique_articles


def filter_by_window(
    articles: list[Article],
    *,
    lookback_hours: int,
    now: datetime | None = None,
) -> list[Article]:
    """Keep undated entries and entries published within the lookback window.

    Undated entries are retained because many feeds omit timestamps. Future-dated
    entries are excluded rather than being treated as recent news.
    """
    if lookback_hours < 1:
        raise ValueError("lookback_hours must be at least 1")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    cutoff = current_utc - timedelta(hours=lookback_hours)

    selected: list[Article] = []
    for article in articles:
        published = article.published_at
        if published is None:
            selected.append(article)
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        published_utc = published.astimezone(timezone.utc)
        if cutoff <= published_utc <= current_utc:
            selected.append(article)
    return selected
