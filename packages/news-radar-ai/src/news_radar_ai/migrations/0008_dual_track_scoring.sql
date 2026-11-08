-- 0008_dual_track_scoring.sql — sprint dual-track-scoring（2026-05-21）
-- 决策见 ai/sprints/active/dual-track-scoring/DECISIONS.md：
--   D1 双轨独立 100 分评分制（行业雷达 + 经验价值，互不挪分）
--   D3 source_bonus 只加行业雷达，不加经验价值
--   D5 历史 scored 数据不回填，新数据用新规则，历史行三字段为 NULL → track='none'
--
-- 字段语义：
--   experience_score_breakdown JSON — DeepSeek 评分阶段返回的经验价值 5 子项原始分
--                                     （cognitive_value / reusability / signal_value / completeness / source_trust）
--   experience_final_score     INTEGER — 经验价值轨道最终分（0-100），等于 5 子项加总，不含 source_bonus
--   track                      TEXT — 'radar' / 'experience' / 'none'，由 filter-score 双门槛判定写入：
--                                       行业雷达 final_score ≥ 65 且 experience_final_score < 65 → 'radar'
--                                       experience_final_score ≥ 65 且 final_score < 65 → 'experience'
--                                       两者都 ≥ 65 → 取分数更高的一方
--                                       两者都 < 65 → 'none'
--
-- 前进式 ALTER：已有 scored 行三字段为 NULL，新数据落库时填充。
-- 现有 final_score 字段含义保持不变（行业雷达分含 source_bonus）。

ALTER TABLE articles_curated_ai ADD COLUMN experience_score_breakdown JSON;
ALTER TABLE articles_curated_ai ADD COLUMN experience_final_score INTEGER;
ALTER TABLE articles_curated_ai ADD COLUMN track TEXT;
