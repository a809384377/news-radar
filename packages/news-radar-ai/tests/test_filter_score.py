"""tests/test_filter_score — helpers/filter_score.run_filter_score 单元测试。

mock 策略：FakeAsyncClient 实现 async context manager；read_raw monkeypatched 到 filter_score 模块。
client_factory 注入（KNOWLEDGE R-9）：测试传 fake async context manager，不依赖 monkeypatch 延迟 import。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_radar_ai.db import get_conn, init_db
from news_radar_ai.helpers.filter_score import FilterScoreStats, run_filter_score
from news_radar_ai.llm.deepseek import (
    DeepSeekAPIError,
    FilterResult,
    FilterScoreResult,
    ScoreResult,
)

# ---- shared helpers -------------------------------------------------------

_SCORES = {"value_density": 25, "significance": 20, "timeliness": 18, "credibility": 12, "readability": 8}
_SCORE_SUM = sum(_SCORES.values())  # 83
_SELECTION_THRESHOLD = 65
_EXPERIENCE_THRESHOLD = 65


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "ai.db"
    init_db(db)
    return db


def _insert(conn, *, eid: str, tier: str, idx: int) -> None:
    conn.execute(
        "INSERT INTO articles_curated_ai "
        "(raw_source_type, raw_external_id, title, url, source_id, source_tier, status, fetched_at) "
        "VALUES ('rss',?,?,?,?,?,'pending','2026-05-17T10:00:00+00:00')",
        (eid, f"Title {idx}", f"https://x/{idx}", f"src-{idx}", tier),
    )
    conn.commit()


def _raw(eids: list[str]):
    return [SimpleNamespace(source_type="rss", external_id=e, body=f"body {e}") for e in eids]


def _passed_result(final_score: int = _SCORE_SUM) -> FilterScoreResult:
    """Return a filter-passed result. Scores are scaled to make sum = final_score (before bonus)."""
    # Use standard scores but override value_density to hit the desired sum
    scores = dict(_SCORES)
    # Override to hit target sum (raw, before bonus)
    delta = final_score - sum(scores.values())
    scores = dict(scores)
    scores["value_density"] = max(0, scores["value_density"] + delta)
    return FilterScoreResult(
        filter=FilterResult(ai_related=True, is_clickbait=False, content_type_hint="model_release", noise_signal="low"),
        filter_passed=True,
        score=ScoreResult(scores=scores,
                          experience_scores={"cognitive_value": 10, "reusability": 10, "signal_value": 5, "completeness": 8, "source_trust": 7},
                          content_type="model_release", tags_fine=["t1"],
                          title_zh="中文标题", summary_zh="摘要",
                          key_points=["要点 1", "要点 2", "要点 3"],
                          entities=["Anthropic", "Claude"],
                          score_reason="reason"),
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )


def _rejected_result() -> FilterScoreResult:
    return FilterScoreResult(
        filter=FilterResult(ai_related=False, is_clickbait=True, content_type_hint="other", noise_signal="high"),
        filter_passed=False,
        usage={"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
    )


# ---- factory helpers -------------------------------------------------------


def _make_factory(client_obj):
    """Wrap a fake client object in an async context manager factory."""
    @asynccontextmanager
    async def factory():
        yield client_obj
    return factory


# ---- 用例 1：基本 pass + reject -------------------------------------------


def test_filter_score_basic_pass_and_reject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1 passed (official_first_party +5 bonus) + 1 rejected; 断言 stats/db/final_score。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-pass", tier="official_first_party", idx=1)
    _insert(conn, eid="e-rej", tier="kol", idx=2)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-pass", "e-rej"])))

    class FakeClient:
        async def run_filter_score(self, title, body):
            return _passed_result() if "1" in title else _rejected_result()

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"official_first_party": 5, "kol": 0, "secondary": -5},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.processed == 2
    assert stats.filter_rejected == 1
    assert stats.scored == 1
    assert stats.failed == 0

    conn = get_conn(db)
    row_p = conn.execute("SELECT status, final_score FROM articles_curated_ai WHERE raw_external_id='e-pass'").fetchone()
    assert row_p["status"] == "scored"
    assert row_p["final_score"] == _SCORE_SUM + 5  # 83 + 5 = 88

    row_r = conn.execute("SELECT status, final_score FROM articles_curated_ai WHERE raw_external_id='e-rej'").fetchone()
    assert row_r["status"] == "filter_rejected"
    assert row_r["final_score"] is None
    conn.close()


# ---- 用例 2：API 错误 → failed + last_error --------------------------------


def test_filter_score_failure_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ext-fail 行抛 DeepSeekAPIError → status='failed'; ext-ok 正常 scored。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-fail", tier="kol", idx=1)
    _insert(conn, eid="e-ok", tier="kol", idx=2)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-fail", "e-ok"])))

    class FakeClient:
        async def run_filter_score(self, title, body):
            if "1" in title:
                raise DeepSeekAPIError("mock HTTP 500")
            return _passed_result()

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"kol": 0},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.processed == 2
    assert stats.filter_rejected == 0
    assert stats.scored == 1
    assert stats.failed == 1

    conn = get_conn(db)
    fail_row = conn.execute("SELECT status, last_error FROM articles_curated_ai WHERE raw_external_id='e-fail'").fetchone()
    assert fail_row["status"] == "failed"
    assert "mock HTTP 500" in fail_row["last_error"]

    ok_row = conn.execute("SELECT status FROM articles_curated_ai WHERE raw_external_id='e-ok'").fetchone()
    assert ok_row["status"] == "scored"
    conn.close()


# ---- 用例 3：retry_failed 重跑 -------------------------------------------


def test_filter_score_retry_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """先跑产生 failed，retry_failed=True 再跑 → 变 scored。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-retry", tier="secondary", idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-retry"])))

    class FailClient:
        async def run_filter_score(self, title, body):
            raise DeepSeekAPIError("first-run failure")

    run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"secondary": -5},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FailClient()),
    )

    conn = get_conn(db)
    assert conn.execute("SELECT status FROM articles_curated_ai WHERE raw_external_id='e-retry'").fetchone()["status"] == "failed"
    conn.close()

    class OkClient:
        async def run_filter_score(self, title, body):
            return _passed_result()

    # monkeypatch read_raw again (iterator exhausted)
    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-retry"])))

    stats2 = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"secondary": -5},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        retry_failed=True,
        client_factory=_make_factory(OkClient()),
    )

    assert stats2.processed == 1
    assert stats2.filter_rejected == 0
    assert stats2.scored == 1
    assert stats2.failed == 0

    conn = get_conn(db)
    row = conn.execute("SELECT status, final_score, last_error FROM articles_curated_ai WHERE raw_external_id='e-retry'").fetchone()
    assert row["status"] == "scored"
    assert row["final_score"] == _SCORE_SUM + (-5)  # 83 - 5 = 78
    assert row["last_error"] is None
    conn.close()


