import email
from email.message import EmailMessage

from lib import gmail


def _msg(subject, sender, date, plain=None, html=None):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Date"] = date
    if plain is not None and html is not None:
        m.set_content(plain)
        m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content(plain or "")
    return m


def test_html_to_text_strips_tags_and_scripts():
    html = "<style>x{}</style><p>Hello <b>world</b></p><script>bad()</script><p>Bye</p>"
    out = gmail._html_to_text(html)
    assert "Hello world" in out
    assert "Bye" in out
    assert "bad()" not in out
    assert "<" not in out


def test_best_body_prefers_plain_over_html():
    m = _msg("S", "a@ft.com", "Mon, 02 Jun 2026 06:00:00 +0000",
             plain="PLAIN BODY", html="<p>HTML BODY</p>")
    assert gmail._best_body(m) == "PLAIN BODY"


def test_best_body_falls_back_to_html():
    m = _msg("S", "a@ft.com", "Mon, 02 Jun 2026 06:00:00 +0000",
             html="<p>Only <b>html</b> here</p>")
    body = gmail._best_body(m)
    assert "Only html here" in body


def test_parse_date_reads_header():
    m = _msg("S", "a@ft.com", "Wed, 04 Jun 2026 07:30:00 +0100", plain="x")
    d = gmail._parse_date(m)
    assert d is not None and d.year == 2026 and d.month == 6 and d.day == 4


def test_imap_date_format():
    import datetime
    assert gmail._imap_date(datetime.date(2026, 6, 5)) == "05-Jun-2026"


def test_decode_mime_subject():
    # =?UTF-8?B?...?= encoded "Café"
    assert gmail._decode("=?utf-8?q?Caf=C3=A9?=") == "Café"


class _FakeIMAP:
    """Minimal IMAP4_SSL stand-in: one FT message matches the ft.com search."""

    def __init__(self, host):
        self.host = host
        self.logged_out = False

    def login(self, user, pw):
        assert user and pw
        return ("OK", [b"ok"])

    def select(self, folder, readonly=False):
        assert readonly is True  # must be read-only
        return ("OK", [b"1"])

    def search(self, charset, *criteria):
        if "ft.com" in criteria:
            return ("OK", [b"1"])
        return ("OK", [b""])

    def fetch(self, uid, spec):
        raw = _msg("FirstFT: markets", "FT <newsletter@ft.com>",
                   "Thu, 05 Jun 2026 06:00:00 +0000",
                   plain="UK PMI fell to 48.5.").as_bytes()
        return ("OK", [(b"1 (RFC822 {100}", raw)])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"bye"])


def test_fetch_since_returns_allowlisted_items(monkeypatch):
    import datetime
    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", _FakeIMAP)
    items = gmail.fetch_since(
        "me@gmail.com", "app-pw",
        {"ft.com": "FT", "nope.com": "Nope"},
        datetime.date(2026, 6, 5),
    )
    assert len(items) == 1
    it = items[0]
    assert it.source == "FT"
    assert "48.5" in it.body
    assert it.subject == "FirstFT: markets"


def test_classify_subject_first_match_wins():
    markers = {"in today's ft": "FT", "the briefing": "The Information"}
    assert gmail.classify_subject("FW: In Today's FT: Alphabet", markers) == "FT"
    assert gmail.classify_subject("FW: The Briefing: Anthropic", markers) == "The Information"
    assert gmail.classify_subject("FW: random thing", markers) is None
    assert gmail.classify_subject("", markers) is None


class _FakeForwarderIMAP(_FakeIMAP):
    """A forwarded FT email arriving from a personal Gmail address."""

    def search(self, charset, *criteria):
        if "you@example.com" in criteria:
            return ("OK", [b"7"])
        return ("OK", [b""])

    def fetch(self, uid, spec):
        raw = _msg("FW: In Today's FT: Alphabet to sell $80bn in stock",
                   "Me <you@example.com>",
                   "Thu, 05 Jun 2026 06:30:00 +0000",
                   plain="Alphabet will sell $80bn in stock to fund AI.").as_bytes()
        return ("OK", [(b"7 (RFC822 {120}", raw)])


def test_fetch_since_classifies_forwarded_by_subject(monkeypatch):
    import datetime
    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", _FakeForwarderIMAP)
    items = gmail.fetch_since(
        "me@gmail.com", "app-pw",
        {"ft.com": "FT"},                       # no direct hit
        datetime.date(2026, 6, 5),
        forwarders=["you@example.com"],
        subject_markers={"in today's ft": "FT"},
    )
    assert len(items) == 1
    # source comes from the SUBJECT, not the forwarder's address
    assert items[0].source == "FT"
    assert items[0].sender.endswith("example.com>")
    assert "Alphabet" in items[0].body


class _FlakyIMAP(_FakeIMAP):
    """Drops the first FETCH mid-command (like Gmail's throttle EOF); the
    reconnect-retry should recover and still return the message."""
    fetch_calls = 0

    def fetch(self, uid, spec):
        _FlakyIMAP.fetch_calls += 1
        if _FlakyIMAP.fetch_calls == 1:
            raise gmail.imaplib.IMAP4.abort("command: FETCH => socket error: EOF")
        return super().fetch(uid, spec)


def test_fetch_since_recovers_from_mid_fetch_drop(monkeypatch):
    import datetime
    _FlakyIMAP.fetch_calls = 0
    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", _FlakyIMAP)
    items = gmail.fetch_since("me@gmail.com", "pw", {"ft.com": "FT"},
                              datetime.date(2026, 6, 5))
    assert len(items) == 1                 # recovered after the dropped fetch
    assert _FlakyIMAP.fetch_calls == 2     # first dropped, retry succeeded
    assert items[0].source == "FT"


def test_fetch_since_unmatched_forward_uses_default_label(monkeypatch):
    import datetime
    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", _FakeForwarderIMAP)
    items = gmail.fetch_since(
        "me@gmail.com", "app-pw", {},
        datetime.date(2026, 6, 5),
        forwarders=["you@example.com"],
        subject_markers={"nomatch": "X"},
        default_forward_label="Forwarded",
    )
    assert items and items[0].source == "Forwarded"
