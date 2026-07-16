"""AutoLearn extension — Automatically learns from tool usage patterns.

Hooks:
  on_after_tool  — Records tool call patterns, detects repeated task sequences
  on_session_end — Suggests skills for repeated patterns (>2 identical tool chains)

Built-in extension for the zall self-evolving agent (Pi-style).
"""

from __future__ import annotations

import logging
from typing import Any
from collections import defaultdict

from zall.core.extension import ExtensionRegistry

_logger = logging.getLogger(__name__)


class AutoLearnExtension:
    """Records tool usage patterns and detects repeated task sequences.

    Tracks:
      - Tool call frequency (per tool_id)
      - Repeated command sequences (same tool + same args pattern)
      - Error patterns (failed tool calls)

    Stores observations in SessionMemory via the injected memory reference.
    """

    name = "auto_learn"

    def __init__(self, registry: ExtensionRegistry | None = None) -> None:
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_chains: list[list[str]] = []  # B13: 保留所有 chain
        self._current_chain: list[str] = []
        self._error_patterns: list[dict[str, Any]] = []

    @property
    def hooks(self) -> dict[str, Any]:
        return {
            "on_after_tool": self._on_after_tool,
            "on_session_end": self._on_session_end,
        }

    def _on_after_tool(self, tool_id: str, result: Any, step: int, **kwargs: Any) -> None:
        """Record tool call and detect patterns."""
        self._tool_counts[tool_id] = self._tool_counts.get(tool_id, 0) + 1
        self._current_chain.append(tool_id)

        # Record error patterns
        if hasattr(result, "success") and not result.success:
            self._error_patterns.append({
                "tool_id": tool_id,
                "step": step,
                "error": getattr(result, "error", "unknown"),
            })

    def _on_session_end(self, egress: Any, **kwargs: Any) -> None:
        """Analyze session patterns and store insights.

        B13: 保留所有 tool chain 不清空, 支持跨 session 分析。
        E2: 检测到重复 tool chain (≥3 次相同序列) 时记录到 SessionMemory。
        """
        if self._current_chain:
            self._tool_chains.append(list(self._current_chain))
            self._current_chain.clear()

        # Store insights into memory if available
        try:
            from zall.core.memory import get_session_memory
            mem = get_session_memory()

            # Record error patterns (if any)
            step_count = getattr(egress, "step_count", 0) or 0
            recent_errors = [e for e in self._error_patterns
                             if e["step"] > max(0, step_count - 5)]
            if recent_errors:
                error_summary = "; ".join(
                    f"{e['tool_id']}: {e['error'][:60]}"
                    for e in recent_errors[:3]
                )
                mem.add("error_patterns", f"Recent tool errors: {error_summary}")

            # Detect repeated tool chains (possible skill candidates)
            if len(self._tool_chains) >= 3:
                # Simple heuristic: same tool repeated in multiple sessions
                frequent_tools = {
                    tid for tid, cnt in self._tool_counts.items()
                    if cnt >= 3
                }
                if frequent_tools:
                    mem.add(
                        "project_knowledge",
                        f"Frequently used tools: {', '.join(sorted(frequent_tools))}",
                    )

            # E2: 检测重复 tool 序列 (≥3 次相同连续 pattern)
            if len(self._tool_chains) >= 3:
                from collections import Counter
                chain_tuples = [tuple(c) for c in self._tool_chains if len(c) >= 2]
                if chain_tuples:
                    repeated = [(seq, count) for seq, count in
                                Counter(chain_tuples).most_common(2)
                                if count >= 2]
                    for seq, count in repeated:
                        mem.add(
                            "project_knowledge",
                            f"Repeated tool sequence ({count}x): {' → '.join(seq)}",
                        )
        except Exception:
            _logger.warning("auto_learn: failed to store session insights", exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        """Return usage statistics for display."""
        return {
            "tool_counts": dict(self._tool_counts),
            "tool_chains": len(self._tool_chains),
            "error_patterns": len(self._error_patterns),
            "current_chain": list(self._current_chain),
        }


# Factory function for easy registration
def create_auto_learn(registry: ExtensionRegistry | None = None) -> AutoLearnExtension:
    return AutoLearnExtension(registry=registry)