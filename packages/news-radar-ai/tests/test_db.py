"""db 模块单元测试（news-radar-ai / ai.db schema）。

覆盖：
1. ``init_db`` 建出 articles_curated_ai / reports 两张主表
2. ``apply_migrations`` 幂等
3. ``schema_migrations`` 跟踪表正确记录
4. articles_curated_ai (raw_source_type, raw_external_id) UNIQUE 约束生效
5. reports report_key UNIQUE 约束生效
6. spec v2 §6 三表 + 索引存在，FK 约束已记录
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from news_radar_ai import db as db_module
from news_radar_ai.db import apply_migrations, get_conn, init_db


# ---- helpers ---------------------------------------------------------------


def _table_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    return {row[0] for row in cur.fetchall()}


# ---- tests -----------------------------------------------------------------


def test_apply_migrations_creates_tables(tmp_path: Path) -> None:
    """0006 后只剩 articles_curated_ai（reports/clusters 已被 0006 DROP）。"""
    db_path = tmp_path / "ai.db"
    init_db(db_path)

    conn = get_conn(db_path)
    try:
        tables = _table_names(conn)
        assert "articles_curated_ai" in tables, f"缺少表 articles_curated_ai; got {tables}"
        # 0006 lean：reports / report_articles / clusters 已被 DROP
        for dropped in ("reports", "report_articles", "clusters"):
            assert dropped not in tables, (
                f"0006 后表 {dropped} 应已 DROP；got {tables}"
            )
        # 旧 v1 单仓库时代的表名不应再出现
        for legacy in ("articles", "source_state", "articles_vec"):
            assert legacy not in tables, (
                f"旧 schema 表 {legacy} 不应存在于 ai.db；got {tables}"
            )
    finally:
        conn.close()


def test_apply_migrations_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "ai.db"
    init_db(db_path)

    # 第二次调用 apply_migrations 应该返回空列表
    conn = get_conn(db_path)
    try:
        migrations_dir = Path(db_module.__file__).parent / "migrations"
        result = apply_migrations(conn, migrations_dir)
        assert result == [], f"第二次迁移不应有新应用文件，got {result}"
    finally:
        conn.close()


def test_schema_migrations_tracking(tmp_path: Path) -> None:
    db_path = tmp_path / "ai.db"
    init_db(db_path)

    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename"
        )
        names = [row[0] for row in cur.fetchall()]
        assert "0001_init.sql" in names, (
            f"schema_migrations 缺少 0001_init.sql: {names}"
        )
    finally:
        conn.close()


def test_articles_curated_ai_unique_constraint(tmp_path: Path) -> None:
    db_path = tmp_path / "ai.db"
    init_db(db_path)

    conn = get_conn(db_path)
    try:
        insert_sql = """
            INSERT INTO articles_curated_ai
                (raw_source_type, raw_external_id,
                 title, url, source_id, source_tier, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            "rss", "https://example.com/post/abc",
            "Test Title", "https://example.com/post/abc",
            "src-rss-test", "kol", "pending", "2026-05-17T00:00:00",
        )
        conn.execute(insert_sql, params)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            # 同样的 (raw_source_type, raw_external_id) 必须冲突
            conn.execute(insert_sql, params)
            conn.commit()
    finally:
        conn.close()


def test_reports_unique_constraint(tmp_path: Path) -> None:
    """reports.report_key UNIQUE 约束（0005 时代；0006 后该表已删，在 0005 截止 db 上测）。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)

    try:
        insert_sql = """
            INSERT INTO reports
                (report_key, window_start, window_end, window_label,
                 file_path, selection, stats)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            "daily-2026-05-08",
            "2026-05-07T00:00:00",
            "2026-05-08T00:00:00",
            "过去 24h",
            "/tmp/2026-05-08.md",
            '{"selected_article_ids": [], "featured_clusters": []}',
            '{"raw_count": 0, "filter_passed": 0, "scored": 0, "clustered": 0, "selected": 0}',
        )
        conn.execute(insert_sql, params)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            # 同 report_key 必须冲突
            conn.execute(insert_sql, params)
            conn.commit()
    finally:
        conn.close()


