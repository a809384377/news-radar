#!/usr/bin/env python3
"""Run package suites separately with a temporary HOME and no inherited API keys."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="news-radar-check-") as temp:
        env = dict(os.environ)
        for key in list(env):
            if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                env.pop(key, None)
        for key in ("NEWSBOX_HOME", "NEWS_RADAR_AI_HOME", "NEWS_RADAR_DATA_DIR"):
            env.pop(key, None)
        env["HOME"] = temp
        env["NEWSBOX_HOME"] = str(Path(temp) / ".newsbox")
        env["NEWS_RADAR_AI_HOME"] = str(Path(temp) / ".news-radar-ai")
        env["XDG_CACHE_HOME"] = str(Path(temp) / ".cache")
        env["NO_COLOR"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        commands = [
            (ROOT / "packages/newsbox", [sys.executable, "-m", "pytest", "-q"]),
            (ROOT / "packages/news-radar-ai", [sys.executable, "-m", "pytest", "-q"]),
            (ROOT, [sys.executable, "-m", "pytest", "-q", "tests"]),
            (ROOT, [sys.executable, "scripts/setup.py", "--non-interactive", "--data-dir", str(Path(temp) / "runtime")]),
            (ROOT, [sys.executable, "scripts/run.py", "--skip-fetch", "--skip-score", "--data-dir", str(Path(temp) / "runtime")]),
            (ROOT, [sys.executable, "scripts/demo.py", "--output-dir", str(Path(temp) / "demo")]),
            (ROOT, [sys.executable, "scripts/install_skills.py", "--target", str(Path(temp) / "skills")]),
        ]
        for cwd, command in commands:
            print("\nCHECK:", " ".join(command), flush=True)
            result = subprocess.run(command, cwd=cwd, env=env)
            if result.returncode:
                return result.returncode
    print("All isolated checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
