"""Send a (possibly long) message to Telegram, chunked under the API cap.
Token/chat_id come from the caller (loaded from a secrets file) — never hardcoded."""
import json
import urllib.request
import urllib.parse

from lib import net

def chunk_text(text: str, limit: int = 4000) -> list[str]:
    chunks, buf = [], ""
    for sec in text.split("\n\n"):
        while len(sec) > limit:
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(sec[:limit]); sec = sec[limit:]
        piece = (buf + "\n\n" + sec) if buf else sec
        if len(piece) > limit and buf:
            chunks.append(buf); buf = sec
        else:
            buf = piece
    if buf:
        chunks.append(buf)
    return chunks

def _post(token: str, chat_id: str, text: str, parse_mode: str | None = None,
          timeout: int = 20) -> bool:
    fields = {"chat_id": chat_id, "text": text}
    if parse_mode:
        fields["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    # Retry transient DNS/connection blips (jobs often fire right on wake, before
    # the network is up) rather than letting one drop crash the whole job.
    r = net.retry(lambda: json.load(urllib.request.urlopen(req, timeout=timeout)),
                  label="telegram.sendMessage")
    if not r.get("ok") and parse_mode:
        # Markdown parse errors are common; retry once as plain text rather than drop.
        return _post(token, chat_id, text, parse_mode=None)
    return bool(r.get("ok"))

def send(token: str, chat_id: str, text: str, limit: int = 4000,
         parse_mode: str | None = None) -> list[bool]:
    return [_post(token, chat_id, c, parse_mode=parse_mode) for c in chunk_text(text, limit)]
