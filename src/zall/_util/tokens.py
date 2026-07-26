"""zall._util.tokens — CJK 感知 token 估算与 completion 上限钳制。

G16 (kimi estimate_request_tokens / compute_max_completion_tokens 对标):
  发请求前把 max_tokens 钳到 min(requested, window - input - margin) —
  防止 "input + requested > 上下文窗口" 触发 provider 400。

  估算刻意保守: ASCII 每 4 字符 1 token (向上取整), 非 ASCII (CJK 等)
  每字符 1 token — 精确分词是 provider 特定的, 调用方靠 margin 兜误差。

IPR constraints:
  IPR-0: tests/test_token_clamp_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import json
from typing import Any

# kimi DEFAULT_COMPLETION_TOKEN_SAFETY_MARGIN 同值
SAFETY_MARGIN = 1024
# 钳制下限: 即使窗口几乎占满也保底可生成 (触发 LENGTH 后由上层压缩处理)
MIN_COMPLETION = 256


def estimate_text_tokens(text: str) -> int:
    """ASCII (n+3)//4, 非 ASCII 每字符 1 (CJK 感知, kimi _estimate_text_tokens 同款)。"""
    ascii_count = sum(ch.isascii() for ch in text)
    non_ascii = len(text) - ascii_count
    return (ascii_count + 3) // 4 + non_ascii


def estimate_body_tokens(body: dict[str, Any]) -> int:
    """整个请求体 (messages + tool schemas + metadata) 的 token 估算。

    直接对 JSON 序列化文本估算 — 覆盖 kimi 分项估算 (system/tools/history)
    的全部内容且天然包含结构开销, 单次 dumps 成本可忽略。
    """
    try:
        return estimate_text_tokens(json.dumps(body, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 0  # 不可序列化 (不应发生): 放弃估算, 不钳制


def clamp_completion_tokens(
    requested: int,
    *,
    window: int,
    input_tokens: int,
    margin: int = SAFETY_MARGIN,
    minimum: int = MIN_COMPLETION,
) -> int:
    """max_tokens = max(minimum, min(requested, window - input - margin))。

    window <= 0 (未知窗口) → requested 原样返回 (不敢钳)。
    """
    if window <= 0 or requested <= 0:
        return requested
    remaining = window - max(0, input_tokens) - margin
    return max(minimum, min(requested, remaining))
