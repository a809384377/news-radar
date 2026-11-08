-- 0004_report_articles_checks.sql — sprint v2-phase-d-multi-report-redesign（codex 评审 🟡#4）
-- 给 report_articles 加 schema 级 CHECK 约束，把"role 合法值 + parent_nullability 与 role 匹配"
-- 从应用层契约升级为物理保障。
--
-- SQLite 不支持 ALTER TABLE ADD CHECK，用 12-step rebuild：
--   1. PRAGMA foreign_keys=OFF（避免 DROP 旧表时 FK 引用错乱）
--   2. CREATE TABLE new WITH CHECKs
--   3. INSERT INTO new SELECT * FROM old
--   4. DROP TABLE old + ALTER RENAME new → old
--   5. 重建索引
--   6. PRAGMA foreign_keys=ON
-- 数据完整性：本 sprint 测试数据可丢；生产环境若已有 report_articles 行，INSERT SELECT 会复制旧行；
-- 若旧行违反新 CHECK（理论上不会，因为应用层一直严格遵守），会在 INSERT 时抛 IntegrityError。

PRAGMA foreign_keys=OFF;

CREATE TABLE report_articles_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('featured', 'related')),
    position INTEGER NOT NULL,
    parent_article_id INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, article_id),
    CHECK(
        (role = 'featured' AND parent_article_id IS NULL)
        OR (role = 'related' AND parent_article_id IS NOT NULL)
    ),
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES articles_curated_ai(id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_article_id) REFERENCES articles_curated_ai(id) ON DELETE RESTRICT
);

INSERT INTO report_articles_new SELECT * FROM report_articles;

DROP TABLE report_articles;
ALTER TABLE report_articles_new RENAME TO report_articles;

CREATE INDEX idx_report_articles_report ON report_articles(report_id);
CREATE INDEX idx_report_articles_article ON report_articles(article_id);

PRAGMA foreign_keys=ON;