def test_report_articles_table_present(tmp_path: Path) -> None:
    """0003 应用后 report_articles 表 + 2 索引存在；UNIQUE + 3 FK 约束齐全（0005 截止 db）。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)

    try:
        tables = _table_names(conn)
        assert "report_articles" in tables, f"缺少表 report_articles; got {tables}"

        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?)",
            ("idx_report_articles_report", "idx_report_articles_article"),
        )
        found = {row[0] for row in cur.fetchall()}
        assert found == {"idx_report_articles_report", "idx_report_articles_article"}, (
            f"report_articles 缺少索引；got {found}"
        )

        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='report_articles'"
        )
        ra_sql = cur.fetchone()[0]
        assert "UNIQUE" in ra_sql, "report_articles 缺少 UNIQUE 约束"
        assert ra_sql.count("FOREIGN KEY") == 3, (
            f"report_articles 应该有 3 个 FOREIGN KEY，DDL={ra_sql}"
        )
        assert "ON DELETE CASCADE" in ra_sql, "report_id FK 应为 ON DELETE CASCADE"
        assert "ON DELETE RESTRICT" in ra_sql, "article FK 应为 ON DELETE RESTRICT"
    finally:
        conn.close()


def _seed_report_and_articles(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """造一份 report 行 + 两篇 article（返回 report_id, art_a_id, art_b_id）供后续测试用。"""
    # article A
    conn.execute(
        """INSERT INTO articles_curated_ai
           (raw_source_type, raw_external_id, title, url, source_id, source_tier, status, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("rss", "ext-a", "Article A", "https://example.com/a",
         "src-a", "kol", "clustered", "2026-05-17T00:00:00"),
    )
    art_a_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # article B
    conn.execute(
        """INSERT INTO articles_curated_ai
           (raw_source_type, raw_external_id, title, url, source_id, source_tier, status, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("rss", "ext-b", "Article B", "https://example.com/b",
         "src-b", "kol", "clustered", "2026-05-17T00:00:00"),
    )
    art_b_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # report
    conn.execute(
        """INSERT INTO reports
           (report_key, window_start, window_end, window_label, file_path, selection, stats)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("AInews-20260517-1030", "2026-05-16T10:30:00", "2026-05-17T10:30:00",
         "过去 24h", "/tmp/x.md", "{}", "{}"),
    )
    report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return report_id, art_a_id, art_b_id


