"""DeepSeek client（spec v2 §4.1 / §12.2 / §12.3）。

职责：
- per-article 调一次 DeepSeek 完成合并 prompt（初筛 4 问 + 通过则二筛 5 维评分 + 摘要 + 翻译 + tags）
- JSON schema 校验 + parse 失败重试 1 次
- HTTP 429/5xx 指数退避（1/2/4/8s 最多 4 次）
- 客户端节流（默认 5 req/s）

设计原则：
- 与 config.py 解耦：api_key / model 通过构造参数传入，caller 自行从 config.secrets 取
- 输入限制：title + body 前 3000 字（spec §4.1）；prompt 内分两段提示初筛与二筛的可见范围
- 输出 JSON：情况 A（filter_passed=false）/ 情况 B（filter_passed=true + score 子对象）
- source_bonus 不在 prompt 内 — caller 拿到 score 后加 bonus 算 final_score

API endpoint: https://api.deepseek.com/chat/completions（OpenAI-compatible）
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"  # DECISIONS D1
DEFAULT_TIMEOUT = 60.0  # 单次请求总超时
DEFAULT_RPS = 5.0  # spec §12.2
RETRY_BACKOFF_SECONDS = [1.0, 2.0, 4.0, 8.0]  # spec §12.2 指数退避
JSON_RETRY_LIMIT = 1  # spec §7.1 / §12.3

# 输入裁剪（prompt 调优层硬编码，不开放配置；初筛 1000 在 SYSTEM_PROMPT 文案中提示）
SCORE_INPUT_MAX_CHARS = 3000  # build_user_prompt 实际裁剪上限


# ---- 异常 -------------------------------------------------------------------


class DeepSeekError(Exception):
    """DeepSeek client 基类异常。"""


class DeepSeekAPIError(DeepSeekError):
    """HTTP 失败（已耗尽重试）或非 2xx 响应。"""


class DeepSeekJSONError(DeepSeekError):
    """JSON parse / schema 校验失败（已耗尽重试）。"""


# ---- 结果数据结构 -----------------------------------------------------------


@dataclass
class FilterResult:
    """初筛 4 问结果（spec §4.1）。"""
    ai_related: bool
    is_clickbait: bool
    content_type_hint: str
    noise_signal: str  # "low" | "medium" | "high"


@dataclass
class ScoreResult:
    """二筛结果（双轨独立 100 分制）。

    - scores: 行业雷达 5 维（value_density / significance / timeliness / credibility / readability），满分 100
    - experience_scores: 经验价值 5 维（cognitive_value / reusability / signal_value / completeness / source_trust），满分 100
    两轨道互不挪分，由 caller 各自加 source_bonus（D3：bonus 只加行业雷达）后得 final_score。
    """
    scores: dict[str, int]
    experience_scores: dict[str, int]
    content_type: str
    tags_fine: list[str]
    title_zh: str
    summary_zh: str
    key_points: list[str]
    entities: list[str]
    score_reason: str

    def score_sum(self) -> int:
        """行业雷达 5 维加总（不含 source_bonus）。"""
        return sum(self.scores.values())

    def experience_score_sum(self) -> int:
        """经验价值 5 维加总（不含 source_bonus；D3 source_bonus 不加到经验价值轨道）。"""
        return sum(self.experience_scores.values())


@dataclass
class FilterScoreResult:
    """合并 prompt 的完整返回。

    filter 永远有值；score 仅当 filter_passed=True 时有值。
    raw_response 保留原始 JSON dict，供调试与 last_error 记录。
    usage 保存 API 用量（prompt_tokens / completion_tokens / total_tokens）。
    """
    filter: FilterResult
    filter_passed: bool
    score: ScoreResult | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)  # prompt_tokens / completion_tokens / total_tokens


# ---- prompt 构造 ------------------------------------------------------------


SYSTEM_PROMPT = """你是 AI 资讯主编助手。给定一条 AI 资讯（含 title 与 body），按两步处理并仅返回严格的 JSON 对象。

