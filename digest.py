#!/usr/bin/env python3
"""Sloth News Digest: an overnight news brief written by a model on your own machine.

Fetches your RSS/Atom feeds, asks a local open-weight model (through Ollama) to
score and summarize each story against your interests, and writes a short
Markdown brief you can read with your coffee.

Standard library only (Python 3.11+). No cloud API, no account, no per-token cost.
If the model is unavailable, it still produces a keyword-ranked digest.

Layout of this file, top to bottom:
  config -> data model -> feeds -> history & de-duplication -> local model & ranking
  -> full-article summaries -> rendering -> command line
"""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import re
import shutil
import socket
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

USER_AGENT = "sloth-news-digest/1.0 (local RSS reader)"
FETCH_TIMEOUT = 20            # seconds, for feeds and articles
MAX_FEED_BYTES = 5_000_000
MAX_ARTICLE_BYTES = 1_500_000
MAX_FEED_SUMMARY_CHARS = 1500  # kept per story from the feed
MAX_ARTICLE_CHARS = 4000       # article text sent to the model
MIN_ARTICLE_CHARS = 300        # below this, treat the page as paywalled or empty
MIN_PARAGRAPH_CHARS = 40
HISTORY_FILE = ".seen.json"
MIN_HISTORY_DAYS = 7           # the weekly look-back needs a week of history

BY_MODEL, BY_KEYWORDS = "model", "keywords"

