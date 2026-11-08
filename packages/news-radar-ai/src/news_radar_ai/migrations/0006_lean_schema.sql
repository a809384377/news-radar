-- 0006_lean_schema.sql — sprint lean-data-and-flow（2026-05-19）
-- 决策见 ai/sprints/active/lean-data-and-flow/DECISIONS.md：
--   D1 报告不持久化（删 reports + report_articles）
--   D3 聚类报告局部（删 clusters 表 + ALTER articles 删 cluster_id / is_cluster_primary）
--
-- 改动顺序：
--   1. 历史残留收敛：clustered → scored（D3 后 'clustered' 状态消失）
--   2. DROP 三张关系/事件表（顺序无关，FK 已 CASCADE）
--   3. 12-step rebuild articles_curated_ai：去掉 cluster_id / is_cluster_primary / clustered_at 列；
--      status CHECK 收敛为 pending / scored / filter_rejected / failed 四态

-- 1. 历史残留收敛
UPDATE articles_curated_ai SET status='scored' WHERE status='clustered';

PRAGMA foreign_keys=OFF;

-- 2. DROP 三表
DROP TABLE IF EXISTS report_articles;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS clusters;

-- 3. 12-step rebuild articles_curated_ai
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
        'pending', 'filter_rejected', 'scored', 'failed'
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
    fetched_at TIMESTAMP NOT NULL,
    curated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scored_at TIMESTAMP,
    UNIQUE(raw_source_type, raw_external_id)
);

-- INSERT 时 only project 保留列（不 SELECT *，因为旧表多 cluster_id / is_cluster_primary / clustered_at 三列）
INSERT INTO articles_curated_ai_new (
    id, raw_source_type, raw_external_id, title, url, published_at,
    source_id, source_tier, status, last_error, filter_result,
    score_breakdown, final_score, title_zh, summary_zh, content_type,
    tags_fine, score_reason, fetched_at, curated_at, scored_at
)
SELECT
    id, raw_source_type, raw_external_id, title, url, published_at,
    source_id, source_tier, status, last_error, filter_result,
    score_breakdown, final_score, title_zh, summary_zh, content_type,
    tags_fine, score_reason, fetched_at, curated_at, scored_at
FROM articles_curated_ai;

DROP TABLE articles_curated_ai;
ALTER TABLE articles_curated_ai_new RENAME TO articles_curated_ai;

-- 重建索引（idx_articles_cluster 不再需要，因为 cluster_id 列已删）
CREATE INDEX idx_articles_status ON articles_curated_ai(status);
CREATE INDEX idx_articles_published ON articles_curated_ai(published_at);
CREATE INDEX idx_articles_score_pub ON articles_curated_ai(final_score DESC, published_at DESC);

PRAGMA foreign_keys=ON;
