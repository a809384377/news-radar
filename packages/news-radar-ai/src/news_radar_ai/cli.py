"""typer CLI 入口（news-radar-ai）。

命令清单（sprint lean-data-and-flow 落地后）：
  db init         数据库初始化
  config init     配置文件管理（init / show / set）
  preflight       环境自检
  pull            newsbox.sdk → ai.db 幂等 INSERT 'pending'
  filter-score    并发 DeepSeek per-article（'pending' → 'scored' / 'filter_rejected' / 'failed'）
  report-candidates  查窗口内 'scored' 文章池（agent 拿数据后用 Write 工具直接写 MD）
  inspect         查 ai.db 状态报表（调试用）

状态机（4 态）：pending → scored / filter_rejected / failed
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import typer

from . import __version__
from . import config as config_module
from . import db as db_module
from .helpers import config_writer
from .helpers import filter_score as filter_score_helper
from .helpers import preflight as preflight_helper
from .helpers import pull as pull_helper
from .helpers import queries as queries_helper

app = typer.Typer(
    no_args_is_help=True,
    help="news-radar-ai — AI 内容雷达（消费 newsbox 采集层 + 出 Obsidian 日报）",
    add_completion=False,
)
db_app = typer.Typer(no_args_is_help=True, help="数据库初始化与维护（ai.db）")
config_app = typer.Typer(no_args_is_help=True, help="配置文件管理")
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")


def _home_option() -> Path:
    return typer.Option(
        config_module.DEFAULT_HOME,
        "--home",
        help="运行时数据目录（默认 ~/.news-radar-ai）",
        envvar="NEWS_RADAR_AI_HOME",
    )


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"news-radar-ai {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="显示版本号并退出",
    ),
) -> None:
    """根回调，仅承载全局选项。"""


# ---- 时间窗口解析 -----------------------------------------------------------


_RELATIVE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([hd])$", re.IGNORECASE)


def parse_since(value: str) -> datetime:
    """解析 --since 字符串为带时区的 datetime（本地时区）。

    支持：
    - '24h' / '2.5h' → 相对小时
    - '2d' / '7d' → 相对天
    - 'today' → 今日 00:00 本地时间
    - '2026-05-10' / '2026-05-10T08:00:00' / ISO 8601 → fromisoformat 解析，无 tz 默认本地
    """
    value = value.strip()
    now = datetime.now().astimezone()

    if value.lower() == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    m = _RELATIVE_RE.match(value)
    if m:
        n = float(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return now - delta

    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise typer.BadParameter(
            f"无法解析时间窗口 {value!r}（支持 24h / 2d / today / ISO 8601）"
        ) from e

    if dt.tzinfo is None:
        dt = dt.astimezone()  # 本地时区
    return dt


# ---- db ---------------------------------------------------------------------


@db_app.command("init")
def db_init(home: Path = _home_option()) -> None:
    """新建 ai.db 并应用所有迁移。"""
    db_path = home / "ai.db"
    db_module.init_db(db_path)
    typer.echo(f"[ok] db ready: {db_path}")


# ---- config -----------------------------------------------------------------


@config_app.command("init")
def config_init(
    home: Path = _home_option(),
    force: bool = typer.Option(False, "--force", help="覆盖已有 config.yaml"),
) -> None:
    """把默认 config.yaml 写到运行时目录。"""
    try:
        path = config_module.write_default_config(home, force=force)
    except FileExistsError as e:
        typer.echo(f"[err] {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"[ok] config initialized: {path}")


@config_app.command("show")
def config_show(home: Path = _home_option()) -> None:
    """加载并显示当前配置（密钥已脱敏）。"""
    import json

    cfg = config_module.load_config(home)
    body = cfg.model_dump(exclude={"secrets"})
    typer.echo(json.dumps(body, indent=2, ensure_ascii=False))
    typer.echo(f"secrets: {cfg.secrets!r}")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="字段 key，支持点号嵌套，如 'report.obsidian_vault_path'"),
    value: str = typer.Argument(..., help="新值（自动识别 bool / int / float / null / str）"),
    home: Path = _home_option(),
) -> None:
    """更新 ~/.news-radar-ai/config.yaml 单字段（保留注释/键序/缩进）。"""
    try:
        result = config_writer.set_config_value(home=home, key_path=key, value=value)
    except FileNotFoundError as e:
        typer.echo(f"[err] {e}（先跑 `news-radar-ai config init`）", err=True)
        raise typer.Exit(code=1) from e
    except KeyError as e:
        typer.echo(f"[err] {e}", err=True)
        raise typer.Exit(code=1) from e
    except ValueError as e:
        typer.echo(f"[err] 配置校验失败：{e}（已恢复原文件）", err=True)
        raise typer.Exit(code=1) from e

    action = "created" if result.created else "updated"
    typer.echo(
        f"[config set] {result.key_path}: {result.old_value!r} → {result.new_value!r} ({action})"
    )


# ---- preflight --------------------------------------------------------------


@app.command("preflight")
def preflight_cmd(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 而非人类可读文本"),
    home: Path = _home_option(),
) -> None:
    """环境自检：检查 newsbox / DEEPSEEK key / obsidian / ai.db / 上游 raw.db。"""
    report = preflight_helper.run_preflight(home=home)

    if json_output:
        body = {
            "overall": report.overall,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail, "fix_hint": c.fix_hint}
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(body, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"[preflight] overall={report.overall}")
        for c in report.checks:
            typer.echo(f"  {c.name:24} {c.status:6} {c.detail}")
            if c.fix_hint:
                typer.echo(f"    → {c.fix_hint}")

    if report.overall == "error":
        raise typer.Exit(code=1)


# ---- 流水线 pull / filter-score / inspect -----------------------------------


@app.command("pull")
def pull_cmd(
    since: str = typer.Option(
        ...,
        "--since",
        help="时间窗口（24h / 2d / today / ISO 8601）",
    ),
    source_type: list[str] = typer.Option(
        None,
        "--source-type",
        help="限定 source_type（rss / web / twikit；可多次传递）",
    ),
    limit: int = typer.Option(None, "--limit", help="最多拉 N 条 raw 行"),
    home: Path = _home_option(),
) -> None:
    """从 newsbox SDK 拉新内容到 ai.db（status='pending'）。"""
    since_dt = parse_since(since)
    cfg = config_module.load_config(home)
    db_path = home / "ai.db"
    db_module.init_db(db_path)  # KNOWLEDGE-LOG #26 入口幂等

    raw_db_path = Path(cfg.upstream.newsbox_raw_db_path).expanduser()

    stats = pull_helper.pull_articles(
        db_path=db_path,
        since=since_dt,
        source_types=source_type or None,
        limit=limit,
        raw_db_path=raw_db_path,
    )
    typer.echo(
        f"[pull] seen={stats.seen} inserted={stats.inserted} skipped={stats.skipped} "
        f"since={since_dt.isoformat()}"
    )


@app.command("filter-score")
def filter_score_cmd(
    limit: int = typer.Option(None, "--limit", help="最多处理 N 条"),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="同时处理 status='failed' 的行"
    ),
    home: Path = _home_option(),
) -> None:
    """并发 DeepSeek per-article 初筛 + 评分（pending → scored / filter_rejected / failed）。"""
    cfg = config_module.load_config(home)
    db_path = home / "ai.db"
    db_module.init_db(db_path)  # KNOWLEDGE-LOG #26 入口幂等

    api_key = cfg.secrets.deepseek_api_key
    if not api_key:
        typer.echo(
            "[err] DEEPSEEK_API_KEY 未配置，请在 ~/.news-radar-ai/.env 设置",
            err=True,
        )
        raise typer.Exit(code=1)

    raw_db_path = Path(cfg.upstream.newsbox_raw_db_path).expanduser()

    stats = filter_score_helper.run_filter_score(
        db_path=db_path,
        api_key=api_key,
        model=cfg.llm.model,
        source_bonus=cfg.scoring.source_bonus,
        selection_threshold=cfg.scoring.selection_threshold,
        experience_threshold=cfg.scoring.experience_threshold,
        concurrency=cfg.llm.concurrency,
        limit=limit,
        retry_failed=retry_failed,
        raw_db_path=raw_db_path,
    )
    typer.echo(
        f"[filter-score] processed={stats.processed} "
        f"scored={stats.scored} filter_rejected={stats.filter_rejected} "
        f"failed={stats.failed} elapsed={stats.elapsed_seconds:.1f}s "
        f"cost=¥{stats.estimated_cost_yuan:.4f}"
    )


# ---- 查询命令 ---------------------------------------------------------------


def _parse_window(window_start: str, window_end: str) -> tuple[datetime, datetime]:
    """统一窗口解析；window_end='now' → 当前本地时间。"""
    start_dt = parse_since(window_start)
    end_dt = (
        datetime.now().astimezone()
        if window_end.lower() == "now"
        else parse_since(window_end)
    )
    return start_dt, end_dt


def _emit_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


@app.command("report-candidates")
def report_candidates_cmd(
    window_start: str = typer.Option(..., "--window-start", help="窗口起：24h / 2d / today / ISO 8601"),
    window_end: str = typer.Option("now", "--window-end", help="窗口止：'now' / ISO 8601；默认 now"),
    min_score: int = typer.Option(
        None,
        "--min-score",
        help=(
            "（仅按行业雷达 final_score 过滤）双轨制下不推荐使用 — "
            "filter-score 已在写库阶段做过双轨低分淘汰（双轨都 < 门槛 → filter_rejected），"
            "status='scored' 本身就是合格池。仅在需要"
            "只看高 radar 分"
            "时显式传。"
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 而非人类可读文本"),
    home: Path = _home_option(),
) -> None:
    """查窗口内 status='scored' 的文章候选池（agent 拿这份数据后直接写 MD）。

    双轨制下：status='scored' = 至少一个轨道（行业雷达 / 经验价值）达门槛；
    agent 拿到候选后按 track 字段拆"精选 · 行业雷达"+ "精选 · 经验好帖"两块。
    """
    cfg = config_module.load_config(home)
    db_path = home / "ai.db"
    db_module.init_db(db_path)  # KNOWLEDGE R-6 入口幂等

    start_dt, end_dt = _parse_window(window_start, window_end)
    # min_score 默认 None → 不过滤；filter-score 阶段已把"双轨都低"的淘汰为 filter_rejected
    threshold = min_score  # None 时 report_candidates 不做 final_score 过滤

    candidates = queries_helper.report_candidates(
        db_path=db_path,
        window_start=start_dt,
        window_end=end_dt,
        min_score=threshold,
    )

    if json_output:
        _emit_json(candidates)
        return

    typer.echo(
        f"[report-candidates] window={start_dt.isoformat()} ~ {end_dt.isoformat()} "
        f"min_score={threshold} found={len(candidates)}"
    )
    if not candidates:
        typer.echo("  （无候选 — 该窗口无 scored 文章或都低于门槛）")
        return
    for art in candidates[:30]:
        title = (art.get("title_zh") or art.get("title") or "")[:60]
        exp = art.get("experience_final_score")
        exp_str = f"exp={exp}" if exp is not None else "exp=-"
        track = art.get("track") or "-"
        typer.echo(
            f"  [{art['id']}] radar={art['final_score']} {exp_str} track={track} "
            f"{art['source_tier']}/{art['source_id']} | {title}"
        )
    if len(candidates) > 30:
        typer.echo(f"  …还有 {len(candidates) - 30} 条（用 --json 看全部）")


# ---- inspect ----------------------------------------------------------------


@app.command("inspect")
def inspect_cmd(
    status: str = typer.Option(None, "--status", help="过滤 status 字段"),
    since: str = typer.Option(None, "--since", help="过滤 fetched_at >= since"),
    home: Path = _home_option(),
) -> None:
    """查 ai.db 状态报表（调试用）。"""
    db_path = home / "ai.db"
    db_module.init_db(db_path)  # KNOWLEDGE-LOG #26

    where_parts: list[str] = []
    params: list[object] = []
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if since:
        since_dt = parse_since(since)
        where_parts.append("fetched_at >= ?")
        params.append(since_dt.isoformat())
    where = " WHERE " + " AND ".join(where_parts) if where_parts else ""

    conn = db_module.get_conn(db_path)
    try:
        cur = conn.execute(
            f"SELECT status, COUNT(*) FROM articles_curated_ai{where} GROUP BY status",
            params,
        )
        counts = list(cur.fetchall())
        typer.echo("状态计数：")
        if not counts:
            typer.echo("  （无匹配行）")
        for st, cnt in counts:
            typer.echo(f"  {st}: {cnt}")

        cur = conn.execute(
            f"SELECT id, status, title, source_id, source_tier, final_score, fetched_at "
            f"FROM articles_curated_ai{where} ORDER BY fetched_at DESC LIMIT 5",
            params,
        )
        rows = cur.fetchall()
        if rows:
            typer.echo("\n最近 5 条：")
            for row in rows:
                title_preview = (row["title"] or "")[:60]
                fs = row["final_score"]
                typer.echo(
                    f"  [{row['id']}] {row['status']:18} | "
                    f"{row['source_tier']}/{row['source_id']} | "
                    f"final_score={fs} | {title_preview}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
