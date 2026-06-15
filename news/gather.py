"""Collect the raw inputs for the daily news briefing. Pure I/O — no LLM here.

Reads today's newsletters from Gmail over IMAP (lib.gmail), plus the most recent
prior briefing from the output dir (so the LLM can avoid restating yesterday's
news). Credentials come from the environment."""
import os
import datetime
from pathlib import Path

import yaml

from lib import gmail, web, store, x_fetch, rss

SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.yaml"
NEWS_DIR = store.OUTPUT_DIR


def load_sources() -> dict:
    return yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))


def find_last_briefing(folder: str = NEWS_DIR) -> str:
    """Return the body of the most recent daily_news_*.md, or '' if none."""
    cands = sorted(Path(folder).glob("daily_news_*.md")) if Path(folder).exists() else []
    if not cands:
        return ""
    return cands[-1].read_text(encoding="utf-8", errors="ignore")


def _format_newsletters(items: list[gmail.MailItem]) -> str:
    if not items:
        return ""
    blocks = []
    for it in items:
        d = it.date.isoformat() if it.date else "?"
        blocks.append(
            f"### [{it.source}] {it.subject}  ({d})\n"
            f"From: {it.sender}\n\n{it.body}"
        )
    return "\n\n---\n\n".join(blocks)


def _format_web(results: list[web.WebResult]) -> str:
    if not results:
        return ""
    blocks = []
    for r in results:
        blocks.append(f"### [web fallback] {r.title}\n{r.url}\n\n{r.content}")
    return "\n\n---\n\n".join(blocks)


def _format_feeds(items: list[rss.FeedItem]) -> str:
    if not items:
        return ""
    blocks = []
    for it in items:
        d = it.published.isoformat() if it.published else "?"
        head = f"### [feed: {it.feed}] {it.title}  ({d})"
        body = "\n".join(p for p in (it.link, it.summary) if p)
        blocks.append(f"{head}\n{body}")
    return "\n\n---\n\n".join(blocks)


def gather_feeds(cfg: dict) -> str:
    """Fetch the configured RSS/Atom feeds and format them for the prompt. Best-effort
    — a missing/empty `feeds:` config (or any fetch error) yields ''. No crash."""
    feeds = cfg.get("feeds") or {}
    if not feeds:
        return ""
    fc = cfg.get("feeds_config", {}) or {}
    max_items = fc.get("max_items", 8)
    since_days = fc.get("since_days", 3)
    try:
        items = rss.fetch_feeds(feeds, max_items=max_items, since_days=since_days)
    except Exception:
        items = []
    return _format_feeds(items)


def gather_web_fallback(cfg: dict) -> tuple[str, list[str]]:
    """Run Tavily fallback queries (housing + smart-money) if TAVILY_API_KEY is
    set. Returns (formatted_material, queries_run). No key -> ('', [])."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    wf = cfg.get("web_fallback", {})
    if not api_key or not wf:
        return "", []
    housing_q = list(wf.get("housing", []))
    smart_q = list(wf.get("smart_money", []))
    max_r = wf.get("max_results", 4)
    # Housing: authoritative domains, general topic, no recency (monthly data).
    housing = web.search_many(
        api_key, housing_q, max_results=max_r, days=None, topic="general",
        include_domains=wf.get("housing_domains") or None,
    )
    # Smart money: authoritative filing/positioning aggregators (13F, insider,
    # activist, fund surveys) — data pages, not news, so general + no recency.
    smart = web.search_many(
        api_key, smart_q, max_results=max_r, days=None, topic="general",
        include_domains=wf.get("smart_money_domains") or None,
    )
    seen, results = set(), []
    for r in housing + smart:
        if r.url and r.url in seen:
            continue
        seen.add(r.url)
        results.append(r)
    web_material = _format_web(results)
    material = web_material
    return material, housing_q + smart_q


def _x_settings(cfg: dict) -> tuple[list, int]:
    """Resolve the tracked X accounts from the TOP level of the config, falling
    back to the legacy web_fallback.x_voices location for backward compatibility."""
    wf = cfg.get("web_fallback", {}) or {}
    handles = cfg.get("x_voices")
    if handles is None:
        handles = wf.get("x_voices", [])
    n = cfg.get("x_posts_per_voice")
    if n is None:
        n = wf.get("x_posts_per_voice", 5)
    return handles or [], n


def gather_x_voices(cfg: dict) -> str:
    """Fetch tracked X voices' recent posts, formatted for the prompt. Best-effort
    — returns '' if disabled or the headless fetch fails (Smart Money still works
    off the institutional Tavily data). Accepts the full config dict; reads
    top-level x_voices/x_posts_per_voice (legacy web_fallback location still works)."""
    handles, n = _x_settings(cfg)
    if not handles:
        return ""
    blocks = []
    for h in handles:
        try:
            posts = x_fetch.fetch_posts(h, max_posts=n)
        except Exception:
            posts = []
        for pst in posts:
            blocks.append(f"### [X @{h}]\n{pst.text}")
    return "\n\n".join(blocks)


def gather_all(today: datetime.date | None = None, since_days: int = 1) -> dict:
    """Fetch today's newsletters + prior briefing. Returns a context dict for the
    prompt. `since_days` widens the IMAP window for manual catch-up runs."""
    run_date = today or datetime.date.today()
    since = run_date - datetime.timedelta(days=since_days)
    cfg = load_sources()
    domains = cfg.get("domains", {})
    forwarders = cfg.get("forwarders", [])
    subject_markers = cfg.get("subject_markers", {})
    default_forward_label = cfg.get("default_forward_label", "Forwarded")
    topics = cfg.get("topics", [])

    user = os.environ.get("GMAIL_USER") or os.environ.get("GMAIL_ADDRESS", "")
    app_pw = os.environ.get("GMAIL_APP_PASSWORD", "")

    items: list[gmail.MailItem] = []
    fetch_error = ""
    if user and app_pw:
        try:
            # A busy daily digest can arrive as many forwarded sections, so allow
            # a high per-sender cap rather than the default 10 (else it truncates).
            items = gmail.fetch_since(
                user, app_pw, domains, since, max_per_domain=40,
                forwarders=forwarders, subject_markers=subject_markers,
                default_forward_label=default_forward_label,
            )
        except Exception as e:  # connection/login/parse — let run.py decide
            fetch_error = f"{type(e).__name__}: {e}"
    else:
        fetch_error = "GMAIL_USER / GMAIL_APP_PASSWORD not set in environment"

    web_material, web_queries = gather_web_fallback(cfg)
    # Tracked X accounts feed the Smart Money / web-fallback material (top-level
    # x_voices, with a legacy web_fallback fallback inside _x_settings).
    x_block = gather_x_voices(cfg)
    web_material = "\n\n---\n\n".join(b for b in (web_material, x_block) if b)
    feeds_material = gather_feeds(cfg)

    sources_present = sorted({it.source for it in items})
    if feeds_material:
        sources_present.append("RSS feeds")
    if web_material:
        sources_present.append("Web fallback (Tavily)")
    return {
        "date": run_date.isoformat(),
        "topics": topics,
        "newsletters": _format_newsletters(items),
        "feeds": feeds_material,
        "web_fallback": web_material,
        "web_queries": web_queries,
        "sources_present": sources_present,
        "item_count": len(items),
        "fetch_error": fetch_error,
        "last_briefing": find_last_briefing(),
    }
