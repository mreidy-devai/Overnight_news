"""Render source-linked HTML and plain-text morning digests."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TITLE = "Your Morning News Digest"
FALLBACK_SUMMARY = (
    "No summary was available from the feed. Open the original article for details."
)


def _date(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "Publication time unavailable"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "Publication time unavailable"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%b %d, %Y · %I:%M %p %Z").strip()


def _safe_http_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return value
    return ""


def _generated_day(value: object) -> str:
    if isinstance(value, str):
        try:
            generated = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return generated.astimezone().strftime("%B %d, %Y")
        except ValueError:
            pass
    return datetime.now().astimezone().strftime("%B %d, %Y")


def build_digest(
    payload: dict[str, Any],
    *,
    title: str = DEFAULT_TITLE,
) -> tuple[str, str, str]:
    """Return ``(subject, html_body, text_body)`` for an email-ready digest."""
    raw_articles = payload.get("articles", [])
    raw_summaries = payload.get("ai_summaries", [])
    articles = raw_articles if isinstance(raw_articles, list) else []
    summaries = raw_summaries if isinstance(raw_summaries, list) else []
    summaries_by_url = {
        item["url"]: item
        for item in summaries
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    }

    day = _generated_day(payload.get("generated_at"))
    safe_title = " ".join(str(title).split())[:160] or DEFAULT_TITLE
    subject = f"{safe_title} — {day}"
    html_parts = [
        '<!doctype html><html><head><meta charset="utf-8"></head>',
        '<body style="font-family:Arial,sans-serif;line-height:1.5;max-width:720px;margin:auto;color:#222">',
        f"<h1>{escape(safe_title)}</h1>",
        f"<p>Good morning. Here are {len(articles)} stories collected for your briefing.</p>",
    ]
    text_parts = [
        safe_title,
        "=" * len(safe_title),
        f"Good morning. Here are {len(articles)} stories collected for your briefing.",
        "",
    ]

    if not articles:
        empty_message = (
            "No stories matched your configured feeds and time window. "
            "Check feed settings or try again later."
        )
        html_parts.append(f"<p>{empty_message}</p>")
        text_parts.extend([empty_message, ""])

    for index, article in enumerate(articles, start=1):
        if not isinstance(article, dict):
            continue

        raw_url = article.get("url", "")
        safe_url = _safe_http_url(raw_url)
        headline = str(article.get("title") or "Untitled story")[:500]
        source = str(article.get("source") or "Unknown source")[:200]
        summary_item = summaries_by_url.get(raw_url, {})
        summary = str(
            summary_item.get("summary")
            or article.get("summary")
            or FALLBACK_SUMMARY
        )[:1_600]
        uncertainty = str(summary_item.get("uncertainty") or "")[:500]
        date_label = _date(article.get("published_at"))

        html_parts.append(
            '<article style="padding:16px 0;border-bottom:1px solid #ddd">'
            f"<h2>{index}. {escape(headline)}</h2>"
            f"<p><strong>{escape(source)}</strong> · {escape(date_label)}</p>"
            f"<p>{escape(summary)}</p>"
        )
        if uncertainty:
            html_parts.append(
                f'<p style="color:#555;font-size:0.9em"><strong>Note:</strong> {escape(uncertainty)}</p>'
            )
        if safe_url:
            html_parts.append(
                f'<p><a href="{escape(safe_url, quote=True)}">Read the original report</a></p>'
            )
        html_parts.append("</article>")

        text_parts.extend(
            [f"{index}. {headline}", f"Source: {source} | {date_label}", summary]
        )
        if uncertainty:
            text_parts.append(f"Note: {uncertainty}")
        if safe_url:
            text_parts.append(f"Original report: {safe_url}")
        text_parts.append("")

    feed_failures = payload.get("feed_failures", [])
    summary_failures = payload.get("summary_failures", [])
    if isinstance(feed_failures, list) and feed_failures:
        message = f"Note: {len(feed_failures)} feed(s) could not be retrieved."
        html_parts.append(f'<p style="color:#666;font-size:0.85em">{message}</p>')
        text_parts.append(message)
    if isinstance(summary_failures, list) and summary_failures:
        message = (
            f"Note: AI summarization failed for {len(summary_failures)} story/stories; "
            "feed text is shown where available."
        )
        html_parts.append(f'<p style="color:#666;font-size:0.85em">{message}</p>')
        text_parts.append(message)

    footer = (
        "Generated locally by Overnight News Digest. Summaries may contain errors; "
        "consult original reporting."
    )
    html_parts.append(
        f'<footer style="color:#777;font-size:0.8em;margin-top:24px">{footer}</footer>'
        "</body></html>"
    )
    text_parts.extend([footer, ""])
    return subject, "".join(html_parts), "\n".join(text_parts)
