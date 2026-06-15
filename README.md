# daily-news-briefing

Generate a daily news briefing from your own email newsletter subscriptions,
enriched with web search and institutional/individual investor signals, delivered
to a file + Telegram. Runs headless via the Anthropic API — no browser agent at
runtime.

## Features

- **Inbox-as-feed.** Reads your allow-listed newsletters from Gmail over IMAP
  (read-only — never deletes, moves, or marks mail), including newsletters you
  auto-forward from another mailbox.
- **LLM synthesis.** A single Anthropic model call turns the raw newsletters into a
  structured, deduplicated, signal-dense briefing (topic sections + Insights +
  Smart Money + TL;DR).
- **Optional web fallback.** When the inbox is thin on a topic (e.g. house-price
  indices), it runs authoritative web queries via the Tavily API to fill the gap.
- **Optional smart-money signal.** Institutional filings (13F / insider / activist /
  positioning surveys) via Tavily, plus an optional headless read of tracked
  individual investors on X.
- **Deduplication.** The prior briefing is fed back in as a baseline so unchanged
  stories are dropped.
- **Headless & resilient.** Pure stdlib I/O for IMAP/web/Telegram, network-wake
  retries, and a model fallback — built to run unattended under cron or launchd.
- **Safe by default.** If Gmail errors or no newsletters are found, the job aborts
  (and optionally alerts) rather than pushing a hallucinated or degraded briefing.

## How it works

1. **Gather** (`news/gather.py`) — pure I/O, no LLM:
   - Reads today's allow-listed newsletters from Gmail over IMAP. Both *direct*
     publishers (matched on the `From` header) and *forwarded* digests (matched on
     the subject line) are supported.
   - If `TAVILY_API_KEY` is set, runs the configured housing + smart-money web
     queries as a fallback enrichment.
   - If any X voices are configured, fetches their recent posts headlessly (see the
     caveat below).
   - Loads the most recent prior briefing as a dedup baseline.
2. **Synthesis** (`news/run.py` + `news/prompt.md`) — builds one prompt from the
   gathered material and sends it through the LLM gateway (`gateway/gateway.py`,
   Anthropic by default).
3. **Output** — writes `daily_news_<date>.md` to the output directory, then (if
   Telegram is configured) pushes the briefing to your bot.

### The IMAP forwarding model

Many newsletters land in a personal mailbox you don't read over IMAP. The pipeline
supports auto-forwarding those digests into the inbox it *does* read: mail from a
`forwarders` address is always included, and the real publication is inferred from
the subject line via `subject_markers` (e.g. `"FW: In Today's FT: ..."` → `FT`).

### The Tavily web fallback

Newsletters rarely carry daily house-price or institutional-positioning data. When
`TAVILY_API_KEY` is set, the pipeline runs the configured `housing` and `smart_money`
queries (restricted to authoritative domains) and passes the results to the model
labelled `[web fallback]`. The prompt is instructed to *prefer* the newsletters and
use the web results only where the inbox lacks coverage. No key → the web fallback
is silently skipped.

### Optional X-voice tracking (advanced, best-effort)

If you list handles under the top-level `x_voices` setting, the pipeline will try
to read their recent posts using a stealth headless browser (patchright) driven with
**your own logged-in browser cookies** (rookiepy reads them from Chrome). This is
strictly best-effort and returns nothing on any failure. Caveats:

- It needs your browser cookies for X — you must be logged in to X in Chrome.
- X actively blocks automated/logged-out reads; this may stop working at any time.
- On macOS, rookiepy decrypts Chrome cookies via the login Keychain, which may be
  locked or non-interactive on a schedule — in that case it degrades to nothing and
  the Smart Money section falls back to the institutional Tavily data.

Leave `x_voices: []` to disable it entirely.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
# 1. Install dependencies
uv sync

# 2. Create a Gmail APP PASSWORD (not your account password):
#    enable 2-Step Verification, then myaccount.google.com -> App passwords.

# 3. (optional) Get a Tavily API key for the web fallback: tavily.com

# 4. (optional) Create a Telegram bot via @BotFather and note its token + your
#    chat id. If unset, the pipeline just writes the file and skips the push.

