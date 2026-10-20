from pathlib import Path
import pytest

from news_digest.config import load_config


def test_loads_valid_config(tmp_path: Path):
    config_file = tmp_path / "feeds.toml"
    config_file.write_text('lookback_hours = 12\nmax_stories = 5\n[[feeds]]\nname = "A"\nurl = "https://example.com/rss"\nenabled = true\n')
    result = load_config(config_file)
    assert result["lookback_hours"] == 12
    assert result["feeds"][0]["name"] == "A"


def test_rejects_invalid_lookback(tmp_path: Path):
    config_file = tmp_path / "feeds.toml"
    config_file.write_text('lookback_hours = 0\n')
    with pytest.raises(ValueError, match="lookback_hours"):
        load_config(config_file)


def test_rejects_non_boolean_feed_enabled(tmp_path: Path):
    config_file = tmp_path / "feeds.toml"
    config_file.write_text('[[feeds]]\nname = "A"\nurl = "https://example.com/rss"\nenabled = "yes"\n')
    with pytest.raises(ValueError, match="enabled"):
        load_config(config_file)


def test_rejects_invalid_model_timeout(tmp_path: Path):
    config_file = tmp_path / "feeds.toml"
    config_file.write_text('[model]\ntimeout_seconds = 0\n')
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_config(config_file)
