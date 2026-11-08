# 报告模板（agent 写 MD 时参考）

本文件是"AI 资讯雷达 Obsidian 报告"的结构骨架样本，给 agent 读完后用 Write 工具
自己写自己的 MD。**不是程序化模板** — agent 应当按当日内容性质灵活调整：
- 候选不足 7 条 → 精选取实际数量
- 无明显趋势 → 跳过"今日趋势"段
- 某 content_type 全无条目 → 跳过该子标题
- 用户对话指示用不同语气/语言 → 自由响应

字段来源：`news-radar-ai report-candidates --json` 的字段（id / title / title_zh /
url / source_id / source_tier / final_score / weighted_score /
**experience_final_score / experience_score_breakdown / track**（dual-track-scoring sprint 新增）/
summary_zh / content_type / tags_fine / score_reason / published_at / raw_body / key_points / entities）。

**双轨精选**：按 `track` 字段拆"精选 · 行业雷达"+「精选 · 经验好帖」两个 H2 区块。
- `track == 'radar'` AND `weighted_score >= 75` → 行业雷达块，按 `weighted_score` 降序
- `track == 'experience'` AND `experience_final_score >= 75` → 经验好帖块，按 `experience_final_score` 降序
- **75 分硬门槛**（`config.scoring.featured_threshold`）— 过线全部展开为精选，不挑选不凑数不限上限（聚簇压缩除外）
- 某块当日无 ≥ 75 分候选 → 整段连 H2 标题一起跳过
- 65-74 分的入库候选只在"全部条目"区出现，不进精选展开块

---

<!-- frontmatter 说明（dual-track-scoring sprint 起 selected 拆双轨）：
  report_key：文件名去掉 .md 后缀，格式 AInews-<YYYYMMDD>-<HHMM>
  window_label：用户原文描述，如"过去 24h"、"这周"
  window：可读时间范围，精确到小时
  total_collected / filter_passed / scored：从 pull + filter-score 的输出计数填
  selected_radar / selected_experience：两块各自的精选主条数；某块为 0 表示当日无该类候选
  clusters：agent 临时聚类后识别出的独立事件数，可不精确

  ⚠️ 重要：写报告时 frontmatter 必须是文件最开头的纯 YAML，仅用 --- 包裹，
  **禁止**外面再套 ```yaml ``` 代码块！下面示例用代码块仅因为本模板自身是 .md 文件
  需要避免 Obsidian 误解析。agent 实际写报告时应输出：
  ---
  report_key: AInews-...
  ...
  ---
  而不是：
  ```yaml
  ---
  ...
  ---
  ```
-->

```yaml
---
report_key: AInews-20260518-2200
window_label: 过去 24h（2026-05-17 ~ 2026-05-18）
window: 2026-05-18 01:00 ~ 2026-05-19 05:00
total_collected: 228
filter_passed: 140
scored: 140
clusters: 43
selected_radar: 5
selected_experience: 3
---
```

<!-- 主标题说明：
  格式固定：AI 资讯雷达 · <YYYY-MM-DD>
  日期取 window_end 对应的本地日期
-->

# AI 资讯雷达 · 2026-05-18

<!-- 统计窗口说明：
  列出精确的 ISO 时间范围 + 用户原文描述 + 报告生成时间
  报告生成时间 = agent 写 MD 的时刻
-->

## 统计窗口

- 范围：`2026-05-18T01:00:00+00:00` ~ `2026-05-19T05:00:00+00:00`
- 周期描述：过去 24h（2026-05-17 ~ 2026-05-18）
- 报告生成时间：2026-05-19 02:39:34

<!-- 今日趋势说明（位置：紧跟统计窗口、在精选之前）：
  可选段落。agent 在读完全部候选后，如果发现跨条的共同线索（多家公司同日宣布
  某类进展、某个叙事主题反复出现），在此写 1-4 条趋势观察。
  每条趋势 1-2 句话，用引用块（>）包裹整段，内部用序号区分。
  无明显趋势时整段跳过（连 H2 一起省略），不要写"今日无明显趋势"这类空话。
  排序约定（2026-05-22 修订）：趋势在前 → 精选在后 → 全部条目兜底；用户先看
  跨条线索的全局判断，再看具体精选展开，全部条目作为查漏补缺。
-->

## 今日趋势

> 今日趋势：
> （1）马斯克生态三线并进 — 法律层 OpenAI 诉讼败诉、产品层 Grok 视频理解、
> 宏叙事层 Samson 峰会画饼 Robotaxi。
> （2）编码代理正从 demo 走向自主部署 — SmallCode 本地 87%、InsForge 开源
> Heroku、Cursor Composer 2.5，一条完整的代理基础设施栈正在成型。

