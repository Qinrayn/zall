"""zall.core.cache_stats — 提示缓存记账 + 上下文剩余度 (吸收轮: Codex 口径)。

四个独立概念 (纯函数/纯数据, 可离线单测):

  1. canonical_usage(...)            — 各家 usage 归一为固定键集 {prompt, cached,
                                       cache_write, completion, total}
  2. CacheStats                      — 逐次累加 + 命中率 + 展示行
  3. context_remaining_percent(...)  — baseline-normalized 上下文剩余百分比
  4. prefix_fingerprint(...)         — system + tools 的稳定摘要; 变化 = 缓存前缀失效

为什么需要它 (工程动机, 对齐 Codex 的 prompt-cache 工程):
  - 各 provider 的 usage 字段不同名 (OpenAI cached_tokens / DeepSeek
    prompt_cache_hit_tokens / Anthropic cache_read_input_tokens / Gemini
    cached_content_token_count), 归一后才能跨 provider 统计命中率;
  - Anthropic 的 input_tokens **不含** cache 读写, 直接当上下文大小会低估;
  - 缓存命中与否取决于请求前缀是否逐字节稳定 — prefix_fingerprint 把它变成
    可观测量 (变化即失效), 这是"可证伪"而不是"估计"。

IPR constraints:
  IPR-3: stdlib only (hashlib/json/dataclasses), 无模型 SDK
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BASELINE_TOKENS",
    "CacheStats",
    "canonical_usage",
    "context_remaining_percent",
    "format_tokens",
    "prefix_fingerprint",
]

# Codex 同口径: 系统提示 + 工具 schema 等"永远在场"的固定底座, 从窗口里扣除,
# 使百分比反映"用户可影响的部分" — 首次提问后即接近 100% left, 而非一上来就掉两成。
BASELINE_TOKENS = 12000


def canonical_usage(
    *,
    prompt: int = 0,
    completion: int = 0,
    cached: int = 0,
    cache_write: int = 0,
) -> dict[str, int]:
    """组装规范 usage 字典 (adapter 层调用)。

    prompt 语义: **输入总量, 含命中缓存的部分**。
    各 provider 对齐方式 (adapter 负责换算):
      OpenAI / DeepSeek / Gemini / GLM: usage.prompt_tokens 已含 cached → 直接传
      Anthropic: input_tokens 不含 cache_read/cache_creation
                 → prompt = input_tokens + cache_read + cache_creation
    """
    p = max(0, int(prompt))
    c = max(0, int(cached))
    w = max(0, int(cache_write))
    out = max(0, int(completion))
    return {
        "prompt": p,
        "cached": min(c, p) if p else c,
        "cache_write": w,
        "completion": out,
        "total": p + out,
    }


@dataclass
class CacheStats:
    """会话级缓存/用量累加器 (纯数据, 无 IO)。

    只做算术与展示, 不做策略判断 — 策略在调用方 (CLI/observer)。
    """

    calls: int = 0
    prompt: int = 0
    cached: int = 0
    cache_write: int = 0
    completion: int = 0
    prefix_changes: int = 0
    last_context_tokens: int = 0      # 最近一次调用的输入量 ≈ 当前上下文大小
    _last_prefix_fp: str = ""
    _prefix_seen: bool = False
    _history: list[float] = field(default_factory=list)  # 每次调用的命中率, 供趋势判断

    # ── 记录 ─

    def record(self, usage: Mapping[str, Any] | None) -> None:
        """累加一次调用的 usage (缺键按 0 处理, 不抛)。"""
        if not usage:
            return
        try:
            p = int(usage.get("prompt", 0) or 0)
            c = int(usage.get("cached", 0) or 0)
            w = int(usage.get("cache_write", 0) or 0)
            o = int(usage.get("completion", 0) or 0)
        except (TypeError, ValueError):
            return
        self.calls += 1
        self.prompt += max(0, p)
        self.cached += max(0, c)
        self.cache_write += max(0, w)
        self.completion += max(0, o)
        if p > 0:
            self.last_context_tokens = p
            self._history.append(max(0.0, min(1.0, c / p)))
            if len(self._history) > 32:
                del self._history[: len(self._history) - 32]

    def record_prefix(self, fingerprint: str) -> bool:
        """记录本次请求的前缀指纹; 返回 True 表示**发生了变化** (缓存前缀失效)。

        首次记录不算变化 (会话第一次调用). 空指纹忽略。
        """
        if not fingerprint:
            return False
        if not self._prefix_seen:
            self._prefix_seen = True
            self._last_prefix_fp = fingerprint
            return False
        if fingerprint != self._last_prefix_fp:
            self._last_prefix_fp = fingerprint
            self.prefix_changes += 1
            return True
        return False

    # ── 派生量 ──

    @property
    def hit_rate(self) -> float:
        """累计命中率 (cached / prompt); 无输入 → 0.0。"""
        if self.prompt <= 0:
            return 0.0
        return max(0.0, min(1.0, self.cached / self.prompt))

    @property
    def recent_hit_rate(self) -> float:
        """最近一次调用的命中率 (比累计值更能反映"现在是否还在命中")。"""
        return self._history[-1] if self._history else 0.0

    @property
    def has_cache_data(self) -> bool:
        return self.cached > 0 or self.cache_write > 0

    def reset(self) -> None:
        self.calls = 0
        self.prompt = 0
        self.cached = 0
        self.cache_write = 0
        self.completion = 0
        self.prefix_changes = 0
        self.last_context_tokens = 0
        self._last_prefix_fp = ""
        self._prefix_seen = False
        self._history.clear()

    # ── 展示 ──

    def format_summary(self, *, with_write: bool = True) -> str:
        """单行摘要 (状态行/`/status` 共用)。

        无缓存数据 → "cache –"; 有命中 → "cache 78% · 12.3k cached";
        有写入 (Anthropic) 且 with_write → 追加 "· 2.1k write"。
        """
        if not self.has_cache_data:
            return "cache \u2013"
        parts = [f"cache {round(self.hit_rate * 100)}%", f"{format_tokens(self.cached)} cached"]
        if with_write and self.cache_write > 0:
            parts.append(f"{format_tokens(self.cache_write)} write")
        return " \u00b7 ".join(parts)

    def format_usage_line(self) -> str:
        """Codex 风格用量行: total / input / (+ cached) / output。"""
        blended = max(0, self.prompt - self.cached) + self.completion
        cached_note = f" (+ {format_tokens(self.cached)} cached)" if self.cached > 0 else ""
        return (
            f"total {format_tokens(blended)} "
            f"input {format_tokens(max(0, self.prompt - self.cached))}{cached_note} "
            f"output {format_tokens(self.completion)}"
        )


def format_tokens(n: int) -> str:
    """人类可读 token 数: 1234 → "1.2k"; 1234567 → "1.2M"; <1000 → 原数。"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "0"
    if v < 1000:
        return str(v)
    if v < 1_000_000:
        return f"{v / 1000:.1f}k"
    return f"{v / 1_000_000:.1f}M"


