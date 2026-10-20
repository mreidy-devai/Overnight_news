import json
from pathlib import Path

from news_digest.main import run


def test_run_writes_json_and_email_preview_without_network(tmp_path: Path):
    config_path = tmp_path / "feeds.toml"
    config_path.write_text(
        'lookback_hours = 24\n'
        'max_stories = 8\n'
        '[email]\n'
        'title = "Test Digest"\n'
    )
    output_path = tmp_path / "nested" / "digest.json"
    preview_path = tmp_path / "nested" / "preview.html"

    exit_code = run(
        str(config_path),
        str(output_path),
        email_preview=str(preview_path),
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["article_count"] == 0
    assert payload["feed_failures"] == []
    assert "Test Digest" in preview_path.read_text(encoding="utf-8")
    assert "No stories matched" in preview_path.read_text(encoding="utf-8")
