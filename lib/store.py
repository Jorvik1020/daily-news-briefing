"""Write briefing notes to the output directory.

Single source of truth for where briefings land. Override by exporting
NEWS_OUTPUT_DIR to point the pipeline at a different folder; defaults to
~/daily-news-output."""
from pathlib import Path
import os

OUTPUT_DIR = os.environ.get("NEWS_OUTPUT_DIR") or os.path.expanduser(
    "~/daily-news-output"
)


def out_path(*parts: str) -> str:
    """Absolute path to a sub-folder/file under the output root."""
    return os.path.join(OUTPUT_DIR, *parts)


def write_note(folder: str, name: str, content: str) -> str:
    """Write `content` to `folder/name`, creating `folder` if needed.
    Returns the absolute path written."""
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    fp = p / name
    fp.write_text(content, encoding="utf-8")
    return str(fp)