# ---- 用例 4（新）：低分 → filter_rejected -----------------------------------


def test_low_score_routes_to_filter_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_passed=True 但 final_score < selection_threshold → filter_rejected + low_score in last_error。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-low", tier="kol", idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-low"])))

    # Build a result with score sum = 50 (below threshold of 65)
    low_scores = {"value_density": 10, "significance": 10, "timeliness": 10, "credibility": 10, "readability": 10}
    assert sum(low_scores.values()) == 50

    class FakeClient:
        async def run_filter_score(self, title, body):
            return FilterScoreResult(
                filter=FilterResult(ai_related=True, is_clickbait=False, content_type_hint="other", noise_signal="low"),
                filter_passed=True,
                score=ScoreResult(scores=low_scores,
                                  experience_scores={"cognitive_value": 5, "reusability": 0, "signal_value": 0, "completeness": 5, "source_trust": 5},
                                  content_type="other", tags_fine=[],
                                  title_zh="标题", summary_zh="摘要",
                                  key_points=[], entities=[], score_reason="low"),
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"kol": 0},
        selection_threshold=65,
        experience_threshold=65,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.processed == 1
    assert stats.filter_rejected == 1
    assert stats.scored == 0

    conn = get_conn(db)
    row = conn.execute("SELECT status, last_error FROM articles_curated_ai WHERE raw_external_id='e-low'").fetchone()
    assert row["status"] == "filter_rejected"
    assert row["last_error"] is not None
    assert "low_score" in row["last_error"]
    assert "50" in row["last_error"]  # final score value in message
    conn.close()


