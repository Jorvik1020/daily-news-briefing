"""Headless RSS/Atom feed reader — stdlib only, no pip dependencies.

A third source type for the briefing (alongside Gmail newsletters and X voices):
pull recent articles from RSS 2.0 or Atom feeds. Dependency-free (urllib +
xml.etree) so it runs under cron/launchd. Best-effort throughout: any network or
parse error yields [] rather than raising, mirroring lib/web.py. Feed URLs are
supplied by the caller from config — never hardcoded."""
from __future__ import annotations

import re
import datetime
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SUMMARY_CAP = 1500


@dataclass
class FeedItem:
    feed: str            # friendly name from config
    title: str
    link: str
    summary: str         # HTML stripped to plain text, capped ~1500 chars
    published: datetime.date | None


def _local(tag: str) -> str:
    """Strip an XML namespace prefix: '{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    if not tag:
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _find_child(el, *names):
    """First direct child whose local tag name matches any of `names` (case-insensitive)."""
    wanted = {n.lower() for n in names}
    for child in el:
        if _local(child.tag).lower() in wanted:
            return child
    return None


def _find_children(el, *names):
    wanted = {n.lower() for n in names}
    return [c for c in el if _local(c.tag).lower() in wanted]


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
                .replace("&nbsp;", " "))
    text = _WS_RE.sub(" ", text).strip()
    return text[:_SUMMARY_CAP]


def _parse_date(text: str) -> datetime.date | None:
    if not text:
        return None
    text = text.strip()
    # RFC 822 (RSS pubDate): "Mon, 09 Jun 2026 13:00:00 GMT"
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return dt.date()
    except Exception:
        pass
    # ISO 8601 (Atom updated/published): "2026-06-09T13:00:00Z"
    iso = text.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(iso).date()
    except Exception:
        pass
    try:
        return datetime.date.fromisoformat(text[:10])
    except Exception:
        return None


def _atom_link(entry) -> str:
    """Atom <link href="..."> — prefer rel="alternate" (or no rel)."""
    best = ""
    for ln in _find_children(entry, "link"):
        href = ln.attrib.get("href", "")
        if not href:
            continue
        rel = ln.attrib.get("rel", "")
        if rel in ("", "alternate"):
            return href
        if not best:
            best = href
    return best


def _text(el) -> str:
    if el is None:
        return ""
    # itertext() flattens nested HTML/XML (e.g. Atom content type="xhtml").
    return "".join(el.itertext()) if len(el) else (el.text or "")


def _parse_entry(name: str, el, is_atom: bool) -> FeedItem | None:
    title = _strip_html(_text(_find_child(el, "title")))
    if is_atom:
        link = _atom_link(el)
        summ_el = _find_child(el, "summary", "content")
        date_el = _find_child(el, "published", "updated")
    else:  # RSS 2.0 item
        link = _text(_find_child(el, "link")).strip()
        summ_el = _find_child(el, "description", "summary", "encoded")
        date_el = _find_child(el, "pubdate", "date")
    summary = _strip_html(_text(summ_el))
    published = _parse_date(_text(date_el))
    if not (title or summary or link):
        return None
    return FeedItem(feed=name, title=title, link=link, summary=summary,
                    published=published)


def fetch(name: str, url: str, max_items: int = 8, since_days: int = 3,
          timeout: int = 20) -> list[FeedItem]:
    """Fetch + parse one RSS 2.0 or Atom feed. Filters to items newer than
    `since_days` when a date is parseable (undated items are kept). Sorts newest
    first, caps at `max_items`. Returns [] on ANY error (best-effort)."""
    if not url:
        return []
    req = urllib.request.Request(url, headers={"User-Agent": "daily-news-briefing/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        root = ET.fromstring(raw)
    except Exception:
        return []

    root_tag = _local(root.tag).lower()
    if root_tag == "feed":            # Atom: entries are children of <feed>
        is_atom = True
        entries = _find_children(root, "entry")
    else:                             # RSS: <rss><channel><item>...
        is_atom = False
        channel = _find_child(root, "channel")
        if channel is None:
            channel = root
        entries = _find_children(channel, "item")

    items: list[FeedItem] = []
    for el in entries:
        try:
            it = _parse_entry(name, el, is_atom)
        except Exception:
            it = None
        if it is not None:
            items.append(it)

    cutoff = datetime.date.today() - datetime.timedelta(days=since_days)
    items = [it for it in items if it.published is None or it.published >= cutoff]
    # Newest first; undated items sort last (treated as oldest).
    items.sort(key=lambda it: it.published or datetime.date.min, reverse=True)
    return items[:max_items]


def fetch_feeds(feeds: dict, max_items: int = 8, since_days: int = 3) -> list[FeedItem]:
    """Fetch every name->url feed, flatten, de-dup by link (keep first). Best-effort
    per feed — one bad feed never sinks the rest."""
    seen, out = set(), []
    for name, url in (feeds or {}).items():
        for it in fetch(name, url, max_items=max_items, since_days=since_days):
            if it.link and it.link in seen:
                continue
            if it.link:
                seen.add(it.link)
            out.append(it)
    return out
