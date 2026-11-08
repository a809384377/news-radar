#!/usr/bin/env python3
"""Render a clearly labeled synthetic report: no network, no key, no production DB."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ROOT
from report import render_report, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".data/demo-reports")
    args = parser.parse_args()
    end = datetime.now(timezone.utc)
    articles = json.loads((ROOT / "examples/candidates.demo.json").read_text(encoding="utf-8"))
    path = write_report(args.output_dir, render_report(articles, window_start=end - timedelta(days=1), window_end=end, demo=True), end)
    print("Offline synthetic demo created:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
