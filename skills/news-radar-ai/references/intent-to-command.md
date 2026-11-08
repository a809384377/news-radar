# 用户意图 → CLI 命令速查

agent 在对话里听到下列意图时，对应到 CLI 命令或操作。

> 报告文件名默认格式 `AInews-<YYYYMMDD>-<HHMM>.md`，时间取 `window_end` 分钟。
> 不区分日报 / 周报 / 周刊 — 统一命名约定。

## 1. 生成 AI 资讯报告

| 用户说什么 | 对应操作 |
|------------|----------|
| "/news-radar-ai 过去 24h" / "生成今天的 AI 资讯报告" | 走主流程 Step 0 → 5 |
| "summarize AI news" / "这周的 AI 资讯" | 同上，Step 1 解析对应时间窗口 |
| "since 2026-05-10" / "从昨天开始" | 同上，`window_start` 设为指定日期 |
| "再出一份补充播报" / "下午这份" | 同上，窗口取新区间；新文件名自动不同 |

完整流水线：
```
news-radar-ai pull --since <ISO>
news-radar-ai filter-score
news-radar-ai report-candidates --window-start <ISO> --window-end <ISO> --json
→ agent 思考（临时聚类 + 精选 + 趋势）→ Write 工具落 MD
```

## 2. 重做某份报告

| 用户说什么 | 对应操作 |
|------------|----------|
| "重做今天这份报告" / "把那份覆盖了重新选" | agent 重新跑 `report-candidates` + Write 工具重写 MD（同名覆盖或加 -v2） |
| "再出一份新的（不覆盖旧的）" | Write 工具写不同文件名（如 `-v2` 后缀） |

数据在 ai.db 里不动，重做 = 重读候选 + 重写文件。

## 3. 调整精选 / 摘要 / 趋势

| 用户说什么 | 对应操作 |
|------------|----------|
| "换第 N 条精选" / "把那个 XX 换上来" | agent 重写 MD（按新精选选择） |
| "再加一条" / "再加几条精选" | agent 重写 MD（精选数 +N） |
| "这条不要 / 撤掉这条" | agent 重写 MD（精选数 -1） |
| "摘要再短点 / 详细点" | agent 重写 MD（修改对应摘要段落） |
| "再写一段今日趋势" / "改一下趋势" / "删掉趋势" | agent 重写 MD（增删趋势段） |
| "重新出一遍 MD" / "再写一次" | agent 重写 MD（内容不变，重新落文件） |

所有调整 = agent 用 Write 工具直接重写 MD 文件。需要重新看数据时：
```
news-radar-ai report-candidates --window-start <ISO> --window-end <ISO> --json
```

## 4. 查 ai.db 状态

| 用户说什么 | 对应命令 |
|------------|----------|
| "今天 scored 几条" / "看 ai.db 里有什么" | `news-radar-ai inspect [--status scored] [--since 24h]` |
| "看一下候选池" / "今天哪些分够" | `news-radar-ai report-candidates --window-start <ISO> --json` |

## 5. 改配置

| 用户说什么 | 对应命令 |
|------------|----------|
| "改 Obsidian 路径" | `news-radar-ai config set report.obsidian_vault_path <路径>` |
| "调整精选门槛" | `news-radar-ai config set scoring.selection_threshold <N>` |
| "改 DeepSeek 模型" | `news-radar-ai config set model.filter_score <model-name>` |
| "看一下当前配置" | `news-radar-ai config show` |
| "第一次跑 / 初始化配置" | `news-radar-ai config init` |

## 6. 环境自检

| 用户说什么 | 对应命令 |
|------------|----------|
| "检查一下环境" / "preflight" / "为什么 X 不工作" | `news-radar-ai preflight` |
| "初始化数据库" | `news-radar-ai db init` |

---

## 不该触发本 skill 的反例

- "查一下我昨天写的笔记" → Obsidian 自身搜索，不是本 skill 职责
- "把这篇文章翻译成英文" → 通用翻译任务，不进 ai.db 流水线
- "上游 newsbox 怎么用" → newsbox 仓库自有 CLI 和文档
- "RAG 问答今天的新闻" → 全文搜索能力暂未提供
- "推送到飞书 / Telegram" → 本 skill 仅写 Obsidian MD

---

## 不要用这些旧命令（已删除）

以下命令在旧架构中存在，现已不存在，**不要调用**：

- `cluster-write` — 聚类不再持久化，agent 直接在 MD 里分组
- `cluster-candidates` — 用 `report-candidates` 替代
- `selection-candidates` — 用 `report-candidates` 替代
- `select-write` — 已删除，agent 用 Write 工具直接写 MD
- `render` — 已删除，agent 用 Write 工具直接写 MD
- `report-show` — 报告是 MD 文件，直接 Read 文件即可
- `article-show` — 用 `inspect` 或 `report-candidates` 查
- `selection-edit` — 已删除，直接重写 MD
- `summary-rewrite` — 已删除，直接重写 MD
- `trailer-edit` — 已删除，直接重写 MD
- `cluster-reset` — 已删除，聚类不持久化所以无需 reset
- `report-delete` — 已删除，MD 文件直接删除即可
- 写 SQL 查 `reports` 表 — 该表已不存在
- 写 SQL 查 `clusters` 表 — 该表已不存在
