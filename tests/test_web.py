import json
import io

from lib import web


def _fake_response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def test_search_no_key_returns_empty():
    assert web.search("", "anything") == []


def test_search_parses_results(monkeypatch):
    payload = {"results": [
        {"title": "Rightmove HPI", "url": "https://rightmove.co.uk/x", "content": "Asking prices up 0.5%."},
        {"title": "Mortgages", "url": "https://ft.com/y", "content": "2yr fix ~5.8%."},
    ]}
    monkeypatch.setattr(web.urllib.request, "urlopen", lambda *a, **k: _fake_response(payload))
    out = web.search("KEY", "UK house prices")
    assert len(out) == 2
    assert out[0].title == "Rightmove HPI"
    assert out[0].query == "UK house prices"
    assert "0.5%" in out[0].content


def test_search_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(web.urllib.request, "urlopen", boom)
    assert web.search("KEY", "q") == []  # best-effort: never raises


def test_search_many_dedups_by_url(monkeypatch):
    calls = {"n": 0}
    def fake_search(api_key, query, **k):
        calls["n"] += 1
        return [web.WebResult(query=query, title="T", url="https://same.com", content="c")]
    monkeypatch.setattr(web, "search", fake_search)
    out = web.search_many("KEY", ["q1", "q2", "q3"])
    assert calls["n"] == 3        # ran every query
    assert len(out) == 1          # deduped to one URL
