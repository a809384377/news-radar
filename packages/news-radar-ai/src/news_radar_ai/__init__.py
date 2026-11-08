"""news-radar-ai 包入口。

定位：AI 内容雷达消费方 — 读取 newsbox SDK 暴露的 raw.db，
做 AI 流水线（filter / score / cluster）+ 出 Obsidian 日报。

历史：本包前身为 news_radar，s2-1-collector-extract sprint 拆分采集层至独立仓库
news-collector 后，本包重定位并改名（2026-05-08）；上游仓库后续改名为 newsbox 并发布 PyPI。
"""

__version__ = "0.1.0"