def test_report_articles_unique_constraint(tmp_path: Path) -> None:
    """同一篇 article 在同一份 report 里只能写一次（4 道闸的"不重复"硬保障）。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        report_id, art_a_id, _ = _seed_report_and_articles(conn)
        insert_ra = """INSERT INTO report_articles
            (report_id, article_id, role, position) VALUES (?, ?, ?, ?)"""
        conn.execute(insert_ra, (report_id, art_a_id, "featured", 0))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert_ra, (report_id, art_a_id, "related", 0))
            conn.commit()
    finally:
        conn.close()


def test_report_articles_cascade_on_report_delete(tmp_path: Path) -> None:
    """删 report 应自动级联删 report_articles 关系。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        report_id, art_a_id, art_b_id = _seed_report_and_articles(conn)
        # featured 不带 parent；related 必须挂在某 featured 下（0004 CHECK 强制）
        conn.execute(
            """INSERT INTO report_articles
               (report_id, article_id, role, position, parent_article_id)
               VALUES (?, ?, 'featured', 0, NULL),
                      (?, ?, 'related', 0, ?)""",
            (report_id, art_a_id, report_id, art_b_id, art_a_id),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM report_articles").fetchone()[0] == 2

        conn.execute("DELETE FROM reports WHERE id=?", (report_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM report_articles").fetchone()[0] == 0
    finally:
        conn.close()


def test_report_articles_restrict_on_article_delete(tmp_path: Path) -> None:
    """删 article 时如有 report_articles 引用应被 RESTRICT 阻止。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        report_id, art_a_id, _ = _seed_report_and_articles(conn)
        conn.execute(
            "INSERT INTO report_articles (report_id, article_id, role, position) VALUES (?, ?, ?, ?)",
            (report_id, art_a_id, "featured", 0),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM articles_curated_ai WHERE id=?", (art_a_id,))
            conn.commit()
    finally:
        conn.close()


def test_articles_curated_ai_dropped_last_report_key(tmp_path: Path) -> None:
    """0003 后 articles_curated_ai 不应再含 last_report_key 列。"""
    db_path = tmp_path / "ai.db"
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        cur = conn.execute("PRAGMA table_info(articles_curated_ai)")
        columns = {row[1] for row in cur.fetchall()}
        assert "last_report_key" not in columns, (
            f"last_report_key 应已被 0003 DROP，但仍存在；现有列={columns}"
        )
    finally:
        conn.close()


def test_reports_has_note_column(tmp_path: Path) -> None:
    """0003 后 reports.note 列应存在且可写 NULL / 文本。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        cur = conn.execute("PRAGMA table_info(reports)")
        columns = {row[1] for row in cur.fetchall()}
        assert "note" in columns, f"reports 缺少 note 列；现有列={columns}"

        # 不传 note → NULL
        conn.execute(
            """INSERT INTO reports
               (report_key, window_start, window_end, window_label, file_path, selection, stats)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("AInews-20260517-1030", "2026-05-16T10:30:00", "2026-05-17T10:30:00",
             "过去 24h", "/tmp/x.md", "{}", "{}"),
        )
        # 传 note → 文本
        conn.execute(
            """INSERT INTO reports
               (report_key, window_start, window_end, window_label, file_path, selection, stats, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("AInews-20260517-1600", "2026-05-17T10:30:00", "2026-05-17T16:00:00",
             "过去 5h", "/tmp/y.md", "{}", "{}", "补充播报"),
        )
        conn.commit()

        rows = conn.execute("SELECT note FROM reports ORDER BY id").fetchall()
        assert [r[0] for r in rows] == [None, "补充播报"]
    finally:
        conn.close()


def test_report_articles_role_check(tmp_path: Path) -> None:
    """0004 后 report_articles.role 只能是 'featured' / 'related'，其他值被 CHECK 拒绝。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        report_id, art_a_id, _ = _seed_report_and_articles(conn)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO report_articles (report_id, article_id, role, position) "
                "VALUES (?, ?, 'bogus_role', 0)",
                (report_id, art_a_id),
            )
            conn.commit()
    finally:
        conn.close()


def test_report_articles_parent_nullability_check(tmp_path: Path) -> None:
    """0004 后 role='featured' 必须 parent_article_id IS NULL；role='related' 必须非 NULL。0005 截止 db。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        report_id, art_a_id, art_b_id = _seed_report_and_articles(conn)

        # featured + parent_article_id 非 NULL → 拒
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO report_articles "
                "(report_id, article_id, role, position, parent_article_id) "
                "VALUES (?, ?, 'featured', 0, ?)",
                (report_id, art_a_id, art_b_id),
            )
            conn.commit()

        # related + parent_article_id NULL → 拒
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO report_articles "
                "(report_id, article_id, role, position, parent_article_id) "
                "VALUES (?, ?, 'related', 0, NULL)",
                (report_id, art_b_id),
            )
            conn.commit()
    finally:
        conn.close()


def test_curation_schema_present(tmp_path: Path) -> None:
    """spec v2 §6（0005 及之前）：三表 + 4 索引存在；UNIQUE + FK 约束已记录。

    注意：0006 之后 clusters / reports / report_articles 已删除，此测试需要在仅
    包含 0001-0005 的环境下跑，因此使用 _apply_up_to_0005 辅助。
    """
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        tables = _table_names(conn)

        # 三表全部存在（0005 时代）
        for tbl in ("articles_curated_ai", "clusters", "reports"):
            assert tbl in tables, f"缺少 spec v2 §6 表 {tbl}; got {tables}"

        # 四个指定索引存在（0005 时代含 idx_articles_cluster）
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?, ?, ?)",
            (
                "idx_articles_status",
                "idx_articles_published",
                "idx_articles_cluster",
                "idx_articles_score_pub",
            ),
        )
        found_indexes = {row[0] for row in cur.fetchall()}
        for idx in (
            "idx_articles_status",
            "idx_articles_published",
            "idx_articles_cluster",
            "idx_articles_score_pub",
        ):
            assert idx in found_indexes, f"缺少索引 {idx}; got {found_indexes}"

        # articles_curated_ai UNIQUE(raw_source_type, raw_external_id) 约束存在
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='articles_curated_ai'"
        )
        aca_sql = cur.fetchone()[0]
        assert "UNIQUE" in aca_sql, (
            "articles_curated_ai 缺少 UNIQUE 约束; DDL=" + aca_sql
        )

        # clusters.primary_article_id FOREIGN KEY 存在
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='clusters'"
        )
        clusters_sql = cur.fetchone()[0]
        assert "FOREIGN KEY" in clusters_sql, (
            "clusters 缺少 FOREIGN KEY 约束; DDL=" + clusters_sql
        )
    finally:
        conn.close()


