"""Command-line entry point for the Overnight News Digest workflow."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .collector import collect_feeds
from .config import load_config
from .dedup import deduplicate, filter_by_window
from .digest import build_digest
from .email_sender import send_email
from .models import Article, FeedFailure
from .summarizer import summarize_articles

LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT = "data/latest_digest.json"
DEFAULT_CONFIG = "config/feeds.toml"
DEFAULT_DIGEST_TITLE = "Your Morning News Digest"


def _serialize_failures(failures: list[FeedFailure]) -> list[dict[str, str]]:
    """Convert failure dataclasses to a stable JSON-friendly shape."""
    return [
        {"source": failure.source, "url": failure.url, "error": failure.error}
        for failure in failures
    ]


def _summarize(
    articles: list[Article],
    config: dict[str, Any],
    *,
    enabled: bool,
    model_override: str | None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    if not enabled:
        return [], []

    model_config = config.get("model", {})
    model_name = (model_override or model_config.get("name", "")).strip()
    if not model_name:
        raise ValueError("Set model.name in config or pass --model when using --summarize")

    return summarize_articles(
        articles,
        model=model_name,
        base_url=str(model_config.get("base_url", "http://127.0.0.1:11434")),
        timeout=float(model_config.get("timeout_seconds", 90)),
        max_input_chars=int(model_config.get("max_input_chars", 6000)),
    )


def _collect_articles(config: dict[str, Any]) -> tuple[list[Article], list[FeedFailure]]:
    """Collect, deduplicate, time-filter, and cap the article list."""
    articles, failures = collect_feeds(config.get("feeds", []))
    articles = deduplicate(articles)
    articles = filter_by_window(
        articles,
        lookback_hours=config.get("lookback_hours", 24),
    )
    return articles[: config.get("max_stories", 8)], failures


def _build_payload(
    articles: list[Article],
    feed_failures: list[FeedFailure],
    summaries: list[dict[str, object]],
    summary_failures: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "articles": [article.to_dict() for article in articles],
        "ai_summaries": summaries,
        "summary_failures": summary_failures,
        "feed_failures": _serialize_failures(feed_failures),
    }


def _write_json(payload: dict[str, object], output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination


def _write_email_preview(html_body: str, preview_path: str | Path) -> Path:
    destination = Path(preview_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html_body, encoding="utf-8")
    return destination


def run(
    config_path: str,
    output_path: str,
    *,
    summarize: bool = False,
    model_override: str | None = None,
    email_preview: str | None = None,
    send_email_now: bool = False,
) -> int:
    """Run the digest pipeline and return 2 if one or more feeds failed."""
    config = load_config(config_path)
    articles, feed_failures = _collect_articles(config)
    summaries, summary_failures = _summarize(
        articles,
        config,
        enabled=summarize,
        model_override=model_override,
    )
    payload = _build_payload(articles, feed_failures, summaries, summary_failures)

    if email_preview or send_email_now:
        email_config = config.get("email", {})
        title = email_config.get("title", DEFAULT_DIGEST_TITLE)
        subject, html_body, text_body = build_digest(payload, title=title)
        if email_preview:
            preview_destination = _write_email_preview(html_body, email_preview)
            LOGGER.info("Saved email preview to %s", preview_destination)
        if send_email_now:
            send_email(subject=subject, html_body=html_body, text_body=text_body)
            LOGGER.info("Morning digest email sent")

    destination = _write_json(payload, output_path)
    LOGGER.info(
        "Collected %d articles; %d feed failures; saved %s",
        len(articles),
        len(feed_failures),
        destination,
    )
    return 0 if not feed_failures else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect RSS/Atom stories for a morning news digest."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to TOML feed configuration")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--summarize", action="store_true", help="Summarize stories with local Ollama")
    parser.add_argument("--model", help="Override the model name configured in the TOML file")
    parser.add_argument(
        "--email-preview",
        help="Write a local HTML email preview to this path; does not send email",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Send the digest by SMTP (requires email environment variables)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        exit_code = run(
            args.config,
            args.output,
            summarize=args.summarize,
            model_override=args.model,
            email_preview=args.email_preview,
            send_email_now=args.send_email,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