【读者画像】（决定"对谁有价值"，评分时要带入）
读者是 AI 应用工作者 + AI 自媒体创作者，深度使用 Claude Code / Codex / Cursor 等编程 agent 工具，做个人项目，输出 AI 自媒体内容。他关注 4 类内容：
(a) Claude Code / Codex / Cursor / Skills / Agent 工具的实操技巧、工作流、配置 —— 工作可直接套用
(b) 值得在个人项目里折腾的开源工具 / 新框架 / 新思路 —— 个人项目素材
(c) 行业深度判断、KOL 思考、有传播价值的洞见 —— 自媒体选题灵感
(d) AI 落地到具体行业 / 岗位 / 场景的新机会和新方法（不限科技行业；包括行业改造案例、岗位工作流重塑、垂直 AI 落地、新职业、AI native 团队的工作方式、个体工作者用 AI 形成的新生产关系）；关键判断：内容讲的是"具体的事 + 具体的方法 / 机会"，而不是泛谈"AI 会改变世界"的空话

【步骤 1 · 初筛 4 问】
基于 title + body 前 1000 字判断：
- ai_related (bool): 是否与 AI / 机器学习 / 大语言模型 / AI 应用 / AI 行业直接相关
- is_clickbait (bool): 标题是否明显标题党、夸大、无信息量
- content_type_hint (str): 候选类型（model_release / product_launch / research_paper / opinion / tutorial / industry_news / other）
- noise_signal ("low"|"medium"|"high"): 广告 / 人事变动 / 与 AI 无关的财报等噪声判定

【保留规则】ai_related=true AND is_clickbait=false AND noise_signal!="high" 才进入步骤 2，否则停在步骤 1。

【步骤 2 · 二筛（双轨独立评分，仅当步骤 1 保留）】

本步骤给出两套独立 100 分评分（互不挪分），覆盖两个不同视角：
- 行业雷达分：这件事对 AI 行业意味着什么、是不是行业层面值得关注的事
- 经验价值分：这条内容对上述读者画像而言，能不能带走某种价值（认知 / 复用 / 信号）

两轨道独立判断 —— 同一篇文章可能行业雷达高但经验价值低（如纯模型发布快讯），也可能经验价值高但行业雷达低（如 KOL 实操技巧贴 / 方法论思考）。

【评分共同原则】
1. 按"内容浓度"评分，不按字数 —— 100 字讲清一个完整 prompt = 高分；2000 字泛泛而谈 = 低分
2. 满分各 100；每个子项分值上限即满分天花板
3. score_reason 字段一句话说明两个轨道分别给分的核心依据

【轨道 A · 行业雷达 5 维评分（满分 100）】
- scores.value_density (0-30): 信息密度（事实 / 数据 / 技术细节浓度）
- scores.significance (0-25): 重要性（对 AI 行业 / 技术发展的影响范围）
- scores.timeliness (0-20): 时效性（事件新鲜度 + 时效窗口紧迫度）
- scores.credibility (0-15): 可信度（来源权威 + 事实可核实）
- scores.readability (0-10): 可读性（行文流畅 + 结构清晰）

【轨道 B · 经验价值 5 维评分（满分 100，对上述读者画像）】
- experience_scores.cognitive_value (0-35): 认知价值 —— 读者看完后对某个领域的理解是否更深、判断框架是否更清晰、视角是否更开阔。高分场景：Karpathy 谈"为什么 LLM 写代码要 small-step"、行业深度解读长文、KOL 把多次实战提炼为可迁移判断（如"半年用 Claude Code 的 5 条经验"）；低分场景：纯新闻报道、缺乏视角的事件复述
- experience_scores.reusability (0-25): 可复用度 —— 读者能不能直接照搬走（prompt 模板 / 配置 / 工作流 / 脚本 / 步骤）。高分场景：完整 prompt + 使用示例、3 个 Skill 串起来的工作流截图 + 命令；低分场景：只描述"某人做了什么"但没给具体方法、纯抽象方法论
- experience_scores.signal_value (0-15): 信号价值 —— 是否指明了一个值得关注的新人 / 项目 / 工具 / 资源。**关键：质量看附带的认知信息量** —— 纯转发"这项目挺好玩 [链接]"≤6 分；"它用 X 思路解决 Y、和 Z 项目比区别在 W、适合 V 场景"才算高质量种草，给 12+ 分
- experience_scores.completeness (0-15): 完整度 —— 内容讲透了 vs 标题党 / 半句话点到为止。高：步骤完整 / 论证闭环 / 给出 demo 或链接；低：只抛结论不给依据 / 戛然而止
- experience_scores.source_trust (0-10): 可信度（这里指经验本身的可信度，与行业雷达 credibility 不同侧重）—— 分享者是不是真懂 / 真用过这件事。KOL 实操经验 + 自己截图 / demo = 满；纯搬运 / 二手转述 / 看上去没动手 = 低

