"""LLM client 层。

唯一 provider：DeepSeek（spec v2 §4.1 / §7.1 / §17 决策 9）。
未来要换其他厂家时新增 client 文件即可，不动 helper 层。
"""

from .deepseek import (
    DeepSeekClient,
    FilterScoreResult,
    DeepSeekError,
    DeepSeekAPIError,
    DeepSeekJSONError,
)

__all__ = [
    "DeepSeekClient",
    "FilterScoreResult",
    "DeepSeekError",
    "DeepSeekAPIError",
    "DeepSeekJSONError",
]
