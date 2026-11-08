-- 0003_multi_report.sql — sprint v2-phase-d-multi-report-redesign（2026-05-17）
-- 改造目标：
--   1. 新建 report_articles 关系表 — 报告与文章之间多对多
--   2. articles_curated_ai 移除 last_report_key 列（不再用单值字段表达"我属于哪份报告"）
--   3. reports 加 note 字段（用户对单份报告的自由备注）
--
-- 应用层契约（不在 schema 中体现）：
--   articles.status 枚举收敛 — 去掉 'report_included'，最高停在 'clustered'。
--   该状态以前用来阻止"已发表"文章被复用；新模型下，被引用 = report_articles 里有行，
--   与 article 本身的加工进度（status）解耦。

-- ─ 1. 新建 report_articles ──────────────────────────────────────────────
CREATE TABLE report_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    role TEXT NOT NULL,                  -- 'featured' / 'related'
    position INTEGER NOT NULL,           -- featured: 精选列表中的位次；related: 在 parent 的相关报道列表中的位次（都从 0 开始）
    parent_article_id INTEGER,           -- 仅 role='related' 使用，指向同一报告中作为 featured 的某篇文章；featured 此字段为 NULL
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, article_id),       -- 同一报告同一文章只允许出现一次（4 道闸之一的物理保障）
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES articles_curated_ai(id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_article_id) REFERENCES articles_curated_ai(id) ON DELETE RESTRICT
);

CREATE INDEX idx_report_articles_report ON report_articles(report_id);
CREATE INDEX idx_report_articles_article ON report_articles(article_id);

-- ─ 2. articles_curated_ai DROP last_report_key（SQLite 3.35+） ─────────
ALTER TABLE articles_curated_ai DROP COLUMN last_report_key;

-- ─ 3. reports 加 note ───────────────────────────────────────────────────
ALTER TABLE reports ADD COLUMN note TEXT;
