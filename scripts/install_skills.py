#!/usr/bin/env python3
"""Install both self-contained skills; refuse to overwrite existing directories."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["claude", "codex", "cursor"], default="claude")
    parser.add_argument("--target", type=Path, help="Override destination skill root")
    parser.add_argument("--global", dest="global_install", action="store_true", help="Install under your home instead of this project")
    args = parser.parse_args()
    parent = Path.home() if args.global_install else ROOT
    destination = (args.target or parent / ("." + args.agent) / "skills").expanduser().resolve()
    names = ["newsbox", "news-radar-ai"]
    for name in names:
        if (destination / name).exists():
            parser.error(f"Refusing to overwrite {destination / name}; choose another --target")
    for name in names:
        shutil.copytree(ROOT / "skills" / name, destination / name)
        print("Installed:", destination / name)
    print("Start the assistant from the repository root; read AGENTS.md for local runtime paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
