"""tests/test_queries — helpers/queries.report_candidates 单元测试。

覆盖：
- min_score 过滤生效（高于门槛留下、低于过滤掉）
- min_score=None 时返回所有 scored
- min_score=0 时也返回所有 scored（不过滤）
- 排序按 weighted_score 降序（含 tier_weight）
- 空窗口返回 []
- 仅返回 status='scored'（pending / filter_rejected / failed 不出现）
- weighted_score 字段计算正确（final_score + tier_weight）
- tags_fine 字段被 json.loads 成 list
- raw_body / key_points / entities 字段（editorial-depth sprint）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from news_radar_ai.db import get_conn, init_db
from news_radar_ai.helpers.queries import report_candidates


# ---- helpers ----------------------------------------------------------------


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "ai.db"
    init_db(db)
    return db


_UNSET = object()  # sentinel：区分"未传"与"显式传 None"


def _insert_article(
    conn,
    *,
    raw_external_id: str,
    title: str = "Test",
    title_zh: str = "测试",
    url: str = "https://example.com/x",
    source_id: str = "src-test",
    source_tier: str = "kol",
    status: str = "scored",
    final_score: float = 70.0,
    content_type: str | None = "model_release",
    tags_fine: list | None = None,
    summary_zh: str = "摘要",
    published_at: str = "2026-05-16T08:00:00+00:00",
    fetched_at: str = "2026-05-16T09:00:00+00:00",
    score_reason: str | None = "原因",
    scored_at: str | None = "2026-05-16T09:10:00+00:00",
    raw_body: object = _UNSET,
    key_points: object = _UNSET,
    entities: object = _UNSET,
    experience_score_breakdown: object = _UNSET,
    experience_final_score: object = _UNSET,
    track: object = _UNSET,
) -> int:
    """INSERT with explicit column list — compatible with SA1's schema (no cluster_id etc.).

    editorial-depth sprint additions（默认不传 = SQL NULL，已有用例零修改）：
    - raw_body:   TEXT；传 str 写入；传 None / 不传 → NULL
    - key_points: JSON 数组；传 list 自动 json.dumps；传 None / 不传 → NULL
    - entities:   JSON 数组；传 list 自动 json.dumps；传 None / 不传 → NULL
    """
    if tags_fine is None:
        tags_fine = ["tag1", "tag2"]

    # editorial-depth 三字段：默认不写（_UNSET）→ NULL；显式 list → json.dumps；显式 None / str 原样
    columns = [
        "raw_source_type", "raw_external_id", "title", "url", "published_at",
        "source_id", "source_tier", "status", "final_score", "title_zh",
        "summary_zh", "content_type", "tags_fine", "fetched_at",
        "score_reason", "scored_at",
    ]
    values: list[object] = [
        "rss", raw_external_id, title, url, published_at,
        source_id, source_tier, status, final_score, title_zh,
        summary_zh, content_type, json.dumps(tags_fine), fetched_at,
        score_reason, scored_at,
    ]
    if raw_body is not _UNSET:
        columns.append("raw_body")
        values.append(raw_body)
    if key_points is not _UNSET:
        columns.append("key_points")
        values.append(json.dumps(key_points) if isinstance(key_points, list) else key_points)
    if entities is not _UNSET:
        columns.append("entities")
        values.append(json.dumps(entities) if isinstance(entities, list) else entities)
    # dual-track-scoring sprint 字段：默认不写 → NULL（历史数据视角）
    if experience_score_breakdown is not _UNSET:
        columns.append("experience_score_breakdown")
        values.append(
            json.dumps(experience_score_breakdown)
            if isinstance(experience_score_breakdown, dict)
            else experience_score_breakdown
        )
    if experience_final_score is not _UNSET:
        columns.append("experience_final_score")
        values.append(experience_final_score)
    if track is not _UNSET:
        columns.append("track")
        values.append(track)

    placeholders = ", ".join("?" for _ in columns)
    cols_sql = ", ".join(columns)
    cur = conn.execute(
        f"INSERT INTO articles_curated_ai ({cols_sql}) VALUES ({placeholders})",
        tuple(values),
    )
    conn.commit()
    return cur.lastrowid


# ---- window constants -------------------------------------------------------

_WS = datetime.fromisoformat("2026-05-16T00:00:00+00:00")
_WE = datetime.fromisoformat("2026-05-17T00:00:00+00:00")


# ---- tests ------------------------------------------------------------------


def test_min_score_filters_below_threshold(tmp_path: Path) -> None:
    """min_score=65 → 低于 65 的被过滤掉，高于或等于的保留。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    hi_id = _insert_article(conn, raw_external_id="a1", final_score=80.0)
    _insert_article(conn, raw_external_id="a2", final_score=50.0)  # 低于门槛
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=65)
    ids = [r["id"] for r in result]
    assert hi_id in ids
    assert len(result) == 1
    assert result[0]["final_score"] == 80.0