RANK_SYSTEM = (
    "You are a careful news editor working for one reader. You judge each story "
    "only from the text given. You reply with JSON only."
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


class ModelDown(Exception):
    """Ollama is unreachable or the model is not installed."""


class BadReply(Exception):
    """The model answered, but not in a usable way."""


class ConfigError(ValueError):
    """The config file is readable but a setting is wrong."""


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

@dataclass
class Config:
    """Everything in the [settings] table of the config file. Defaults live here."""

    model: str = "qwen2.5:7b"
    ollama_url: str = "http://localhost:11434"
    hours_back: int = 24
    max_items_per_feed: int = 25
    top_n: int = 8
    radar_n: int = 10
    min_score: float = 5.0
    batch_size: int = 12
    timeout_seconds: int = 300
    num_ctx: int = 8192
    keep_days: int = 14
    dup_threshold: float = 0.6     # title similarity (0-1) at which two stories count as the same; 0 = exact titles only
    fulltext_top_n: int = 5        # read the full article for this many top stories; 0 = feed summaries only
    allow_private_links: bool = False  # let article fetching reach private/local addresses (leave off)
    weekly_day: str = "Mon"        # day for the "skipped but close" section; "" = never
    weekly_n: int = 5
    weekly_min_score: float = 3.0
    interests: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    output_dir: Path = Path("digests")

    @classmethod
    def from_settings(cls, settings: dict, base_dir: Path) -> Config:
        specs = {f.name: f for f in fields(cls)}
        for key in sorted(set(settings) - set(specs)):
            log(f"  warning: unknown setting '{key}' in config (ignored)")
        values = {}
        for key, raw in settings.items():
            if key in specs:
                spec = specs[key]
                default = spec.default if spec.default is not MISSING else spec.default_factory()
                values[key] = _coerce(key, raw, default)
        cfg = cls(**values)
        if not cfg.output_dir.is_absolute():
            cfg.output_dir = base_dir / cfg.output_dir
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.batch_size < 1:
            raise ConfigError("batch_size must be at least 1")
        if not 0 <= self.dup_threshold <= 1:
            raise ConfigError("dup_threshold must be between 0 and 1")


def _coerce(name: str, raw, default):
    """Check a setting against the type of its default; return the value in that type."""
    if isinstance(default, bool):
        ok, value = isinstance(raw, bool), raw
        expected = "true or false"
    elif isinstance(default, int):
        ok, value = isinstance(raw, int) and not isinstance(raw, bool), raw
        expected = "a whole number"
    elif isinstance(default, float):
        ok = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        value = float(raw) if ok else raw
        expected = "a number"
    elif isinstance(default, Path):
        ok, value = isinstance(raw, str), Path(raw).expanduser() if isinstance(raw, str) else raw
        expected = "a path in quotes"
    elif isinstance(default, list):
        ok, value = isinstance(raw, list) and all(isinstance(x, str) for x in raw), raw
        expected = "a list of strings"
    else:
        ok, value = isinstance(raw, str), raw
        expected = "text in quotes"
    if not ok:
        raise ConfigError(f"'{name}' must be {expected}")
    return value


@dataclass
class Feed:
    name: str
    url: str


def load_config(path: Path) -> tuple[Config, list[Feed]]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    cfg = Config.from_settings(data.get("settings", {}), path.parent)
    feeds = []
    for entry in data.get("feeds", []):
        if entry.get("url"):
            feeds.append(Feed(entry.get("name") or entry["url"], entry["url"]))
        else:
            log("  warning: a [[feeds]] entry has no url (ignored)")
    return cfg, feeds


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Item:
    feed: str
    title: str
    link: str
    summary: str
    published: datetime | None
    key: str
    score: float = 0.0
    blurb: str = ""
    by: str = BY_KEYWORDS
    also: list[str] = field(default_factory=list)  # other feeds that ran the same story
    full: bool = False  # blurb written from the full article text


class Gathered(NamedTuple):
    items: list[Item]
    failed: list[str]  # feeds that could not be read
    ok: int            # feeds that were read


@dataclass
class DigestView:
    """Everything render() needs, so it takes one argument instead of ten."""

    top: list[Item]
    radar: list[Item]
    tldr: str
    kept: int
    feeds_ok: int
    failed: list[str]
    weekly: list[dict]
    ranking_note: str
    now: datetime


# --------------------------------------------------------------------------
# Feeds: fetch and parse
# --------------------------------------------------------------------------

def local_name(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def child_text(el: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:  # honour priority order of names
        for c in el:
            if local_name(c.tag) == name and c.text and c.text.strip():
                return c.text
    return ""


def entry_link(el: ET.Element) -> str:
    fallback = ""
    for c in el:
        if local_name(c.tag) != "link":
            continue
        href = c.get("href")
        if href:
            if c.get("rel") in (None, "alternate"):
                return href.strip()
            fallback = fallback or href.strip()
        elif c.text and c.text.strip():
            return c.text.strip()
    return fallback


def parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    d = None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def norm_key(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def fetch(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_FEED_BYTES)


def parse_feed(data: bytes, feed_name: str) -> list[Item]:
    root = ET.fromstring(data)
    items: list[Item] = []
    for el in root.iter():
        if local_name(el.tag) not in ("item", "entry"):
            continue
        title = clean_text(child_text(el, ("title",)))
        if not title:
            continue
        summary = clean_text(child_text(el, ("description", "summary", "encoded", "content")))
        published = parse_date(child_text(el, ("pubDate", "published", "updated", "date")))
        items.append(Item(feed_name, title, entry_link(el), summary[:MAX_FEED_SUMMARY_CHARS], published, norm_key(title)))
    return items


def collect(feeds: list[Feed], cfg: Config, now: datetime) -> Gathered:
    cutoff = now - timedelta(hours=cfg.hours_back)
    items: list[Item] = []
    failed: list[str] = []
    ok = 0
    for feed in feeds:
        try:
            entries = parse_feed(fetch(feed.url), feed.name)
        except (OSError, ET.ParseError, ValueError) as e:
            failed.append(f"{feed.name} ({type(e).__name__})")
            log(f"  skipped {feed.name}: {e}")
            continue
        ok += 1
        entries.sort(key=lambda it: it.published or now, reverse=True)
        items.extend(e for e in entries[: cfg.max_items_per_feed] if e.published is None or e.published >= cutoff)
    return Gathered(items, failed, ok)


# --------------------------------------------------------------------------
# History (so tomorrow's digest does not repeat today's) and de-duplication
# --------------------------------------------------------------------------

def load_seen(path: Path, keep_days: int, today: str) -> dict[str, dict]:
    """Local history: key -> {d: date, t: title, l: link, f: feed, s: score, b: blurb, top: shown in top list}."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = (datetime.fromisoformat(today) - timedelta(days=keep_days)).date().isoformat()
    seen: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, str):  # older format: key -> date
            v = {"d": v}
        if isinstance(v, dict) and str(v.get("d", "")) >= cutoff:
            seen[k] = v
    return seen


class History:
    """The on-disk record of recent stories, used for de-duplication and the weekly look-back."""

    def __init__(self, path: Path, keep_days: int, today: str) -> None:
        self.path = path
        self.today = today
        self.entries = load_seen(path, max(keep_days, MIN_HISTORY_DAYS), today)

    def record(self, items: list[Item], shown: set[int]) -> None:
        """Remember today's stories; `shown` holds id() of those that made the top list."""
        for it in items:
            self.entries[it.key] = {
                "d": self.today, "t": it.title, "l": it.link, "f": it.feed,
                "s": it.score, "b": it.blurb[:240], "top": id(it) in shown,
            }

    def save(self) -> None:
        self.path.write_text(json.dumps(self.entries), encoding="utf-8")

    def weekly_picks(self, cfg: Config) -> list[dict]:
        """Past week's stories that scored closest to the bar but never made the top list."""
        cutoff = (datetime.fromisoformat(self.today) - timedelta(days=7)).date().isoformat()
        rows = []
        for v in self.entries.values():
            try:
                score = float(v.get("s", 0))
            except (TypeError, ValueError):
                continue
            recent = cutoff <= str(v.get("d", "")) < self.today
            if v.get("t") and not v.get("top") and recent and score >= cfg.weekly_min_score:
                rows.append({**v, "s": score})
        rows.sort(key=lambda v: -v["s"])
        return rows[: cfg.weekly_n]


STOP_WORDS = {
    "the", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from", "is", "are",
    "was", "were", "be", "as", "it", "its", "this", "that", "new", "says", "say", "after", "over",
}


def title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 2 and w not in STOP_WORDS}


def is_dup(a: set[str], b: set[str], threshold: float) -> bool:
    if len(a) < 3 or len(b) < 3:  # too short to judge fuzzily
        return False
    return len(a & b) / len(a | b) >= threshold


def dedupe(items: list[Item], seen: dict[str, dict], threshold: float) -> list[Item]:
    """Drop repeats: exact titles, and (if threshold > 0) near-identical titles, within this run and vs history.
    When another feed ran the same story, remember it on the kept item as `also`."""
    out: list[Item] = []
    toks: list[set[str]] = []
    by_key: dict[str, Item] = {}
    seen_toks = [title_tokens(v["t"]) for v in seen.values() if v.get("t")] if threshold > 0 else []

    def note(kept: Item, other: Item) -> None:
        if other.feed != kept.feed and other.feed not in kept.also:
            kept.also.append(other.feed)

    for it in items:
        if it.key in seen:
            continue
        if it.key in by_key:
            note(by_key[it.key], it)
            continue
        t = title_tokens(it.title)
        if threshold > 0:
            if any(is_dup(t, s, threshold) for s in seen_toks):
                continue
            match = next((o for o, ot in zip(out, toks) if is_dup(t, ot, threshold)), None)
            if match is not None:
                note(match, it)
                continue
        by_key[it.key] = it
        out.append(it)
        toks.append(t)
    return out


# --------------------------------------------------------------------------
# Local model (Ollama) and ranking
# --------------------------------------------------------------------------

def parse_json_loose(s: str):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", s, re.S)
        if not m:
            raise BadReply("reply was not JSON")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise BadReply("reply was not valid JSON") from e


class Ollama:
    """Minimal client for a local Ollama server's chat endpoint."""

    def __init__(self, model: str, url: str, timeout: int, num_ctx: int) -> None:
        self.model, self.url, self.timeout, self.num_ctx = model, url.rstrip("/"), timeout, num_ctx

    @classmethod
    def from_config(cls, cfg: Config) -> Ollama:
        return cls(cfg.model, cfg.ollama_url, cfg.timeout_seconds, cfg.num_ctx)

    def ask(self, system: str, user: str):
        """Send one prompt and return the reply parsed as JSON. Raises ModelDown or BadReply."""
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        req = urllib.request.Request(
            self.url + "/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ModelDown(f"model '{self.model}' is not installed (run: ollama pull {self.model})") from e
            raise BadReply(f"HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise ModelDown(f"cannot reach Ollama at {self.url} ({e.reason})") from e
        except (OSError, ValueError) as e:
            raise BadReply(type(e).__name__) from e
        return parse_json_loose((body.get("message") or {}).get("content", ""))

    def ask_field(self, system: str, user: str, name: str) -> str:
        """Like ask(), for replies shaped {"<name>": "text"}; returns "" if the field is missing."""
        data = self.ask(system, user)
        return str(data.get(name, "")).strip() if isinstance(data, dict) else ""


def has_term(text: str, term: str) -> bool:
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", text) is not None


def keyword_score(it: Item, interests: list[str], avoid: list[str]) -> float:
    t, s = it.title.lower(), it.summary.lower()
    title_hits = sum(1 for k in interests if has_term(t, k))
    body_hits = sum(1 for k in interests if has_term(s, k))
    score = min(8.0, 4 + 2 * title_hits + body_hits) if (title_hits or body_hits) else 0.0
    if any(has_term(t, a) for a in avoid):
        score = max(0.0, score - 4)
    return score


def first_sentence(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    m = re.match(r"(.{20,}?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def ranking_prompt(batch: list[Item], cfg: Config) -> str:
    stories = "\n".join(
        f"[{n}] {it.title}\n    source: {it.feed}\n    text: {it.summary[:350] or '(no summary)'}"
        for n, it in enumerate(batch, 1)
    )
    interests = ", ".join(cfg.interests) or "general news"
    avoid = ", ".join(cfg.avoid) or "nothing in particular"
    return (
        f"Reader's interests: {interests}\n"
        f"Reader wants to skip: {avoid}\n\n"
        "For every story below, return:\n"
        "  id: the number in square brackets\n"
        "  score: integer 0-10 (10 = important and squarely in the reader's interests, "
        "0 = noise, spam, or off-topic)\n"
        "  blurb: ONE plain sentence of at most 25 words saying what happened. "
        "Use only facts present in the story text. No hype, no opinions.\n\n"
        'Reply as JSON: {"stories": [{"id": 1, "score": 7, "blurb": "..."}]}\n\n'
        "STORIES:\n" + stories
    )


def parse_scores(data, batch_size: int) -> dict[int, tuple[float, str]]:
    """Turn the model's reply into {position in batch: (score 0-10, blurb)}, ignoring malformed rows."""
    rows = data.get("stories") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise BadReply("no stories list in reply")
    out: dict[int, tuple[float, str]] = {}
    for r in rows:
        try:
            n, score = int(r["id"]), float(r["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= n <= batch_size:
            out[n] = (max(0.0, min(10.0, score)), str(r.get("blurb", "")).strip()[:240])
    return out


class Ranker:
    """Scores stories batch by batch with the local model, falling back to keywords when it can't."""

    def __init__(self, cfg: Config, ollama: Ollama | None) -> None:
        self.cfg = cfg
        self.ollama = ollama
        self.available = ollama is not None
        self.strikes = 0  # consecutive batches the model failed twice

    def rank(self, items: list[Item]) -> None:
        size = self.cfg.batch_size
        for start in range(0, len(items), size):
            batch = items[start : start + size]
            scores = self._score(batch, start, len(items)) if self.available else {}
            self._apply(batch, scores)

    def _score(self, batch: list[Item], start: int, total: int) -> dict[int, tuple[float, str]]:
        log(f"  scoring stories {start + 1}-{start + len(batch)} of {total}")
        prompt = ranking_prompt(batch, self.cfg)
        for attempt in (1, 2):
            try:
                scores = parse_scores(self.ollama.ask(RANK_SYSTEM, prompt), len(batch))
            except ModelDown as e:
                log(f"  model unavailable: {e}")
                self.available = False
                return {}
            except BadReply as e:
                log(f"  unusable reply ({e}), attempt {attempt}")
            else:
                self.strikes = 0
                return scores
        self.strikes += 1
        if self.strikes >= 2:
            log("  two batches in a row failed; switching to keywords")
            self.available = False
        return {}

    def _apply(self, batch: list[Item], scores: dict[int, tuple[float, str]]) -> None:
        for n, it in enumerate(batch, 1):
            if n in scores:
                it.score, blurb = scores[n]
                it.blurb = blurb or first_sentence(it.summary)
                it.by = BY_MODEL
            else:
                it.score = keyword_score(it, self.cfg.interests, self.cfg.avoid)
                it.blurb = first_sentence(it.summary)
                it.by = BY_KEYWORDS


def select(items: list[Item], cfg: Config, now: datetime) -> tuple[list[Item], list[Item]]:
    """Split ranked stories into the top list (at or above the bar) and the headline-only radar."""
    ranked = sorted(items, key=lambda it: (-it.score, -(it.published or now).timestamp()))
    top = [it for it in ranked if it.score >= cfg.min_score][: cfg.top_n]
    top_ids = {id(it) for it in top}
    radar = [it for it in ranked if id(it) not in top_ids][: cfg.radar_n]
    return top, radar


def make_tldr(top: list[Item], ollama: Ollama) -> str:
    lines = "\n".join(f"- {it.title}: {it.blurb}" for it in top)
    prompt = (
        "Write a two-sentence overview of this morning's top stories for a busy reader. "
        "Plain and factual, no hype, using only the information below.\n"
        'Reply as JSON: {"tldr": "..."}\n\n' + lines
    )
    try:
        return ollama.ask_field(RANK_SYSTEM, prompt, "tldr")
    except (ModelDown, BadReply) as e:
        log(f"  no TL;DR: {e}")
        return ""


# --------------------------------------------------------------------------
# Full-article summaries for the top stories
# --------------------------------------------------------------------------

class _TextGrabber(HTMLParser):
    """Collects paragraph text, ignoring navigation, scripts, and other page furniture."""

    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_p = False
        self.buf: list[str] = []
        self.paras: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "p" and not self.skip:
            self.in_p, self.buf = True, []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "p" and self.in_p:
            text = " ".join("".join(self.buf).split())
            if len(text) >= MIN_PARAGRAPH_CHARS:
                self.paras.append(text)
            self.in_p = False

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.buf.append(data)


def check_article_url(url: str, allow_private: bool) -> None:
    """Feed links are untrusted: only fetch public http(s) pages, never file:// or local network addresses."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http(s) article links are fetched")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"cannot resolve {u.hostname}") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise ValueError("link points to a private or local address")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Applies the same public-address rule to every redirect hop."""

    def __init__(self, allow_private: bool) -> None:
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_article_url(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_article_text(url: str, allow_private: bool) -> str:
    check_article_url(url, allow_private)
    opener = urllib.request.build_opener(_CheckedRedirects(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with opener.open(req, timeout=FETCH_TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            raise ValueError(f"not an HTML page ({ctype or 'unknown type'})")
        raw = r.read(MAX_ARTICLE_BYTES)
        charset = r.headers.get_content_charset() or "utf-8"
    try:
        page = raw.decode(charset, errors="replace")
    except LookupError:
        page = raw.decode("utf-8", errors="replace")
    grabber = _TextGrabber()
    grabber.feed(page)
    grabber.close()
    return " ".join(grabber.paras)[:MAX_ARTICLE_CHARS]


def article_prompt(it: Item, text: str) -> str:
    return (
        "Summarize this article in one or two plain sentences, at most 45 words in total. "
        "Use only facts in the text. No hype, no opinions.\n"
        'Reply as JSON: {"blurb": "..."}\n\n'
        f"TITLE: {it.title}\nTEXT: {text}"
    )


def add_full_text(top: list[Item], cfg: Config, ollama: Ollama) -> None:
    """Replace the feed-based blurb with one written from the full article, where the page can be read."""
    for it in top[: cfg.fulltext_top_n]:
        if not it.link:
            continue
        try:
            text = fetch_article_text(it.link, cfg.allow_private_links)
        except (OSError, ValueError) as e:
            log(f"  could not read {it.link}: {e}")
            continue
        if len(text) < MIN_ARTICLE_CHARS:
            log(f"  too little text at {it.link} (paywall or script-heavy page?)")
            continue
        try:
            blurb = ollama.ask_field(RANK_SYSTEM, article_prompt(it, text), "blurb")
        except ModelDown as e:
            log(f"  stopping full-text summaries: {e}")
            return
        except BadReply as e:
            log(f"  unusable full-text reply for {it.title[:40]!r} ({e})")
            continue
        if blurb:
            it.blurb, it.full = blurb[:400], True


# --------------------------------------------------------------------------
# Rendering and output
# --------------------------------------------------------------------------

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def is_weekly_day(weekly_day: str, local_now: datetime) -> bool:
    d = weekly_day.strip().lower()[:3]
    return d in DAYS and DAYS.index(d) == local_now.weekday()


def md_link(title: str, url: str) -> str:
    t = title.replace("[", "\\[").replace("]", "\\]")
    # Feed links are untrusted: only emit clickable http(s) links (no javascript:, file:, data:).
    if not url or urllib.parse.urlparse(url).scheme.lower() not in ("http", "https"):
        return t
    u = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"[{t}]({u})"


def ago(d: datetime | None, now: datetime) -> str:
    if d is None:
        return "undated"
    secs = (now - d).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def ranking_note(items: list[Item], model: str) -> str:
    n_model = sum(1 for it in items if it.by == BY_MODEL)
    if items and n_model == len(items):
        return f"ranked by {model} on this machine"
    if n_model == 0:
        return "ranked by keywords (no model was available)"
    return f"mostly ranked by {model}; some stories fell back to keywords"


def _render_top(top: list[Item], now: datetime) -> list[str]:
    out = ["## Worth your coffee", ""]
    for i, it in enumerate(top, 1):
        blurb = f" — {it.blurb}" if it.blurb else ""
        also = f" (also: {', '.join(it.also)})" if it.also else ""
        full = " · summarized from the full article" if it.full else ""
        out.append(f"{i}. **{md_link(it.title, it.link)}**{blurb}")
        out.append(f"   _{it.feed}{also} · {ago(it.published, now)} · {it.score:g}/10{full}_")
    return out + [""]


def _render_radar(radar: list[Item], now: datetime) -> list[str]:
    out = ["## Also on the radar", ""]
    out += [f"- {md_link(it.title, it.link)} ({it.feed}, {ago(it.published, now)})" for it in radar]
    return out + [""]


def _render_weekly(weekly: list[dict]) -> list[str]:
    out = [
        "## Last week, just below your bar",
        "",
        "_Stories from the past week that scored closest to your cutoff but did not make the top list._",
        "",
    ]
    for v in weekly:
        d = datetime.fromisoformat(str(v["d"]))
        blurb = f" — {v['b']}" if v.get("b") else ""
        out.append(f"- {md_link(v['t'], v.get('l', ''))} ({v.get('f', '?')}, {d:%b} {d.day}){blurb}")
    return out + [""]


def render(view: DigestView) -> str:
    local_now = view.now.astimezone()
    out = [
        f"# Morning digest: {local_now:%A, %B} {local_now.day}, {local_now.year}",
        "",
        f"_{view.kept} new stories from {view.feeds_ok} feeds · {view.ranking_note} · {local_now:%H:%M}_",
        "",
    ]
    if view.tldr:
        out += [f"**TL;DR** {view.tldr}", ""]
    out += _render_top(view.top, view.now) if view.top else ["Quiet night. Nothing cleared your bar.", ""]
    if view.radar:
        out += _render_radar(view.radar, view.now)
    if view.weekly:
        out += _render_weekly(view.weekly)
    if view.failed:
        out += ["---", f"_Feeds that could not be read: {', '.join(view.failed)}_", ""]
    return "\n".join(out)


def write_outputs(text: str, out_dir: Path, now: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    local_now = now.astimezone()
    path = out_dir / f"{local_now:%Y-%m-%d}.md"
    if path.exists():  # never overwrite an earlier digest from the same day
        path = out_dir / f"{local_now:%Y-%m-%d-%H%M}.md"
    path.write_text(text, encoding="utf-8")
    shutil.copyfile(path, out_dir / "latest.md")
    return path


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Overnight news digest, written by a local open-weight model.")
    ap.add_argument("-c", "--config", default=str(Path(__file__).with_name("feeds.toml")))
    ap.add_argument("--hours", type=int, help="look back this many hours (overrides config)")
    ap.add_argument("--model", help="Ollama model name (overrides config)")
    ap.add_argument("--no-model", action="store_true", help="skip the model; rank by keywords only")
    ap.add_argument("--stdout", action="store_true", help="print the digest; write no files and remember nothing")
    ap.add_argument("--ignore-seen", action="store_true", help="include stories from earlier digests; remember nothing")
    ap.add_argument("--no-fulltext", action="store_true", help="do not fetch full articles; use feed summaries only")
    ap.add_argument("--weekly", action="store_true", help="include the weekly look-back section today")
    return ap.parse_args(argv)


def apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.hours:
        cfg.hours_back = args.hours
    if args.model:
        cfg.model = args.model
    if args.no_fulltext:
        cfg.fulltext_top_n = 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg, feeds = load_config(Path(args.config))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as e:
        log(f"Could not read config {args.config}: {e}")
        return 2
    apply_overrides(cfg, args)
    if not feeds:
        log("No feeds configured. Add [[feeds]] entries to the config.")
        return 2

    now = datetime.now(timezone.utc)
    today = now.astimezone().date().isoformat()
    history = History(cfg.output_dir / HISTORY_FILE, cfg.keep_days, today)

    log(f"Fetching {len(feeds)} feeds...")
    gathered = collect(feeds, cfg, now)
    if gathered.ok == 0:
        log("Could not read any feed (offline?). Nothing written.")
        return 1

    already_seen = {} if args.ignore_seen else history.entries
    items = dedupe(gathered.items, already_seen, cfg.dup_threshold)
    log(f"{len(items)} new stories to consider")

    ollama = None if args.no_model else Ollama.from_config(cfg)
    Ranker(cfg, ollama).rank(items)
    top, radar = select(items, cfg, now)

    tldr = ""
    if ollama and top and any(it.by == BY_MODEL for it in items):
        if cfg.fulltext_top_n > 0:
            log("Reading the top stories in full")
            add_full_text(top, cfg, ollama)
        log("Writing the TL;DR")
        tldr = make_tldr(top, ollama)

    weekly = history.weekly_picks(cfg) if args.weekly or is_weekly_day(cfg.weekly_day, now.astimezone()) else []
    view = DigestView(top, radar, tldr, len(items), gathered.ok, gathered.failed, weekly, ranking_note(items, cfg.model), now)
    text = render(view)

    if args.stdout:
        print(text)
        return 0
    path = write_outputs(text, cfg.output_dir, now)
    if not args.ignore_seen:
        history.record(items, {id(it) for it in top})
        history.save()
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