def context_remaining_percent(
    used_tokens: int,
    context_window: int,
    *,
    baseline: int = BASELINE_TOKENS,
) -> int | None:
    """上下文剩余百分比 (baseline-normalized, Codex 口径)。

    window 未知 (<=0) → None (调用方不显示, 不猜)。
    window <= baseline → 0 (退化, 与 Codex 一致)。
    分子分母同减 baseline, 使"刚开新会话"显示接近 100%。
    """
    try:
        window = int(context_window)
        used = int(used_tokens)
    except (TypeError, ValueError):
        return None
    if window <= 0:
        return None
    if window <= baseline:
        return 0
    effective = window - baseline
    used_eff = max(0, used - baseline)
    remaining = max(0, effective - used_eff)
    return int(round(max(0.0, min(1.0, remaining / effective)) * 100))


def prefix_fingerprint(system_text: str | None, tools: Iterable[Any] | None) -> str:
    """请求前缀 (system + tools) 的稳定摘要 — 16 hex 字符。

    缓存命中的充分条件之一: 前缀逐字节稳定。tools 用 sort_keys 规范化 JSON,
    使键序差异不产生假"变化"; system 逐字节参与。
    空输入 → "" (调用方跳过, 不算变化)。
    """
    if not system_text and not tools:
        return ""
    h = hashlib.sha256()
    h.update((system_text or "").encode("utf-8", errors="replace"))
    h.update(b"\x00")
    if tools:
        try:
            blob = json.dumps(list(tools), sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = repr(list(tools))
        h.update(blob.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]