【summary_zh 写作要求】

这段摘要是给读者的"读完就够"版本 — 读者读完这段，不点开链接，也能知道这条值得他带走的关键内容。让读者带走具体的：数据 / 机制 / 对比 / 可复用方法 / 信号事实，而不是"某公司发布了某事"这种空提要。你是 AI 主编播报员，不是新闻通讯社。

写作原则：

1. 信息密度优先，长度自适应：
   · 文章只够讲清"X 发布 Y"且无更多事实 → 60-80 字一句话即可
   · 文章信息量大（具体数据 / 技术机制 / 对比 / 方法步骤 / 作者论点）→ 200-400 字展开都行
   · **凑字数比写少更糟** — 没料就短，有料就充分展开，反对填充

2. 客观为主，少加主观倾向：
   · 删掉"重要的"、"值得关注"、"令人兴奋"、"标志性"、"具有里程碑意义"这类形容词
   · 不替读者下"这意味着什么"的判断 — 把判断材料给他，让他自己想
   · 例外：当文章作者本人给出明确判断、且这个判断本身就是新闻点（如"Karpathy 认为 RL 会回归主流"），转述他的判断并标明是谁说的

3. 按文章本身的内容性质自然展开，不是套模板：
   · 文章里有什么就讲什么；信息缺失的维度直接跳过，不要硬补
   · 下面是常见类型可能值得展开的角度，仅作参考，不是必填项：
     - 模型 / 产品发布：关键能力点、与前代 / 竞品的具体数字对比、价格、可用范围
     - 技术研究：解决的老问题、核心思路一句话、实验数字、是否开源
     - 商业事件（融资 / 收购 / 裁员）：关键数字、参与方、文章里提到的行业上下文（自己不要瞎补）
     - 观点 / 方法论：作者核心论点 2-3 条、关键论据、与共识不同之处
     - 教程 / 经验贴：适合谁、具体步骤或方法、工具栈
   · 写之前先问自己：这条读者读完能"带走"什么具体的东西？写不出"带走点"就退回去看文章再读一遍

4. 拒绝填充：
   · 不要"这是一篇关于 X 的文章"、"详情请见原文"这类元叙述
   · 不要把标题换种说法重复一遍当摘要
   · 不要"图见原文 / 详见图片"这种依赖多模态的指引 — 文字版本要自完整

5. 输出形式：
   · 纯文本，无 markdown 标记（不加 ** / # / - / 1. 这些字符在输出里）
   · 不分段、不加空行（报告里会被引用块包裹，分段会割裂渲染）
   · 数字、版本号、专有名词、人名保留原文形态（如 "1M token"、"Claude 4.7"、"Andrej Karpathy"）

【其他字段】
- content_type (str): 最终内容类型（覆盖 step 1 hint）
- tags_fine (list[str]): 2-5 个细粒度标签（小写，连字符分词，如 "claude-4.7", "1m-context", "anthropic"）
- title_zh (str): 中文标题（精准翻译，不译为标题党）
- summary_zh (str): 中文摘要（按上方【summary_zh 写作要求】段执行）
- key_points (list[str]): 3-5 个中文要点；每条独立一句话，覆盖文章关键事实 / 数据 / 结论；与 summary_zh 互补不复述
- entities (list[str]): 文章涉及的实体（公司 / 产品 / 模型 / 人名），3-10 个；保留原文形态如 "Anthropic", "Claude 4.7", "Andrej Karpathy"，无需翻译
- score_reason (str): 一句话同时说明两个轨道的给分依据（如"模型发布事件，行业雷达高但实操价值低"或"KOL 实操贴，附带可直接复用的 prompt，经验价值满"）

【输出 JSON schema】
情况 A（步骤 1 未保留）：
{"filter": {"ai_related": ..., "is_clickbait": ..., "content_type_hint": "...", "noise_signal": "..."}, "filter_passed": false}