# ---- 用例 5（新）：高分 → scored -------------------------------------------


def test_high_score_routes_to_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_passed=True 且 final_score >= selection_threshold → status='scored'。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-high", tier="kol", idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-high"])))

    # score sum = 80 (well above threshold of 65)
    high_scores = {"value_density": 25, "significance": 20, "timeliness": 18, "credibility": 12, "readability": 5}
    assert sum(high_scores.values()) == 80

    class FakeClient:
        async def run_filter_score(self, title, body):
            return FilterScoreResult(
                filter=FilterResult(ai_related=True, is_clickbait=False, content_type_hint="model_release", noise_signal="low"),
                filter_passed=True,
                score=ScoreResult(scores=high_scores,
                                  experience_scores={"cognitive_value": 20, "reusability": 12, "signal_value": 8, "completeness": 10, "source_trust": 8},
                                  content_type="model_release", tags_fine=["t1"],
                                  title_zh="标题", summary_zh="摘要",
                                  key_points=["要点甲", "要点乙"], entities=["OpenAI"],
                                  score_reason="high"),
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"kol": 0},
        selection_threshold=65,
        experience_threshold=65,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.scored == 1
    assert stats.filter_rejected == 0

    conn = get_conn(db)
    row = conn.execute("SELECT status, final_score FROM articles_curated_ai WHERE raw_external_id='e-high'").fetchone()
    assert row["status"] == "scored"
    assert row["final_score"] == 80
    conn.close()


# ---- 用例 6（新）：elapsed + cost 字段 ------------------------------------


def test_summary_includes_elapsed_and_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stats.elapsed_seconds > 0 且 stats.estimated_cost_yuan >= 0。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-cost", tier="kol", idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-cost"])))

    class FakeClient:
        async def run_filter_score(self, title, body):
            return _passed_result()

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"kol": 0},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.elapsed_seconds > 0
    assert stats.estimated_cost_yuan >= 0


# ---- 用例 8（editorial-depth）：key_points + entities 落库 ----------------


def test_key_points_and_entities_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepSeek 返回 key_points / entities，filter-score 写库后 JSON 反序列化正确。"""
    import json as json_mod

    db = _db(tmp_path)
    conn = get_conn(db)
    _insert(conn, eid="e-enrich", tier="official_first_party", idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(["e-enrich"])))

    class FakeClient:
        async def run_filter_score(self, title, body):
            return FilterScoreResult(
                filter=FilterResult(ai_related=True, is_clickbait=False,
                                    content_type_hint="model_release", noise_signal="low"),
                filter_passed=True,
                score=ScoreResult(
                    scores=_SCORES,
                    experience_scores={"cognitive_value": 15, "reusability": 5, "signal_value": 8, "completeness": 12, "source_trust": 10},
                    content_type="model_release", tags_fine=["claude-4.7", "1m-context"],
                    title_zh="Claude 4.7 发布", summary_zh="Anthropic 发布 Claude 4.7，支持 1M 上下文。",
                    key_points=[
                        "Claude 4.7 支持 1M tokens 上下文窗口",
                        "Sonnet 4.6 同步推出 Fast mode",
                        "API 价格与上代持平",
                    ],
                    entities=["Anthropic", "Claude 4.7", "Sonnet 4.6"],
                    score_reason="重要模型发布",
                ),
                usage={"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
            )

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"official_first_party": 5},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.scored == 1

    conn = get_conn(db)
    row = conn.execute(
        "SELECT key_points, entities FROM articles_curated_ai "
        "WHERE raw_external_id='e-enrich'"
    ).fetchone()
    assert row["key_points"] is not None, "key_points 应落库"
    assert row["entities"] is not None, "entities 应落库"
    kp = json_mod.loads(row["key_points"])
    ent = json_mod.loads(row["entities"])
    assert kp == [
        "Claude 4.7 支持 1M tokens 上下文窗口",
        "Sonnet 4.6 同步推出 Fast mode",
        "API 价格与上代持平",
    ]
    assert ent == ["Anthropic", "Claude 4.7", "Sonnet 4.6"]
    conn.close()


# ---- 用例 7（新）：并发验证 ------------------------------------------------


def test_concurrency_respects_semaphore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 ≥2 个调用在时间上重叠（确认并发起效）。使用 concurrency=3 + 5 篇文章。"""
    db = _db(tmp_path)
    conn = get_conn(db)
    eids = [f"e-{i}" for i in range(5)]
    for i, eid in enumerate(eids):
        _insert(conn, eid=eid, tier="kol", idx=i)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw(eids)))

    call_times: list[float] = []

    class FakeClient:
        async def run_filter_score(self, title, body):
            call_times.append(time.monotonic())
            await asyncio.sleep(0.05)  # simulate IO latency
            return _passed_result()

    stats = run_filter_score(
        db_path=db,
        api_key="fake-key",
        model="fake-model",
        source_bonus={"kol": 0},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        concurrency=3,
        client_factory=_make_factory(FakeClient()),
    )

    assert stats.processed == 5
    assert len(call_times) == 5

    # With concurrency=3 and 5 articles each sleeping 50ms,
    # total time should be ~100ms (2 batches) rather than ~250ms (serial).
    # Verify that at least 2 calls started within 30ms of each other (overlapping).
    call_times_sorted = sorted(call_times)
    # At least the first 3 calls should start within 30ms (concurrent batch)
    overlap_found = any(
        call_times_sorted[i + 1] - call_times_sorted[i] < 0.03
        for i in range(len(call_times_sorted) - 1)
    )
    assert overlap_found, "Expected concurrent API calls but none found within 30ms overlap"


