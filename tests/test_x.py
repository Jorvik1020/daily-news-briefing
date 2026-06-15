from lib import x_fetch
from news import gather


def test_clean_strips_trailing_engagement_counts():
    raw = "Investor\n@exampleinvestor\n5h\nticker thesis here\n196\n38\n719\n291K"
    out = x_fetch._clean(raw)
    assert out.endswith("ticker thesis here")
    assert "291K" not in out


def test_fetch_posts_degrades_to_empty_on_cookie_failure(monkeypatch):
    def boom():
        raise RuntimeError("Keychain locked")
    monkeypatch.setattr(x_fetch, "_chrome_cookies", boom)
    assert x_fetch.fetch_posts("exampleinvestor") == []


def test_fetch_posts_empty_when_no_cookies(monkeypatch):
    monkeypatch.setattr(x_fetch, "_chrome_cookies", lambda: [])
    assert x_fetch.fetch_posts("exampleinvestor") == []


def test_gather_x_voices_disabled_when_no_handles():
    assert gather.gather_x_voices({}) == ""


def test_gather_x_voices_formats_posts(monkeypatch):
    # top-level x_voices (the user-facing location)
    cfg = {"x_voices": ["exampleinvestor"], "x_posts_per_voice": 3}
    monkeypatch.setattr(gather.x_fetch, "fetch_posts",
                        lambda h, max_posts=5: [x_fetch.XPost(h, "ticker thesis here")])
    out = gather.gather_x_voices(cfg)
    assert "[X @exampleinvestor]" in out
    assert "ticker thesis here" in out


def test_gather_x_voices_reads_top_level(monkeypatch):
    captured = {}
    def fake_fetch(h, max_posts=5):
        captured["max_posts"] = max_posts
        return [x_fetch.XPost(h, "thesis")]
    monkeypatch.setattr(gather.x_fetch, "fetch_posts", fake_fetch)
    out = gather.gather_x_voices({"x_voices": ["topvoice"], "x_posts_per_voice": 7})
    assert "[X @topvoice]" in out
    assert captured["max_posts"] == 7


def test_gather_x_voices_legacy_web_fallback_location(monkeypatch):
    # back-compat: x_voices nested under web_fallback still works
    monkeypatch.setattr(gather.x_fetch, "fetch_posts",
                        lambda h, max_posts=5: [x_fetch.XPost(h, "legacy thesis")])
    out = gather.gather_x_voices({"web_fallback": {"x_voices": ["legacyvoice"]}})
    assert "[X @legacyvoice]" in out


def test_gather_x_voices_survives_fetch_exception(monkeypatch):
    wf = {"x_voices": ["exampleinvestor"]}
    def boom(h, max_posts=5):
        raise RuntimeError("X blocked")
    monkeypatch.setattr(gather.x_fetch, "fetch_posts", boom)
    assert gather.gather_x_voices(wf) == ""    # swallowed, no crash