def test_min_score_none_returns_all_scored(tmp_path: Path) -> None:
    """min_score=None → 返回所有 status='scored'，不过滤分数。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    _insert_article(conn, raw_external_id="b1", final_score=80.0)
    _insert_article(conn, raw_external_id="b2", final_score=30.0)
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 2


def test_min_score_zero_returns_all_scored(tmp_path: Path) -> None:
    """min_score=0 → 同 None，不过滤分数。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    _insert_article(conn, raw_external_id="c1", final_score=80.0)
    _insert_article(conn, raw_external_id="c2", final_score=10.0)
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=0)
    assert len(result) == 2


def test_sort_by_weighted_score_descending(tmp_path: Path) -> None:
    """排序按 weighted_score 降序（含 tier_weight）。

    official_first_party=10 / kol=5 / secondary=0
    a1: final=70, kol → weighted=75
    a2: final=72, secondary → weighted=72
    → a1 在前
    """
    db = _db(tmp_path)
    conn = get_conn(db)

    a1 = _insert_article(
        conn, raw_external_id="d1", final_score=70.0, source_tier="kol",
        fetched_at="2026-05-16T10:00:00+00:00",
    )
    a2 = _insert_article(
        conn, raw_external_id="d2", final_score=72.0, source_tier="secondary",
        fetched_at="2026-05-16T11:00:00+00:00",
    )
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 2
    assert result[0]["id"] == a1  # weighted=75 > 72
    assert result[0]["weighted_score"] == 75.0
    assert result[1]["id"] == a2
    assert result[1]["weighted_score"] == 72.0


def test_empty_window_returns_empty_list(tmp_path: Path) -> None:
    """窗口外无 scored 文章 → 返回空列表（窗口按 published_at 判定）。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    _insert_article(
        conn, raw_external_id="e1", final_score=80.0,
        published_at="2026-05-14T08:00:00+00:00",  # 窗口外（窗口 5/16~5/17）
        fetched_at="2026-05-14T09:00:00+00:00",
    )
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert result == []


def test_only_scored_returned(tmp_path: Path) -> None:
    """pending / filter_rejected / failed 不出现在结果中。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    scored_id = _insert_article(conn, raw_external_id="f1", status="scored", final_score=80.0)
    _insert_article(conn, raw_external_id="f2", status="pending", final_score=80.0)
    _insert_article(conn, raw_external_id="f3", status="filter_rejected", final_score=80.0)
    _insert_article(conn, raw_external_id="f4", status="failed", final_score=80.0)
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    ids = [r["id"] for r in result]
    assert ids == [scored_id]


def test_weighted_score_calculation_correct(tmp_path: Path) -> None:
    """weighted_score = final_score + tier_weight（各 tier 验证）。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    a_off = _insert_article(
        conn, raw_external_id="g1", final_score=60.0,
        source_tier="official_first_party",
        fetched_at="2026-05-16T10:00:00+00:00",
    )
    a_kol = _insert_article(
        conn, raw_external_id="g2", final_score=60.0,
        source_tier="kol",
        fetched_at="2026-05-16T11:00:00+00:00",
    )
    a_sec = _insert_article(
        conn, raw_external_id="g3", final_score=60.0,
        source_tier="secondary",
        fetched_at="2026-05-16T12:00:00+00:00",
    )
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    by_id = {r["id"]: r for r in result}

    assert by_id[a_off]["weighted_score"] == 70.0   # 60 + 10
    assert by_id[a_kol]["weighted_score"] == 65.0   # 60 + 5
    assert by_id[a_sec]["weighted_score"] == 60.0   # 60 + 0


def test_tags_fine_json_deserialized(tmp_path: Path) -> None:
    """tags_fine 字段返回 list（已 json.loads），不是 raw 字符串。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    _insert_article(conn, raw_external_id="h1", final_score=70.0,
                    tags_fine=["claude", "gemini"])
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 1
    assert isinstance(result[0]["tags_fine"], list)
    assert result[0]["tags_fine"] == ["claude", "gemini"]


