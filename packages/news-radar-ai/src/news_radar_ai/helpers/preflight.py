"""helpers/preflight — 环境自检 helper（spec v2 Phase C 模块 1）。

每次 agent 跑 /news-radar-ai 前先调 run_preflight() 做 5 项检查，
返回结构化 PreflightReport。永不抛异常。

5 项检查：
1. newsbox_installed      — import newsbox + newsbox.sdk
2. deepseek_api_key       — load_config() + secrets.deepseek_api_key 非空
3. obsidian_vault_path    — 目录存在 + 可写
4. ai_db_schema           — ai.db 存在 + 3 表齐全
5. upstream_raw_db        — newsbox raw.db 路径存在
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

import news_radar_ai.config as config_module

# newsbox 是被检查对象，必须在检查内部 try/except 延迟 import


# ---- 数据结构 ----------------------------------------------------------------


@dataclass
class CheckResult:
    name: str            # 检查项 ID，如 "newsbox_installed"
    status: str          # "ok" | "warn" | "error"
    detail: str          # 一句话说明（人话）
    fix_hint: str | None # 修复建议，None 表示无需修复


@dataclass
class PreflightReport:
    overall: str           # "ok" | "warn" | "error"（取所有 check 中最严重的）
    checks: list[CheckResult]


# ---- 优先级映射（用于 overall 计算）-----------------------------------------

_SEVERITY: dict[str, int] = {"ok": 0, "warn": 1, "error": 2}

_REQUIRED_TABLES = {"articles_curated_ai"}


# ---- 单项检查函数 -------------------------------------------------------------


def _check_newsbox_installed() -> CheckResult:
    """检查 newsbox 包是否已安装（import newsbox + newsbox.sdk）。"""
    try:
        import newsbox  # noqa: F401
        import newsbox.sdk  # noqa: F401
        return CheckResult(
            name="newsbox_installed",
            status="ok",
            detail="newsbox 和 newsbox.sdk 均可导入",
            fix_hint=None,
        )
    except ImportError as exc:
        return CheckResult(
            name="newsbox_installed",
            status="error",
            detail=f"newsbox 包未安装：{exc}",
            fix_hint="uv add newsbox",
        )
    except Exception as exc:
        return CheckResult(
            name="newsbox_installed",
            status="error",
            detail=f"newsbox 包导入时发生意外错误：{exc}",
            fix_hint="uv add newsbox",
        )


def _check_deepseek_api_key(home: Path) -> CheckResult:
    """检查 DEEPSEEK_API_KEY 是否已在 .env 中配置且非空。"""
    try:
        cfg = config_module.load_config(home)
        key = cfg.secrets.deepseek_api_key
        if not key:
            return CheckResult(
                name="deepseek_api_key",
                status="error",
                detail="DEEPSEEK_API_KEY 未配置或为空字符串",
                fix_hint="在 ~/.news-radar-ai/.env 设置 DEEPSEEK_API_KEY=...",
            )
        return CheckResult(
            name="deepseek_api_key",
            status="ok",
            detail=f"DEEPSEEK_API_KEY 已配置（前 4 位：{key[:4]}****）",
            fix_hint=None,
        )
    except Exception as exc:
        return CheckResult(
            name="deepseek_api_key",
            status="error",
            detail=f"加载配置时发生错误：{exc}",
            fix_hint="在 ~/.news-radar-ai/.env 设置 DEEPSEEK_API_KEY=...",
        )


def _check_obsidian_vault_path(home: Path) -> CheckResult:
    """检查 Obsidian vault 路径存在且可写。"""
    try:
        cfg = config_module.load_config(home)
        raw_path = cfg.report.obsidian_vault_path
        vault_path = Path(raw_path).expanduser()

        if not vault_path.exists():
            return CheckResult(
                name="obsidian_vault_path",
                status="error",
                detail=f"Obsidian vault 路径不存在：{vault_path}",
                fix_hint="news-radar-ai config set report.obsidian_vault_path <可写路径>",
            )

        if not os.access(vault_path, os.W_OK):
            return CheckResult(
                name="obsidian_vault_path",
                status="error",
                detail=f"Obsidian vault 路径不可写：{vault_path}",
                fix_hint="news-radar-ai config set report.obsidian_vault_path <可写路径>",
            )

        return CheckResult(
            name="obsidian_vault_path",
            status="ok",
            detail=f"Obsidian vault 路径存在且可写：{vault_path}",
            fix_hint=None,
        )
    except Exception as exc:
        return CheckResult(
            name="obsidian_vault_path",
            status="error",
            detail=f"检查 Obsidian vault 路径时发生错误：{exc}",
            fix_hint="news-radar-ai config set report.obsidian_vault_path <可写路径>",
        )


def _check_ai_db_schema(home: Path) -> CheckResult:
    """检查 ai.db 存在且业务表齐全。"""
    db_path = home / "ai.db"

    if not db_path.exists():
        return CheckResult(
            name="ai_db_schema",
            status="warn",
            detail=f"ai.db 不存在：{db_path}",
            fix_hint="首次跑 pull 会自动 init",
        )

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            placeholders = ",".join(f"'{t}'" for t in _REQUIRED_TABLES)
            rows = conn.execute(
                f"SELECT name FROM sqlite_master "
                f"WHERE type='table' AND name IN ({placeholders})"
            ).fetchall()
            found_tables = {row[0] for row in rows}
        finally:
            conn.close()

        missing = _REQUIRED_TABLES - found_tables
        if missing:
            missing_str = ", ".join(sorted(missing))
            return CheckResult(
                name="ai_db_schema",
                status="error",
                detail=f"ai.db 缺少表：{missing_str}（已有：{', '.join(sorted(found_tables)) or '无'}）",
                fix_hint="news-radar-ai db init",
            )

        return CheckResult(
            name="ai_db_schema",
            status="ok",
            detail=f"ai.db 业务表齐全（{', '.join(sorted(_REQUIRED_TABLES))}）",
            fix_hint=None,
        )
    except Exception as exc:
        return CheckResult(
            name="ai_db_schema",
            status="error",
            detail=f"读取 ai.db 时发生错误：{exc}",
            fix_hint="news-radar-ai db init",
        )


def _check_upstream_raw_db(home: Path) -> CheckResult:
    """检查上游 newsbox raw.db 路径是否存在。"""
    try:
        cfg = config_module.load_config(home)
        raw_path = cfg.upstream.newsbox_raw_db_path
        db_path = Path(raw_path).expanduser()

        if not db_path.exists():
            return CheckResult(
                name="upstream_raw_db",
                status="warn",
                detail=f"上游 newsbox raw.db 不存在：{db_path}",
                fix_hint="先在 newsbox 仓库跑 `newsbox fetch`",
            )

        return CheckResult(
            name="upstream_raw_db",
            status="ok",
            detail=f"上游 newsbox raw.db 存在：{db_path}",
            fix_hint=None,
        )
    except Exception as exc:
        return CheckResult(
            name="upstream_raw_db",
            status="warn",
            detail=f"检查上游 raw.db 时发生错误：{exc}",
            fix_hint="先在 newsbox 仓库跑 `newsbox fetch`",
        )


# ---- 主入口 ------------------------------------------------------------------


def run_preflight(*, home: Path) -> PreflightReport:
    """跑 5 项检查，返回报告。永不抛异常（任何检查失败都包装成 CheckResult）。

    overall 规则：
    - 任一 error → overall=error
    - 无 error 但有 warn → overall=warn
    - 全 ok → overall=ok
    """
    home = Path(home)
    logger.debug(f"preflight: 开始自检，home={home}")

    checks: list[CheckResult] = [
        _check_newsbox_installed(),
        _check_deepseek_api_key(home),
        _check_obsidian_vault_path(home),
        _check_ai_db_schema(home),
        _check_upstream_raw_db(home),
    ]

    max_severity = max(_SEVERITY[c.status] for c in checks)
    overall = {v: k for k, v in _SEVERITY.items()}[max_severity]

    for check in checks:
        log_fn = logger.info if check.status == "ok" else logger.warning
        log_fn(
            f"preflight [{check.status.upper()}] {check.name}: {check.detail}"
            + (f" → {check.fix_hint}" if check.fix_hint else "")
        )

    logger.debug(f"preflight 完成：overall={overall}")
    return PreflightReport(overall=overall, checks=checks)