# ---- 双轨道写入测试（dual-track-scoring sprint）-------------------------------


def _dual_passed_result(radar_sum: int, exp_sum: int) -> FilterScoreResult:
    """构造双轨指定分数的 mock — radar_sum 是 5 维老评分加总（不含 source_bonus）；
    exp_sum 是经验价值 5 维加总。让首个子项承载全部分数，其余归 0（简化构造，便于精确控制总和）。"""
    radar_scores = {"value_density": radar_sum, "significance": 0, "timeliness": 0, "credibility": 0, "readability": 0}
    exp_scores = {"cognitive_value": exp_sum, "reusability": 0, "signal_value": 0, "completeness": 0, "source_trust": 0}

    return FilterScoreResult(
        filter=FilterResult(ai_related=True, is_clickbait=False, content_type_hint="other", noise_signal="low"),
        filter_passed=True,
        score=ScoreResult(
            scores=radar_scores, experience_scores=exp_scores,
            content_type="other", tags_fine=["t"],
            title_zh="标题", summary_zh="摘要",
            key_points=["要点"], entities=["实体"], score_reason="reason",
        ),
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )


def _run_with_dual_mock(db: Path, eid: str, radar_sum: int, exp_sum: int,
                         monkeypatch, tier: str = "kol") -> None:
    """Insert one article with given tier + run filter_score with dual-track mock."""
    conn = get_conn(db)
    _insert(conn, eid=eid, tier=tier, idx=1)
    conn.close()

    monkeypatch.setattr("news_radar_ai.helpers.filter_score.read_raw",
                        lambda **_: iter(_raw([eid])))

    class FakeClient:
        async def run_filter_score(self, title, body):
            return _dual_passed_result(radar_sum, exp_sum)

    run_filter_score(
        db_path=db, api_key="fake-key", model="fake-model",
        source_bonus={"official_first_party": 5, "kol": 0, "secondary": -5},
        selection_threshold=_SELECTION_THRESHOLD,
        experience_threshold=_EXPERIENCE_THRESHOLD,
        client_factory=_make_factory(FakeClient()),
    )


def test_track_radar_when_only_radar_passes(tmp_path: Path, monkeypatch) -> None:
    """雷达 ≥ 65、经验 < 65 → track='radar'。"""
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e1", radar_sum=80, exp_sum=40, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT status, track, final_score, experience_final_score FROM articles_curated_ai WHERE raw_external_id='e1'").fetchone()
    conn.close()

    assert row["status"] == "scored"
    assert row["track"] == "radar"
    assert row["final_score"] == 80  # kol tier bonus=0
    assert row["experience_final_score"] == 40


