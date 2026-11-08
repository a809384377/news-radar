-- 0005_articles_status_check.sql — 补 0003 的应用层契约为 schema 物理保障。
-- 0003 注释里写了 "articles.status 枚举收敛去掉 'report_included'"，但 SQL 没加 CHECK 兜底，
-- 旧库可能残留 status='report_included' 行（Phase C 时代写入或测试遗迹）。本 migration：
--   1. 把残留 'report_included' 行 UPDATE 为 'clustered'（语义等价：已加工到聚类阶段；
--      新模型下"是否被报告引用"由 report_articles 关系表回答，与 status 解耦）
--   2. 12-step rebuild 加 CHECK 约束（模板与 0004 一致）
--
-- 生产环境若 INSERT SELECT 阶段还遇到不在白名单的 status，会抛 IntegrityError 中止迁移；
-- 此时人工排查 ai.db 后再决定补哪条 UPDATE。

UPDATE articles_curated_ai SET status='clustered' WHERE status='report_included';

PRAGMA foreign_keys=OFF;

CREATE TABLE articles_curated_ai_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    raw_source_type TEXT NOT NULL,
    raw_external_id TEXT NOT NULL,

    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TIMESTAMP,
    source_id TEXT NOT NULL,
    source_tier TEXT NOT NULL,

    status TEXT NOT NULL CHECK(status IN (
        'pending', 'filter_rejected', 'scored', 'clustered', 'failed'
    )),
    last_error TEXT,

    filter_result JSON,

    score_breakdown JSON,
    final_score REAL,
    title_zh TEXT,
    summary_zh TEXT,
    content_type TEXT,
    tags_fine JSON,
    score_reason TEXT,

    cluster_id TEXT,
    is_cluster_primary BOOLEAN DEFAULT 0,

    fetched_at TIMESTAMP NOT NULL,
    curated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scored_at TIMESTAMP,
    clustered_at TIMESTAMP,

    UNIQUE(raw_source_type, raw_external_id)
);

INSERT INTO articles_curated_ai_new SELECT * FROM articles_curated_ai;

DROP TABLE articles_curated_ai;
ALTER TABLE articles_curated_ai_new RENAME TO articles_curated_ai;

CREATE INDEX idx_articles_status ON articles_curated_ai(status);
CREATE INDEX idx_articles_published ON articles_curated_ai(published_at);
CREATE INDEX idx_articles_cluster ON articles_curated_ai(cluster_id);
CREATE INDEX idx_articles_score_pub ON articles_curated_ai(final_score DESC, published_at DESC);

PRAGMA foreign_keys=ON;
