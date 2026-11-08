#!/usr/bin/env python3
"""Collect -> pull -> bounded paid scoring -> JSON and basic Markdown report."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from news_radar_ai.cli import parse_since
from news_radar_ai.config import load_config
from news_radar_ai.helpers.filter_score import run_filter_score
from news_radar_ai.helpers.pull import pull_articles
from news_radar_ai.helpers.queries import report_candidates

from common import data_root, runtime_env
from report import render_report, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="24h", help="24h, 7d, today or ISO timestamp")
    parser.add_argument("--limit", type=int, default=20, help="Maximum pending articles to score (API cost bound)")
    parser.add_argument("--data-dir")
    parser.add_argument("--source", default="all", help="newsbox source ID/type; disabled sources stay disabled")
    parser.add_argument("--skip-fetch", action="store_true", help="Use previously collected data")
    parser.add_argument("--skip-score", action="store_true", help="No paid API calls; export already scored candidates")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    base = data_root(args.data_dir)
    home = base / "news-radar-ai"
    if not (home / "config.yaml").exists():
        parser.error("Run uv run python scripts/setup.py first")
    cfg = load_config(home)
    if not args.skip_score and not cfg.secrets.deepseek_api_key:
        parser.error("DeepSeek key missing; run setup interactively or configure your process environment. No fetch/API request has been made.")
    start = parse_since(args.since)
    if start > datetime.now().astimezone():
        parser.error("--since must be in the past")
    if not args.skip_fetch:
        command = [sys.executable, "-m", "newsbox.cli", "fetch", "--since", start.isoformat(), "--source", args.source, "--json"]
        proc = subprocess.run(command, env=runtime_env(base), text=True, capture_output=True)
        if proc.returncode:
            print("Collector failed; inspect the local collector logs (no scoring was run).", file=sys.stderr)
            return 1
        try:
            result = json.loads(proc.stdout)
            payload = result.get("details", result)
            entries = payload.get("results", [])
            errors = [r for r in entries if r.get("error")]
            print(f"Collection: {len(entries)} sources, {len(errors)} failed")
            if entries and len(errors) == len(entries):
                print("All matched sources failed; no scoring was run.", file=sys.stderr)
                return 1
        except (ValueError, TypeError, AttributeError):
            print("Unexpected collector response; no scoring was run.", file=sys.stderr)
            return 1
    raw_db = Path(cfg.upstream.newsbox_raw_db_path).expanduser()
    if not raw_db.exists():
        print("Raw database missing; run setup and collect first.", file=sys.stderr)
        return 1
    pulled = pull_articles(db_path=home / "ai.db", since=start, raw_db_path=raw_db)
    print(f"Pull: seen={pulled.seen} inserted={pulled.inserted} skipped={pulled.skipped}")
    failed = 0
    if not args.skip_score:
        print(f"Scoring up to {args.limit} pending articles with {cfg.llm.model}; this sends article text to DeepSeek and incurs API charges.", flush=True)
        scored = run_filter_score(db_path=home / "ai.db", api_key=cfg.secrets.deepseek_api_key, model=cfg.llm.model, source_bonus=cfg.scoring.source_bonus, selection_threshold=cfg.scoring.selection_threshold, experience_threshold=cfg.scoring.experience_threshold, concurrency=cfg.llm.concurrency, limit=args.limit, raw_db_path=raw_db)
        failed = scored.failed
        print(f"Scoring: processed={scored.processed} scored={scored.scored} rejected={scored.filter_rejected} failed={failed}")
    end = datetime.now().astimezone()
    candidates = report_candidates(home / "ai.db", start, end)
    output = Path(cfg.report.obsidian_vault_path).expanduser()
    report_path = write_report(output, render_report(candidates, window_start=start, window_end=end, threshold=cfg.scoring.featured_threshold, featured_max=cfg.report.featured_max), end)
    with report_path.with_suffix(".json").open("x", encoding="utf-8") as file:
        json.dump(candidates, file, ensure_ascii=False, indent=2, default=str)
    print("Report:", report_path)
    print("Candidates:", len(candidates))
    if failed:
        print("Warning: some API calls failed; output may be incomplete. Check the model name and local failure records.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
