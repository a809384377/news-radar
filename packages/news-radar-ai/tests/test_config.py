"""配置加载测试（spec v2 §7.1 单套 AppConfig）。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from news_radar_ai.config import (
    AppConfig,
    Secrets,
    load_config,
    write_default_config,
)


def test_load_config_pure_default(tmp_path: Path) -> None:
    """空 home 加载 → 默认 AppConfig；logs/ 与 cache/ 被创建；六段字段值正确。"""
    config = load_config(home=tmp_path)
    assert isinstance(config, AppConfig)

    # logs 与 cache 目录被创建
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "cache").is_dir()

    # upstream
    assert config.upstream.newsbox_raw_db_path == "~/.newsbox/raw.db"
    assert config.upstream.fetch_freshness_threshold_hours == 4

    # llm（D1: model=deepseek-v4-flash）
    assert config.llm.provider == "deepseek"
    assert config.llm.model == "deepseek-v4-flash"

    # scoring
    assert config.scoring.selection_threshold == 65
    assert config.scoring.source_bonus == {
        "official_first_party": 5,
        "kol": 0,
        "secondary": -5,
    }

    # report（editorial-depth sprint：新增 featured_max / short_body_fetch_threshold）
    assert config.report.obsidian_vault_path.startswith("~")
    assert config.report.featured_max == 20
    assert config.report.short_body_fetch_threshold == 500

    # logging
    assert config.logging.level == "info"
    assert config.logging.file == "~/.news-radar-ai/logs/news-radar-ai.log"
    assert config.logging.rotation == "daily"
    assert config.logging.retention_days == 30

    # 无 .env → secrets 全 None
    assert config.secrets.deepseek_api_key is None
    assert config.secrets.gemini_api_key is None
    assert config.secrets.openai_api_key is None
    assert config.secrets.anthropic_api_key is None


def test_load_config_user_override_partial(tmp_path: Path) -> None:
    """用户覆盖 scoring.selection_threshold，其余字段保留默认。"""
    user_yaml = tmp_path / "config.yaml"
    user_yaml.write_text(
        "scoring:\n  selection_threshold: 70\n",
        encoding="utf-8",
    )

    config = load_config(home=tmp_path)

    # 用户覆盖字段生效
    assert config.scoring.selection_threshold == 70
    # 同模块其他字段保留默认
    assert config.scoring.source_bonus == {
        "official_first_party": 5,
        "kol": 0,
        "secondary": -5,
    }
    # 其他段保留默认
    assert config.llm.model == "deepseek-v4-flash"
    assert config.upstream.newsbox_raw_db_path == "~/.newsbox/raw.db"


def test_load_config_user_override_nested_dict(tmp_path: Path) -> None:
    """用户只覆盖 source_bonus 中的一个 tier，其他 tier 应保留。"""
    user_yaml = tmp_path / "config.yaml"
    user_yaml.write_text(
        "scoring:\n"
        "  source_bonus:\n"
        "    secondary: -10\n",
        encoding="utf-8",
    )

    config = load_config(home=tmp_path)
    assert config.scoring.source_bonus == {
        "official_first_party": 5,
        "kol": 0,
        "secondary": -10,  # 被覆盖
    }
    # 兄弟字段未动
    assert config.scoring.selection_threshold == 65


def test_load_config_user_override_llm_model(tmp_path: Path) -> None:
    """用户覆盖 llm.model（如换 deepseek-chat）后生效，同段未覆盖字段保留默认。"""
    user_yaml = tmp_path / "config.yaml"
    user_yaml.write_text(
        "llm:\n  model: deepseek-chat\n",
        encoding="utf-8",
    )

    config = load_config(home=tmp_path)
    assert config.llm.model == "deepseek-chat"
    assert config.llm.provider == "deepseek"


def test_load_config_env_secrets(tmp_path: Path) -> None:
    """.env 中的密钥被装进 Secrets，真实值可访问。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=sk-test12345678\n"
        "OPENAI_API_KEY=sk-openai-abcdefgh\n",
        encoding="utf-8",
    )

    config = load_config(home=tmp_path)

    assert config.secrets.deepseek_api_key == "sk-test12345678"
    assert config.secrets.openai_api_key == "sk-openai-abcdefgh"
    assert config.secrets.gemini_api_key is None
    assert config.secrets.anthropic_api_key is None

    # get_raw 接口
    assert config.secrets.get_raw("DEEPSEEK_API_KEY") == "sk-test12345678"
    assert config.secrets.get_raw("GEMINI_API_KEY") is None


def test_secrets_repr_redacted(tmp_path: Path) -> None:
    """repr(secrets) 不含完整 key，含前 4 与后 4 字符，中间是 * 序列。"""
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-test12345678\n", encoding="utf-8")

    config = load_config(home=tmp_path)
    rendered = repr(config.secrets)

    # 完整 key 不应出现
    assert "sk-test12345678" not in rendered
    # 前缀与后缀片段应在
    assert "sk-t" in rendered
    assert "5678" in rendered
    # 中间应有 * 字符
    assert "*" in rendered
    # 其他字段显示 None
    assert "gemini=None" in rendered
    assert "openai=None" in rendered
    assert "anthropic=None" in rendered


def test_secrets_repr_short_key_full_redact(tmp_path: Path) -> None:
    """短于 9 字符的密钥整体脱敏为 ***。"""
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=short\n", encoding="utf-8")

    config = load_config(home=tmp_path)
    rendered = repr(config.secrets)

    assert "short" not in rendered
    assert "deepseek=***" in rendered


def test_write_default_config(tmp_path: Path) -> None:
    """write_default_config 把默认模板拷到 tmp_path/config.yaml；二次调用拒绝。"""
    target = write_default_config(home=tmp_path)

    assert target == tmp_path / "config.yaml"
    assert target.is_file()

    # 内容能被 yaml 解析且含 spec v2 六段
    with target.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    for section in ("upstream", "llm", "scoring", "report", "logging"):
        assert section in data, f"缺少 {section} 段"
    assert data["scoring"]["selection_threshold"] == 65
    assert data["llm"]["model"] == "deepseek-v4-flash"

    # 不带 force 二次调用应抛 FileExistsError
    with pytest.raises(FileExistsError):
        write_default_config(home=tmp_path)

    # force=True 可覆盖
    target2 = write_default_config(home=tmp_path, force=True)
    assert target2 == target


def test_write_default_config_creates_home(tmp_path: Path) -> None:
    """home 不存在时 write_default_config 应创建。"""
    sub = tmp_path / "nested" / "home"
    target = write_default_config(home=sub)
    assert sub.is_dir()
    assert target.is_file()
