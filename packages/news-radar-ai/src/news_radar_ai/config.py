"""配置加载（news-radar-ai / spec v2 §7.1 单套结构）。

加载顺序：
1. 包内默认 _defaults/config.default.yaml
2. 用户 ~/.news-radar-ai/config.yaml（deep merge 覆盖）
3. ~/.news-radar-ai/.env（python-dotenv → Secrets）

结构（六段顶级键，与 spec v2 §7.1 一一对应）：
  upstream / llm / scoring / clustering / report / logging + secrets
"""

from __future__ import annotations

import os
import shutil
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_HOME = Path.home() / ".news-radar-ai"

# .env 中识别的密钥环境变量名 → Secrets 字段名
_SECRET_ENV_MAP: dict[str, str] = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "GEMINI_API_KEY": "gemini_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
}


# ---- 六段子模型（spec v2 §7.1） ----------------------------------------------


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newsbox_raw_db_path: str
    fetch_freshness_threshold_hours: int


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    concurrency: int = 8  # filter-score 并发上限；尊重 DeepSeek 5 rps 限流


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_threshold: int        # 入库门槛：行业雷达 final_score 此值以上才 status='scored'（不算被 filter_rejected）
    experience_threshold: int       # 入库门槛：经验价值此值以上才 status='scored'
    featured_threshold: int         # 精选门槛：agent 在 SKILL Step 5 写报告时只挑此分以上进精选区（dual-track-scoring sprint）
    source_bonus: dict[str, int]   # tier → bonus；D3 只加行业雷达，不加经验价值


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obsidian_vault_path: str
    featured_max: int                 # 精选条数兜底上限（sprint editorial-depth：纵向判断 + 上限）
    short_body_fetch_threshold: int   # raw_body 字符数 ≤ 此阈值时 agent 用 WebFetch 补全原文



class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str
    file: str
    rotation: str
    retention_days: int


# ---- Secrets ---------------------------------------------------------------


def _redact(value: str | None) -> str:
    """脱敏：保前 4 后 4，中间 ****；< 9 字符直接 ***；None 显示 None。"""
    if value is None:
        return "None"
    if len(value) < 9:
        return "***"
    return f"{value[:4]}****{value[-4:]}"


class Secrets(BaseModel):
    """从 .env 读取的密钥。

    `__repr__` / `__str__` 会脱敏（保前 4 后 4，中间 ****）。
    访问真实值用 `secrets.get_raw('DEEPSEEK_API_KEY')`。

    spec v2 §7.2 实际仅用 DEEPSEEK_API_KEY；其余字段保留为 None 默认（不影响功能），
    未来切换 provider 时可启用。
    """

    model_config = ConfigDict(extra="forbid")

    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    def get_raw(self, key: str) -> str | None:
        """通过环境变量名（DEEPSEEK_API_KEY 等）取真实值。"""
        field = _SECRET_ENV_MAP.get(key)
        if field is None:
            if key.lower() in _SECRET_ENV_MAP.values():
                field = key.lower()
            else:
                return None
        return getattr(self, field, None)

    def __repr__(self) -> str:  # pragma: no cover - 简单格式化
        parts = [
            f"deepseek={_redact(self.deepseek_api_key)}",
            f"gemini={_redact(self.gemini_api_key)}",
            f"openai={_redact(self.openai_api_key)}",
            f"anthropic={_redact(self.anthropic_api_key)}",
        ]
        return f"Secrets({', '.join(parts)})"

    def __str__(self) -> str:  # pragma: no cover
        return self.__repr__()


# ---- 顶层 AppConfig --------------------------------------------------------


class AppConfig(BaseModel):
    """spec v2 §7.1 配置顶层模型。"""

    model_config = ConfigDict(extra="forbid")

    upstream: UpstreamConfig
    llm: LLMConfig
    scoring: ScoringConfig
    report: ReportConfig
    logging: LoggingConfig
    secrets: Secrets = Field(default_factory=Secrets)


# ---- 内部工具 --------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深合并：override 覆盖 base。

    - 双方都是 dict → 递归合并
    - 否则 override 整体替换 base 对应键
    - base 中存在而 override 没有的键保留
    """
    result = dict(base)
    for key, ov_val in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(ov_val, dict)
        ):
            result[key] = _deep_merge(result[key], ov_val)
        else:
            result[key] = ov_val
    return result


def _load_default_yaml() -> dict[str, Any]:
    """从包内 _defaults/config.default.yaml 读默认。"""
    res = resource_files("news_radar_ai._defaults") / "config.default.yaml"
    text = res.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("config.default.yaml 必须是 mapping")
    return data


def _default_yaml_path() -> Path:
    """返回包内默认 yaml 文件的实际路径（用于 write_default_config 拷贝）。"""
    res = resource_files("news_radar_ai._defaults") / "config.default.yaml"
    return Path(str(res))


# ---- 公共接口 --------------------------------------------------------------


def load_config(home: Path = DEFAULT_HOME) -> AppConfig:
    """加载配置。

    步骤：
    1. 确保 home / 'logs' 与 home / 'cache' 目录存在
    2. 读包内默认 yaml 作为基础
    3. 若 home / 'config.yaml' 存在 → deep merge 覆盖
    4. 读 home / '.env'（dotenv_values，不污染 os.environ），装填 Secrets
    5. 验证并返回 AppConfig
    """
    home = Path(home)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "cache").mkdir(parents=True, exist_ok=True)

    merged = _load_default_yaml()

    user_yaml = home / "config.yaml"
    if user_yaml.exists():
        with user_yaml.open("r", encoding="utf-8") as f:
            user_data = yaml.safe_load(f) or {}
        if not isinstance(user_data, dict):
            raise ValueError(f"{user_yaml} 必须是 mapping")
        merged = _deep_merge(merged, user_data)

    secrets_data: dict[str, str | None] = {}
    env_path = home / ".env"
    if env_path.exists():
        env_values = dotenv_values(env_path)
        for env_key, field in _SECRET_ENV_MAP.items():
            val = env_values.get(env_key)
            if val is not None and val != "":
                secrets_data[field] = val

    # Explicit process environment takes precedence; supports secret managers/CI.
    # Never log or print the values.
    for env_key, field in _SECRET_ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            secrets_data[field] = value
    secrets = Secrets(**secrets_data)
    return AppConfig(**merged, secrets=secrets)


def write_default_config(home: Path = DEFAULT_HOME, force: bool = False) -> Path:
    """把包内默认 yaml 拷贝到 home / 'config.yaml'。

    - home 不存在则创建
    - 目标存在且 force=False → FileExistsError
    返回写入的目标路径。
    """
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    target = home / "config.yaml"
    if target.exists() and not force:
        raise FileExistsError(f"{target} 已存在；如需覆盖请传 force=True")

    src = _default_yaml_path()
    if src.is_file():
        shutil.copyfile(src, target)
    else:
        res = resource_files("news_radar_ai._defaults") / "config.default.yaml"
        target.write_text(res.read_text(encoding="utf-8"), encoding="utf-8")
    return target
