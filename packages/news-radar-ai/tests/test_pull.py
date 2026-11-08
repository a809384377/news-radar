"""tests/test_pull — helpers/pull.pull_articles 单元测试。

用例：
1. test_pull_basic      — 3 条 mock 数据首次 pull；seen=3 / inserted=3 / skipped=0；
                          ai.db 含 3 行 status='pending'；字段映射正确。
2. test_pull_idempotent — 同 3 条跑两次；第二次 inserted=0 / skipped=3；总行数仍 3。

mock 策略：monkeypatch.setattr("news_radar_ai.helpers.pull.read_raw", ...)
           不依赖真实 ~/.newsbox/raw.db。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_radar_ai.db import get_conn, init_db
from news_radar_ai.helpers.pull import PullStats, pull_articles


# ---- fixture helpers -------------------------------------------------------


def _make_articles():
    """返回 3 条固定 ArticleRaw-like 对象（用 SimpleNamespace 代替真实 dataclass）。"""
    from types import SimpleNamespace

    base_dt = datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(1, 4):
        rows.append(
            SimpleNamespace(
                id=i,
                source_type="rss",
                source_id=f"src-rss-{i}",
                source_tier="kol",
                external_id=f"ext-id-{i}",
                url=f"https://example.com/article/{i}",
                title=f"Test Article {i}",
                body=f"Body text {i}",
                content_hash=f"hash{i}",
                published_at=datetime(2026, 5, 17, 8, i, 0, tzinfo=timezone.utc),
                fetched_at=base_dt,
                domain_tags=["ai"],
            )
        )
    return rows


def _init_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ai.db"
    init_db(db_path)
    return db_path


# ---- tests -----------------------------------------------------------------


def test_pull_basic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首次 pull 3 条：seen=3 / inserted=3 / skipped=0；字段映射正确。"""
    articles = _make_articles()
    monkeypatch.setattr(
        "news_radar_ai.helpers.pull.read_raw",
        lambda **_kwargs: iter(articles),
    )

    db_path = _init_test_db(tmp_path)
    since = datetime(2026, 5, 17, 0, 0, 0, tzinfo=timezone.utc)
    stats = pull_articles(db_path=db_path, since=since)

    assert stats == PullStats(seen=3, inserted=3, skipped=0)

    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM articles_curated_ai WHERE status = 'pending'"
        )
        assert cur.fetchone()[0] == 3, "ai.db 中应有 3 行 status='pending'"

        # 验证第 1 条字段映射（含 raw_body — editorial-depth sprint 0007 落库）
        cur = conn.execute(
            "SELECT raw_source_type, raw_external_id, title, url, "
            "source_id, source_tier, status, raw_body "
            "FROM articles_curated_ai WHERE raw_external_id = 'ext-id-1'"
        )
        row = cur.fetchone()
        assert row is not None, "ext-id-1 行应存在"
        assert row["raw_source_type"] == "rss"
        assert row["raw_external_id"] == "ext-id-1"
        assert row["title"] == "Test Article 1"
        assert row["url"] == "https://example.com/article/1"
        assert row["source_id"] == "src-rss-1"
        assert row["source_tier"] == "kol"
        assert row["status"] == "pending"
        assert row["raw_body"] == "Body text 1", (
            "raw_body 应落库为 ArticleRaw.body（editorial-depth Step 1）"
        )
    finally:
        conn.close()


def test_pull_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同 3 条跑两次；第二次 inserted=0 / skipped=3；ai.db 总行数仍 3。"""
    articles = _make_articles()
    monkeypatch.setattr(
        "news_radar_ai.helpers.pull.read_raw",
        lambda **_kwargs: iter(articles),
    )

    db_path = _init_test_db(tmp_path)
    since = datetime(2026, 5, 17, 0, 0, 0, tzinfo=timezone.utc)

    first = pull_articles(db_path=db_path, since=since)
    assert first == PullStats(seen=3, inserted=3, skipped=0)

    second = pull_articles(db_path=db_path, since=since)
    assert second == PullStats(seen=3, inserted=0, skipped=3)

    conn = get_conn(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM articles_curated_ai")
        assert cur.fetchone()[0] == 3, "重跑后总行数应仍为 3"
    finally:
        conn.close()
