"""tests/test_config_writer — helpers/config_writer.set_config_value 单元测试。

核心目标：ruamel.yaml round-trip 保留注释/键序/缩进。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import news_radar_ai.config as config_module
from news_radar_ai.helpers.config_writer import ConfigSetResult, set_config_value


# ---- 用例 1：顶层字段（一层路径），断言注释被保留 ---------------------------


def test_set_top_level(tmp_path: Path) -> None:
    """logging.level info → debug；写后文件仍含原注释（# spec §）。"""
    config_module.write_default_config(tmp_path)

    result = set_config_value(home=tmp_path, key_path="logging.level", value="debug")

    assert isinstance(result, ConfigSetResult)
    assert result.key_path == "logging.level"
    assert result.old_value == "info"
    assert result.new_value == "debug"
    assert result.created is False
    assert result.config_path == tmp_path / "config.yaml"

    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")

    # 核心：注释必须保留（ruamel.yaml round-trip 的核心目的）
    assert "# spec §" in text or "# ====" in text or "# news-radar-ai" in text

    # 修改已生效
    assert "debug" in text

    # 其他段的注释字符串（来自 config.default.yaml 各行 inline comment）
    # 检测任意一个 inline comment 关键字即可确认注释未被清除
    assert "#" in text


# ---- 用例 2：嵌套路径，断言 old/new/created 语义 ---------------------------


def test_set_nested_path(tmp_path: Path) -> None:
    """report.obsidian_vault_path 改路径；断言 old_value/new_value/created=False 正确。"""
    config_module.write_default_config(tmp_path)

    new_path = "/Users/alice/MyVault/AI Daily/"
    result = set_config_value(
        home=tmp_path,
        key_path="report.obsidian_vault_path",
        value=new_path,
    )

    assert result.old_value == "~/Documents/ObsidianVault/AI Daily/"
    assert result.new_value == new_path
    assert result.created is False

    # 验证写入内容
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert new_path in text

    # load_config 应能读到新值
    cfg = config_module.load_config(tmp_path)
    assert cfg.report.obsidian_vault_path == new_path


# ---- 用例 3：中间键不存在 → KeyError，match 缺失的键名 ---------------------


def test_invalid_key_path(tmp_path: Path) -> None:
    """nonexistent.foo 中间键 'nonexistent' 不存在 → KeyError 含 'nonexistent'。"""
    config_module.write_default_config(tmp_path)

    with pytest.raises(KeyError, match="nonexistent"):
        set_config_value(home=tmp_path, key_path="nonexistent.foo", value="bar")

    # 失败后原文件应保持合法（load_config 不抛异常）
    cfg = config_module.load_config(tmp_path)
    assert cfg.logging.level == "info"


# ---- 用例 4：叶子键拼写错误 → extra=forbid 在 load 阶段拒绝 ----------------


def test_set_misspelled_leaf_key_rejected(tmp_path: Path) -> None:
    """report.obsidian_vault_pat（少了 'h'）应被 extra=forbid 拒绝。

    防回归：若 BaseModel 默认 extra='ignore'，此类拼写错会被静默接受，
    user 配的值永远进不了 AppConfig.report.obsidian_vault_path，
    导致渲染时仍走默认 ~/Documents/ObsidianVault/AI Daily/。

    断言：
    - set_config_value 抛 ValueError（来自 load 验证失败的恢复路径）
    - 错误信息包含 pydantic 校验失败的关键字
    - config.yaml 内容未被污染（恢复到调用前）
    """
    config_module.write_default_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    original_text = config_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="配置验证失败"):
        set_config_value(
            home=tmp_path,
            key_path="report.obsidian_vault_pat",   # 故意少 'h'
            value="/tmp/y",
        )

    # 验证原文件未被污染（事务恢复路径）
    after_text = config_path.read_text(encoding="utf-8")
    assert after_text == original_text
    assert "obsidian_vault_pat\n" not in after_text
    assert "obsidian_vault_pat:" not in after_text