# ---- editorial-depth sprint：raw_body / key_points / entities ---------------


def test_enrichment_fields_returned_and_deserialized(tmp_path: Path) -> None:
    """raw_body 原样返回；key_points / entities 被 json.loads 成 list。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    raw_body_text = "Anthropic 今日宣布 Karpathy 加入。" * 5  # ~75 字符
    _insert_article(
        conn,
        raw_external_id="i1",
        final_score=80.0,
        raw_body=raw_body_text,
        key_points=["Karpathy 加入 Anthropic", "Claude Code 团队", "长期愿景对齐"],
        entities=["Anthropic", "Karpathy", "Claude Code"],
    )
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 1
    row = result[0]

    assert row["raw_body"] == raw_body_text
    assert isinstance(row["key_points"], list)
    assert row["key_points"] == [
        "Karpathy 加入 Anthropic",
        "Claude Code 团队",
        "长期愿景对齐",
    ]
    assert isinstance(row["entities"], list)
    assert row["entities"] == ["Anthropic", "Karpathy", "Claude Code"]


def test_enrichment_fields_null_returns_none(tmp_path: Path) -> None:
    """raw_body / key_points / entities 为 NULL（fixture 不传）→ 返回 None。"""
    db = _db(tmp_path)
    conn = get_conn(db)

    _insert_article(conn, raw_external_id="j1", final_score=80.0)
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 1
    row = result[0]

    assert row["raw_body"] is None
    assert row["key_points"] is None
    assert row["entities"] is None


# ---- 双轨道字段（dual-track-scoring sprint）-----------------------------------


def test_dual_track_fields_returned(tmp_path: Path) -> None:
    """report_candidates 返回 dict 包含 experience_final_score / experience_score_breakdown / track 三字段。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert_article(
        conn,
        raw_external_id="e-dual",
        final_score=80.0,
        experience_final_score=70,
        experience_score_breakdown={
            "cognitive_value": 25, "reusability": 18, "signal_value": 12,
            "completeness": 10, "source_trust": 5,
        },
        track="radar",
    )
    conn.close()

    results = report_candidates(db, _WS, _WE)
    assert len(results) == 1
    art = results[0]

    assert "experience_final_score" in art
    assert "experience_score_breakdown" in art
    assert "track" in art

    assert art["experience_final_score"] == 70
    assert art["track"] == "radar"


def test_experience_score_breakdown_deserialized_to_dict(tmp_path: Path) -> None:
    """experience_score_breakdown 字段 JSON 反序列化为 dict。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    breakdown = {
        "cognitive_value": 33, "reusability": 5, "signal_value": 5,
        "completeness": 13, "source_trust": 10,
    }
    _insert_article(
        conn,
        raw_external_id="e-karpathy",
        final_score=62.0,
        experience_final_score=66,
        experience_score_breakdown=breakdown,
        track="experience",
    )
    conn.close()

    results = report_candidates(db, _WS, _WE)
    art = results[0]

    assert isinstance(art["experience_score_breakdown"], dict)
    assert art["experience_score_breakdown"] == breakdown
    assert art["track"] == "experience"


def test_dual_track_fields_null_for_historical_data(tmp_path: Path) -> None:
    """历史 scored 数据（dual-track-scoring sprint 前）experience_* / track 为 NULL → 返回 None（D5 兼容性）。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert_article(
        conn,
        raw_external_id="e-historical",
        final_score=78.0,
        # 不传 experience_* / track → SQL NULL（模拟历史数据）
    )
    conn.close()

    results = report_candidates(db, _WS, _WE)
    art = results[0]

    assert art["experience_final_score"] is None
    assert art["experience_score_breakdown"] is None
    assert art["track"] is None


