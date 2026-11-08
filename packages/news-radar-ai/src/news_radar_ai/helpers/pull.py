"""helpers/pull — newsbox SDK → ai.db 幂等 INSERT 'pending'（spec v2 §3 Step 3）。

字段映射：
    raw_source_type ← ArticleRaw.source_type
    raw_external_id ← ArticleRaw.external_id
    title           ← ArticleRaw.title
    url             ← ArticleRaw.url
    published_at    ← ArticleRaw.published_at（None → NULL）
    source_id       ← ArticleRaw.source_id
    source_tier     ← ArticleRaw.source_tier
    raw_body        ← ArticleRaw.body（editorial-depth sprint：让 agent 拿到原文上下文）
    fetched_at      ← ArticleRaw.fetched_at
    status          = 'pending'（硬编码）
    curated_at      ← DEFAULT CURRENT_TIMESTAMP（不显式传）

幂等策略：INSERT OR IGNORE + UNIQUE(raw_source_type, raw_external_id)。
统计：sqlite3.connection.total_changes 前后差值 = inserted；seen - inserted = skipped。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from newsbox.sdk import read_raw

from news_radar_ai.db import get_conn


@dataclass
class PullStats:
    seen: int      # SDK 读出的 raw 行数
    inserted: int  # 新插入 articles_curated_ai 的行数
    skipped: int   # UNIQUE 冲突跳过的行数


_INSERT_SQL = """
INSERT OR IGNORE INTO articles_curated_ai
    (raw_source_type, raw_external_id,
     title, url, published_at,
     source_id, source_tier,
     raw_body,
     fetched_at, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
"""


def pull_articles(
    *,
    db_path: Path,
    since: datetime,
    source_types: list[str] | None = None,
    limit: int | None = None,
    domain: str = "ai",
    raw_db_path: Path | None = None,
) -> PullStats:
    """从 newsbox SDK 拉数据，幂等 INSERT 到 articles_curated_ai。

    Args:
        db_path:       ai.db 路径（本仓库消费方数据库）。
        since:         只拉 fetched_at >= since 的行。
        source_types:  可选信源类型过滤，None = 不过滤。
        limit:         可选最大条数，None = 不限。
        domain:        newsbox domain 参数，默认 "ai"。
        raw_db_path:   上游 raw.db 路径（None = newsbox SDK 默认）。

    Returns:
        PullStats(seen, inserted, skipped)
    """
    conn = get_conn(db_path)
    try:
        seen = 0
        changes_before = conn.total_changes

        for article in read_raw(
            domain=domain,
            since=since,
            source_types=source_types,
            limit=limit,
            db_path=raw_db_path,
        ):
            seen += 1
            pub_at = (
                article.published_at.isoformat()
                if article.published_at is not None
                else None
            )
            conn.execute(
                _INSERT_SQL,
                (
                    article.source_type,
                    article.external_id,
                    article.title,
                    article.url,
                    pub_at,
                    article.source_id,
                    article.source_tier,
                    article.body,
                    article.fetched_at.isoformat(),
                ),
            )

        conn.commit()
        inserted = conn.total_changes - changes_before
        skipped = seen - inserted
        return PullStats(seen=seen, inserted=inserted, skipped=skipped)
    finally:
        conn.close()
