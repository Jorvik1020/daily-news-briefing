"""Headless X (Twitter) profile reader — fetch a tracked individual's recent
posts for the Smart Money section. Uses the user's logged-in Chrome cookies
(rookiepy) + a stealth headless browser (patchright), since X blocks logged-out
viewing. Best-effort: returns [] on any failure so the job never breaks.

Caveat: rookiepy decrypts Chrome cookies via the macOS login Keychain. Under
launchd the Keychain may be locked/non-interactive — if so this degrades to []
and the Smart Money section falls back to the institutional Tavily data."""
from __future__ import annotations

import re
from dataclasses import dataclass

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_ENGAGE = re.compile(r"^[\d.,]+[KM]?$")  # bare engagement counts (likes/reposts)


@dataclass
class XPost:
    handle: str
    text: str


def _chrome_cookies() -> list[dict]:
    import rookiepy
    raw = rookiepy.chrome(["x.com", ".x.com", "twitter.com", ".twitter.com"])
    out = []
    for c in raw:
        same = {"strict": "Strict", "lax": "Lax", "none": "None",
                "no_restriction": "None", "unspecified": "Lax"}.get(
                    str(c.get("same_site") or "Lax").lower(), "Lax")
        ck = {"name": c["name"], "value": c["value"], "domain": c["domain"],
              "path": c.get("path", "/"), "secure": bool(c.get("secure", True)),
              "httpOnly": bool(c.get("http_only", False)), "sameSite": same}
        if c.get("expires"):
            try:
                ck["expires"] = float(c["expires"])
            except (TypeError, ValueError):
                pass
        out.append(ck)
    return out


def _clean(article_text: str) -> str:
    """Trim an article's inner_text to the post body — drop trailing bare
    engagement-count lines."""
    lines = [ln.strip() for ln in article_text.splitlines() if ln.strip()]
    while lines and _ENGAGE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def fetch_posts(handle: str, max_posts: int = 5, timeout_ms: int = 45000) -> list[XPost]:
    """Return up to `max_posts` recent posts from x.com/<handle>. Best-effort:
    any error (no cookies, launch failure, X block, DOM change) -> []."""
    try:
        cookies = _chrome_cookies()
        if not cookies:
            return []
        from patchright.sync_api import sync_playwright
    except Exception:
        return []

    posts: list[XPost] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(user_agent=UA,
                                          viewport={"width": 1280, "height": 2200})
                ctx.add_cookies(cookies)
                page = ctx.new_page()
                page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                try:
                    page.wait_for_selector("article", timeout=20000)
                except Exception:
                    pass
                page.wait_for_timeout(3500)
                for a in page.query_selector_all("article")[:max_posts]:
                    txt = _clean(a.inner_text())
                    if txt:
                        posts.append(XPost(handle=handle, text=txt))
            finally:
                browser.close()
    except Exception:
        return posts  # whatever we managed to collect before the failure
    return posts
