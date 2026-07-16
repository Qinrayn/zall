"""zall.core.compactor — Context compression strategy (DESIGN.md §9.2.9).

Corresponds to:
  §9.2.9  Compactor: 上下文压缩 (模型语义摘要 + timeline 全保真)
  §7     "上下文策略可换" — Compactor 是策略接口

Design:
  - Compactor Protocol: 可插拔压缩策略
  - ModelCompactor: 用模型生成语义摘要 (默认实现)
  - WatermarkMonitor: 主动水位监测, 在 LENGTH 前触发压缩
  - 压缩只发生在 model context window; timeline 不可压缩 (§6.1)
  - 每次压缩记录为 timeline 一条 CONTEXT_COMPACTION 事件

v0.0.10: 首次实现 — 替代旧的简单 n-2 折叠
v0.1.0:  增强 — 主动水位监控 + 智能触发阈值

IPR constraints:
  IPR-0: invariant tests at tests/test_compactor_invariants.py
  IPR-1: corresponds to DESIGN.md §9.2.9 + §7
  IPR-3: pydantic / stdlib only, no model SDK (ModelAdapter 是 Protocol)
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Any

from pydantic import BaseModel, ConfigDict

from zall.core.model import Message, ModelAdapter
from zall._util.model_registry import get_window_size as _get_window_size


# ──────────────────────────────────────────────────────────────────────────
# Compactor Protocol (§7 "contextstrategy可换")
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Compactor(Protocol):
    """context压缩strategyinterface (§7, §9.2.9)。

    可插拔设计: 不同压缩策略 (模型摘要 / 滑动窗口 / 规则折叠)
    实现同一接口, AgentLoop 不绑定具体策略。
    """

    def compact(self, messages: list[Message], model: ModelAdapter) -> CompactResult: ...

    @property
    def watermark_monitor(self) -> WatermarkMonitor | None:
        """水位monitor器 (可选)。None 表示不支持水位monitor。"""
        ...


# ──────────────────────────────────────────────────────────────────────────
# CompactResult
# ──────────────────────────────────────────────────────────────────────────


class CompactResult(BaseModel):
    """一次压缩的产出。

    compressed_messages: 压缩后的消息列表 (可直接替换 loop._messages)
    compacted_count:       被压缩的消息数 (original - compressed)
    summary:               生成的摘要文本 (供 auditing)
    strategy:              使用的策略名 (eg. "model_summary_v1")

    IPR-0 不变量:
        - frozen
        - compressed_messages 非空
        - compacted_count ≥ 0
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    compressed_messages: list[Message]
    compacted_count: int
    summary: str
    strategy: str = "rule_folding_v1"


# ──────────────────────────────────────────────────────────────────────────
# WatermarkMonitor — 主动水位监测 (v0.1.0)
# ──────────────────────────────────────────────────────────────────────────

# 粗略 token 估算: 每字符约 0.25 token (英文) / 0.6 token (中文)
# 这是保守估算, 实际window由model决定, 我们只做水位预警。
# v0.1.2: 使用语言感知加权, 而非fixed chars/token。
# O9: 模型感知 — 不同模型的 tokenizer 效率不同, 通过 model_name 前缀调整。
_CHARS_PER_TOKEN_EN = 4.0    # 英文: ~4 chars/token (GPT-4 baseline)
_CHARS_PER_TOKEN_CJK = 1.6   # 中文/日文/韩文: ~1.6 chars/token (GPT-4 baseline)
_SAMPLE_SIZE = 1000           # 采样字符数用于估算语言比例
_SAFE_WATERMARK = 0.75       # 水位 > 75% 触发预警压缩
_CRITICAL_WATERMARK = 0.9    # 水位 > 90% 强制压缩

# CJK 统一码range (CJK Unified Ideographs)
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),   # Fullwidth Forms
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F), # CJK Compatibility Ideographs Supplement
)

# O9: 模型感知 tokenizer 效率调整因子 (相对于 GPT-4 baseline)
# 值越小 = 同样的字符数占用更多 token
_MODEL_TOKEN_EFFICIENCY: dict[str, float] = {
    "agnes-": 0.65,       # DeepSeek tokenizer: ~2.6 chars/token EN
    "deepseek": 0.65,      # DeepSeek tokenizer
    "claude": 0.85,        # Claude: ~3.4 chars/token EN
    "gemini": 0.75,        # Gemini: ~3.0 chars/token EN
    "gpt-4": 1.0,          # GPT-4 baseline
    "gpt-3.5": 1.0,        # GPT-3.5 same as GPT-4
    "qwen": 0.70,          # Qwen tokenizer: ~2.8 chars/token
    "glm": 0.75,           # GLM tokenizer
    "llama": 0.80,         # Llama tokenizer: ~3.2 chars/token
}