# ---- 0006 lean_schema 测试 --------------------------------------------------


def _apply_up_to_0005(db_path: Path, tmp_path: Path) -> sqlite3.Connection:
    """辅助：在 db_path 上应用 0001-0005，返回打开的连接（调用方负责关闭）。"""
    migrations_dir = Path(db_module.__file__).parent / "migrations"
    tmp_migrations = tmp_path / "migrations_0005"
    tmp_migrations.mkdir(exist_ok=True)
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name <= "0005_articles_status_check.sql":
            shutil.copy(f, tmp_migrations / f.name)

    conn = get_conn(db_path)
    apply_migrations(conn, tmp_migrations)
    return conn


def _apply_0006(conn: sqlite3.Connection) -> None:
    """辅助：在已有连接上单独应用 0006 迁移文件。"""
    migrations_dir = Path(db_module.__file__).parent / "migrations"
    sql_0006 = (migrations_dir / "0006_lean_schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql_0006)
    conn.commit()


def test_migration_0006_drops_three_tables(tmp_path: Path) -> None:
    """0006 应用后 report_articles / reports / clusters 三表全部 DROP；
    只剩 articles_curated_ai + schema_migrations。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        # 0005 结束时三张关系表应存在
        tables_before = _table_names(conn)
        for tbl in ("reports", "clusters"):
            assert tbl in tables_before, f"前置条件失败：{tbl} 应在 0005 后存在"

        _apply_0006(conn)

        tables_after = _table_names(conn)
        for dropped in ("report_articles", "reports", "clusters"):
            assert dropped not in tables_after, (
                f"0006 应 DROP 表 {dropped}，但仍存在；现有表={tables_after}"
            )
        # 仅剩的两张表
        assert "articles_curated_ai" in tables_after
        assert "schema_migrations" in tables_after
    finally:
        conn.close()


def test_migration_0006_drops_cluster_columns(tmp_path: Path) -> None:
    """0006 应用后 articles_curated_ai 不含 cluster_id / is_cluster_primary / clustered_at。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        _apply_0006(conn)

        cur = conn.execute("PRAGMA table_info(articles_curated_ai)")
        columns = {row[1] for row in cur.fetchall()}
        for removed_col in ("cluster_id", "is_cluster_primary", "clustered_at"):
            assert removed_col not in columns, (
                f"0006 应删除列 {removed_col}，但仍存在；现有列={columns}"
            )
    finally:
        conn.close()


def test_migration_0006_status_check_4_states(tmp_path: Path) -> None:
    """0006 后 status CHECK 收敛为四态：pending / scored / filter_rejected / failed 允许；
    clustered / report_included 被 CHECK 拒绝。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        _apply_0006(conn)

        insert_sql = """
            INSERT INTO articles_curated_ai
                (raw_source_type, raw_external_id, title, url,
                 source_id, source_tier, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

        # 四个合法状态都能写入
        for i, status in enumerate(
            ("pending", "scored", "filter_rejected", "failed")
        ):
            conn.execute(
                insert_sql,
                (
                    "rss", f"ext-status-{i}",
                    f"Title {status}", f"https://example.com/{i}",
                    "src-test", "kol", status, "2026-05-19T00:00:00",
                ),
            )
        conn.commit()

        # clustered 被拒
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                insert_sql,
                (
                    "rss", "ext-clustered",
                    "Title clustered", "https://example.com/c",
                    "src-test", "kol", "clustered", "2026-05-19T00:00:00",
                ),
            )
            conn.commit()

        # report_included 被拒
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                insert_sql,
                (
                    "rss", "ext-report-included",
                    "Title ri", "https://example.com/ri",
                    "src-test", "kol", "report_included", "2026-05-19T00:00:00",
                ),
            )
            conn.commit()
    finally:
        conn.close()


