# ai.db 字段速查

本 skill 加工产物落 `~/.news-radar-ai/ai.db`（消费方私有库；上游
`~/.newsbox/raw.db` 是只读源，**禁止**直接写 SQL）。核心表：`articles_curated_ai`。

> agent 一律走 CLI 查询出口（`report-candidates` / `inspect`），不直接读写 SQL。
> 此文档仅作字段语义参考。

## articles_curated_ai（核心加工表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `raw_source_type` | TEXT | 上游信源类型（rss / web / twikit），与 `raw_external_id` 组成 UNIQUE |
| `raw_external_id` | TEXT | 上游文章 external_id，幂等 INSERT 用 |
| `title` | TEXT | 原始标题（上游缓存字段） |
| `url` | TEXT | 文章链接（上游缓存字段） |
| `published_at` | TIMESTAMP | 发布时间（上游缓存字段） |
| `source_id` | TEXT | 信源标识（上游缓存字段） |
| `source_tier` | TEXT | 信源级别：`official_first_party` / `kol` / `secondary` |
| `status` | TEXT | 状态机（见下方状态机图） |
| `last_error` | TEXT | LLM 调用失败时记录错误片段 |
| `filter_result` | JSON | 初筛产物：`{ai_related, is_clickbait, content_type_hint, noise_signal}` |
| `score_breakdown` | JSON | 行业雷达 5 维分值：`{value_density, significance, timeliness, credibility, readability}` |
| `final_score` | REAL | 行业雷达综合分：`sum(score_breakdown) + source_bonus` |
| `experience_score_breakdown` | JSON | 经验价值 5 维分值：`{cognitive_value, reusability, signal_value, completeness, source_trust}` |
| `experience_final_score` | INTEGER | 经验价值综合分：`sum(experience_score_breakdown)`（**不含 source_bonus**） |
| `track` | TEXT | 双轨道归属：`radar` / `experience` / `none`，由 filter-score 双门槛判定写入 |
| `title_zh` | TEXT | 中文标题（DeepSeek 翻译） |
| `summary_zh` | TEXT | 中文摘要（≤500 字短 body 一句话 60-80 字；>500 字结构化 150-200 字） |
| `content_type` | TEXT | 内容类型（如 `model_release`、`product_update`、`research_paper`） |
| `tags_fine` | JSON | 细粒度标签列表（如 `["claude-4.7", "1M-context", "anthropic"]`） |
| `score_reason` | TEXT | 评分理由一句话（覆盖两个轨道的给分依据） |
| `raw_body` | TEXT | 上游 ArticleRaw.body 原文（editorial-depth 落库；给精选阶段提供原文上下文） |
| `key_points` | JSON | 3-5 个中文要点（与 summary_zh 互补不复述） |
| `entities` | JSON | 文章涉及实体（公司 / 产品 / 模型 / 人名，原文形态） |
| `fetched_at` | TIMESTAMP | 镜像自上游 `ArticleRaw.fetched_at` |
| `curated_at` | TIMESTAMP | 首次进本库时间（DEFAULT CURRENT_TIMESTAMP） |
| `scored_at` | TIMESTAMP | DeepSeek 评分完成时间 |

## 状态机（4 态）

```
pending  ──filter-score 通过（高分）──▶  scored
         ├─filter-score 通过（低分）──▶  scored
         ├─filter-score 未通过────────▶  filter_rejected
         └─filter-score LLM 失败────▶  failed
                                          │
                              filter-score --retry-failed
                                          │
                                        pending（重试）
```

- `pending`：pull 新写入，等待 filter-score 处理
- `scored`：通过初筛 + 完成评分，可进入 report-candidates 候选池
  （`final_score >= selection_threshold` 的排名靠前，低分条目也是 scored，
  只是在候选池排序靠后）
- `filter_rejected`：初筛淘汰（非 AI 相关 / 标题党 / 高噪音）
- `failed`：LLM 调用或解析失败，可用 `--retry-failed` 续跑

## 双轨评分制（dual-track-scoring sprint）

每条 scored 文章带两套独立 100 分：

**轨道 A · 行业雷达分（`final_score`，含 source_bonus）**
- 5 维：信息密度 30 / 重要性 25 / 时效性 20 / 可信度 15 / 可读性 10
- source_bonus 加在这里：`official_first_party` +5 / `kol` 0 / `secondary` -5
- 衡量"这件事对 AI 行业意味着什么"

**轨道 B · 经验价值分（`experience_final_score`，**不含** source_bonus）**
- 5 维：认知价值 35 / 可复用度 25 / 信号价值 15 / 完整度 15 / 可信度 10
- 衡量"这条内容对读者（AI 应用工作者 + 自媒体创作者）能不能带走价值"

**两道门槛**（入库 65 / 精选 75）：
- **入库门槛 65**（`config.scoring.selection_threshold` / `experience_threshold`）—
  filter-score 阶段判定：两道都低于 65 → status='filter_rejected'（不进候选池）；
  至少一道 ≥ 65 → status='scored' + `track` 字段标记归属
- **精选门槛 75**（`config.scoring.featured_threshold`）—
  SKILL Step 5 中 agent 写报告时用：精选区只放 `weighted_score ≥ 75`（radar 轨道）或
  `experience_final_score ≥ 75`（experience 轨道）；65-74 分的入库候选只在"全部条目"区出现

**track 字段判定**（写入门槛 65）：
- 行业雷达 ≥ 65 且经验价值 < 65 → `radar`
- 经验价值 ≥ 65 且行业雷达 < 65 → `experience`
- 两者都 ≥ 65 → 取分数更高的一方
- 两者都 < 65 → 不进候选池（filter_rejected）

`report-candidates --json` 输出中所有 scored（即两道至少一道过 65）的候选，
agent 在 SKILL Step 5 中再按 75 硬门槛筛精选 + 按 track 分两块展示。

> 历史 scored 数据（dual-track-scoring sprint 之前）`experience_*` 字段为 NULL，
> `track=NULL`（按 'none' 处理），仅在"全部条目"区展示。