def test_dual_track_mixed_pool(tmp_path: Path) -> None:
    """混合候选池：radar / experience / 历史 NULL 三类共存，agent 可按 track 字段分组。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert_article(
        conn,
        raw_external_id="e-radar",
        title_zh="行业雷达条目",
        final_score=85.0, experience_final_score=40,
        experience_score_breakdown={"cognitive_value": 10, "reusability": 5, "signal_value": 8, "completeness": 10, "source_trust": 7},
        track="radar",
    )
    _insert_article(
        conn,
        raw_external_id="e-exp",
        title_zh="经验好帖条目",
        final_score=60.0, experience_final_score=82,
        experience_score_breakdown={"cognitive_value": 30, "reusability": 22, "signal_value": 12, "completeness": 12, "source_trust": 6},
        track="experience",
    )
    _insert_article(
        conn,
        raw_external_id="e-hist",
        title_zh="历史数据条目",
        final_score=75.0,
        # 历史 NULL
    )
    conn.close()

    results = report_candidates(db, _WS, _WE)
    assert len(results) == 3

    by_track: dict = {}
    for art in results:
        by_track.setdefault(art["track"], []).append(art["title_zh"])

    assert by_track["radar"] == ["行业雷达条目"]
    assert by_track["experience"] == ["经验好帖条目"]
    assert by_track[None] == ["历史数据条目"]


# ---- 时区与窗口语义统一（2026-05-22 修复回归）---------------------------------


def test_window_boundary_with_non_utc_timezone(tmp_path: Path) -> None:
    """跨时区窗口边界：published_at 入库为 UTC ISO，window 给本地 +08:00 时仍能正确命中。

    重现 bug：published_at='2026-05-20T15:00:00+00:00'（UTC 15:00 = 北京 5/20 23:00），
    window_start='2026-05-20T18:00:00+08:00'（北京 5/20 18:00 = UTC 10:00）。
    语义上 UTC 15:00 > UTC 10:00 → 应命中；
    修复前 SQL 直接字符串比较：'15:00:00+00:00' < '18:00:00+08:00' → 漏。
    修复后 queries.py 把 window 端点统一 .astimezone(timezone.utc) 后再喂 SQL。
    """
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert_article(
        conn,
        raw_external_id="tz1",
        final_score=80.0,
        published_at="2026-05-20T15:00:00+00:00",  # UTC 15:00 = 北京 5/20 23:00
        fetched_at="2026-05-20T15:30:00+00:00",
    )
    conn.close()

    cst = timezone(timedelta(hours=8))
    ws_local = datetime(2026, 5, 20, 18, 0, 0, tzinfo=cst)  # 北京 5/20 18:00 = UTC 10:00
    we_local = datetime(2026, 5, 21, 18, 0, 0, tzinfo=cst)  # 北京 5/21 18:00 = UTC 5/21 10:00

    result = report_candidates(db, ws_local, we_local, min_score=None)
    assert len(result) == 1, (
        "published_at UTC 15:00（北京 23:00）应落在 [北京 5/20 18:00, 北京 5/21 18:00) 窗口内；"
        "若返回 0 条则说明字符串字典序未对齐到 UTC，时区修复回归"
    )
    assert result[0]["published_at"] == "2026-05-20T15:00:00+00:00"


def test_window_uses_published_at_not_fetched_at(tmp_path: Path) -> None:
    """窗口语义按 published_at 判定，与 fetched_at 解耦。

    2026-05-22 回归：上游 newsbox fetch 完成后落库时间（fetched_at）通常滞后于
    用户希望的"窗口尾部"几分钟。若以 fetched_at 过滤，会让"过去 24h 内刚发布、
    刚 fetch 进来"的高分内容被卡在窗口外（如宝玉的 Codex 长帖）。
    窗口必须用 published_at（发布时间）—— 用户视角的窗口。

    本测试构造的 case：published_at 在窗口内 + fetched_at 在窗口外 → 应命中。
    """
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert_article(
        conn,
        raw_external_id="pub-vs-fetch-1",
        final_score=80.0,
        published_at="2026-05-16T23:00:00+00:00",  # 窗口 [5/16 00:00, 5/17 00:00) 内
        fetched_at="2026-05-17T01:00:00+00:00",    # fetched 已超出 window_end
    )
    conn.close()

    result = report_candidates(db, _WS, _WE, min_score=None)
    assert len(result) == 1, (
        "published_at 在窗口内即应命中；若返回 0 条则说明 queries.py 又把窗口过滤"
        "退回到 fetched_at（regression 上游 fetch 完成时刻卡窗口的 bug）"
    )
    assert result[0]["published_at"] == "2026-05-16T23:00:00+00:00"
