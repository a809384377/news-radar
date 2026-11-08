"""tests/test_deepseek — DeepSeek 响应 schema 解析（dual-track-scoring sprint）。

覆盖：
- 情况 A（filter_passed=false）解析 — 不要求 score 段
- 情况 B（filter_passed=true）解析 — 要求 scores + experience_scores 双轨
- experience_scores 5 子项校验缺失 / 类型错误
- ScoreResult.experience_score_sum() 计算
"""

from __future__ import annotations

import json

import pytest

from news_radar_ai.llm.deepseek import (
    DeepSeekJSONError,
    parse_response,
)


# ---- 数据构造工具 ---------------------------------------------------------


def _filter_passed_payload() -> dict:
    return {
        "filter": {
            "ai_related": True,
            "is_clickbait": False,
            "content_type_hint": "tutorial",
            "noise_signal": "low",
        },
        "filter_passed": True,
        "score": {
            "scores": {
                "value_density": 14,
                "significance": 8,
                "timeliness": 12,
                "credibility": 12,
                "readability": 9,
            },
            "experience_scores": {
                "cognitive_value": 14,
                "reusability": 23,
                "signal_value": 6,
                "completeness": 14,
                "source_trust": 10,
            },
            "content_type": "tutorial",
            "tags_fine": ["claude-code", "prompt-engineering"],
            "title_zh": "用这个 prompt 让 Codex 自动给文章配图",
            "summary_zh": "花叔分享一个让 Codex 自动给文章生成配图的完整 prompt 模板",
            "key_points": ["完整 prompt 模板", "用 Skills 兼容性", "已实测可用"],
            "entities": ["Codex", "Claude Skills"],
            "score_reason": "行业雷达分中等，经验价值高（完整可复用 prompt）",
        },
    }


def _filter_rejected_payload() -> dict:
    return {
        "filter": {
            "ai_related": False,
            "is_clickbait": True,
            "content_type_hint": "other",
            "noise_signal": "high",
        },
        "filter_passed": False,
    }


# ---- 情况 A：filter_passed=false -----------------------------------------


def test_parse_filter_rejected_no_score_required() -> None:
    """filter_passed=false 时不要求 score 段；score 为 None。"""
    raw = json.dumps(_filter_rejected_payload())
    result = parse_response(raw)

    assert result.filter_passed is False
    assert result.score is None
    assert result.filter.ai_related is False
    assert result.filter.noise_signal == "high"


# ---- 情况 B：filter_passed=true + 双轨 --------------------------------------


def test_parse_passed_returns_dual_track_scores() -> None:
    """filter_passed=true 时返回双轨独立分数；两轨道各自加总正确。"""
    raw = json.dumps(_filter_passed_payload())
    result = parse_response(raw)

    assert result.filter_passed is True
    assert result.score is not None

    # 行业雷达轨道 5 维加总 = 14+8+12+12+9 = 55
    assert result.score.score_sum() == 55
    # 经验价值轨道 5 维加总 = 14+23+6+14+10 = 67
    assert result.score.experience_score_sum() == 67

    # 双轨字段独立存在
    assert "value_density" in result.score.scores
    assert "cognitive_value" in result.score.experience_scores
    assert result.score.scores["value_density"] == 14
    assert result.score.experience_scores["reusability"] == 23


def test_parse_passed_with_high_radar_low_experience() -> None:
    """模型发布快讯类内容：行业雷达高 / 经验价值低。"""
    payload = _filter_passed_payload()
    payload["score"]["scores"] = {
        "value_density": 25, "significance": 23, "timeliness": 19,
        "credibility": 14, "readability": 9,
    }
    payload["score"]["experience_scores"] = {
        "cognitive_value": 5, "reusability": 0, "signal_value": 5,
        "completeness": 12, "source_trust": 10,
    }
    result = parse_response(json.dumps(payload))

    assert result.score.score_sum() == 90        # 高雷达
    assert result.score.experience_score_sum() == 32  # 低经验


# ---- schema 校验：缺字段 / 类型错误 ----------------------------------------


def test_missing_experience_scores_raises() -> None:
    """score 段缺 experience_scores 字段必须抛错。"""
    payload = _filter_passed_payload()
    del payload["score"]["experience_scores"]

    with pytest.raises(DeepSeekJSONError, match="缺少字段"):
        parse_response(json.dumps(payload))


def test_experience_scores_missing_dim_raises() -> None:
    """experience_scores 子项不全（如缺 signal_value）必须抛错。"""
    payload = _filter_passed_payload()
    del payload["score"]["experience_scores"]["signal_value"]

    with pytest.raises(DeepSeekJSONError, match="experience_scores 缺少维度"):
        parse_response(json.dumps(payload))


def test_experience_scores_negative_value_raises() -> None:
    """experience_scores 子项负数必须抛错。"""
    payload = _filter_passed_payload()
    payload["score"]["experience_scores"]["cognitive_value"] = -5

    with pytest.raises(DeepSeekJSONError, match="cognitive_value"):
        parse_response(json.dumps(payload))


def test_experience_scores_not_dict_raises() -> None:
    """experience_scores 不是 dict 必须抛错。"""
    payload = _filter_passed_payload()
    payload["score"]["experience_scores"] = "should be dict"

    with pytest.raises(DeepSeekJSONError, match="experience_scores 必须是 dict"):
        parse_response(json.dumps(payload))


# ---- ScoreResult 数据类 -------------------------------------------------


def test_score_sums_independent() -> None:
    """两轨道加总互不影响（验证 D1 双轨独立设计）。"""
    raw = json.dumps(_filter_passed_payload())
    result = parse_response(raw)

    # 修改 experience_scores 应当不影响 score_sum()
    result.score.experience_scores["cognitive_value"] = 999
    assert result.score.score_sum() == 55  # 行业雷达仍为 55
    assert result.score.experience_score_sum() == 999 + 23 + 6 + 14 + 10
