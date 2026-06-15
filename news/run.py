"""Standalone daily-news job. No browser agent at runtime.

  uv run python -m news.run [--dry-run] [--since-days N] [--no-push]

Pipeline: gather (IMAP newsletters + prior briefing) -> Opus synthesises the
briefing -> write to the output dir -> (optionally) push to Telegram."""
import os
import sys
import argparse
from pathlib import Path

import gateway.gateway as gateway
from news import gather
from lib import store, telegram, net

NEWS_DIR = store.OUTPUT_DIR
PROMPT_PATH = Path(__file__).parent / "prompt.md"


def build_prompt(ctx: dict) -> str:
    tmpl = PROMPT_PATH.read_text(encoding="utf-8")
    sources_line = ", ".join(ctx["sources_present"]) or "(none — newsletters not found today)"
    return tmpl.format(
        date=ctx["date"],
        sources_line=sources_line,
        newsletters=ctx.get("newsletters") or "(no newsletters fetched today)",
        feeds=ctx.get("feeds") or "(no feeds configured)",
        web_fallback=ctx.get("web_fallback") or "(no web fallback — TAVILY_API_KEY unset or no results)",
        last_briefing=ctx.get("last_briefing") or "(no prior briefing on file)",
    )


def _telegram_creds() -> tuple[str, str]:
    """Read the Telegram bot token + chat id from the environment.
    Either being unset means 'skip Telegram and just write the file'."""
    return (os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            os.environ.get("TELEGRAM_CHAT_ID", ""))


def _notify(briefing: str) -> None:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        print("Telegram creds unset (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — "
              "skipping push.", file=sys.stderr)
        return
    telegram.send(token, chat_id, briefing, parse_mode="Markdown")


def _alert_failure(reason: str, gmail_errored: bool, has_web: bool) -> None:
    """Send a short alert that the briefing was skipped, so it's never silent."""
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        return
    why = "Gmail fetch error" if gmail_errored else "no newsletters in window"
    tail = " (web fallback held back to avoid a degraded briefing)" if has_web else ""
    try:
        telegram.send(token, chat_id,
                      f"⚠️ Daily news skipped this run — {why}{tail}.\n{reason}")
    except Exception:
        pass


def run_job(dry_run: bool = False, since_days: int = 1,
            no_push: bool = False, model: str | None = None) -> int:
    ctx = gather.gather_all(since_days=since_days)

    no_news = ctx["item_count"] == 0
    gmail_errored = bool(ctx["fetch_error"])
    has_web = bool(ctx.get("web_fallback"))

    if no_news:
        msg = ctx["fetch_error"] or "no allow-listed newsletters in the inbox window"
        print(f"⚠️  No source newsletters fetched ({msg}).", file=sys.stderr)
        # Abort (don't push) when there's genuinely nothing to synthesize from, OR
        # when Gmail ERRORED — a transient fetch failure must not silently replace
        # the real newsletter briefing with a web-only one. A genuinely-empty inbox
        # with web fallback present is allowed to proceed (the intended fallback).
        if (not has_web or gmail_errored) and not dry_run:
            _alert_failure(msg, gmail_errored, has_web)
            return 2

    prompt = build_prompt(ctx)
    briefing = gateway.ask(prompt, model=model)

    if dry_run:
        print(f"[gathered {ctx['item_count']} newsletters from: "
              f"{', '.join(ctx['sources_present']) or 'none'}]\n", file=sys.stderr)
        print(briefing)
        return 0

    filename = f"daily_news_{ctx['date']}.md"
    path = store.write_note(NEWS_DIR, filename, briefing)
    print(f"Wrote {path}")

    if no_push:
        print("Telegram push skipped (--no-push).")
        return 0
    try:
        _notify(briefing)
    except Exception as e:
        print(f"⚠️  Telegram push failed (briefing saved): {e}", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Generate the daily news briefing")
    ap.add_argument("--dry-run", action="store_true",
                    help="gather + synthesize + print; no write or push")
    ap.add_argument("--since-days", type=int, default=1,
                    help="IMAP lookback window in days (default 1; widen for catch-up)")
    ap.add_argument("--no-push", action="store_true",
                    help="write the briefing but don't push to Telegram")
    ap.add_argument("--model", default=None, help="LLM gateway alias (default from config)")
    args = ap.parse_args()
    net.wait_for_network()  # absorb the scheduler wake-race before any network call
    sys.exit(run_job(dry_run=args.dry_run, since_days=args.since_days,
                     no_push=args.no_push, model=args.model))


if __name__ == "__main__":
    main()
