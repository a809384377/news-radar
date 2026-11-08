-- 0007_add_raw_body_and_enrichments.sql — sprint editorial-depth（2026-05-20）
-- 决策见 ai/sprints/active/editorial-depth/DECISIONS.md D3：
--   ai.db 新增三字段并行落地（raw_body + key_points + entities），不分 sprint。
--
-- 字段语义：
--   raw_body   TEXT  — 上游 newsbox ArticleRaw.body 直接落库；给精选阶段提供原文上下文，
--                       agent 写"编辑视角总结"时基于此而非 summary_zh 拼接
--   key_points JSON  — DeepSeek 评分阶段返回的 3-5 个要点（JSON 数组）；让 agent 拿到结构化要点
--   entities   JSON  — DeepSeek 返回的实体列表（公司 / 产品 / 人名等，JSON 数组）；本 sprint 不消费，
--                       留给后续 sprint 做跨阵营对照 / 因果链等关联时启用
--
-- 前进式 ALTER：已有行三字段为 NULL，pull / filter-score 新行落库时填充；不回填历史。

ALTER TABLE articles_curated_ai ADD COLUMN raw_body TEXT;
ALTER TABLE articles_curated_ai ADD COLUMN key_points JSON;
ALTER TABLE articles_curated_ai ADD COLUMN entities JSON;
