"""tests/test_preflight — helpers/preflight.run_preflight 单元测试。

覆盖（4 用例）：
1. test_all_ok              — 所有依赖均满足 → overall='ok'，5 个 check 全 status='ok'
2. test_missing_deepseek_key — home 含空 .env → overall='error'，deepseek_api_key='error'
3. test_obsidian_path_not_exists — obsidian_vault_path 指向不存在路径 → overall='error'
4. test_schema_partial       — ai.db 存在但缺核心表 articles_curated_ai → overall='error'
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

from news_radar_ai.db import init_db
from news_radar_ai.helpers.preflight import CheckResult, PreflightReport, run_preflight

# ---- 共用 helpers ------------------------------------------------------------


def _make_home(
    tmp_path: Path,
    *,
    deepseek_key: str = "sk-testkey12345678",
    obsidian_path: str | None = None,
    upstream_raw_db: str | None = None,
) -> Path:
    """在 tmp_path 下建 fake home，写 config.yaml + .env。

    obsidian_path: None → 用 tmp_path/obsidian_vault（自动创建）
    upstream_raw_db: None → 用 tmp_path/upstream/raw.db（自动创建）
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)

    # 默认 obsidian vault — 实际存在 + 可写
    if obsidian_path is None:
        vault = tmp_path / "obsidian_vault"
        vault.mkdir(parents=True, exist_ok=True)
        obs_str = str(vault)
    else:
        obs_str = obsidian_path

    # 默认 upstream raw.db — 实际存在
    if upstream_raw_db is None:
        upstream_dir = tmp_path / "upstream"
        upstream_dir.mkdir(parents=True, exist_ok=True)
        raw_db = upstream_dir / "raw.db"
        raw_db.touch()
        upstream_str = str(raw_db)
    else:
        upstream_str = upstream_raw_db

    config_data = {
        "upstream": {
            "newsbox_raw_db_path": upstream_str,
            "fetch_freshness_threshold_hours": 4,
        },
        "llm": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        },
        "scoring": {
            "selection_threshold": 65,
            "source_bonus": {
                "official_first_party": 5,
                "kol": 0,
                "secondary": -5,
            },
        },
        "report": {
            "obsidian_vault_path": obs_str,
            "featured_max": 20,
            "short_body_fetch_threshold": 500,
        },
        "logging": {
            "level": "info",
            "file": str(home / "logs" / "news-radar-ai.log"),
            "rotation": "daily",
            "retention_days": 30,
        },
    }

    (home / "config.yaml").write_text(
        yaml.dump(config_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    if deepseek_key:
        (home / ".env").write_text(
            f"DEEPSEEK_API_KEY={deepseek_key}\n", encoding="utf-8"
        )
    else:
        # 空 .env（key 存在但值为空，用于触发 error）
        (home / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")

    return home


def _check_by_name(report: PreflightReport, name: str) -> CheckResult:
    """从 report 中按 name 取 CheckResult；找不到则 pytest.fail。"""
    for c in report.checks:
        if c.name == name:
            return c
    pytest.fail(f"CheckResult '{name}' not found in report checks")


# ---- 用例 1：全部 ok ---------------------------------------------------------


def test_all_ok(tmp_path: Path) -> None:
    """所有依赖均满足 → overall='ok'，5 个 check 全 status='ok'。

    newsbox_installed：当前开发环境已安装（本测试在项目 venv 中跑）。
    其余 4 项：全部用 tmp_path 提供满足条件的环境。
    """
    home = _make_home(tmp_path)

    # ai.db 完整 schema
    init_db(home / "ai.db")

    report = run_preflight(home=home)

    assert isinstance(report, PreflightReport)
    assert report.overall == "ok", (
        f"expected overall='ok', got '{report.overall}'; "
        f"checks={[(c.name, c.status, c.detail) for c in report.checks]}"
    )
    assert len(report.checks) == 5

    for check in report.checks:
        assert check.status == "ok", (
            f"check '{check.name}' expected ok, got '{check.status}': {check.detail}"
        )
        assert check.fix_hint is None, (
            f"check '{check.name}' expected fix_hint=None, got {check.fix_hint!r}"
        )


# ---- 用例 2：DEEPSEEK_API_KEY 为空 ------------------------------------------


def test_missing_deepseek_key(tmp_path: Path) -> None:
    """home 含空 .env（DEEPSEEK_API_KEY=<空>）→ overall='error'，deepseek_api_key='error'。"""
    home = _make_home(tmp_path, deepseek_key="")  # 空字符串 → 写 DEEPSEEK_API_KEY=

    # 提供 ai.db + obsidian vault（其他检查项不干扰）
    init_db(home / "ai.db")

    report = run_preflight(home=home)

    assert report.overall == "error", (
        f"expected overall='error', got '{report.overall}'"
    )

    deepseek_check = _check_by_name(report, "deepseek_api_key")
    assert deepseek_check.status == "error"
    assert deepseek_check.fix_hint is not None
    assert "DEEPSEEK_API_KEY" in deepseek_check.fix_hint


# ---- 用例 3：obsidian vault 路径不存在 ---------------------------------------


def test_obsidian_path_not_exists(tmp_path: Path) -> None:
    """obsidian_vault_path 指向不存在路径 → overall='error'，obsidian_vault_path='error'。"""
    nonexistent = str(tmp_path / "does_not_exist" / "vault")
    home = _make_home(tmp_path, obsidian_path=nonexistent)

    # 提供 ai.db + upstream raw.db（其他检查项不干扰）
    init_db(home / "ai.db")

    report = run_preflight(home=home)

    assert report.overall == "error", (
        f"expected overall='error', got '{report.overall}'"
    )

    obs_check = _check_by_name(report, "obsidian_vault_path")
    assert obs_check.status == "error"
    assert obs_check.fix_hint is not None
    assert "config set" in obs_check.fix_hint


# ---- 用例 4：ai.db 存在但缺表 -----------------------------------------------


def test_schema_partial(tmp_path: Path) -> None:
    """ai.db 存在但核心表 articles_curated_ai 被 DROP → overall='error'，
    ai_db_schema check status='error'。
    """
    home = _make_home(tmp_path)

    db_path = home / "ai.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE IF EXISTS articles_curated_ai")
        conn.commit()

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "articles_curated_ai" not in tables
    finally:
        conn.close()

    report = run_preflight(home=home)

    assert report.overall == "error", (
        f"expected overall='error', got '{report.overall}'"
    )

    schema_check = _check_by_name(report, "ai_db_schema")
    assert schema_check.status == "error"
    assert "articles_curated_ai" in schema_check.detail
    assert schema_check.fix_hint is not None
    assert "db init" in schema_check.fix_hint