def test_track_experience_when_only_experience_passes(tmp_path: Path, monkeypatch) -> None:
    """雷达 < 65、经验 ≥ 65 → track='experience'。"""
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e2", radar_sum=50, exp_sum=80, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT status, track, final_score, experience_final_score FROM articles_curated_ai WHERE raw_external_id='e2'").fetchone()
    conn.close()

    assert row["status"] == "scored"
    assert row["track"] == "experience"
    assert row["final_score"] == 50
    assert row["experience_final_score"] == 80


def test_track_radar_when_both_pass_and_radar_higher(tmp_path: Path, monkeypatch) -> None:
    """双轨都过 + radar 更高 → track='radar'。"""
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e3", radar_sum=85, exp_sum=70, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT track, final_score, experience_final_score FROM articles_curated_ai WHERE raw_external_id='e3'").fetchone()
    conn.close()

    assert row["track"] == "radar"
    assert row["final_score"] == 85
    assert row["experience_final_score"] == 70


def test_track_experience_when_both_pass_and_experience_higher(tmp_path: Path, monkeypatch) -> None:
    """双轨都过 + experience 更高 → track='experience'。"""
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e4", radar_sum=68, exp_sum=88, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT track, final_score, experience_final_score FROM articles_curated_ai WHERE raw_external_id='e4'").fetchone()
    conn.close()

    assert row["track"] == "experience"
    assert row["final_score"] == 68
    assert row["experience_final_score"] == 88


def test_dual_low_routes_to_filter_rejected(tmp_path: Path, monkeypatch) -> None:
    """双轨都 < 65 → filter_rejected + last_error 含双轨分数。"""
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e5", radar_sum=40, exp_sum=30, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT status, track, last_error, final_score, experience_final_score FROM articles_curated_ai WHERE raw_external_id='e5'").fetchone()
    conn.close()

    assert row["status"] == "filter_rejected"
    assert row["track"] is None  # 没写 track
    assert row["final_score"] is None  # 没写双轨分（按字段语义）
    assert row["experience_final_score"] is None
    assert "low_score" in row["last_error"]
    assert "radar=40" in row["last_error"]
    assert "exp=30" in row["last_error"]


def test_source_bonus_not_added_to_experience(tmp_path: Path, monkeypatch) -> None:
    """D3 验证：source_bonus 只加 final_score（行业雷达），不加 experience_final_score。

    场景：official_first_party tier（bonus=+5），radar 5 维=60、exp 5 维=70。
    预期：final_score=65（60+5 含 bonus），experience_final_score=70（不含 bonus）。
    """
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e6", radar_sum=60, exp_sum=70,
                        monkeypatch=monkeypatch, tier="official_first_party")

    conn = get_conn(db)
    row = conn.execute("SELECT final_score, experience_final_score, track FROM articles_curated_ai WHERE raw_external_id='e6'").fetchone()
    conn.close()

    assert row["final_score"] == 65  # 60 + 5 bonus → 达到雷达门槛 65
    assert row["experience_final_score"] == 70  # 70 + 0（不加 bonus）
    # radar=65 / exp=70 双过线，exp 更高 → track='experience'
    assert row["track"] == "experience"


def test_experience_breakdown_persisted_as_json(tmp_path: Path, monkeypatch) -> None:
    """experience_score_breakdown 字段 JSON 序列化落库正确。"""
    import json as _json
    db = _db(tmp_path)
    _run_with_dual_mock(db, eid="e7", radar_sum=70, exp_sum=80, monkeypatch=monkeypatch)

    conn = get_conn(db)
    row = conn.execute("SELECT experience_score_breakdown FROM articles_curated_ai WHERE raw_external_id='e7'").fetchone()
    conn.close()

    breakdown = _json.loads(row["experience_score_breakdown"])
    assert set(breakdown.keys()) == {"cognitive_value", "reusability", "signal_value", "completeness", "source_trust"}
    assert sum(breakdown.values()) == 80