<!-- 精选说明（双轨制，dual-track-scoring sprint）：
  精选拆两块（H2），按 `track` 字段分组：

  ## 精选 · 行业雷达     ← agent 取 track=='radar' 的候选，按 weighted_score 降序
  ## 精选 · 经验好帖     ← agent 取 track=='experience' 的候选，按 experience_final_score 降序

  每个精选条目 = 一个临时簇的主条（同簇分数最高的那条）。
  子标题分数行写法不同：
    - 行业雷达：`<final_score> 分` `<source_tier 简写>-<source_id>`
    - 经验好帖：`经验 <experience_final_score> 分` `<source_tier 简写>-<source_id>`
    source_tier 简写：official_first_party → "官方"，kol → "KOL"，secondary → "二手"
  正文：summary_zh（如 summary_zh 太短或质量差，agent 可适当补写）
  链接：[原文链接](<url>) · <published_at>（本地时间，精确到分钟）
  相关报道：同一临时簇内非主条，格式 "- 相关报道：[<title_zh>](<url>)"
  关键词：纯文本一行，从 tags_fine 提取，逗号分隔（不用 #，避免污染 Obsidian tag 库）

  规则：
  - 不硬凑数。某块当日无候选 → 整段连 H2 标题一起跳过。
  - 不跨块重复（track 字段已自动归类到分数高的一边）。
  - 无相关报道时，省略"相关报道"行和"（N 条相关）"括号注。
  - 精选区块之间用 --- 分隔线。
-->

## 精选 · 行业雷达

### OpenAI 正式发布 GPT-5（3 条相关）`95 分` `官方-openai_news`

> OpenAI 今日正式发布 GPT-5，多项基准测试性能比 GPT-4 提升 30%，
> 即日起向 Plus / Team / Enterprise 用户开放，API 同步上线。

[原文链接](https://openai.com/news/gpt-5) · 2026-05-21 09:00

- 相关报道：[Sam Altman 直播 demo GPT-5 现场代码生成](https://x.com/sama/status/...)
- 相关报道：[第三方 benchmark 团队对比 GPT-5 vs Claude 4.7](https://example.com)

关键词：gpt-5, openai, model-release

---

### <另一个行业雷达主条>（1 条）`88 分` `KOL-x_dotey`

> <summary_zh 内容>

[原文链接](https://example.com) · 2026-05-21 14:30

关键词：anthropic, claude-code

---

<!-- 行业雷达块通常 3-7 条，0 条则整块跳过 -->

## 精选 · 经验好帖

### 用 Skills + Codex 组合自动给文章配图的完整工作流 `经验 82 分` `KOL-x_op7418`

> 藏师傅分享了一套用 Claude Skills + Codex 让 AI 自动给长文配图的工作流：
> 先让 Codex 解析正文识别需要插图的段落，再调 Skills 生成对应 prompt 喂给画图模型。
> 附完整 Skill 代码 + 实测案例链接。

[原文链接](https://x.com/op7418/status/...) · 2026-05-21 19:12

关键词：claude-skills, codex, workflow-automation

---

### Karpathy 谈"为什么 LLM 写代码要 small-step" `经验 79 分` `KOL-x_karpathy`

> Karpathy 长推阐述：与人类不同，LLM 在长链路代码任务里更容易在中段失焦；
> 把任务拆成"每步可验证的最小动作"+ 让模型每步暂停校验，是当前阶段最稳的协作范式。

[原文链接](https://x.com/karpathy/status/...) · 2026-05-21 11:05

关键词：llm-coding, agent-design, methodology

---

<!-- 经验好帖块通常 2-5 条，0 条则整块跳过；不硬凑数 -->

<!-- 全部条目说明：
  按 content_type 分组，列出窗口内所有 status='scored' 的文章（包括未入精选的）。
  子标题格式：### <content_type 中文名>（N 条）
  每行格式：- [<title_zh>](<url>) `<final_score>` `<source_id>`
  每个 content_type 最多 20 条。

  content_type 中文名映射（常见类型）：
    model_release → 模型发布/更新
    product_launch / product_update → 产品发布
    research_paper → 论文/研究
    industry_news → 行业新闻
    tutorial / explainer → 教程/科普
    discussion / opinion → 讨论/观点
    tool_release → 工具发布
    technical_benchmark → 技术基准
    technical_blog → 技术博客
    technical_report → 技术报告
    benchmark → benchmark
    其他 → 其他

  某个 content_type 全无条目时跳过整个子标题。
-->

## 全部条目

### 模型发布/更新（6 条）
- [Qwen 迫不及待要发布 3.7 模型](https://example.com) `66` `r_localllama`
- [<其他文章 title_zh>](<url>) `<final_score>` `<source_id>`

### 产品发布（20 条）
- [Grok 新增对视频的理解能力](https://example.com) `84` `x_elonmusk`
- [<其他文章 title_zh>](<url>) `<final_score>` `<source_id>`

### 论文/研究（5 条）
- [<title_zh>](<url>) `<final_score>` `<source_id>`

### 行业新闻（20 条）
- [<title_zh>](<url>) `<final_score>` `<source_id>`

### 教程/科普（14 条）
- [<title_zh>](<url>) `<final_score>` `<source_id>`

### 讨论/观点（20 条）
- [<title_zh>](<url>) `<final_score>` `<source_id>`

### 其他（12 条）
- [<title_zh>](<url>) `<final_score>` `<source_id>`
