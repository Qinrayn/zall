"""G16 completion token 钳制不变量测试 (IPR-0, 含反例).

不变量:
  I-TK-1  estimate_text_tokens: ASCII ceil(n/4), 非 ASCII 每字符 1
  I-TK-2  clamp: requested 放得下 → 原样 (反例); 超出 → window-input-margin;
          窗口近满 → minimum 保底
  I-TK-3  window<=0 (未知) → 原样不钳 (反例)
  I-TK-4  adapter 集成: openai_compat / anthropic _build_body 的 max_tokens
          不超过窗口余量; 合理值不被乱钳 (反例)
"""

from __future__ import annotations

from zall._util.tokens import (
    MIN_COMPLETION,
    SAFETY_MARGIN,
    clamp_completion_tokens,
    estimate_body_tokens,
    estimate_text_tokens,
)

# ── I-TK-1 估算 ──


def test_estimate_ascii_quarters():
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("abcde") == 2
    assert estimate_text_tokens("") == 0


def test_estimate_cjk_one_per_char():
    assert estimate_text_tokens("中文测试") == 4


def test_estimate_mixed_additive():
    assert estimate_text_tokens("abcd中文") == 1 + 2


def test_estimate_body_covers_nested():
    small = estimate_body_tokens({"messages": []})
    big = estimate_body_tokens({"messages": [{"content": "x" * 4000}]})
    assert big > small + 900


# ── I-TK-2 钳制 ──


def test_clamp_fits_untouched_counterexample():
    """反例: 放得下时原样返回, 不乱钳。"""
    assert clamp_completion_tokens(
        4096, window=128_000, input_tokens=1000
    ) == 4096


def test_clamp_caps_to_remaining():
    out = clamp_completion_tokens(100_000, window=128_000, input_tokens=50_000)
    assert out == 128_000 - 50_000 - SAFETY_MARGIN


def test_clamp_floor_when_window_nearly_full():
    out = clamp_completion_tokens(4096, window=8192, input_tokens=8100)
    assert out == MIN_COMPLETION


# ── I-TK-3 未知窗口 ──


def test_unknown_window_untouched_counterexample():
    """反例: window<=0 不敢钳, 原样透传。"""
    assert clamp_completion_tokens(9999, window=0, input_tokens=10**9) == 9999


# ── I-TK-4 adapter 集成 ──


def _mk_messages():
    from zall.core.model import Message

    return [Message(role="user", content="hello " * 2000)]  # ~12KB → ~3000 tokens


def test_openai_compat_build_body_clamps():
    from zall.adapters.openai_compat import OpenAICompatAdapter
    from zall.core.model import ToolChoice

    a = OpenAICompatAdapter(
        api_key="k", api_base="https://x", model="gpt-4o-mini", max_tokens=500_000
    )
    body = a._build_body(_mk_messages(), [], ToolChoice.AUTO)
    # gpt-4o-mini window=128k → 钳到余量之内, 绝不透传 500k
    assert body["max_tokens"] < 128_000
    assert body["max_tokens"] >= MIN_COMPLETION


def test_openai_compat_reasonable_request_untouched_counterexample():
    """反例: 4096 在 128k 窗口下原样保留。"""
    from zall.adapters.openai_compat import OpenAICompatAdapter
    from zall.core.model import ToolChoice

    a = OpenAICompatAdapter(
        api_key="k", api_base="https://x", model="gpt-4o-mini", max_tokens=4096
    )
    body = a._build_body(_mk_messages(), [], ToolChoice.AUTO)
    assert body["max_tokens"] == 4096


def test_anthropic_build_body_clamps():
    from zall.adapters.anthropic import AnthropicAdapter
    from zall.core.model import ToolChoice

    a = AnthropicAdapter(
        api_key="k", model="claude-sonnet-4-20250514", max_tokens=999_999
    )
    body = a._build_body(_mk_messages(), [], ToolChoice.AUTO)
    assert body["max_tokens"] < 999_999
    assert body["max_tokens"] >= MIN_COMPLETION
