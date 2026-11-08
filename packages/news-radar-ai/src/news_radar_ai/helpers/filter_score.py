"""helpers/filter_score — 批量 DeepSeek per-article 初筛 + 评分（async 并发）。

并发模式：
- 仅 DeepSeek API 调用走 asyncio 并发（IO-bound）
- asyncio.Semaphore(concurrency) 控制并发上限
- SQLite 写操作串行（全部 API 调用完成后按 article_id 顺序写库）
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from newsbox.sdk import read_raw

from news_radar_ai.db import get_conn
from news_radar_ai.llm.deepseek import (
    AsyncDeepSeekClient,
    DeepSeekAPIError,
    DeepSeekJSONError,
)


# ---- cost 估算常量 -----------------------------------------------------------

# DeepSeek v4-flash pricing（USD per 1M tokens）；汇率 ¥7/USD（估算用，无需精确）
_PRICE_USD_PER_M_INPUT = 0.07
_PRICE_USD_PER_M_OUTPUT = 0.27
_USD_TO_YUAN = 7.0


def _estimate_cost_yuan(prompt_tokens: int, completion_tokens: int) -> float:
    usd = (prompt_tokens / 1_000_000) * _PRICE_USD_PER_M_INPUT \
        + (completion_tokens / 1_000_000) * _PRICE_USD_PER_M_OUTPUT
    return usd * _USD_TO_YUAN


# ---- 数据结构 ---------------------------------------------------------------


@dataclass
class FilterScoreStats:
    processed: int         # 实际跑了 LLM 的条数
    filter_rejected: int   # 初筛淘汰 + 低分淘汰（D4）
    scored: int            # final_score >= selection_threshold 的
    failed: int            # API / JSON 失败
    elapsed_seconds: float = 0.0
    estimated_cost_yuan: float = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_body_map(
    rows: list[sqlite3.Row],
    raw_db_path: Path | None,
) -> dict[tuple[str, str], str]:
    """调 read_raw 一次，返回 (source_type, external_id) → body map。
    since 取所有待处理行最小 fetched_at；无则取 epoch 起点。
    """
    min_fetched_at: datetime | None = None
    for row in rows:
        fa_str = row["fetched_at"]
        if fa_str:
            try:
                fa = datetime.fromisoformat(fa_str)
                if fa.tzinfo is None:
                    fa = fa.replace(tzinfo=timezone.utc)
                if min_fetched_at is None or fa < min_fetched_at:
                    min_fetched_at = fa
            except ValueError:
                pass

    if min_fetched_at is None:
        min_fetched_at = datetime.fromtimestamp(0, tz=timezone.utc)

    kwargs: dict = {"domain": "ai", "since": min_fetched_at}
    if raw_db_path is not None:
        kwargs["db_path"] = raw_db_path

    return {
        (a.source_type, a.external_id): (a.body or "")
        for a in read_raw(**kwargs)
    }


# ---- async 内核 -------------------------------------------------------------


async def _process_article(
    article_id: int,
    title: str,
    body: str,
    client: AsyncDeepSeekClient,
    semaphore: asyncio.Semaphore,
) -> tuple[int, object]:
    """单条 article 调 DeepSeek（async）；返回 (article_id, result_or_exception)。"""
    async with semaphore:
        try:
            result = await client.run_filter_score(title=title, body=body)
            return article_id, result
        except (DeepSeekAPIError, DeepSeekJSONError) as exc:
            return article_id, exc


async def _run_filter_score_async(
    *,
    rows: list[sqlite3.Row],
    body_map: dict[tuple[str, str], str],
    client: AsyncDeepSeekClient,
    source_bonus: dict[str, int],
    selection_threshold: int,
    experience_threshold: int,
    concurrency: int,
) -> tuple[list[tuple[int, object]], dict[int, sqlite3.Row]]:
    """并发调 DeepSeek；返回 (results_list, id→row_map)。"""
    semaphore = asyncio.Semaphore(concurrency)
    row_map: dict[int, sqlite3.Row] = {row["id"]: row for row in rows}

    tasks = []
    for row in rows:
        article_id: int = row["id"]
        source_type: str = row["raw_source_type"]
        external_id: str = row["raw_external_id"]
        title: str = row["title"]
        body = body_map.get((source_type, external_id))
        if body is None:
            logger.warning(
                f"article id={article_id} ({source_type}/{external_id}) "
                "在 raw.db 中未找到 body，使用空字符串继续"
            )
            body = ""
        tasks.append(_process_article(article_id, title, body, client, semaphore))

    results = await asyncio.gather(*tasks)
    return list(results), row_map


# ---- 公共入口 ---------------------------------------------------------------


def run_filter_score(
    *,
    db_path: Path,
    api_key: str,
    model: str,
    source_bonus: dict[str, int],
    selection_threshold: int,
    experience_threshold: int,
    concurrency: int = 8,
    limit: int | None = None,
    retry_failed: bool = False,
    raw_db_path: Path | None = None,
    client_factory: Callable[..., AbstractAsyncContextManager] | None = None,
) -> FilterScoreStats:
    """批量对 pending（或 failed）行跑 DeepSeek 初筛 + 评分。

    - 并发 API 调用（asyncio.Semaphore(concurrency)）
    - 串行 SQLite 写操作（全部完成后按序写库）
    - D4 双轨低分淘汰：两个轨道 final_score 都低于各自门槛 → filter_rejected
      （last_error='low_score: radar=X exp=Y'）；至少一个达标 → status='scored'，
      track 由双门槛判定（radar / experience / 高分一边）
    - source_bonus 只加行业雷达（D3），经验价值轨道不加 bonus
    - client_factory 注入用于测试；默认 None 时 helper 内部 new AsyncDeepSeekClient
    """
    t_start = time.monotonic()
    conn = get_conn(db_path)
    try:
        statuses = ("'pending'", "'failed'") if retry_failed else ("'pending'",)
        limit_clause = f" LIMIT {limit}" if limit is not None else ""
        rows = conn.execute(
            f"SELECT id, raw_source_type, raw_external_id, title, source_tier, fetched_at "
            f"FROM articles_curated_ai "
            f"WHERE status IN ({', '.join(statuses)}){limit_clause}"
        ).fetchall()

        if not rows:
            elapsed = time.monotonic() - t_start
            return FilterScoreStats(
                processed=0, filter_rejected=0, scored=0, failed=0,
                elapsed_seconds=elapsed, estimated_cost_yuan=0.0,
            )

        body_map = _build_body_map(rows, raw_db_path)

        factory = client_factory or (lambda: AsyncDeepSeekClient(api_key=api_key, model=model))

        async def _run():
            async with factory() as client:
                return await _run_filter_score_async(
                    rows=rows,
                    body_map=body_map,
                    client=client,
                    source_bonus=source_bonus,
                    selection_threshold=selection_threshold,
                    experience_threshold=experience_threshold,
                    concurrency=concurrency,
                )

        results, row_map = asyncio.run(_run())

        # ---- 串行写库 --------------------------------------------------------
        stats = FilterScoreStats(processed=0, filter_rejected=0, scored=0, failed=0)
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for article_id, result_or_exc in results:
            row = row_map[article_id]
            source_tier: str = row["source_tier"]
            stats.processed += 1

            if isinstance(result_or_exc, Exception):
                exc = result_or_exc
                logger.error(f"article id={article_id} LLM 失败: {exc}")
                conn.execute(
                    "UPDATE articles_curated_ai SET status='failed', last_error=? WHERE id=?",
                    (str(exc), article_id),
                )
                conn.commit()
                stats.failed += 1
                continue

            result = result_or_exc
            # 累计 token 用量
            usage = result.usage
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            now_iso = _now_iso()
            filter_json = json.dumps({
                "ai_related": result.filter.ai_related,
                "is_clickbait": result.filter.is_clickbait,
                "content_type_hint": result.filter.content_type_hint,
                "noise_signal": result.filter.noise_signal,
            })

            if not result.filter_passed:
                # 初筛淘汰
                conn.execute(
                    "UPDATE articles_curated_ai "
                    "SET status='filter_rejected', filter_result=?, scored_at=?, last_error=NULL "
                    "WHERE id=?",
                    (filter_json, now_iso, article_id),
                )
                conn.commit()
                stats.filter_rejected += 1
            else:
                score = result.score  # guaranteed non-None when filter_passed
                bonus = source_bonus.get(source_tier, 0)
                # 行业雷达轨道：5 维和 + source_bonus（D3）
                final_score = score.score_sum() + bonus
                # 经验价值轨道：5 维和，不加 bonus（D3）
                experience_final_score = score.experience_score_sum()

                # 双轨门槛判定 track（D1 + D4）
                radar_passes = final_score >= selection_threshold
                experience_passes = experience_final_score >= experience_threshold

                if not radar_passes and not experience_passes:
                    # 双轨都低于门槛 → 淘汰
                    conn.execute(
                        "UPDATE articles_curated_ai "
                        "SET status='filter_rejected', filter_result=?, scored_at=?, "
                        "last_error=? "
                        "WHERE id=?",
                        (
                            filter_json, now_iso,
                            f"low_score: radar={final_score} exp={experience_final_score}",
                            article_id,
                        ),
                    )
                    conn.commit()
                    stats.filter_rejected += 1
                else:
                    # 至少一个轨道达标 → scored，track 字段标识归属
                    if radar_passes and not experience_passes:
                        track = "radar"
                    elif experience_passes and not radar_passes:
                        track = "experience"
                    else:
                        # 两个都达标 → 取分数高的一边
                        track = "radar" if final_score >= experience_final_score else "experience"

                    conn.execute(
                        """UPDATE articles_curated_ai SET
                            status='scored', filter_result=?, score_breakdown=?,
                            final_score=?, experience_score_breakdown=?,
                            experience_final_score=?, track=?,
                            title_zh=?, summary_zh=?, content_type=?,
                            tags_fine=?, key_points=?, entities=?, score_reason=?,
                            scored_at=?, last_error=NULL
                        WHERE id=?""",
                        (
                            filter_json,
                            json.dumps(score.scores),
                            final_score,
                            json.dumps(score.experience_scores),
                            experience_final_score,
                            track,
                            score.title_zh, score.summary_zh, score.content_type,
                            json.dumps(score.tags_fine, ensure_ascii=False),
                            json.dumps(score.key_points, ensure_ascii=False),
                            json.dumps(score.entities, ensure_ascii=False),
                            score.score_reason,
                            now_iso, article_id,
                        ),
                    )
                    conn.commit()
                    stats.scored += 1

        elapsed = time.monotonic() - t_start
        stats.elapsed_seconds = elapsed
        stats.estimated_cost_yuan = _estimate_cost_yuan(total_prompt_tokens, total_completion_tokens)

        logger.info(
            f"[filter-score] 完成: processed={stats.processed} "
            f"filter_rejected={stats.filter_rejected} scored={stats.scored} failed={stats.failed} "
            f"elapsed={elapsed:.1f}s cost≈¥{stats.estimated_cost_yuan:.4f}"
        )
        return stats
    finally:
        conn.close()
