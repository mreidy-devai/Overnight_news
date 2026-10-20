"""Fetch and parse RSS/Atom feeds with bounded requests and failure isolation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .models import Article, FeedFailure

MAX_FEED_BYTES = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 12.0
USER_AGENT = "OvernightNewsDigest/0.1 (+personal RSS reader)"
Fetcher = Callable[[str, float], bytes]


def _http_fetch(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # nosec: configured feed URL
        return response.read(MAX_FEED_BYTES + 1)


def _local_name(tag: str) -> str:
    """Return a namespace-independent, lowercase XML element name."""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> str:
    """Return text from a direct child, avoiding accidental nested matches."""
    wanted = {name.lower() for name in names}
    for child in list(node):
        if _local_name(child.tag) in wanted:
            if child.text and child.text.strip():
                return child.text.strip()
            # Some feeds wrap text in XHTML elements.
            nested_text = " ".join(part.strip() for part in child.itertext() if part.strip())
            if nested_text:
                return nested_text
    return ""


def _parse_datetime(value: object) -> datetime | None:
    """Parse common RSS/Atom dates and normalize them to UTC."""
    if isinstance(value, struct_time):
        return datetime(*value[:6], tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def _entry_link(entry: ET.Element) -> str:
    """Read RSS text links or the preferred Atom alternate-link attribute."""
    for node in list(entry):
        if _local_name(node.tag) != "link":
            continue
        href = node.attrib.get("href", "").strip()
        if href:
            relation = node.attrib.get("rel", "alternate").lower()
            if relation == "alternate":
                return href
        if node.text and node.text.strip():
            return node.text.strip()
    return ""


def _feed_entries(root: ET.Element) -> tuple[str, list[ET.Element]]:
    """Return the feed title and its entries for RSS, Atom, or RSS 1.0."""
    root_name = _local_name(root.tag)
    if root_name == "feed":  # Atom
        return _child_text(root, "title"), [node for node in list(root) if _local_name(node.tag) == "entry"]
    if root_name == "rss":
        channel = next(
            (node for node in list(root) if _local_name(node.tag) == "channel"),
            root,
        )
        entries = [node for node in list(channel) if _local_name(node.tag) == "item"]
        return _child_text(channel, "title"), entries
    if root_name == "rdf":  # RSS 1.0 / RDF
        entries = [node for node in list(root) if _local_name(node.tag) == "item"]
        return _child_text(root, "title"), entries
    raise ValueError(f"Unsupported feed root element: {root_name}")


def _parse_entries(root: ET.Element, fallback_source: str) -> list[Article]:
    feed_title, entries = _feed_entries(root)
    source = feed_title or fallback_source
    articles: list[Article] = []

    for entry in entries:
        title = " ".join(_child_text(entry, "title").split())
        link = _entry_link(entry)
        if not title or not _valid_http_url(link):
            continue

        excerpt = _child_text(entry, "summary", "description", "encoded", "content")
        excerpt = " ".join(excerpt.split())[:8_000]
        published_raw = _child_text(entry, "pubdate", "published", "updated", "date")
        article_id = _child_text(entry, "guid", "id") or link
        articles.append(
            Article(
                title=title,
                url=link,
                source=source,
                summary=excerpt,
                published_at=_parse_datetime(published_raw),
                article_id=article_id,
            )
        )
    return articles


def collect_feeds(
    feeds: Iterable[dict[str, object]],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetcher: Fetcher = _http_fetch,
) -> tuple[list[Article], list[FeedFailure]]:
    """Collect enabled feeds; isolate failures so other sources can continue.

    Feed tables require ``name`` and ``url``. The ``enabled`` setting defaults
    to true. Fetches are time-limited and response bodies are capped at 5 MB.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    articles: list[Article] = []
    failures: list[FeedFailure] = []
    for feed in feeds:
        if not bool(feed.get("enabled", True)):
            continue

        name = str(feed.get("name") or feed.get("url") or "Unnamed feed").strip()
        url = str(feed.get("url") or "").strip()
        if not _valid_http_url(url):
            failures.append(FeedFailure(name, url, "Feed URL must be a valid HTTP or HTTPS URL"))
            continue

        try:
            payload = fetcher(url, timeout)
            if len(payload) > MAX_FEED_BYTES:
                raise ValueError("Feed exceeds the 5 MB response limit")
            root = ET.fromstring(payload)
            articles.extend(_parse_entries(root, name))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            failures.append(FeedFailure(name, url, f"{type(exc).__name__}: {exc}"))
        except Exception as exc:  # unexpected parser/fetcher failure should not block other feeds
            failures.append(FeedFailure(name, url, f"Unexpected {type(exc).__name__}: {exc}"))

    return articles, failures
