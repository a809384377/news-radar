-- 0002_curation_schema.sql — spec v2 §6 落地（articles_curated_ai / clusters / reports）

-- 重建 articles_curated_ai：删旧占位表，按 §6 全字段重建
DROP TABLE IF EXISTS articles_curated_ai;

CREATE TABLE articles_curated_ai (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 上游关联键（B 选项；幂等 INSERT 用）
    raw_source_type TEXT NOT NULL,
    raw_external_id TEXT NOT NULL,

    -- B 选项"一行字"上游显示字段（report 自闭环依赖）
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TIMESTAMP,
    source_id TEXT NOT NULL,
    source_tier TEXT NOT NULL,        -- official_first_party / kol / secondary

    -- 流水线状态机
    status TEXT NOT NULL,             -- pending / filter_rejected / scored / clustered / report_included / failed
    last_error TEXT,

    -- 初筛产物（DeepSeek 给）
    filter_result JSON,               -- {ai_related, is_clickbait, content_type_hint, noise_signal}

    -- 二筛产物（DeepSeek 给）
    score_breakdown JSON,             -- {value_density, significance, timeliness, credibility, readability}
    final_score REAL,                 -- sum(scores) + source_bonus
    title_zh TEXT,
    summary_zh TEXT,
    content_type TEXT,
    tags_fine JSON,                   -- ["claude-4.7", "1M-context", ...]
    score_reason TEXT,

    -- 聚类产物（Agent 给）
    cluster_id TEXT,
    is_cluster_primary BOOLEAN DEFAULT 0,

    -- 报告归属
    last_report_key TEXT,             -- 最近一次进入哪份报告（如 'daily-2026-05-16'）

    -- 时间戳
    fetched_at TIMESTAMP NOT NULL,    -- 镜像自上游 ArticleRaw.fetched_at
    curated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 首次进本仓库
    scored_at TIMESTAMP,              -- DeepSeek 跑完时间
    clustered_at TIMESTAMP,           -- agent 聚类决策时间

    -- 表级约束（SQLite 要求约束置于所有列定义之后）
    UNIQUE(raw_source_type, raw_external_id)
);

CREATE INDEX idx_articles_status ON articles_curated_ai(status);
CREATE INDEX idx_articles_published ON articles_curated_ai(published_at);
CREATE INDEX idx_articles_cluster ON articles_curated_ai(cluster_id);
CREATE INDEX idx_articles_score_pub ON articles_curated_ai(final_score DESC, published_at DESC);

-- 重建 reports：删旧占位表，按 §6 全字段重建
DROP TABLE IF EXISTS reports;

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key TEXT UNIQUE NOT NULL,      -- e.g. 'daily-2026-05-16'
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    window_label TEXT NOT NULL,           -- 用户参数原样，e.g. '过去 24h' / '过去 2 天'
    file_path TEXT NOT NULL,
    selection JSON NOT NULL,              -- {selected_article_ids: [...], featured_clusters: [...]}
    stats JSON NOT NULL,                  -- {raw_count, filter_passed, scored, clustered, selected}
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE clusters (
    id TEXT PRIMARY KEY,                  -- agent 命名，e.g. 'evt-2026-05-claude4.7'
    cluster_title TEXT NOT NULL,          -- agent 起的事件标题
    primary_article_id INTEGER NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 1,
    earliest_published_at TIMESTAMP,
    cluster_summary TEXT,                 -- 跨簇洞察可写到这里（精选时 agent 决定写不写）
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (primary_article_id) REFERENCES articles_curated_ai(id)
);

CREATE INDEX idx_clusters_published ON clusters(earliest_published_at);
