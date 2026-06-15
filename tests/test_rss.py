import io
import datetime

from lib import rss
from news import gather

# Use a far-future "recent" date so the since_days filter keeps these in tests
# that run any year. Build dates relative to today so recency logic is exercised.
_TODAY = datetime.date.today()
_RECENT = _TODAY - datetime.timedelta(days=1)
_OLD = _TODAY - datetime.timedelta(days=30)


def _rfc822(d: datetime.date) -> str:
    return d.strftime("%a, %d %b %Y 09:00:00 GMT")


def _rss_xml(recent, old):
    return f"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Recent Story</title>
      <link>https://example.com/recent</link>
      <description>Markets &lt;b&gt;rallied&lt;/b&gt; 2.5% today.&amp;nbsp;Strong.</description>
      <pubDate>{_rfc822(recent)}</pubDate>
    </item>
    <item>
      <title>Old Story</title>
      <link>https://example.com/old</link>
      <description>Ancient news.</description>
      <pubDate>{_rfc822(old)}</pubDate>
    </item>
  </channel>
</rss>""".encode()


def _atom_xml(recent):
    iso = recent.strftime("%Y-%m-%dT09:00:00Z")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <entry>
    <title>Atom Story</title>
    <link href="https://atom.example.com/post1" rel="alternate"/>
    <link href="https://atom.example.com/edit" rel="edit"/>
    <summary type="html">A &lt;i&gt;summary&lt;/i&gt; with markup.</summary>
    <updated>{iso}</updated>
  </entry>
</feed>""".encode()


def _patch_urlopen(monkeypatch, body: bytes):
    monkeypatch.setattr(rss.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(body))


def test_fetch_rss_parses_and_filters(monkeypatch):
    _patch_urlopen(monkeypatch, _rss_xml(_RECENT, _OLD))
    items = rss.fetch("Example Feed", "https://x/rss", since_days=3)
    # Old item filtered out by since_days; only the recent one survives.
    assert len(items) == 1
    it = items[0]
    assert it.feed == "Example Feed"
    assert it.title == "Recent Story"
    assert it.link == "https://example.com/recent"
    assert it.published == _RECENT
    # HTML tags stripped, entities decoded.
    assert "<b>" not in it.summary
    assert "rallied 2.5%" in it.summary
    assert "&nbsp;" not in it.summary and "Strong" in it.summary


def test_fetch_atom_parses(monkeypatch):
    _patch_urlopen(monkeypatch, _atom_xml(_RECENT))
    items = rss.fetch("Atom Example", "https://x/atom", since_days=3)
    assert len(items) == 1
    it = items[0]
    assert it.title == "Atom Story"
    # rel="alternate" link preferred over rel="edit".
    assert it.link == "https://atom.example.com/post1"
    assert it.published == _RECENT
    assert "<i>" not in it.summary and "summary with markup" in it.summary


def test_fetch_since_days_keeps_all_when_wide(monkeypatch):
    _patch_urlopen(monkeypatch, _rss_xml(_RECENT, _OLD))
    items = rss.fetch("Example Feed", "https://x/rss", since_days=60)
    assert len(items) == 2
    # Sorted newest first.
    assert items[0].title == "Recent Story"
    assert items[1].title == "Old Story"


def test_fetch_summary_capped(monkeypatch):
    big = "x" * 5000
    body = f"""<rss version="2.0"><channel><item>
      <title>Big</title><link>https://e/c/big</link>
      <description>{big}</description>
      <pubDate>{_rfc822(_RECENT)}</pubDate>
    </item></channel></rss>""".encode()
    _patch_urlopen(monkeypatch, body)
    items = rss.fetch("F", "https://x/rss")
    assert len(items[0].summary) <= 1500


def test_fetch_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(rss.urllib.request, "urlopen", boom)
    assert rss.fetch("F", "https://x/rss") == []  # best-effort: never raises


def test_fetch_empty_url():
    assert rss.fetch("F", "") == []


def test_fetch_feeds_dedups_by_link(monkeypatch):
    # Two named feeds returning the same link -> deduped to one.
    def fake_fetch(name, url, **k):
        return [rss.FeedItem(feed=name, title="T", link="https://same/x",
                             summary="s", published=_RECENT)]
    monkeypatch.setattr(rss, "fetch", fake_fetch)
    out = rss.fetch_feeds({"A": "https://a", "B": "https://b"})
    assert len(out) == 1


def test_fetch_feeds_empty_dict():
    assert rss.fetch_feeds({}) == []


# --- gather.gather_feeds integration (rss.fetch_feeds monkeypatched) ---

def test_gather_feeds_formats_blocks(monkeypatch):
    items = [rss.FeedItem(feed="Example", title="Big AI Round",
                          link="https://e/c/ai", summary="Raised $1B.",
                          published=datetime.date(2026, 6, 14))]
    monkeypatch.setattr(gather.rss, "fetch_feeds", lambda feeds, **k: items)
    out = gather.gather_feeds({"feeds": {"Example": "https://e/rss"}})
    assert "[feed: Example] Big AI Round" in out
    assert "https://e/c/ai" in out
    assert "Raised $1B." in out
    assert "(2026-06-14)" in out


def test_gather_feeds_empty_when_no_config():
    # Missing/empty feeds config -> empty string, no crash.
    assert gather.gather_feeds({}) == ""
    assert gather.gather_feeds({"feeds": {}}) == ""


def test_gather_feeds_swallows_fetch_error(monkeypatch):
    def boom(feeds, **k):
        raise RuntimeError("parse blew up")
    monkeypatch.setattr(gather.rss, "fetch_feeds", boom)
    assert gather.gather_feeds({"feeds": {"A": "https://a"}}) == ""