def _get_model_efficiency(model_name: str) -> float:
    """根据模型名前缀查找 tokenizer 效率因子, 未知模型返回 1.0 (GPT-4 baseline)。"""
    name_lower = model_name.lower()
    for prefix, factor in _MODEL_TOKEN_EFFICIENCY.items():
        if name_lower.startswith(prefix):
            return factor
    return 1.0


def _estimate_chars_per_token(text: str, model_name: str = "") -> float:
    """根据文本中 CJK 字符比例和模型类型估算 chars/token 系数 (v0.1.2, O9)。

    采样 text 前 _SAMPLE_SIZE 字符, 统计 CJK 占比后加权计算,
    再乘以模型 tokenizer 效率因子。
    纯英文 GPT-4 → 4.0, 纯中文 GPT-4 → 1.6,
    DeepSeek 纯英文 → 4.0×0.65≈2.6, DeepSeek 纯中文 → 1.6×0.65≈1.0。
    """
    sample = text[:_SAMPLE_SIZE]
    if not sample:
        return _CHARS_PER_TOKEN_EN * _get_model_efficiency(model_name)
    cjk_count = 0
    for ch in sample:
        cp = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                cjk_count += 1
                break
    ratio = cjk_count / len(sample)
    efficiency = _get_model_efficiency(model_name)
    return ((1 - ratio) * _CHARS_PER_TOKEN_EN + ratio * _CHARS_PER_TOKEN_CJK) * efficiency


# 常见modelwindow大小 (token) 已迁移至 _util/model_meta.py (C1)
# 通过 get_window_size() 统一query


class WatermarkMonitor:
    """主动水位监测 — 在 LENGTH 前预测并触发压缩 (v0.1.0).

    工作原理:
      1. 粗略估算当前消息列表的 token 数 (字符数 / 4)
      2. 根据模型名查窗口大小 (未知模型用 32K 保守值)
      3. 水位 > 75% → 建议压缩 (SAFE)
      4. 水位 > 90% → 强制压缩 (CRITICAL)
      5. 每次 step() 后检查, 非强制时不重复压缩 (watermark 缓降)

    注意: 这是估算, 不精确。精确窗口由模型 API 返回的 LENGTH 信号决定。
    WatermarkMonitor 的作用是**减少** LENGTH 的发生, 不是替代。
    """

    __test__ = False

    def __init__(self) -> None:
        self._last_compaction_step: int = 0
        self._min_compaction_interval: int = 5  # 至少隔 5 步才再次压缩
        # O1: running token estimate cache with dirty flag — avoids O(n) re-scan on repeated calls
        self._cached_token_estimate: int = 0
        self._token_estimate_dirty: bool = True

    def estimate_tokens(self, messages: list[Message], model_name: str = "") -> int:
        """粗略估算messagelist的 token 数 (v0.1.2: 语言感知加权, O9: 模型感知)。

        收集全部消息文本, 抽样估算 CJK 比例后加权计算 chars/token,
        再根据模型 tokenizer 效率调整。
        不考虑 tokenizer 差异, 只做水位预警参考。

        O1: cached with dirty flag — avoids O(n) re-scan when messages haven't changed.
        """
        if not self._token_estimate_dirty:
            return self._cached_token_estimate
        # O4: 用 list accumulator 替代字符串拼接 (O(n²) → O(n))
        # B1 fix: total_chars mustinit化
        text_parts: list[str] = []
        total_chars = 0
        for m in messages:
            content = m.content or ""
            total_chars += len(content)
            text_parts.append(content)
            # tool调用名和parameter也占 token
            if m.tool_calls:
                for tc in m.tool_calls:
                    tc_str = tc.tool_id + str(tc.args)
                    total_chars += len(tc_str)
                    text_parts.append(tc_str)
        combined_text = "".join(text_parts)
        # 语言感知 + 模型感知的 chars/token 系数 (O9)
        chars_per_token = _estimate_chars_per_token(combined_text, model_name=model_name)
        # 每条message的 role + overhead 约 10 token
        overhead = len(messages) * 10
        self._cached_token_estimate = int(total_chars / chars_per_token) + overhead
        self._token_estimate_dirty = False
        return self._cached_token_estimate

    @staticmethod
    def get_window_size(model_name: str) -> int:
        """查modelwindow大小 (委托 _util/model_meta.py, C1 统一元数据)。"""
        return _get_window_size(model_name)

    def check_watermark(
        self, messages: list[Message], model_name: str, step: int
    ) -> str | None:
        """check当前水位, return建议的operation。

        Returns:
            None          — 水位正常, 不需要压缩
            "suggest"     — 建议压缩 (水位 > 75%, 非强制)
            "force"       — 强制压缩 (水位 > 90%)

        防抖: 每 _min_compaction_interval 步只触发一次建议压缩。
        """
        if step - self._last_compaction_step < self._min_compaction_interval:
            # 刚压缩过, 等水位稳定
            return None

        tokens = self.estimate_tokens(messages, model_name=model_name)
        window = self.get_window_size(model_name)
        ratio = tokens / window if window > 0 else 0

        if ratio >= _CRITICAL_WATERMARK:
            return "force"
        if ratio >= _SAFE_WATERMARK:
            return "suggest"
        return None

    def record_compaction(self, step: int) -> None:
        """记录一次压缩的发生 (用于防抖)。"""
        self._last_compaction_step = step

    def mark_dirty(self) -> None:
        """标记 token estimate cache为脏 (messages 变化后调用)。"""
        self._token_estimate_dirty = True

    def get_watermark_report(self, messages: list[Message], model_name: str) -> dict[str, Any]:
        """return水位报告 (供 /doctor 或调试用)。"""
        tokens = self.estimate_tokens(messages, model_name=model_name)
        window = self.get_window_size(model_name)
        ratio = tokens / window if window > 0 else 0
        return {
            "estimated_tokens": tokens,
            "window_size": window,
            "watermark": round(ratio, 3),
            "messages": len(messages),
            "status": "critical" if ratio >= _CRITICAL_WATERMARK else (
                "warning" if ratio >= _SAFE_WATERMARK else "normal"
            ),
        }


