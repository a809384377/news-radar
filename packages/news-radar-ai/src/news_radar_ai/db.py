"""SQLite 连接、迁移、查询封装（news-radar-ai / ai.db）。

定位：本库（ai.db）只装 AI 加工产物 + 日报记录。原始采集数据存在
newsbox 仓库的 raw.db（``~/.newsbox/raw.db``），由 ``newsbox.sdk.read_raw()`` 读取（D2）。

迁移策略：
- ``apply_migrations`` 扫描 migrations 目录下所有 ``*.sql`` 文件，按文件名升序应用。
- 已应用过的迁移记录在 ``schema_migrations`` 表（``filename`` 主键 + ``applied_at`` 时间戳）。

历史：旧版（news-radar/news.db）含 sqlite-vec 虚拟表与 ``-- @vec`` 可选段加载逻辑。
S2.1 重构后采集层迁出，向量索引等待 S3 真做 AI 流水线时再决定（届时未必继续用
sqlite-vec — 可能改为外部向量库或直接相似度计算），故本期一并清掉 vec 加载残留。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


# ---- 连接 -------------------------------------------------------------------


def get_conn(db_path: Path) -> sqlite3.Connection:
    """打开 SQLite 连接。

    - 自动创建父目录（若 ``db_path`` 含尚不存在的父路径）。
    - 启用 ``row_factory = sqlite3.Row``，方便按列名取值。
    - 启用 ``PRAGMA foreign_keys = ON``。
    - 调用方负责关闭连接。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---- 迁移 -------------------------------------------------------------------


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.commit()


def _applied_filenames(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT filename FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def apply_migrations(
    conn: sqlite3.Connection, migrations_dir: Path
) -> list[str]:
    """扫描 ``migrations_dir`` 下 ``*.sql`` 按文件名升序应用未执行过的迁移。

    返回本次新应用的文件名列表（按应用顺序）。
    幂等：第二次调用时已应用的迁移会被跳过，返回空列表。
    """
    migrations_dir = Path(migrations_dir)
    _ensure_migrations_table(conn)
    applied = _applied_filenames(conn)

    sql_files = sorted(p for p in migrations_dir.glob("*.sql"))
    newly_applied: list[str] = []

    for sql_file in sql_files:
        if sql_file.name in applied:
            continue

        sql_text = sql_file.read_text(encoding="utf-8")
        if sql_text.strip():
            conn.executescript(sql_text)

        conn.execute(
            "INSERT INTO schema_migrations(filename, applied_at) VALUES (?, ?)",
            (sql_file.name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        newly_applied.append(sql_file.name)
        logger.info(f"已应用迁移：{sql_file.name}")

    return newly_applied


# ---- 高层入口 ---------------------------------------------------------------


def _package_migrations_dir() -> Path:
    """定位包内 migrations 目录（与本模块同级）。"""
    return Path(__file__).parent / "migrations"


def init_db(db_path: Path) -> None:
    """新建/打开 ``db_path``，应用包内所有迁移，完成后关闭连接。"""
    conn = get_conn(db_path)
    try:
        apply_migrations(conn, _package_migrations_dir())
    finally:
        conn.close()
