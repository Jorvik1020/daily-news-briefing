"""LLM Gateway: the only place that calls an LLM provider.
Swap providers by editing config.yaml — no job code changes."""
from pathlib import Path
import yaml
import litellm

from lib import net

_CFG = None

def _config():
    global _CFG
    if _CFG is None:
        _CFG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())
    return _CFG

def ask(prompt: str, model: str | None = None, _seen: set | None = None, **kw) -> str:
    """Send a single prompt, return the text response.
    `model` is an alias from config.yaml (e.g. 'claude'); falls back on error."""
    cfg = _config()
    alias = model or cfg["default"]
    _seen = _seen or set()
    real = cfg["aliases"].get(alias, alias)
    try:
        # Retry transient provider hiccups (server disconnects, connection drops)
        # before giving up on this alias; non-transient errors fall through to the
        # alias fallback below immediately.
        r = net.retry(
            lambda: litellm.completion(
                model=real, messages=[{"role": "user", "content": prompt}], **kw
            ),
            tries=3, label=f"llm:{alias}",
        )
        return r["choices"][0]["message"]["content"]
    except Exception:
        _seen.add(alias)
        fb = cfg.get("fallback", {}).get(alias)
        if fb and fb not in _seen:
            return ask(prompt, model=fb, _seen=_seen, **kw)
        raise