# 5. Configure your sources:
cp config/sources.example.yaml config/sources.yaml
#    then edit config/sources.yaml (it is gitignored).
```

## Configuration

Edit `config/sources.yaml` (copied from `config/sources.example.yaml`). Sections:

- **`domains`** — `From`-header substring → source label, for publishers that send
  *directly* to your inbox (e.g. `"ft.com": "FT"`).
- **`forwarders`** — addresses you forward newsletters *from*. Mail from these is
  always included; the source label is inferred from the subject.
- **`subject_markers`** — subject substring → source label, for forwarded mail. First
  match wins, so order matters.
- **`default_forward_label`** — label for forwarded mail whose subject matches no
  marker.
- **`feeds`** — `name: url` map of RSS/Atom feeds to pull recent items from
  (best-effort, no API key). Empty by default.
- **`x_voices`** / **`x_posts_per_voice`** — top-level setting: handles of specific
  X accounts to track (empty = disabled), and how many recent posts per account.
- **`telegram`** — `push: true|false`. When `false`, the briefing is written to file
  but not pushed (the token/chat id still come from env, never this file). Defaults
  to `true` when the section is absent.
- **`topics`** — the standing topic lenses; the prompt builds one section per topic
  that has coverage.
- **`web_fallback`** — optional Tavily enrichment:
  - `housing` / `housing_domains` — house-price-index queries and the authoritative
    domains to restrict them to. The shipped examples are UK-centric — retune them
    to your region.
  - `smart_money` / `smart_money_domains` — institutional-filing / positioning queries
    and their authoritative aggregator domains.
  - `max_results` / `days` — per-query result cap and recency window.

## Running

```sh
# Export your secrets (or source a .env — see scripts/run.sh):
export ANTHROPIC_API_KEY=...
export GMAIL_USER=you@gmail.com
export GMAIL_APP_PASSWORD=...
export TAVILY_API_KEY=...          # optional
export TELEGRAM_BOT_TOKEN=...      # optional
export TELEGRAM_CHAT_ID=...        # optional

# Dry run: gather + synthesize + print to stdout, no write or push.
uv run python -m news.run --dry-run

# Real run: write daily_news_<date>.md to the output dir and push to Telegram.
uv run python -m news.run

# Or via the wrapper (cd-safe):
sh scripts/run.sh --dry-run
```

Other flags: `--since-days N` widens the IMAP lookback window (for catch-up runs),
`--no-push` writes the file but skips Telegram, `--model <alias>` overrides the LLM
gateway alias from `gateway/config.yaml`.

## Scheduling

Run it every morning with cron:

```cron
# 06:30 daily — assumes secrets are exported in the cron environment or sourced
# from a .env inside scripts/run.sh.
30 6 * * * /bin/sh /path/to/daily-news-briefing/scripts/run.sh >> /tmp/daily-news.log 2>&1
```

On macOS, a launchd `StartCalendarInterval` agent works too (the network-wake retry
in `lib/net.py` is there specifically to absorb the gap between wake and Wi-Fi when
a missed scheduled run fires).

## Environment variables

| Variable              | Required | Purpose                                                        |
| --------------------- | -------- | -------------------------------------------------------------- |
| `ANTHROPIC_API_KEY`   | yes      | LLM synthesis via the Anthropic API.                           |
| `GMAIL_USER`          | yes      | Gmail address read over IMAP (`GMAIL_ADDRESS` also accepted).  |
| `GMAIL_APP_PASSWORD`  | yes      | Gmail **app password** (needs 2-Step Verification).            |
| `TAVILY_API_KEY`      | no       | Enables the web fallback (housing + smart money). Skipped if unset. |
| `TELEGRAM_BOT_TOKEN`  | no       | Telegram bot token. If unset, the push is skipped.             |
| `TELEGRAM_CHAT_ID`    | no       | Telegram chat id to deliver to. If unset, the push is skipped. |
| `NEWS_OUTPUT_DIR`     | no       | Where briefings are written (default `~/daily-news-output`).   |

## Security note

All secrets are read from the environment only — nothing is hardcoded, logged, or
committed. `config/sources.yaml` and `.env` are gitignored. Gmail access uses a
read-only IMAP session with an app password (never your account password). Never
commit your real `config/sources.yaml`, `.env`, or any token.

## License

MIT — see [LICENSE](LICENSE).