# ──────────────────────────────────────────────────────────────────────────
# 压缩prompt词
# ──────────────────────────────────────────────────────────────────────────

_COMPACT_PROMPT = """\
Summarize the conversation so far between a developer and a coding agent.
Focus on:
1. What the developer asked for (goal)
2. What files were examined or modified (paths)
3. What was accomplished or decided
4. Any unresolved issues or next steps

Be concise. Write as a structured summary, not a narrative.
Do NOT include tool outputs verbatim — just describe what was found/done.
"""

# 压缩后preserve的最近message数
_KEEP_RECENT = 4


class ModelCompactor:
    """用model生成语义digest的压缩strategy (defaultimplementation, v0.0.10)。

    压缩算法 (A4 fix):
      1. 提取 system prompt (首条 role=system)
      2. 取中间的消息 (排除 system + 最近 _KEEP_RECENT 条)
      3. 用规则折叠生成结构化摘要 (不调模型, 避免 LENGTH 时雪上加霜)
      4. 返回: [system] + [compaction_summary] + [recent_N_messages]

    timeline: 压缩本身记录为 CONTEXT_COMPACTION 事件 (由 AgentLoop 负责,
    不在 compactor 内做 —— compactor 是纯算法, 不写 timeline)。

    v0.1.0: 集成 WatermarkMonitor 水位监控
    v0.1.4 (A4): 规则折叠替代模型摘要, 避免 LENGTH 时调模型雪上加霜
    """

    __test__ = False

    def __init__(self, keep_recent: int = _KEEP_RECENT) -> None:
        self._keep_recent = keep_recent
        self._watermark = WatermarkMonitor()

    @property
    def watermark_monitor(self) -> WatermarkMonitor:
        """水位monitor器 (只读)。"""
        return self._watermark

    def compact(self, messages: list[Message], model: ModelAdapter) -> CompactResult:
        """压缩messagelist, return CompactResult。

        不修改传入的 messages —— 返回新的列表。
        """
        if len(messages) <= self._keep_recent + 2:
            # 太少, 不需要压缩
            return CompactResult(
                compressed_messages=list(messages),
                compacted_count=0,
                summary="(no compaction needed)",
            )

        # 1. 分离 system 和其他message
        system_msgs = [m for m in messages if m.role == "system"]
        others = [m for m in messages if m.role != "system"]

        if len(others) <= self._keep_recent:
            return CompactResult(
                compressed_messages=list(messages),
                compacted_count=0,
                summary="(no compaction needed)",
            )

        # 2. 取需要压缩的中间部分 + 最近 N 条
        to_compact = others[: -self._keep_recent]
        recent = others[-self._keep_recent:]

        # v2 fix (B1): 用 tool_call_id 配对算法确保不split tool_call/result。
        # 收集 recent 中所有 tool_result reference的 tool_call_id
        orphaned_tool_ids: set[str] = set()
        for m in recent:
            if m.role == "tool" and m.tool_call_id:
                orphaned_tool_ids.add(m.tool_call_id)

        if orphaned_tool_ids:
            # 从边界向前找, 找出包含这些 tool_call_id 的最早 assistant message
            recent_start_idx = len(others) - self._keep_recent
            adjusted_start = recent_start_idx
            for i in range(recent_start_idx - 1, -1, -1):
                msg = others[i]
                if msg.role == "assistant" and msg.tool_calls:
                    # check这个 assistant 的 tool_calls 是否包含 orphaned tool_call_id
                    has_match = any(tc.id in orphaned_tool_ids for tc in msg.tool_calls)
                    if has_match:
                        adjusted_start = i
                        # 继续向前找, 确保包含所有中间message (可能多个 assistant 块交错)
                    # 即使不匹配也要继续向前扫描, 因为 tool_result 可能跨多条 assistant
                # 还要check tool message: 如果找到另一个 tool_result reference了不同的 id 集, 继续向前
            # 重新切分
            to_compact = others[:adjusted_start]
            recent = others[adjusted_start:]

        # 3. 生成语义digest (A4: rule折叠, 不调model)
        summary = self._generate_summary(to_compact, model)

        # 4. construct压缩后的messagelist
        compaction_msg = Message(
            role="system",
            content=(
                f"[CONVERSATION HISTORY SUMMARY — compacted {len(to_compact)} messages]\n"
                f"{summary}\n"
                f"[/SUMMARY — full timeline preserved in Verifiability §6.1]"
            ),
        )
        # M7: Deduplicate system messages — remove any prior compaction summaries
        filtered_system = [
            m for m in system_msgs
            if "[CONVERSATION HISTORY SUMMARY" not in (m.content or "")
        ]
        compressed = list(filtered_system) + [compaction_msg] + recent

        # O1: mark token estimate dirty after compaction
        self._watermark.mark_dirty()

        return CompactResult(
            compressed_messages=compressed,
            compacted_count=len(to_compact),
            summary=summary,
        )

    def _generate_summary(self, messages: list[Message], model: ModelAdapter) -> str:
        """用rule折叠生成结构化digest (A4 fix: 不再调model, 避免 LENGTH 雪上加霜)。

        规则折叠算法:
          1. 统计每类 role 的消息数
          2. 提取文件名操作 (read/write/edit/grep 命令的目标路径)
          3. 构建结构化摘要: 什么角色做了什么事
        """
        if not messages:
            return "(no messages to summarize)"

        counts: dict[str, int] = {}
        file_ops: list[str] = []
        bash_cmds: list[str] = []
        key_decisions: list[str] = []

        for m in messages:
            role = m.role or "unknown"
            counts[role] = counts.get(role, 0) + 1
            content = m.content or ""

            # 提取fileoperationpath
            if m.tool_calls:
                for tc in m.tool_calls:
                    if tc.tool_id in ("read_file", "write_file", "edit_file", "grep"):
                        path = tc.args.get("path", "") or tc.args.get("file_path", "")
                        if path:
                            file_ops.append(f"{tc.tool_id}:{path}")
                    elif tc.tool_id == "bash":
                        cmd = tc.args.get("command", "")[:80]
                        if cmd:
                            bash_cmds.append(cmd)

            # 提取关键决策 (user message或重要 assistant reply)
            if role == "user" and content:
                first_line = content.split("\n")[0][:120]
                key_decisions.append(f"user: {first_line}")

        # 去重 + truncate
        file_ops = list(dict.fromkeys(file_ops))[:10]
        bash_cmds = list(dict.fromkeys(bash_cmds))[:5]
        key_decisions = list(dict.fromkeys(key_decisions))[:5]

        # build结构化digest
        parts: list[str] = []
        parts.append(f"Messages: {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}")
        if file_ops:
            parts.append(f"Files: {', '.join(file_ops)}")
        if bash_cmds:
            parts.append(f"Commands: {'; '.join(bash_cmds)}")
        if key_decisions:
            parts.append(f"Decisions: {' | '.join(key_decisions)}")

        return " | ".join(parts)