-- ai.db 占位 schema（s2-1-collector-extract Step 8 重写）
-- 设计原则：本库为消费方私有库，存 AI 加工与日报记录；
--          采集层原始数据由 newsbox.sdk.read_raw() 拉取（D2 决策），不在本库
-- S3 sprint 会扩展 articles_curated_ai 字段（filter_result / score_breakdown / cluster_id 等）

CREATE TABLE articles_curated_ai (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 关联 newsbox raw.db（~/.newsbox/raw.db）的 articles_raw（跨 db 无 FK，存 external 唯一键）
    raw_source_type TEXT NOT NULL,
    raw_external_id TEXT NOT NULL,
    -- TODO S3: filter_passed BOOLEAN / filter_result JSON / score_breakdown JSON
    --        / final_score REAL / title_zh TEXT / summary_zh TEXT / cluster_id TEXT / ...
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(raw_source_type, raw_external_id)
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                 -- daily / weekly / monthly
    date DATE NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    file_path TEXT NOT NULL,
    stats JSON,
    generated_at TIMESTAMP NOT NULL,
    UNIQUE(type, date)
);

-- 索引等到 S3 真有读模式后再加
