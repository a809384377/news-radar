"""helpers/queries — 报告候选池查询（sprint lean-data-and-flow 后职责）。

新职责（精简）：
- report_candidates(db, window_start, window_end, min_score)
    返回窗口内 status='scored' 的文章候选列表，供 agent 直接取数据后用
    Write 工具写 MD；不再承担 cluster / report 写入端的查询职责。

状态机（4 态）：pending / scored / filter_rejected / failed
（clustered 态已在 migration 0006 中废除）
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from news_radar_ai.db import get_conn


# ── source_tier → 加权常量 ────────────────────────────────────────────────────
# official_first_party=10 / kol=5 / secondary=0
_TIER_WEIGHT: dict[str, int] = {
    "official_first_party": 10,
    "kol": 5,
    "secondary": 0,
}


# ── 工具 ──────────────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(zip(row.keys(), tuple(row)))


def _json_loads_or_none(value: object) -> object:
    """字符串 → json.loads；解析失败 / 非 str → None。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _parse_tags(value: object) -> list | None:
    parsed = _json_loads_or_none(value)
    if isinstance(parsed, list):
        return parsed
    return None


# ── report_candidates ─────────────────────────────────────────────────────────


def report_candidates(
    db_path: Path,
    window_start: datetime,
    window_end: datetime,
    min_score: int | None = None,
) -> list[dict]:
    """返回窗口内 status='scored' 的文章候选列表（双轨评分，sprint dual-track-scoring）。

    字段（每个 dict）：
      id, title, title_zh, url, source_id, source_tier,
      # 行业雷达轨道
      final_score, weighted_score（= final_score + tier_weight），
      # 经验价值轨道（dual-track-scoring sprint 新增）
      experience_final_score（INTEGER 或 None；历史数据 None）,
      experience_score_breakdown（dict 或 None；5 子项 cognitive_value / reusability / signal_value / completeness / source_trust）,
      track（'radar' / 'experience' / 'none'；历史数据 None → agent 按 'none' 处理）,
      # 通用
      summary_zh, content_type, tags_fine, score_reason, published_at, fetched_at,
      raw_body, key_points, entities

    排序：weighted_score 降序（兼容性默认）；agent 在 SKILL Step 5 中按 track 分组后各自按对应轨道分数再排。

    min_score：
      None  → 不做分数过滤，返回所有 scored（含 track='none' 历史数据）
      0     → 同 None（不过滤）
      N>0   → 要求 final_score >= N（仅按行业雷达分过滤；对经验轨道无意义）
    """
    conn = get_conn(db_path)
    try:
        # SQL 字符串比较；两端统一转 UTC ISO 后字典序 = 时间序（published_at 入库即 UTC ISO，
        # 见 pull.py 的 isoformat()；ai.db 实测全部 +00:00 后缀）。
        # 不转的话本地时区 ISO（如 +08:00）跟 UTC ISO 字典序不对齐，边界条目会漏。
        # 用 published_at 而非 fetched_at：窗口是用户视角的"发布时间窗口"，
        # 不是采集时间窗口；fetched_at 受 newsbox fetch 完成时刻影响会卡掉刚抓回来的高分内容。
        ws_utc = window_start.astimezone(timezone.utc).isoformat()
        we_utc = window_end.astimezone(timezone.utc).isoformat()

        _COLS = (
            "id, title, title_zh, url, source_id, source_tier, "
            "final_score, experience_final_score, experience_score_breakdown, track, "
            "summary_zh, content_type, tags_fine, "
            "score_reason, published_at, fetched_at, "
            "raw_body, key_points, entities"
        )
        if min_score is not None and min_score > 0:
            rows = conn.execute(
                f"SELECT {_COLS} "
                "FROM articles_curated_ai "
                "WHERE status = 'scored' "
                "  AND published_at >= ? AND published_at < ? "
                "  AND final_score >= ? "
                "ORDER BY final_score DESC",
                (ws_utc, we_utc, min_score),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_COLS} "
                "FROM articles_curated_ai "
                "WHERE status = 'scored' "
                "  AND published_at >= ? AND published_at < ? "
                "ORDER BY final_score DESC",
                (ws_utc, we_utc),
            ).fetchall()

        results: list[dict] = []
        for r in rows:
            tier = r["source_tier"] or ""
            final_score = r["final_score"] or 0.0
            weighted = float(final_score) + _TIER_WEIGHT.get(tier, 0)
            exp_breakdown = _json_loads_or_none(r["experience_score_breakdown"])
            results.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "title_zh": r["title_zh"],
                    "url": r["url"],
                    "source_id": r["source_id"],
                    "source_tier": tier,
                    "final_score": final_score,
                    "weighted_score": weighted,
                    "experience_final_score": r["experience_final_score"],
                    "experience_score_breakdown": exp_breakdown if isinstance(exp_breakdown, dict) else None,
                    "track": r["track"],
                    "summary_zh": r["summary_zh"],
                    "content_type": r["content_type"],
                    "tags_fine": _parse_tags(r["tags_fine"]),
                    "score_reason": r["score_reason"],
                    "published_at": r["published_at"],
                    "fetched_at": r["fetched_at"],
                    "raw_body": r["raw_body"],
                    "key_points": _parse_tags(r["key_points"]),
                    "entities": _parse_tags(r["entities"]),
                }
            )

        # 应用层按 weighted_score 再排一次（DB 只按 final_score 排）
        results.sort(key=lambda x: x["weighted_score"], reverse=True)
        return results
    finally:
        conn.close()
