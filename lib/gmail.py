"""Headless Gmail reader over IMAP — the only place this pipeline reads mail.

Read-only: connects, SELECTs INBOX, fetches; never deletes, moves, or marks.
Auth is a Gmail *app password* (not the account password), supplied by the
caller from the environment — never hardcoded, never logged. App passwords need
2-Step Verification on the account (myaccount.google.com -> App passwords).

This is deliberately dependency-free (stdlib imaplib/email/html.parser) so it
runs under cron/launchd with the repo's plain `uv` env and no extra wheels."""
from __future__ import annotations

import datetime
import email
import imaplib
import re
import time
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser

IMAP_HOST = "imap.gmail.com"


@dataclass
class MailItem:
    source: str          # friendly label from the caller's allowlist (e.g. "FT")
    sender: str          # raw From header
    subject: str
    date: datetime.date | None
    body: str            # plain text (HTML stripped)


class _TextExtractor(HTMLParser):
    """Collapse HTML to readable text: drop script/style, keep block breaks."""

    _SKIP = {"script", "style", "head", "title"}
    _BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return p.text()


def _best_body(msg: Message) -> str:
    """Prefer text/plain; fall back to the richest text/html part."""
    plain, html = [], []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain":
                plain.append(_part_text(part))
            elif ctype == "text/html":
                html.append(_part_text(part))
    else:
        if msg.get_content_type() == "text/html":
            html.append(_part_text(msg))
        else:
            plain.append(_part_text(msg))
    if any(t.strip() for t in plain):
        return "\n".join(t for t in plain if t.strip()).strip()
    return _html_to_text("\n".join(html)).strip()


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _parse_date(msg: Message) -> datetime.date | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        return None


def _imap_date(d: datetime.date) -> str:
    return d.strftime("%d-%b-%Y")  # e.g. 05-Jun-2026, the format IMAP SEARCH wants


def _connect(user: str, app_password: str, folder: str,
             retries: int = 3, backoff: float = 4.0) -> imaplib.IMAP4_SSL:
    """Open a read-only IMAP session, retrying transient TLS/connection drops
    (Gmail throttles bursts of logins with SSLEOFError). Raises after `retries`."""
    last = None
    for attempt in range(retries):
        try:
            conn = imaplib.IMAP4_SSL(IMAP_HOST)
            conn.login(user, app_password)
            conn.select(folder, readonly=True)
            return conn
        except (imaplib.IMAP4.error, OSError) as e:  # OSError covers ssl.SSLError
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last  # type: ignore[misc]


def classify_subject(subject: str, markers: dict[str, str]) -> str | None:
    """Map a subject line to a source label via the first matching substring.
    Used for FORWARDED newsletters, whose From is the forwarder, not the
    publisher — the real source is in the (often "FW: ...") subject."""
    s = (subject or "").lower()
    for needle, label in markers.items():
        if needle.lower() in s:
            return label
    return None


def fetch_since(
    user: str,
    app_password: str,
    domains: dict[str, str],
    since: datetime.date,
    folder: str = "INBOX",
    max_per_domain: int = 10,
    body_cap: int = 12000,
    forwarders: list[str] | None = None,
    subject_markers: dict[str, str] | None = None,
    default_forward_label: str = "Forwarded",
) -> list[MailItem]:
    """Return newsletter mail received on/after `since` from any allow-listed sender.

    Two channels:
    - `domains`: From-header substrings for DIRECT publishers, mapped to a label
      (e.g. {"theinformation.com": "The Information"}). Source = the mapped label.
    - `forwarders`: addresses you forward newsletters from (e.g. another personal
      mailbox). The From is the forwarder, so the real source is derived from the
      subject via `subject_markers` (falling back to `default_forward_label`).

    Read-only. Raises on connection/login failure (caller decides fallback)."""
    forwarders = forwarders or []
    subject_markers = subject_markers or {}
    # (needle, kind, label) — kind "domain" uses label; "forwarder" classifies by subject.
    needles: list[tuple[str, str, str | None]] = [
        (n, "domain", lbl) for n, lbl in domains.items()
    ] + [(a, "forwarder", None) for a in forwarders]

    items: list[MailItem] = []
    conn = _connect(user, app_password, folder)
    since_str = _imap_date(since)

    def _safe(call):
        """Run an IMAP call, reconnecting once if the session drops mid-command
        (Gmail throttles with a socket EOF / IMAP abort after a login burst)."""
        nonlocal conn
        try:
            return call(conn)
        except (imaplib.IMAP4.error, OSError):
            try:
                conn.logout()
            except Exception:
                pass
            conn = _connect(user, app_password, folder)
            try:
                return call(conn)
            except (imaplib.IMAP4.error, OSError):
                return ("NO", None)

    try:
        seen_uids: set[bytes] = set()
        for needle, kind, label in needles:
            typ, data = _safe(lambda c: c.search(None, "SINCE", since_str, "FROM", needle))
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-max_per_domain:]
            for uid in uids:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
                typ, msg_data = _safe(lambda c, u=uid: c.fetch(u, "(RFC822)"))
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = _decode(msg.get("Subject"))
                if kind == "forwarder":
                    source = classify_subject(subject, subject_markers) or default_forward_label
                else:
                    source = label
                body = _best_body(msg)[:body_cap]
                items.append(
                    MailItem(
                        source=source,
                        sender=_decode(msg.get("From")),
                        subject=subject,
                        date=_parse_date(msg),
                        body=body,
                    )
                )
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    items.sort(key=lambda m: (m.date or since), reverse=True)
    return items
