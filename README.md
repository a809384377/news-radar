# News Radar · AI 信源采集与内容生产

将 **newsbox 采集层 + news-radar-ai 加工层 + 两套 Agent Skills** 放在同一仓库。
克隆或下载 ZIP 后，通过统一依赖安装和初始化即可开始使用，不依赖作者电脑上的路径、虚拟环境或数据库。

```text
RSS / Atom / 网页 / 可选 X、RSSHub
              ↓ newsbox
          原始内容 SQLite
              ↓ news-radar-ai + DeepSeek
        初筛 · 双轨评分 · 中文摘要
              ↓
     基础 Markdown 报告 + JSON 候选
              ↓ 可选 Agent Skill
       事件聚类 · 精选编辑 · 趋势洞察
```

> **真实使用条件**：首次需要联网安装 Python 依赖；实时采集需要可访问对应网站；AI 评分需要你自己的 DeepSeek API key，调用会产生费用。X 与 RSSHub 是可选项，默认不开启。仓库不包含作者的任何凭据、历史数据库或私人报告。**不是无需配置、永久免费的离线爬虫。**

## 1. 下载与安装

支持路径：macOS / Linux，Python **3.11–3.12**（推荐 3.12）、[uv](https://docs.astral.sh/uv/getting-started/installation/)。Windows 建议先用 WSL；原生 Windows 尚未验证。

```bash
git clone https://github.com/a809384377/news-radar.git
cd news-radar
uv sync --locked
```

也可在 GitHub 点 **Code → Download ZIP**，解压后进入目录执行 `uv sync --locked`。
uv 仅创建项目内 `.venv`；两个包都从仓库源码安装，其他依赖按 `uv.lock` 获取，不会修改全局 newsbox。

没有 uv 也可在已准备的 Python 3.11+ 虚拟环境内运行：

```bash
python -m pip install ./packages/newsbox ./packages/news-radar-ai
python scripts/setup.py
```

pip 方式不会使用 uv 锁文件；下文的 `uv run python` 可改为该虚拟环境的 `python`。

## 2. 先试离线演示（无需 key）

```bash
uv run python scripts/demo.py
```

生成明确标注为**虚构演示**的 Markdown，位于 `.data/demo-reports/`。这只验证报告排版，不代表实时采集或模型调用成功。

## 3. 初始化真实运行

```bash
uv run python scripts/setup.py
```

初始化脚本会：

- 创建项目内 `.data/newsbox/`、`.data/news-radar-ai/` 与 `.data/reports/`；
- 初始化数据库、默认配置和信源清单，不接触你已有的用户级安装；
- 默认只启用不依赖登录或本地 RSSHub 的直接 RSS / 网页信源；
- 在交互终端通过**隐藏输入**询问 DeepSeek key，回车可跳过；
- 不抓取、不调用 API、不启动 Docker、不覆盖已有配置或凭据。

模型默认沿用原项目 `deepseek-v4-flash`。请以你账户实际可用模型为准，可初始化时指定：

```bash
uv run python scripts/setup.py --model YOUR_AVAILABLE_MODEL
```

非交互部署使用 `--non-interactive`；通过你自己的环境或秘密管理工具提供 `DEEPSEEK_API_KEY`，进程环境优先于本地 `.env`。**不要把 key 写进仓库、shell 命令历史、Issue 或聊天消息。**

## 4. 采集并生产报告

```bash
uv run python scripts/run.py --since 24h --limit 20
```

流程：抓取 → SDK 拉取入加工库 → 最多处理 20 条待评分文章 → 输出 Markdown 和 JSON 候选。

- `--since 7d`：扩大采集与报告时间范围；没有新文章不一定是故障。
- `--source openai_news`：仅抓指定信源，适合第一次小范围尝试。
- `--skip-fetch`：不重新采集，复用已有原始数据。
- `--skip-score`：不产生新的模型调用，只导出已经评分的候选。
- `--limit`：限制本轮**待评分条数**，不是报告最终条数。已有 pending 队列也可能被本轮处理；它不是精确金额预算。
- `--data-dir` / `NEWS_RADAR_DATA_DIR`：使用另一运行目录；setup 与 run 要保持一致。

生成文件默认在 `.data/reports/`。可以直接用任意 Markdown 阅读器或 Obsidian 打开；**Obsidian 不是必需依赖**。同一分钟重复生成会自动加版本后缀，不覆盖报告。

### 两种报告模式

| 模式 | 能力 | 额外要求 |
|---|---|---|
| 独立命令行基础版 | 汇总模型摘要、双轨分数和来源链接；精确 URL 去重 | DeepSeek key |
| Agent 编辑版 | 依据 Skill 做事件聚类、精选编辑、补充阅读和趋势洞察 | 支持 Skills 的 AI 助手；其费用另计 |

基础版**不会伪称完成语义聚类、事实核查或趋势分析**；它和原有 Agent 编辑工作流明确区分。

## 5. 使用两套 Skills

已随仓库包含：

- [`skills/newsbox/`](skills/newsbox/SKILL.md)：信源维护、采集、读取、诊断；包含全部引用文档。
- [`skills/news-radar-ai/`](skills/news-radar-ai/SKILL.md)：报告编辑流程、双轨评分解释、Markdown 模板和数据库参考。

安装到当前项目的 Claude Code / Codex / Cursor：

```bash
uv run python scripts/install_skills.py --agent claude
# 或 --agent codex / --agent cursor
```

脚本遇到已有同名 Skill 会拒绝覆盖。也可用 `--target` 指定技能目录，或明确指定 `--global` 安装到个人目录。

**在仓库根目录打开 AI 助手，并让它读取 [AGENTS.md](AGENTS.md)。** 对它说：

> 使用 newsbox 和 news-radar-ai，生成过去 24 小时的 AI 资讯报告。

Skill 原文保留了独立程序的用户级默认路径。仓库运行必须使用 `.data/` 目录与 `uv run`；AGENTS.md 给出了准确的环境设置，两个 Skill 开头也有说明。

## 6. 信源与可选增强

完整目录共 **76 条定义：30 RSS、4 网页、42 X**；其中原始配置有 2 条关闭。完整目录不等于全部默认开启，也不等于当前全部可访问。

- 完整清单：[sources.seed.yaml](docs/newsbox/sources.seed.yaml)
- 自定义与日常运维：[newsbox Skill](skills/newsbox/SKILL.md)
- Python 接口：[SDK 指南](docs/newsbox/sdk-usage.md)
- X / RSSHub：[可选信源配置](docs/optional-sources.md)

先用默认直接 RSS / 网页即可；不要为了基础使用运行会启动容器的 `newsbox setup`。
X 需要自行授权的账号与 cookies；RSSHub 需要 Docker。外部网站变化、反自动化限制和地区网络状况可能导致部分信源不可用，项目无法保证 100% 抓取。

### 直接调用原生 CLI

从仓库根目录运行（自定义数据目录时相应替换）：

```bash
export NEWSBOX_HOME="$PWD/.data/newsbox"
export NEWS_RADAR_AI_HOME="$PWD/.data/news-radar-ai"
uv run newsbox sources list
uv run newsbox fetch --source openai_news --since 7d
uv run news-radar-ai pull --since 7d
uv run news-radar-ai preflight
uv run news-radar-ai report-candidates --window-start 7d --json
```

不要把完整数据库或 JSON 候选提交到 Git。它们可能包含受版权保护的文章文本；AI 评分会把待处理文章文本发送到 DeepSeek。

## 7. 项目结构

```text
packages/newsbox/       采集 CLI、SDK、适配器、迁移、配置与种子信源
packages/news-radar-ai/ 加工 CLI、模型提示词、评分逻辑与数据库迁移
skills/                两套自包含 Agent Skills
scripts/               setup / run / demo / report / install_skills / check
examples/              明确标识的虚构候选样例
tests/                 安装与跨包集成测试
docs/                  安装补充、运维参考和验证说明
uv.lock                依赖锁文件
.data/                 运行数据（自动生成，不入 Git）
```

## 8. 验证与开发

```bash
uv sync --locked
uv run python scripts/check.py
```

分别运行两包测试，再运行分发与跨包集成测试、隔离初始化、无付费导出、离线演示和 Skill 安装验证。临时 HOME 避免读取你的真实密钥或原有数据库。

GitHub Actions 配置了 macOS / Linux 与 Python 3.11 / 3.12 测试矩阵。实际验证结果与未覆盖项见 [验证说明](docs/validation.md)。

## 安全、费用与许可证

- [安全与隐私说明](SECURITY.md)
- [来源与整合范围](NOTICE.md)
- [MIT License](LICENSE)，仅涵盖项目代码；第三方依赖和采集内容各自保留原许可证或版权。
- 模型评分、摘要可能出错；发布前核对原文，遵守来源网站条款与频率限制。
