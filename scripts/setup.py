#!/usr/bin/env python3
"""Initialize a repository-local runtime. No scraping, API call or Docker startup."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from newsbox.config import write_default_config as write_collector_config
from newsbox.db import init_db as init_raw_db
from newsbox.sources import DEFAULT_SEED_PATH
from news_radar_ai.config import load_config, write_default_config
from news_radar_ai.db import init_db as init_ai_db
from news_radar_ai.helpers.config_writer import set_config_value

from common import ROOT, data_root


def direct_public_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        if not ipaddress.ip_address(host).is_global:
            return False
    except ValueError:
        pass
    return True


def initialize(base: Path, *, all_sources: bool = False, model: str | None = None) -> dict:
    collector, radar, reports = base / "newsbox", base / "news-radar-ai", base / "reports"
    base.mkdir(parents=True, exist_ok=True)
    collector.mkdir(exist_ok=True)
    radar.mkdir(exist_ok=True)
    reports.mkdir(exist_ok=True)
    for home in (base, collector, radar):
        if os.name == "posix":
            home.chmod(0o700)
    if not (collector / "config.yaml").exists():
        write_collector_config(home=collector)
    if not (collector / "sources.yaml").exists():
        catalog = yaml.safe_load(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
        if not all_sources:
            for kind, items in catalog.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and (kind == "twikit" or not direct_public_url(str(item.get("url", "")))):
                            item["enabled"] = False
        (collector / "sources.yaml").write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if not (radar / "config.yaml").exists():
        write_default_config(home=radar)
        set_config_value(home=radar, key_path="upstream.newsbox_raw_db_path", value=str(collector / "raw.db"))
        set_config_value(home=radar, key_path="report.obsidian_vault_path", value=str(reports))
        set_config_value(home=radar, key_path="logging.file", value=str(radar / "logs/news-radar-ai.log"))
    if model:
        set_config_value(home=radar, key_path="llm.model", value=model)
    init_raw_db(collector / "raw.db")
    init_ai_db(radar / "ai.db")
    cfg = load_config(radar)
    return {"collector": collector, "radar": radar, "reports": Path(cfg.report.obsidian_vault_path).expanduser()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="Default: repository .data or NEWS_RADAR_DATA_DIR")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt for an API key")
    parser.add_argument("--all-sources", action="store_true", help="First setup only: also enable upstream optional X/RSSHub sources")
    parser.add_argument("--model", help="DeepSeek model available to your account (original default: deepseek-v4-flash)")
    args = parser.parse_args()
    paths = initialize(data_root(args.data_dir), all_sources=args.all_sources, model=args.model)
    key_file = paths["radar"] / ".env"
    cfg = load_config(paths["radar"])
    if not cfg.secrets.deepseek_api_key and not args.non_interactive and sys.stdin.isatty():
        key = getpass.getpass("DeepSeek API key (hidden; Enter to skip): ").strip()
        if key:
            if "\n" in key or "\r" in key or not all(c.isascii() and (c.isalnum() or c in "-_") for c in key):
                parser.error("Unexpected key format; no credential was saved")
            if key_file.exists():
                print("Existing .env was preserved; configure the key in your environment instead.")
            else:
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write("DEEPSEEK_API_KEY=" + key + "\n")
    print("Setup complete. Existing configs, source choices and credentials were preserved.")
    print("Data:", data_root(args.data_dir))
    print("Reports:", paths["reports"])
    print("No network requests, API charges or Docker services were started.")
    print("Next: uv run python scripts/demo.py  (offline, no key)")
    print("Or:   uv run python scripts/run.py --since 24h --limit 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
