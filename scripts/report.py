"""Deterministic report export, not a substitute for agent editorial judgment."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def text(value: object) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    for char in ("\\", "[", "]", "*", "_", "<", ">", "|"):
        value = value.replace(char, "\\" + char)
    return value


def link(title: object, url: object) -> str:
    value = str(url or "")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return text(title)
    except ValueError:
        return text(title)
    value = value.replace("\n", "").replace("\r", "").replace("<", "%3C").replace(">", "%3E")
    return f"[{text(title)}](<{value}>)"


def render_report(candidates: list[dict], *, window_start: datetime, window_end: datetime, threshold: int = 75, featured_max: int = 20, demo: bool = False) -> str:
    key = "AInews-" + window_end.strftime("%Y%m%d-%H%M")
    # Deduplicate only exact URLs. Semantic event clustering belongs to the skill/agent.
    unique = list({str(a.get("url") or a.get("id")): a for a in reversed(candidates)}.values())
    radar = sorted((a for a in unique if a.get("track") == "radar" and (a.get("weighted_score") or 0) >= threshold), key=lambda a: a.get("weighted_score") or 0, reverse=True)[:featured_max]
    experience = sorted((a for a in unique if a.get("track") == "experience" and (a.get("experience_final_score") or 0) >= threshold), key=lambda a: a.get("experience_final_score") or 0, reverse=True)[:featured_max]
    lines = ["---", f"report_key: {key}", f"window_start: {json.dumps(window_start.isoformat())}", f"window_end: {json.dumps(window_end.isoformat())}", f"candidate_count: {len(candidates)}", f"demo: {str(demo).lower()}", "generator: deterministic-export", "---", "", "# AI 资讯雷达", "", f"统计窗口：{window_start.isoformat()} → {window_end.isoformat()}", "", "> 离线演示：以下条目和评分均为虚构测试数据，不是真实新闻。" if demo else "> 自动基础版：汇总已有模型摘要与分数，不含 AI 助手的语义聚类、交叉核验和趋势判断。", ""]
    for heading, items, score_key in [("精选 · 行业雷达", radar, "weighted_score"), ("精选 · 经验好帖", experience, "experience_final_score")]:
        if not items:
            continue
        lines.extend(["## " + heading, ""])
        for article in items:
            lines.extend(["### " + link(article.get("title_zh") or article.get("title"), article.get("url")), "", f"分数：{article.get(score_key)} · 信源：{text(article.get('source_id'))}", "", text(article.get("summary_zh") or "暂无摘要"), ""])
    lines.extend(["## 全部候选", ""])
    if not candidates:
        lines.extend(["此窗口没有已通过评分的候选。请检查采集结果、发布时间窗口、模型失败记录或扩大窗口。", ""])
    for article in candidates:
        lines.append(f"- {link(article.get('title_zh') or article.get('title'), article.get('url'))} · {text(article.get('track') or 'none')} · 行业 {article.get('final_score')} / 经验 {article.get('experience_final_score')}")
    lines.extend(["", "---", "来源内容版权归原作者；模型摘要和评分可能有误，请打开原文核实。", ""])
    return "\n".join(lines)


def write_report(directory: Path, body: str, end: datetime) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = "AInews-" + end.strftime("%Y%m%d-%H%M")
    for version in range(1, 10000):
        path = directory / (stem + (f"-v{version}" if version > 1 else "") + ".md")
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(body)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("Too many same-minute report versions")