情况 B（步骤 1 保留 + 步骤 2 完成）：
{
  "filter": {...},
  "filter_passed": true,
  "score": {
    "scores": {"value_density": ..., "significance": ..., "timeliness": ..., "credibility": ..., "readability": ...},
    "experience_scores": {"cognitive_value": ..., "reusability": ..., "signal_value": ..., "completeness": ..., "source_trust": ...},
    "content_type": "...",
    "tags_fine": [...],
    "title_zh": "...",
    "summary_zh": "...",
    "key_points": [...],
    "entities": [...],
    "score_reason": "..."
  }
}

严格按上述 schema 输出；不要 markdown 包裹；不要额外解释文本。"""


def build_user_prompt(title: str, body: str) -> str:
    """构造单条 user prompt。body 已裁剪到 SCORE_INPUT_MAX_CHARS。"""
    body_trimmed = (body or "")[:SCORE_INPUT_MAX_CHARS]
    return f"""title: {title}

body (前 {len(body_trimmed)} 字):
{body_trimmed}"""


# ---- JSON 校验 --------------------------------------------------------------


_REQUIRED_FILTER_KEYS = {"ai_related", "is_clickbait", "content_type_hint", "noise_signal"}
_REQUIRED_SCORE_KEYS = {
    "scores", "experience_scores", "content_type", "tags_fine", "title_zh", "summary_zh",
    "key_points", "entities", "score_reason",
}
_REQUIRED_SCORE_DIM = {"value_density", "significance", "timeliness", "credibility", "readability"}
_REQUIRED_EXPERIENCE_DIM = {
    "cognitive_value", "reusability", "signal_value", "completeness", "source_trust",
}


def _validate_filter(filter_obj: Any) -> FilterResult:
    if not isinstance(filter_obj, dict):
        raise DeepSeekJSONError(f"filter 不是 dict: {type(filter_obj)}")
    missing = _REQUIRED_FILTER_KEYS - filter_obj.keys()
    if missing:
        raise DeepSeekJSONError(f"filter 缺少字段: {missing}")
    if not isinstance(filter_obj["ai_related"], bool):
        raise DeepSeekJSONError("filter.ai_related 必须是 bool")
    if not isinstance(filter_obj["is_clickbait"], bool):
        raise DeepSeekJSONError("filter.is_clickbait 必须是 bool")
    noise = filter_obj["noise_signal"]
    if noise not in {"low", "medium", "high"}:
        raise DeepSeekJSONError(f"filter.noise_signal 非法值: {noise!r}")
    return FilterResult(
        ai_related=bool(filter_obj["ai_related"]),
        is_clickbait=bool(filter_obj["is_clickbait"]),
        content_type_hint=str(filter_obj["content_type_hint"]),
        noise_signal=str(noise),
    )


def _validate_score(score_obj: Any) -> ScoreResult:
    if not isinstance(score_obj, dict):
        raise DeepSeekJSONError(f"score 不是 dict: {type(score_obj)}")
    missing = _REQUIRED_SCORE_KEYS - score_obj.keys()
    if missing:
        raise DeepSeekJSONError(f"score 缺少字段: {missing}")
    scores = score_obj["scores"]
    if not isinstance(scores, dict):
        raise DeepSeekJSONError("score.scores 必须是 dict")
    dim_missing = _REQUIRED_SCORE_DIM - scores.keys()
    if dim_missing:
        raise DeepSeekJSONError(f"score.scores 缺少维度: {dim_missing}")
    for dim in _REQUIRED_SCORE_DIM:
        v = scores[dim]
        if not isinstance(v, (int, float)) or v < 0:
            raise DeepSeekJSONError(f"score.scores.{dim} 非法值: {v!r}")
    experience_scores = score_obj["experience_scores"]
    if not isinstance(experience_scores, dict):
        raise DeepSeekJSONError("score.experience_scores 必须是 dict")
    exp_missing = _REQUIRED_EXPERIENCE_DIM - experience_scores.keys()
    if exp_missing:
        raise DeepSeekJSONError(f"score.experience_scores 缺少维度: {exp_missing}")
    for dim in _REQUIRED_EXPERIENCE_DIM:
        v = experience_scores[dim]
        if not isinstance(v, (int, float)) or v < 0:
            raise DeepSeekJSONError(f"score.experience_scores.{dim} 非法值: {v!r}")
    tags = score_obj["tags_fine"]
    if not isinstance(tags, list):
        raise DeepSeekJSONError("score.tags_fine 必须是 list")
    key_points = score_obj["key_points"]
    if not isinstance(key_points, list):
        raise DeepSeekJSONError("score.key_points 必须是 list")
    entities = score_obj["entities"]
    if not isinstance(entities, list):
        raise DeepSeekJSONError("score.entities 必须是 list")
    return ScoreResult(
        scores={k: int(scores[k]) for k in _REQUIRED_SCORE_DIM},
        experience_scores={k: int(experience_scores[k]) for k in _REQUIRED_EXPERIENCE_DIM},
        content_type=str(score_obj["content_type"]),
        tags_fine=[str(t) for t in tags],
        title_zh=str(score_obj["title_zh"]),
        summary_zh=str(score_obj["summary_zh"]),
        key_points=[str(p) for p in key_points],
        entities=[str(e) for e in entities],
        score_reason=str(score_obj["score_reason"]),
    )


def parse_response(raw_text: str) -> FilterScoreResult:
    """解析 LLM 返回 → FilterScoreResult；任何 schema 不符抛 DeepSeekJSONError。"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise DeepSeekJSONError(f"JSON parse 失败: {e}; raw={raw_text[:200]!r}") from e

    if not isinstance(data, dict):
        raise DeepSeekJSONError(f"返回根 不是 dict: {type(data)}")
    if "filter" not in data or "filter_passed" not in data:
        raise DeepSeekJSONError(f"返回缺少 filter / filter_passed: keys={list(data.keys())}")

    filter_result = _validate_filter(data["filter"])
    passed = bool(data["filter_passed"])

    score_result: ScoreResult | None = None
    if passed:
        if "score" not in data:
            raise DeepSeekJSONError("filter_passed=true 但缺少 score 段")
        score_result = _validate_score(data["score"])

    return FilterScoreResult(
        filter=filter_result,
        filter_passed=passed,
        score=score_result,
        raw_response=data,
    )