def test_migration_0006_converts_clustered_to_scored(tmp_path: Path) -> None:
    """0006 前手插 status='clustered' 行，0006 后该行 status 应变为 'scored'。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        # 0005 允许 clustered；插入一行
        conn.execute(
            """INSERT INTO articles_curated_ai
               (raw_source_type, raw_external_id, title, url,
                source_id, source_tier, status, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "rss", "ext-was-clustered",
                "Clustered Article", "https://example.com/cls",
                "src-test", "kol", "clustered", "2026-05-19T00:00:00",
            ),
        )
        conn.commit()

        _apply_0006(conn)

        row = conn.execute(
            "SELECT status FROM articles_curated_ai WHERE raw_external_id='ext-was-clustered'"
        ).fetchone()
        assert row is not None, "目标行不见了"
        assert row[0] == "scored", (
            f"clustered 行应被 UPDATE 为 'scored'，实际 status='{row[0]}'"
        )
    finally:
        conn.close()


def test_migration_0006_preserves_indexes(tmp_path: Path) -> None:
    """0006 后存在 idx_articles_status / idx_articles_published / idx_articles_score_pub；
    idx_articles_cluster 不应存在（cluster_id 列已删）。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        _apply_0006(conn)

        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in cur.fetchall()}

        for expected in (
            "idx_articles_status",
            "idx_articles_published",
            "idx_articles_score_pub",
        ):
            assert expected in indexes, (
                f"0006 后缺少索引 {expected}；现有索引={indexes}"
            )

        assert "idx_articles_cluster" not in indexes, (
            f"idx_articles_cluster 应在 0006 后被移除，但仍存在；索引={indexes}"
        )
    finally:
        conn.close()


# ---- 0007 add_raw_body_and_enrichments 测试 --------------------------------


def test_migration_0007_adds_three_columns(tmp_path: Path) -> None:
    """0007 应用后 articles_curated_ai 含 raw_body / key_points / entities 三列（editorial-depth sprint）。"""
    db_path = tmp_path / "ai.db"
    init_db(db_path)  # 已应用全部 migrations（含 0007）
    conn = get_conn(db_path)
    try:
        cur = conn.execute("PRAGMA table_info(articles_curated_ai)")
        col_info = {row[1]: row[2] for row in cur.fetchall()}  # name -> type
        for col, expected_type in (
            ("raw_body", "TEXT"),
            ("key_points", "JSON"),
            ("entities", "JSON"),
        ):
            assert col in col_info, (
                f"0007 后缺少列 {col}；现有列={list(col_info)}"
            )
            assert col_info[col] == expected_type, (
                f"列 {col} 类型应为 {expected_type}，实际 {col_info[col]}"
            )
    finally:
        conn.close()


def test_migration_0007_writes_null_for_existing_rows(tmp_path: Path) -> None:
    """0007 前已存在的行，三新字段应为 NULL（前进式 ALTER 不回填）。"""
    db_path = tmp_path / "ai.db"
    conn = _apply_up_to_0005(db_path, tmp_path)
    try:
        # 0005 时代插入一行
        conn.execute(
            """INSERT INTO articles_curated_ai
               (raw_source_type, raw_external_id, title, url,
                source_id, source_tier, status, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "rss", "ext-pre-0007",
                "Pre-0007 Article", "https://example.com/p",
                "src-test", "kol", "scored", "2026-05-19T00:00:00",
            ),
        )
        conn.commit()

        _apply_0006(conn)
        # 单独应用 0007
        migrations_dir = Path(db_module.__file__).parent / "migrations"
        sql_0007 = (migrations_dir / "0007_add_raw_body_and_enrichments.sql").read_text(encoding="utf-8")
        conn.executescript(sql_0007)
        conn.commit()

        row = conn.execute(
            "SELECT raw_body, key_points, entities "
            "FROM articles_curated_ai WHERE raw_external_id='ext-pre-0007'"
        ).fetchone()
        assert row is not None
        assert row[0] is None, "raw_body 在 0007 前已存在的行应为 NULL"
        assert row[1] is None, "key_points 在 0007 前已存在的行应为 NULL"
        assert row[2] is None, "entities 在 0007 前已存在的行应为 NULL"
    finally:
        conn.close()
