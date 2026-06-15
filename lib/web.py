"""Headless web search via the Tavily API — the pipeline's only runtime web reach.

Used as a FALLBACK to enrich a briefing when the inbox lacks a topic (e.g.
housing indices, or smart-money signal queries). Dependency-free (stdlib urllib)
so it runs under cron/launchd. The API key is supplied by the caller from the
environment — never hardcoded. If no key, the caller simply skips web search."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

API_URL = "https://api.tavily.com/search"


@dataclass
class WebResult:
    query: str
    title: str
    url: str
    content: str


def search(api_key: str, query: str, max_results: int = 4,
           search_depth: str = "basic", days: int | None = 3,
           topic: str = "news", include_domains: list[str] | None = None,
           timeout: int = 20) -> list[WebResult]:
    """Run one Tavily search. `topic` "news" applies a `days` recency filter;
    "general" ignores recency (right for monthly index pages). `include_domains`
    restricts to authoritative sources. Returns [] on any error (best-effort)."""
    if not api_key:
        return []
    body = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "include_answer": False,
    }
    if days is not None and topic == "news":
        body["days"] = days
    if include_domains:
        body["include_domains"] = include_domains
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        API_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return []
    out = []
    for it in data.get("results", []):
        out.append(WebResult(
            query=query,
            title=it.get("title", ""),
            url=it.get("url", ""),
            content=(it.get("content", "") or "")[:2000],
        ))
    return out


def search_many(api_key: str, queries: list[str], max_results: int = 4,
                days: int | None = 3, topic: str = "news",
                include_domains: list[str] | None = None) -> list[WebResult]:
    """Run several queries, flatten, de-dup by URL (keep first)."""
    seen, out = set(), []
    for q in queries:
        for res in search(api_key, q, max_results=max_results, days=days,
                          topic=topic, include_domains=include_domains):
            if res.url and res.url in seen:
                continue
            seen.add(res.url)
            out.append(res)
    return out