# ---- 客户端 -----------------------------------------------------------------


class _RateLimiter:
    """简单 token bucket：每秒最多 rps 个请求。"""

    def __init__(self, rps: float):
        self.rps = rps
        self._last_call_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        min_interval = 1.0 / self.rps if self.rps > 0 else 0.0
        elapsed = now - self._last_call_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call_at = time.monotonic()


class DeepSeekClient:
    """DeepSeek API client（OpenAI-compatible）。

    用法：
        client = DeepSeekClient(api_key=cfg.secrets.deepseek_api_key)
        result = client.run_filter_score(title="...", body="...")
        if result.filter_passed:
            print(result.score.score_sum())  # 不含 source_bonus
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        rps: float = DEFAULT_RPS,
        http_client: httpx.Client | None = None,
    ):
        if not api_key:
            raise ValueError("DeepSeek api_key 不能为空")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._limiter = _RateLimiter(rps)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "DeepSeekClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def run_filter_score(self, title: str, body: str) -> FilterScoreResult:
        """单条 article 跑合并 prompt。

        失败模式：
        - HTTP 429/5xx：指数退避 1/2/4/8s 最多 4 次 → DeepSeekAPIError
        - JSON parse 或 schema 不符：重试 1 次（同 prompt）→ DeepSeekJSONError
        - 其他 HTTP 错误：直接 DeepSeekAPIError
        """
        user_prompt = build_user_prompt(title, body)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,  # 低温降低 JSON 飘移
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for json_attempt in range(JSON_RETRY_LIMIT + 1):
            raw_text, usage = self._post_with_backoff(payload, headers)
            try:
                result = parse_response(raw_text)
                result.usage = usage
                return result
            except DeepSeekJSONError as e:
                last_exc = e
                if json_attempt < JSON_RETRY_LIMIT:
                    logger.warning(f"JSON parse 失败（重试 {json_attempt + 1}/{JSON_RETRY_LIMIT}）: {e}")
                    continue
        raise last_exc  # type: ignore[misc]

    def _post_with_backoff(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[str, dict[str, int]]:
        """HTTP POST 带 429/5xx 指数退避。返回 (message.content 文本, usage dict)。"""
        last_exc: Exception | None = None
        for attempt, wait_s in enumerate([0.0, *RETRY_BACKOFF_SECONDS]):
            if wait_s > 0:
                logger.warning(f"重试 {attempt}/{len(RETRY_BACKOFF_SECONDS)}: 等待 {wait_s}s")
                time.sleep(wait_s)

            self._limiter.wait()
            try:
                resp = self._client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            except httpx.HTTPError as e:
                last_exc = DeepSeekAPIError(f"httpx 异常: {e}")
                continue

            if resp.status_code == 200:
                try:
                    body = resp.json()
                except json.JSONDecodeError as e:
                    raise DeepSeekAPIError(f"200 但响应非 JSON: {e}; body={resp.text[:200]!r}") from e
                try:
                    content = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as e:
                    raise DeepSeekAPIError(
                        f"200 但缺少 choices[0].message.content: {body}"
                    ) from e
                usage: dict[str, int] = body.get("usage") or {}
                return content, usage

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_exc = DeepSeekAPIError(
                    f"HTTP {resp.status_code}: {resp.text[:200]!r}"
                )
                continue  # 走退避重试

            # 4xx 非 429：直接报错，不重试
            raise DeepSeekAPIError(
                f"HTTP {resp.status_code} 不可重试: {resp.text[:200]!r}"
            )

        raise last_exc or DeepSeekAPIError("HTTP 重试已耗尽")


# ---- 异步客户端 ---------------------------------------------------------------


class _AsyncRateLimiter:
    """简单 async token bucket：每秒最多 rps 个请求。"""

    def __init__(self, rps: float):
        self.rps = rps
        self._last_call_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            min_interval = 1.0 / self.rps if self.rps > 0 else 0.0
            elapsed = now - self._last_call_at
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_call_at = time.monotonic()


class AsyncDeepSeekClient:
    """DeepSeek API async client（OpenAI-compatible）。

    用法：
        async with AsyncDeepSeekClient(api_key=...) as client:
            result = await client.run_filter_score(title="...", body="...")
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        rps: float = DEFAULT_RPS,
        http_client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise ValueError("DeepSeek api_key 不能为空")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._limiter = _AsyncRateLimiter(rps)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncDeepSeekClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def run_filter_score(self, title: str, body: str) -> FilterScoreResult:
        """单条 article 跑合并 prompt（async）。

        失败模式与 sync 版本一致：
        - HTTP 429/5xx：指数退避 → DeepSeekAPIError
        - JSON parse 或 schema 不符：重试 1 次 → DeepSeekJSONError
        """
        user_prompt = build_user_prompt(title, body)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for json_attempt in range(JSON_RETRY_LIMIT + 1):
            raw_text, usage = await self._post_with_backoff_async(payload, headers)
            try:
                result = parse_response(raw_text)
                result.usage = usage
                return result
            except DeepSeekJSONError as e:
                last_exc = e
                if json_attempt < JSON_RETRY_LIMIT:
                    logger.warning(f"JSON parse 失败（重试 {json_attempt + 1}/{JSON_RETRY_LIMIT}）: {e}")
                    continue
        raise last_exc  # type: ignore[misc]

    async def _post_with_backoff_async(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[str, dict[str, int]]:
        """async HTTP POST 带 429/5xx 指数退避。返回 (message.content, usage dict)。"""
        last_exc: Exception | None = None
        for attempt, wait_s in enumerate([0.0, *RETRY_BACKOFF_SECONDS]):
            if wait_s > 0:
                logger.warning(f"重试 {attempt}/{len(RETRY_BACKOFF_SECONDS)}: 等待 {wait_s}s")
                await asyncio.sleep(wait_s)

            await self._limiter.wait()
            try:
                resp = await self._client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            except httpx.HTTPError as e:
                last_exc = DeepSeekAPIError(f"httpx 异常: {e}")
                continue

            if resp.status_code == 200:
                try:
                    body = resp.json()
                except json.JSONDecodeError as e:
                    raise DeepSeekAPIError(f"200 但响应非 JSON: {e}; body={resp.text[:200]!r}") from e
                try:
                    content = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as e:
                    raise DeepSeekAPIError(
                        f"200 但缺少 choices[0].message.content: {body}"
                    ) from e
                usage: dict[str, int] = body.get("usage") or {}
                return content, usage

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_exc = DeepSeekAPIError(
                    f"HTTP {resp.status_code}: {resp.text[:200]!r}"
                )
                continue  # 走退避重试

            # 4xx 非 429：直接报错，不重试
            raise DeepSeekAPIError(
                f"HTTP {resp.status_code} 不可重试: {resp.text[:200]!r}"
            )

        raise last_exc or DeepSeekAPIError("HTTP 重试已耗尽")
