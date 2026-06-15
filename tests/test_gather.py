import datetime

from news import gather
from lib import gmail, web


def _item(source, subj, day, body):
    return gmail.MailItem(source=source, sender=f"x@{source}", subject=subj,
                          date=datetime.date(2026, 6, day), body=body)


def test_load_sources_has_domains_and_topics():
    cfg = gather.load_sources()
    assert "ft.com" in cfg["domains"]
    assert "Tech & AI" in cfg["topics"]


def test_format_newsletters_blocks_each_item():
    items = [_item("FT", "FirstFT", 5, "PMI 48.5"),
             _item("The Information", "AM", 5, "OpenAI IPO")]
    out = gather._format_newsletters(items)
    assert "[FT] FirstFT" in out
    assert "PMI 48.5" in out
    assert "[The Information] AM" in out


def test_format_newsletters_empty():
    assert gather._format_newsletters([]) == ""


def test_gather_all_uses_imap_and_builds_context(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    def fake_fetch(user, pw, domains, since, **k):
        captured["since"] = since
        return [_item("FT", "FirstFT", 5, "PMI 48.5")]

    monkeypatch.setattr(gather.gmail, "fetch_since", fake_fetch)
    monkeypatch.setattr(gather, "find_last_briefing", lambda folder=None: "yesterday")
    ctx = gather.gather_all(today=datetime.date(2026, 6, 5), since_days=1)
    assert ctx["item_count"] == 1
    assert ctx["sources_present"] == ["FT"]
    assert ctx["fetch_error"] == ""
    assert captured["since"] == datetime.date(2026, 6, 4)
    assert "PMI 48.5" in ctx["newsletters"]
    assert ctx["last_briefing"] == "yesterday"


def test_gather_web_fallback_runs_when_key_set(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tav")
    calls = []
    def fake_many(api_key, queries, **k):
        calls.append({"queries": queries, "kw": k})
        # tag result by the first query so we can tell housing vs smart-money apart
        return [web.WebResult(query="q", title=f"hit:{queries[0][:10]}",
                              url=f"u/{len(calls)}", content="x")]
    monkeypatch.setattr(gather.web, "search_many", fake_many)
    material, queries = gather.gather_web_fallback(gather.load_sources())
    assert "[web fallback]" in material
    assert len(calls) == 2                                   # housing + smart-money split
    housing_call = calls[0]
    assert housing_call["kw"]["topic"] == "general"          # housing uses general topic
    assert housing_call["kw"]["days"] is None                # no recency filter
    assert housing_call["kw"]["include_domains"]             # restricted to authoritative domains
    assert any("Rightmove" in q or "Nationwide" in q for q in housing_call["queries"])


def test_gather_web_fallback_skipped_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    material, queries = gather.gather_web_fallback(gather.load_sources())
    assert material == "" and queries == []


def test_gather_all_missing_creds_sets_error(monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(gather, "find_last_briefing", lambda folder=None: "")
    ctx = gather.gather_all(today=datetime.date(2026, 6, 5))
    assert ctx["item_count"] == 0
    assert "GMAIL" in ctx["fetch_error"]


def test_gather_all_fetch_exception_captured(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")

    def boom(*a, **k):
        raise ConnectionError("imap down")

    monkeypatch.setattr(gather.gmail, "fetch_since", boom)
    monkeypatch.setattr(gather, "find_last_briefing", lambda folder=None: "")
    ctx = gather.gather_all(today=datetime.date(2026, 6, 5))
    assert ctx["item_count"] == 0
    assert "imap down" in ctx["fetch_error"]
