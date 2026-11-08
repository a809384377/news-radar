from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from report import link, render_report, write_report

spec = importlib.util.spec_from_file_location("radar_repo_setup", ROOT / "scripts/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_setup_isolated_idempotent(tmp_path):
    base = tmp_path / "runtime"
    paths = setup.initialize(base)
    assert (paths["collector"] / "raw.db").is_file()
    assert (paths["radar"] / "ai.db").is_file()
    config = yaml.safe_load((paths["radar"] / "config.yaml").read_text())
    assert config["upstream"]["newsbox_raw_db_path"] == str(base / "newsbox/raw.db")
    sources = yaml.safe_load((paths["collector"] / "sources.yaml").read_text())
    assert len(sources["twikit"]) == 42
    assert all(row.get("enabled") is False for row in sources["twikit"])
    for kind in ["rss", "web"]:
        for row in sources[kind]:
            if row.get("enabled", True):
                assert setup.direct_public_url(row["url"])
    assert sum(len(sources[k]) for k in ["rss", "web", "twikit"]) == 76
    before = (paths["collector"] / "sources.yaml").read_bytes()
    setup.initialize(base, all_sources=True)
    assert before == (paths["collector"] / "sources.yaml").read_bytes()
    assert not (paths["radar"] / ".env").exists()


def test_environment_secret_precedence(tmp_path, monkeypatch):
    from news_radar_ai.config import load_config
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=fake-file-value\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-process-value")
    assert load_config(tmp_path).secrets.deepseek_api_key == "fake-process-value"


def test_report_is_labeled_safe_and_non_overwriting(tmp_path):
    now = datetime.now(timezone.utc)
    articles = json.loads((ROOT / "examples/candidates.demo.json").read_text())
    body = render_report(articles, window_start=now - timedelta(days=1), window_end=now, demo=True)
    assert body.startswith("---\n")
    assert "demo: true" in body
    assert "虚构" in body
    assert "精选 · 行业雷达" in body
    assert "精选 · 经验好帖" in body
    assert link("unsafe", "javascript:alert(1)") == "unsafe"
    assert link("private", "https://user:pass@example.com") == "private"
    first = write_report(tmp_path, body, now)
    second = write_report(tmp_path, body, now)
    assert first != second
    assert first.read_text() == second.read_text()


def test_source_catalog_copies_match():
    source = ROOT / "packages/newsbox/src/newsbox/data/sources.seed.yaml"
    for target in [ROOT / "docs/newsbox/sources.seed.yaml", ROOT / "skills/newsbox/references/sources.seed.yaml"]:
        assert source.read_bytes() == target.read_bytes()


def test_sdk_to_processing_integration(tmp_path):
    """Synthetic row only: exercise actual newsbox SDK -> pull -> candidate query."""
    from news_radar_ai.helpers.pull import pull_articles
    from news_radar_ai.helpers.queries import report_candidates
    paths = setup.initialize(tmp_path / "runtime")
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(paths["collector"] / "raw.db")
    conn.execute(
        "INSERT INTO articles_raw (source_type, source_id, source_tier, external_id, url, title, body, published_at, fetched_at, domain_tags, content_hash, url_canonical_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("rss", "synthetic", "official_first_party", "demo-1", "https://example.com/demo", "Synthetic AI test", "Not real news", now.isoformat(), now.isoformat(), '["ai"]', "synthetic-hash", "synthetic-url-hash"),
    )
    conn.commit()
    conn.close()
    since = now - timedelta(hours=1)
    stats = pull_articles(db_path=paths["radar"] / "ai.db", since=since, raw_db_path=paths["collector"] / "raw.db")
    assert stats.inserted == 1
    again = pull_articles(db_path=paths["radar"] / "ai.db", since=since, raw_db_path=paths["collector"] / "raw.db")
    assert again.inserted == 0 and again.skipped == 1
    conn = sqlite3.connect(paths["radar"] / "ai.db")
    conn.execute("UPDATE articles_curated_ai SET status='scored', final_score=82, track='radar', summary_zh='Synthetic fixture only'")
    conn.commit()
    conn.close()
    candidates = report_candidates(paths["radar"] / "ai.db", since, now + timedelta(seconds=1))
    assert len(candidates) == 1 and candidates[0]["weighted_score"] == 92
