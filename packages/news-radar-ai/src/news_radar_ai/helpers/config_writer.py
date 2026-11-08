"""helpers/config_writer — 单字段安全更新 ~/.news-radar-ai/config.yaml（spec v2 §7.1）。

保留注释 / 键序 / 缩进（ruamel.yaml round-trip 模式）。
事务写入：验证失败时原文件内容保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from ruamel.yaml import YAML

import news_radar_ai.config as config_module


@dataclass
class ConfigSetResult:
    config_path: Path   # 实际写入的 config.yaml 路径
    key_path: str       # 用户传入的点号路径，e.g. "report.obsidian_vault_path"
    old_value: object   # 写入前的值（可能为 None 如键不存在但需创建）
    new_value: object   # 解析后写入的值
    created: bool       # True = key 之前不存在（新建）, False = 已存在被覆盖


def _parse_value(s: str) -> object:
    """将字符串推断为 YAML scalar 类型。

    规则：
      "true" / "false"  → bool
      "null"            → None
      整数字面量         → int
      浮点字面量         → float
      其余              → str（保留原字符串）
    """
    low = s.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    # 尝试 int（先于 float，避免 "42" 被解析为 42.0）
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def set_config_value(*, home: Path, key_path: str, value: str) -> ConfigSetResult:
    """安全更新 ~/.news-radar-ai/config.yaml 的单字段。

    - 若 config.yaml 不存在 → 抛 FileNotFoundError（提示先 `config init`）
    - 用 ruamel.yaml round-trip 读写（保留注释/键序/缩进）
    - key_path 支持点号嵌套，如 "report.obsidian_vault_path"
    - value 是字符串，按 YAML scalar 类型推断（见 _parse_value）
    - 嵌套路径中间键不存在时抛 KeyError（明确写出哪一级缺失）
    - 写完后调 config_module.load_config(home) 验证整体配置仍合法；
      失败则抛 ValueError 并恢复原文件内容
    """
    home = Path(home)
    config_path = home / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} 不存在。请先运行 `news-radar-ai config init` 生成默认配置。"
        )

    rt = YAML(typ="rt")
    rt.preserve_quotes = True

    # --- 读原始文档（保留注释/键序/缩进）---
    original_text = config_path.read_text(encoding="utf-8")
    doc = rt.load(original_text)

    # --- 按 key_path 导航到目标键 ---
    keys = key_path.split(".")
    node = doc
    for segment in keys[:-1]:
        if not isinstance(node, dict) or segment not in node:
            raise KeyError(
                f"key_path '{key_path}' 中间键 '{segment}' 不存在于配置文档"
            )
        node = node[segment]

    leaf_key = keys[-1]
    created = not (isinstance(node, dict) and leaf_key in node)
    old_value = None if created else node[leaf_key]

    new_value = _parse_value(value)

    logger.info(
        f"config set: {key_path!r} {old_value!r} → {new_value!r} "
        f"({'created' if created else 'updated'})"
    )

    # --- SET 新值 ---
    node[leaf_key] = new_value

    # --- 事务写入（write tmp → validate → rename）---
    tmp_path = home / "config.yaml.tmp"

    with tmp_path.open("w", encoding="utf-8") as fh:
        rt.dump(doc, fh)

    # 把 tmp 换成正式文件再跑 load_config（load_config 读 home/config.yaml）
    # 先备份原内容（字符串），rename tmp；失败则写回原内容
    try:
        tmp_path.replace(config_path)
        try:
            config_module.load_config(home)
        except Exception as exc:
            # 验证失败：恢复原文件内容
            config_path.write_text(original_text, encoding="utf-8")
            raise ValueError(
                f"config set 后配置验证失败（已恢复原文件）：{exc}"
            ) from exc
    except ValueError:
        raise
    except Exception as exc:
        # rename 本身失败时也确保清理 tmp
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"config.yaml 写入失败：{exc}") from exc

    return ConfigSetResult(
        config_path=config_path,
        key_path=key_path,
        old_value=old_value,
        new_value=new_value,
        created=created,
    )
