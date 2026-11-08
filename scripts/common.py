"""Runtime paths isolated from the user's existing newsbox installations."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_root(value: str | None = None) -> Path:
    return Path(value or os.environ.get("NEWS_RADAR_DATA_DIR") or ROOT / ".data").expanduser().resolve()


def runtime_env(base: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["NEWSBOX_HOME"] = str(base / "newsbox")
    env["NEWS_RADAR_AI_HOME"] = str(base / "news-radar-ai")
    return env
