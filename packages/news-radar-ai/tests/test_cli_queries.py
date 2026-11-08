"""tests/test_cli_queries — report-candidates CLI 端到端测试。

覆盖：
- --json 输出格式（list of dict + 关键字段存在）
- min_score 参数过滤生效
- 空窗口情况（返回 0 条 + 人类可读提示）
- 人类可读输出（不带 --json）
- min_score=0 不过滤
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from news_radar_ai.cli import app
from news_radar_ai.db import get_conn, init_db


runner = CliRunner()


# ---- helpers ----------------------------------------------------------------


def _init_home(tmp_path: Path) -> Path:
    """最小可用 home：init_db。"""
    init_db(tmp_path / "ai.db")
    return tmp_path


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
) -> int:
    """INSERT with explicit column list — no cluster_id / is_cluster_primary / clustered_at."""
    if tags_fine is None:
        tags_fine = ["tag1", "tag2"]
    cur = conn.execute(
        """INSERT INTO articles_curated_ai
           (raw_source_type, raw_external_id, title, url, published_at, source_id,
            source_tier, status, final_score, title_zh, summary_zh, content_type,
            tags_fine, fetched_at, score_reason, scored_at)
           VALUES ('rss',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            raw_external_id, title, url, published_at, source_id,
            source_tier, status, final_score, title_zh, summary_zh,
            content_type, json.dumps(tags_fine), fetched_at,
            score_reason, scored_at,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture()
def home_with_scored_articles(tmp_path: Path) -> Path:
    """3 条 scored 文章：90/75/55 — 用于门槛测试。"""
    home = _init_home(tmp_path)
    conn = get_conn(home / "ai.db")
    _insert_article(conn, raw_external_id="h", final_score=90.0,
                    source_tier="official_first_party",  # weighted=100
                    fetched_at="2026-05-16T10:00:00+00:00")
    _insert_article(conn, raw_external_id="m", final_score=75.0,
                    source_tier="kol",  # weighted=80
                    fetched_at="2026-05-16T11:00:00+00:00")
    _insert_article(conn, raw_external_id="l", final_score=55.0,
                    source_tier="secondary",  # weighted=55
                    fetched_at="2026-05-16T12:00:00+00:00")
    conn.close()
    return home


@pytest.fixture()
def home_empty(tmp_path: Path) -> Path:
    """空 DB — 无任何文章。"""
    return _init_home(tmp_path)


# ---- report-candidates --json -----------------------------------------------


def test_report_candidates_json_smoke(home_with_scored_articles: Path) -> None:
    """--json → exit 0 + JSON list + 关键字段存在。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--json",
            "--home", str(home_with_scored_articles),
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 3
    for item in data:
        assert "id" in item
        assert "final_score" in item
        assert "weighted_score" in item
        assert "source_tier" in item
        assert "title_zh" in item


def test_report_candidates_json_sorted_by_weighted(home_with_scored_articles: Path) -> None:
    """--json → 按 weighted_score 降序（100 > 80 > 55）。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--json",
            "--home", str(home_with_scored_articles),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    scores = [item["weighted_score"] for item in data]
    assert scores == sorted(scores, reverse=True)
    assert data[0]["weighted_score"] == 100.0  # 90 + 10


def test_report_candidates_min_score_filters(home_with_scored_articles: Path) -> None:
    """--min-score 65 → 只返回 final_score >= 65 的（55 分那条被过滤掉）。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "65",
            "--json",
            "--home", str(home_with_scored_articles),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 2
    assert all(item["final_score"] >= 65.0 for item in data)


def test_report_candidates_min_score_zero_no_filter(home_with_scored_articles: Path) -> None:
    """--min-score 0 → 不过滤，返回全部 scored。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--json",
            "--home", str(home_with_scored_articles),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 3


def test_report_candidates_empty_window(home_empty: Path) -> None:
    """空 DB → exit 0 + JSON 是空 list。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--json",
            "--home", str(home_empty),
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    data = json.loads(result.stdout)
    assert data == []


def test_report_candidates_human_readable_output(home_with_scored_articles: Path) -> None:
    """不带 --json → 人类可读文本，含 [report-candidates] 前缀 + found 计数。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--home", str(home_with_scored_articles),
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    out = result.stdout
    assert "[report-candidates]" in out
    assert "found=3" in out
    # 不是合法 JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_report_candidates_human_readable_empty(home_empty: Path) -> None:
    """空结果人类可读输出：含'无候选'提示。"""
    result = runner.invoke(
        app,
        [
            "report-candidates",
            "--window-start", "2026-05-16T00:00:00+00:00",
            "--window-end", "2026-05-17T00:00:00+00:00",
            "--min-score", "0",
            "--home", str(home_empty),
        ],
    )
    assert result.exit_code == 0
    assert "无候选" in result.stdout
