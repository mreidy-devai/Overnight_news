# Sloth News Digest

An overnight news brief written by an open-weight model running on your own machine.

While you sleep, one script pulls your RSS/Atom feeds, asks a local model (via [Ollama](https://ollama.com)) to score each story against your interests and write a one-sentence summary, and drops a short Markdown brief in a folder. You wake up, open `digests/latest.md`, and you're done.

```
# Morning digest: Tuesday, October 20, 2026

_12 new stories from 5 feeds · ranked by qwen2.5:7b on this machine · 05:31_

**TL;DR** ...two sentences...

## Worth your coffee
1. **[Story title](link)** — one-sentence summary
   _Source · 2h ago · 9/10_

## Also on the radar
- [Headline](link) (Source, 5h ago)
```

## Why it's built this way

- **Open model at the core.** Ranking and summarizing run through a local model in Ollama. No API key, no per-token bill, no account.
- **Your reading profile stays local.** The interests list and the stories being scored go to `localhost`, not to a vendor.
- **Swappable.** Change one line (`model = ...`) to try a different open-weight model. Edit the interests in plain English to change what gets surfaced.
- **Degrades gracefully.** If Ollama isn't running or the model isn't installed, you still get a keyword-ranked digest instead of nothing.
- **No dependencies.** Python 3.11+ standard library only.

## Quick start

```bash
# 1. Install Ollama (https://ollama.com) and pull a model
ollama pull qwen2.5:7b        # roughly 5 GB of memory; use a smaller model on small machines

# 2. Edit feeds.toml: your feeds and your interests

# 3. Run it
python3 digest.py
# -> digests/2026-10-20.md and digests/latest.md
```

Useful flags:

| Flag | What it does |
| --- | --- |
| `--stdout` | Print the digest, write nothing, remember nothing (good for trying settings) |
| `--hours 48` | Look back further than `hours_back` |
| `--model llama3.2:3b` | Try another model without editing the config |
| `--no-model` | Skip the model entirely; keyword ranking only |
| `--ignore-seen` | Include stories from earlier digests |
| `--no-fulltext` | Skip reading full articles (faster; feed summaries only) |
| `--weekly` | Include the "Last week, just below your bar" section today, whatever day it is |

## Make it actually lazy: schedule it

**Linux/macOS (cron)**, every day at 5:30:

```
30 5 * * * /usr/bin/python3 /full/path/to/digest.py >> /full/path/to/digest.log 2>&1
```

Laptops that are asleep at 5:30 will skip a cron job. Use a systemd timer with `Persistent=true`, a macOS launchd agent, or on Windows Task Scheduler's "run as soon as possible after a scheduled start is missed" option so the digest runs when the machine wakes.

Make sure Ollama is running at that time. The desktop apps start it in the background; on Linux, `systemctl enable --now ollama`. The first call after boot is slow while the model loads, which is why `timeout_seconds` defaults to 300.

## How it works

1. Fetch each feed (newest `max_items_per_feed`, within `hours_back`).
2. Drop repeats: exact titles, plus near-identical titles (word overlap at or above `dup_threshold`) within the day and against the last `keep_days` days. If another feed ran the same story, the kept one shows "(also: Other Feed)".
3. Send stories to the model in batches of `batch_size`; it returns a 0-10 score and a one-sentence blurb for each, as JSON.
4. Stories at or above `min_score` become "Worth your coffee"; the next few become headline-only "Also on the radar".
5. For the top `fulltext_top_n` stories, fetch the article, keep only the paragraph text, and have the model write a one-to-two-sentence summary from it. Those entries are marked "summarized from the full article". Pages that are paywalled, thin, or not HTML keep the feed-based blurb.
6. One more model call writes the two-sentence TL;DR.
7. On `weekly_day` (or with `--weekly`), add "Last week, just below your bar": the highest-scoring stories from the past 7 days that did not make a top list.
8. Write `digests/YYYY-MM-DD.md` (never overwriting an earlier digest from the same day) and `latest.md`.

If a batch comes back unusable twice, those stories are ranked by keyword match instead. The header of each digest says whether the model, keywords, or a mix did the ranking.

## Notes and limits

- Scoring uses each feed's headline and summary only. Full articles are read just for the top `fulltext_top_n` stories, so a feed that publishes only headlines gives thin scores for everything below that.
- Fetching feeds and articles needs the internet (publishers see a normal request from your IP). Scoring and summarizing do not.
- Feed text and article text are untrusted input. The model here has no tools and can't take actions, so the worst a hostile page can do is skew a score or a blurb. Blurbs are only as reliable as a small local model; use the link when it matters.
- Links in feeds are untrusted too. Article fetching accepts only public `http(s)` addresses (it refuses `file:`, private, and local addresses, including via redirects; it resolves the hostname before connecting, so it is a guard rather than a guarantee against a hostile DNS server), and the digest only renders `http(s)` links as clickable.
- Near-duplicate detection compares title words, so it is a heuristic. Two different stories with very similar headlines can be merged, and one story with very different headlines can slip through. Tune `dup_threshold`.
- Settings are checked when the script starts: an unknown setting name prints a warning (so a typo like `topn` doesn't silently do nothing), and a wrong value type or out-of-range value stops the run with a plain-English message.
- Code map for tinkering: `digest.py` is one file, ordered config, data model, feeds, history and de-duplication, the `Ollama` client and `Ranker`, full-article summaries, rendering, then the command line. Prompts are in `ranking_prompt()`, `article_prompt()`, and `make_tldr()`.
- The folder `digests/` keeps a small local history file (`.seen.json`) of recent story titles, links, scores, and blurbs for de-duplication and the weekly look-back. It never leaves your machine; delete it any time to reset.
