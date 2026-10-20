# Overnight News Digest

A local-first Python application that collects RSS/Atom articles, removes duplicates, filters by publication time, optionally summarizes feed excerpts with a locally running Ollama model, and creates an email-ready morning digest. It can save a JSON snapshot, write an HTML preview, or send an email through SMTP when explicitly requested.

## Current status

Implemented in code:

- RSS 2.0, Atom, and RSS 1.0 feed parsing
- HTTP(S) feed fetching with timeouts and a 5 MB response limit
- Per-feed failure isolation
- URL normalization and duplicate removal
- Configurable publication window and story limit
- JSON output
- Optional local Ollama summaries with structured output validation
- HTML and plain-text digest rendering
- Local HTML email preview
- SMTP delivery using STARTTLS and environment-based credentials
- Automated unit tests

Not yet configured or verified on your Mac: the live Ollama model, real feed collection, delivery to your email provider, and unattended scheduling. Scheduling, persistent history, retries/catch-up, relevance ranking, and full-article extraction are not included yet.

## Requirements

- Python 3.11 or newer
- Internet access for feeds and (if enabled) SMTP delivery
- Ollama installed and running locally only if you want AI summaries
- No runtime Python dependencies; `pytest` is used for tests

## Install

From the project directory:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

## Configure feeds

Edit `config/feeds.toml`. Replace the disabled example with RSS/Atom feed URLs you are allowed to access.

```toml
lookback_hours = 24
max_stories = 8

[[feeds]]
name = "Publisher name"
url = "https://publisher.example/rss.xml"
enabled = true

[model]
name = ""
base_url = "http://127.0.0.1:11434"
timeout_seconds = 90
max_input_chars = 6000

[email]
title = "Your Morning News Digest"
```

Feeds are enabled by default if `enabled` is omitted. Disabled placeholder feeds are skipped. Feed descriptions may contain only excerpts; the application does not fetch or extract full article pages. Respect publisher terms and rate limits.

## Run collection

```bash
python -m news_digest.main --config config/feeds.toml --output data/latest_digest.json
```

Or use the installed command:

```bash
news-digest --config config/feeds.toml --output data/latest_digest.json
```

The JSON output includes collected articles, AI summaries, summary failures, and feed failures. Exit code `0` means all enabled feeds were retrieved successfully; exit code `2` means one or more feeds failed, even if other feeds succeeded. Configuration or file errors are reported by the command-line parser.

## Optional local AI summaries

1. Install Ollama for macOS from [ollama.com/download/mac](https://ollama.com/download/mac) and start it.
2. Choose and install a compatible model. For example, you can benchmark a small model such as `qwen2.5:1.5b` if it is available and suitable for your machine:

   ```bash
   ollama pull qwen2.5:1.5b
   ```

3. Set `model.name = "qwen2.5:1.5b"` in `config/feeds.toml`.
4. Run:

   ```bash
   python -m news_digest.main --config config/feeds.toml --output data/latest_digest.json --summarize
   ```

Use `--model MODEL_TAG` to override the configured model for one run. The summarizer sends only the headline, publisher, URL metadata, and feed-provided excerpt to the local Ollama endpoint. If an excerpt is missing, it avoids calling the model and records that the available information is insufficient. Model failures are isolated per article.

**Hardware note:** an 8 GB Mac may be constrained by model size, macOS, and other running applications. Start with a small model and verify its actual memory use, speed, license, and output quality locally. The application uses the local Ollama HTTP endpoint; it does not intentionally call a hosted AI API.

AI output can be wrong or incomplete. Treat summaries as a reading aid and consult the original source before relying on a claim.

## Preview the email

Generate a local HTML preview without sending anything:

```bash
python -m news_digest.main \
  --config config/feeds.toml \
  --output data/latest_digest.json \
  --summarize \
  --email-preview data/email-preview.html
```

Open `data/email-preview.html` in your browser and check the stories and source links. The preview command does **not** send email. If you omit `--summarize`, the digest uses feed excerpts when available.

## Send the email

Configure SMTP settings in your shell. These are examples, not real credentials:

```bash
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USERNAME="your-smtp-username"
export SMTP_PASSWORD="your-app-password"
export DIGEST_EMAIL_FROM="you@example.com"
export DIGEST_EMAIL_TO="you@example.com"
```

Then explicitly request delivery:

```bash
python -m news_digest.main \
  --config config/feeds.toml \
  --output data/latest_digest.json \
  --summarize \
  --send-email
```

The sender uses SMTP with STARTTLS and a plain-text fallback. Provider requirements vary; an app password or SMTP-specific credentials may be required. Never commit credentials or put passwords in `feeds.toml`. Test with a non-critical inbox before considering automation.

## Run tests

```bash
pytest
```

Tests use fixtures and mocked network/model behavior; they do not require internet access, a running Ollama model, or a real SMTP account.

## Security and privacy notes

- Only HTTP(S) feed URLs are accepted.
- Feed responses are limited to 5 MB and requests have a timeout.
- Feed content is untrusted input. The summarization prompt explicitly treats it as source material, not instructions; this reduces risk but does not guarantee model behavior.
- Keep SMTP secrets out of source control and configuration files.
- Review generated content and source links before relying on or forwarding the digest.
- The application does not currently include a scheduler. Configure unattended runs only after validating the entire workflow on your machine.
