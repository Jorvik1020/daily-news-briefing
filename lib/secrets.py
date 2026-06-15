"""Read KEY=VALUE secret files (e.g. the Telegram creds file).
Never log or print the values."""
from pathlib import Path


def load_env_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    env = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        env[k.strip()] = v
    return env
