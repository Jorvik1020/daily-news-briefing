from news import run

CTX = {
    "date": "2026-06-05",
    "topics": ["Macro Economy", "Tech & AI", "Housing Market", "Gaming"],
    "newsletters": "### [FT] FirstFT\nUK PMI 48.5",
    "sources_present": ["FT", "The Information"],
    "item_count": 2,
    "fetch_error": "",
    "last_briefing": "*📰 Daily Briefing — 2026-06-04*",
}


def test_build_prompt_fills_template():
    p = run.build_prompt(CTX)
    assert "2026-06-05" in p
    assert "FT, The Information" in p          # sources line
    assert "UK PMI 48.5" in p
    assert "{newsletters}" not in p
    assert "{last_briefing}" not in p


def test_dry_run_does_not_write_or_push(monkeypatch, capsys):
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "*📰 Daily Briefing — 2026-06-05*")
    wrote = {"n": 0}; pushed = {"n": 0}
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1))
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("n", pushed["n"] + 1))
    rc = run.run_job(dry_run=True)
    assert rc == 0
    assert wrote["n"] == 0 and pushed["n"] == 0
    assert "Daily Briefing" in capsys.readouterr().out


def test_zero_newsletters_no_web_aborts_and_alerts(monkeypatch):
    empty = {**CTX, "item_count": 0, "sources_present": [], "newsletters": "",
             "fetch_error": "GMAIL_USER not set", "web_fallback": ""}
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: empty)
    called = {"ask": 0, "write": 0, "push": 0, "alert": 0}
    monkeypatch.setattr(run.gateway, "ask", lambda *a, **k: called.__setitem__("ask", 1) or "x")
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: called.__setitem__("write", 1))
    monkeypatch.setattr(run, "_notify", lambda b: called.__setitem__("push", 1))
    monkeypatch.setattr(run, "_alert_failure", lambda *a, **k: called.__setitem__("alert", 1))
    rc = run.run_job(dry_run=False)
    assert rc == 2
    assert called == {"ask": 0, "write": 0, "push": 0, "alert": 1}


def test_gmail_error_with_web_still_aborts(monkeypatch):
    # Gmail ERRORED but web fallback exists -> do NOT push a degraded web-only
    # briefing in place of the real one; skip + alert.
    ctx = {**CTX, "item_count": 0, "sources_present": ["Web fallback (Tavily)"],
           "newsletters": "", "fetch_error": "socket EOF", "web_fallback": "### [web fallback] x"}
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: ctx)
    called = {"ask": 0, "alert": 0}
    monkeypatch.setattr(run.gateway, "ask", lambda *a, **k: called.__setitem__("ask", 1) or "x")
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: None)
    monkeypatch.setattr(run, "_notify", lambda b: None)
    monkeypatch.setattr(run, "_alert_failure", lambda *a, **k: called.__setitem__("alert", 1))
    assert run.run_job(dry_run=False) == 2
    assert called == {"ask": 0, "alert": 1}


def test_empty_inbox_with_web_proceeds(monkeypatch):
    # Gmail OK (no error) but genuinely empty inbox + web fallback present ->
    # proceed with the web-fallback briefing (the intended fallback path).
    ctx = {**CTX, "item_count": 0, "sources_present": ["Web fallback (Tavily)"],
           "newsletters": "", "fetch_error": "", "web_fallback": "### [web fallback] housing"}
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: ctx)
    monkeypatch.setattr(run.gateway, "ask", lambda *a, **k: "briefing")
    written = []
    monkeypatch.setattr(run.store, "write_note", lambda f, n, c: written.append(n) or "/p")
    pushed = {"n": 0}
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("n", 1))
    rc = run.run_job(dry_run=False)
    assert rc == 0
    assert written and pushed["n"] == 1


def test_real_run_writes_output_and_pushes(monkeypatch):
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "*📰 Daily Briefing — 2026-06-05*\nbody")
    names = []
    monkeypatch.setattr(run.store, "write_note",
                        lambda folder, name, content: names.append((folder, name)) or f"{folder}/{name}")
    pushed = {"done": False}
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("done", True))
    rc = run.run_job(dry_run=False)
    assert rc == 0
    # ONE write to the output dir (no legacy mirror)
    assert len(names) == 1
    folder, name = names[0]
    assert name == "daily_news_2026-06-05.md"
    assert folder == run.NEWS_DIR
    assert pushed["done"] is True


def test_no_push_flag_writes_but_skips_telegram(monkeypatch):
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "briefing")
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: "/p")
    pushed = {"n": 0}
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("n", pushed["n"] + 1))
    rc = run.run_job(dry_run=False, no_push=True)
    assert rc == 0 and pushed["n"] == 0


def test_telegram_push_false_in_config_skips_push(monkeypatch, capsys):
    # config telegram.push: false must behave like --no-push: write the file,
    # skip the push, print a short note.
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gather, "load_sources", lambda: {"telegram": {"push": False}})
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "briefing")
    wrote = {"n": 0}
    monkeypatch.setattr(run.store, "write_note",
                        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1) or "/p")
    pushed = {"n": 0}
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("n", pushed["n"] + 1))
    rc = run.run_job(dry_run=False)
    assert rc == 0
    assert wrote["n"] == 1          # file still written
    assert pushed["n"] == 0         # push skipped
    assert "telegram.push" in capsys.readouterr().out


def test_telegram_push_default_true_when_absent(monkeypatch):
    # No telegram section -> push stays enabled (back-compat).
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gather, "load_sources", lambda: {})
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "briefing")
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: "/p")
    pushed = {"n": 0}
    monkeypatch.setattr(run, "_notify", lambda b: pushed.__setitem__("n", pushed["n"] + 1))
    rc = run.run_job(dry_run=False)
    assert rc == 0 and pushed["n"] == 1


def test_push_failure_still_succeeds(monkeypatch):
    monkeypatch.setattr(run.gather, "gather_all", lambda **k: CTX)
    monkeypatch.setattr(run.gateway, "ask", lambda prompt, **k: "briefing")
    monkeypatch.setattr(run.store, "write_note", lambda *a, **k: "/p")
    def boom(b): raise RuntimeError("tg down")
    monkeypatch.setattr(run, "_notify", boom)
    assert run.run_job(dry_run=False) == 0


def test_notify_skipped_without_telegram_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    sent = {"n": 0}
    monkeypatch.setattr(run.telegram, "send", lambda *a, **k: sent.__setitem__("n", 1))
    run._notify("hello")            # no creds -> must NOT call telegram.send
    assert sent["n"] == 0


def test_notify_sends_when_telegram_env_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    captured = {}
    monkeypatch.setattr(run.telegram, "send",
                        lambda token, chat_id, text, **k: captured.update(
                            token=token, chat_id=chat_id, text=text))
    run._notify("hello")
    assert captured["token"] == "tok" and captured["chat_id"] == "123"
    assert captured["text"] == "hello"
