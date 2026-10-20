"""Load and validate the TOML configuration for Overnight News Digest."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_MAX_STORIES = 8


def _positive_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    # bool is a subclass of int, but is not a meaningful numeric setting here.
    if type(value) is not int or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _validate_table(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Config '{key}' must be a table")
    return value


def _validate_feeds(config: dict[str, Any]) -> None:
    feeds = config.get("feeds", [])
    if not isinstance(feeds, list):
        raise ValueError("Config 'feeds' must be an array of tables")

    for index, feed in enumerate(feeds, start=1):
        if not isinstance(feed, dict):
            raise ValueError(f"Feed entry {index} must be a table")
        if not isinstance(feed.get("name"), str) or not feed["name"].strip():
            raise ValueError(f"Feed entry {index} requires a non-empty name")
        if not isinstance(feed.get("url"), str) or not feed["url"].strip():
            raise ValueError(f"Feed entry {index} requires a non-empty URL")
        if "enabled" in feed and type(feed["enabled"]) is not bool:
            raise ValueError(f"Feed entry {index} field 'enabled' must be true or false")


def _validate_model(config: dict[str, Any]) -> None:
    model = _validate_table(config, "model")
    if "name" in model and not isinstance(model["name"], str):
        raise ValueError("model.name must be a string")
    if "base_url" in model:
        base_url = model["base_url"]
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("model.base_url must be an HTTP(S) URL")
    timeout = model.get("timeout_seconds", 90)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("model.timeout_seconds must be a positive number")
    max_chars = model.get("max_input_chars", 6000)
    if type(max_chars) is not int or max_chars < 100:
        raise ValueError("model.max_input_chars must be an integer of at least 100")


def load_config(path: str | Path) -> dict[str, Any]:
    """Read TOML and validate supported settings before starting network work."""
    config_path = Path(path)
    with config_path.open("rb") as file:
        config = tomllib.load(file)

    _validate_feeds(config)
    _positive_int(config, "lookback_hours", DEFAULT_LOOKBACK_HOURS)
    _positive_int(config, "max_stories", DEFAULT_MAX_STORIES)
    _validate_model(config)

    email = _validate_table(config, "email")
    if "title" in email and not isinstance(email["title"], str):
        raise ValueError("email.title must be a string")
    return config
