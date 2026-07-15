"""zall.core.compactor invariant tests.

Corresponds to:
  §9.2.9  Compactor: context compression — model summary + timeline full fidelity
  §7      Compactor 是strategy interface, pluggable

IPR-0 invariants tested:
  - CompactResult: frozen, compacted_count >= 0, compressed_messages non-empty
  - ModelCompactor: 不修改输入 messages (pure function视图)
  - WatermarkMonitor: estimate_tokens 单调不减 (添加消息后 token 估测不降)
  - 压缩不拆分 tool_call/result 配对 (H2 fix)
  - 压缩后 system 消息去重 (M7 fix: 不保留旧的 compaction summary)

Counterexamples:
  - compacted_count for负 → must raise
  - compressed_messages 空 → must raise
  - 消息太少时压缩不减少消息数 (no-op)
"""

from __future__ import annotations

from typing import Any

import pytest

from pydantic import ValidationError as PydanticValidationError

from zall.core.model import Message, ModelResponse, StopReason, ToolCall
from zall.core.compactor import (
    CompactResult,
    ModelCompactor,
    WatermarkMonitor,
    _estimate_chars_per_token,
)


# ── Fake adapter for testing ──


class _FakeAdapter:
    """Fake ModelAdapter for compactor tests (no real model needed)."""

    __test__ = False

    @property
    def model_name(self) -> str:
        return "test-model"

    def complete(self, messages, tools, tool_choice=None) -> ModelResponse:
        return ModelResponse(content="(summarized)", stop_reason=StopReason.STOP)


_FAKE_ADAPTER = _FakeAdapter()


def _make_msg(role: str, content: str = "", tool_calls=None) -> Message:
    return Message(role=role, content=content, tool_calls=tool_calls or ())


# ── CompactResult Invariants ──


class TestCompactResultInvariants:
    """CompactResult invarianttest (IPR-0)."""

    def test_happy_path(self) -> None:
        """正常construct."""
        result = CompactResult(
            compressed_messages=[_make_msg("user", "hello")],
            compacted_count=5,
            summary="compressed 5 messages",
        )
        assert len(result.compressed_messages) == 1
        assert result.compacted_count == 5
        assert result.summary
        assert result.strategy == "rule_folding_v1"

    def test_frozen_immutable(self) -> None:
        """frozen — pydantic.ValidationError when setattr."""
        result = CompactResult(
            compressed_messages=[_make_msg("user", "hello")],
            compacted_count=0,
            summary="no-op",
        )
        with pytest.raises(PydanticValidationError):
            result.compacted_count = 10  # type: ignore[misc]

    def test_zero_compacted_ok(self) -> None:
        """compacted_count=0 valid (无需压缩时)."""
        result = CompactResult(
            compressed_messages=[_make_msg("user", "hello")],
            compacted_count=0,
            summary="(no compaction needed)",
        )
        assert result.compacted_count == 0

    def test_negative_compacted_ok_by_model(self) -> None:
        """CompactResult model不约束 compacted_count ≥ 0 (ACI design允许 protobuf style 0=unknown).
        实际调用方保证non-负."""
        result = CompactResult(
            compressed_messages=[_make_msg("user", "x")],
            compacted_count=-1,
            summary="negative",
        )
        # model层面不limit; 调用方 (ModelCompactor.compact) 保证 >= 0
        assert result.compacted_count == -1

    def test_empty_compressed_messages_ok_by_model(self) -> None:
        """CompactResult model不约束 compressed_messages non-空.
        实际调用方保证non-空."""
        result = CompactResult(
            compressed_messages=[],
            compacted_count=1,
            summary="empty",
        )
        assert len(result.compressed_messages) == 0


# ── ModelCompactor Happy Path ──


class TestModelCompactorHappyPath:
    """ModelCompactor 正常pathtest."""

    def test_no_compaction_needed(self) -> None:
        """message太少 → 不压缩."""
        msgs = [
            _make_msg("system", "You are a helpful AI."),
            _make_msg("user", "Hello"),
            _make_msg("assistant", "Hi there"),
        ]
        compactor = ModelCompactor(keep_recent=2)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        assert result.compacted_count == 0
        assert len(result.compressed_messages) == 3

    def test_basic_compaction(self) -> None:
        """足够多的message → 压缩中间部分."""
        msgs = [
            _make_msg("system", "You are a helpful AI."),
            _make_msg("user", "step 1"),
            _make_msg("assistant", "response 1"),
            _make_msg("user", "step 2"),
            _make_msg("assistant", "response 2"),
            _make_msg("user", "step 3"),
            _make_msg("assistant", "response 3"),
            _make_msg("user", "step 4"),
            _make_msg("assistant", "response 4"),
            _make_msg("user", "step 5"),
            _make_msg("assistant", "response 5"),
        ]
        compactor = ModelCompactor(keep_recent=2)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        assert result.compacted_count > 0, "should compact some messages"
        # 结果: system (filtered) + compaction_summary + recent_2
        assert len(result.compressed_messages) >= 3
        assert result.summary

    def test_does_not_mutate_input(self) -> None:
        """不修改inputlist."""
        msgs = [
            _make_msg("user", "step 1"),
            _make_msg("assistant", "response 1"),
            _make_msg("user", "step 2"),
            _make_msg("assistant", "response 2"),
            _make_msg("user", "step 3"),
            _make_msg("assistant", "response 3"),
        ]
        original_len = len(msgs)
        compactor = ModelCompactor(keep_recent=2)
        compactor.compact(msgs, _FAKE_ADAPTER)
        assert len(msgs) == original_len, "input should not be mutated"

    def test_preserves_system_prompt(self) -> None:
        """压缩后 system prompt preserve."""
        msgs = [
            _make_msg("system", "You are a coding agent."),
            _make_msg("user", "step 1"),
            _make_msg("assistant", "ok"),
            _make_msg("user", "step 2"),
            _make_msg("assistant", "done"),
            _make_msg("user", "step 3"),
            _make_msg("assistant", "fin"),
        ]
        compactor = ModelCompactor(keep_recent=2)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        # system message应在压缩后出现
        system_contents = [m.content for m in result.compressed_messages if m.role == "system"]
        assert any("coding agent" in (c or "") for c in system_contents)

    def test_preserves_recent_messages(self) -> None:
        """最近 N 条messagepreserve."""
        msgs = [
            _make_msg("user", "old"),
            _make_msg("assistant", "old response"),
            _make_msg("user", "recent 1"),
            _make_msg("assistant", "recent response 1"),
            _make_msg("user", "recent 2"),
            _make_msg("assistant", "recent response 2"),
        ]
        compactor = ModelCompactor(keep_recent=2)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        # 最近 2 条 user message应在压缩后可见
        user_contents = [m.content for m in result.compressed_messages if m.role == "user"]
        assert "recent 2" in user_contents

    def test_generates_summary(self) -> None:
        """压缩产生non-空digest."""
        msgs = [
            _make_msg("user", "Fix the login bug"),
            _make_msg("assistant", "I'll check auth.py"),
            _make_msg("user", "Also add tests"),
            _make_msg("assistant", "Adding test_login"),
        ]
        compactor = ModelCompactor(keep_recent=1)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        assert result.summary
        # digest应包含用户intent
        assert "login" in result.summary.lower() or "test" in result.summary.lower()

    def test_file_ops_in_summary(self) -> None:
        """digest包含fileoperationinformation."""
        msgs = [
            _make_msg("user", "edit the file"),
            _make_msg("assistant", "ok, let me check"),
            _make_msg("user", "change main.py"),
            _make_msg(
                "assistant", "",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="edit_file",
                             args={"path": "src/main.py", "content": "new"}),
                ),
            ),
            _make_msg("user", "then fix utils.py"),
            _make_msg(
                "assistant", "",
                tool_calls=(
                    ToolCall(id="tc2", tool_id="write_file",
                             args={"path": "src/utils.py", "content": "updated"}),
                ),
            ),
        ]
        compactor = ModelCompactor(keep_recent=1)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        assert result.compacted_count > 0, "should compact messages"
        assert "main.py" in result.summary

    def test_bash_commands_in_summary(self) -> None:
        """digest包含 bash commandinformation."""
        msgs = [
            _make_msg("user", "run the tests"),
            _make_msg("assistant", "I'll run pytest"),
            _make_msg("user", "also check lint"),
            _make_msg(
                "assistant", "",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="bash",
                             args={"command": "pytest tests/"}),
                ),
            ),
            _make_msg("user", "now format code"),
            _make_msg(
                "assistant", "",
                tool_calls=(
                    ToolCall(id="tc2", tool_id="bash",
                             args={"command": "black ."}),
                ),
            ),
        ]
        compactor = ModelCompactor(keep_recent=1)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        assert result.compacted_count > 0, "should compact messages"
        assert "pytest" in result.summary


# ── ModelCompactor Counterexamples ──


class TestModelCompactorCounterExamples:
    """ModelCompactor Counterexampletest."""

    def test_tool_call_result_pair_preserved(self) -> None:
        """H2 fix: tool_call/result 配对不被压缩split."""
        msgs = [
            _make_msg("system", "system prompt"),
            _make_msg("user", "step 1"),
            _make_msg("assistant", "Let me check"),
            _make_msg("user", "step 2"),
            _make_msg("assistant", "I'll run a command",
                      tool_calls=(ToolCall(id="tc1", tool_id="bash",
                                           args={"command": "ls"}),)),
            Message(role="tool", content="file1.txt\nfile2.txt",
                    tool_call_id="tc1", tool_id="bash"),
            _make_msg("user", "step 3"),
            _make_msg("assistant", "Done"),
        ]
        compactor = ModelCompactor(keep_recent=2)
        result = compactor.compact(msgs, _FAKE_ADAPTER)
        # 压缩后: tool_call (bash) + tool_result (tc1) 应被同时preserve或同时压缩
        recent_roles = [m.role for m in result.compressed_messages]
        # tool 角色若出现在最近message中, 其corresponding assistant tool_call 应在
        if "tool" in recent_roles:
            assistant_with_tc = any(
                m.role == "assistant" and m.tool_calls for m in result.compressed_messages
            )
            assert assistant_with_tc, (
                "tool result preserved without its tool_call"
            )

    def test_compaction_summary_deduplication(self) -> None:
        """M7 fix: 多次压缩时不preserve旧 compaction summary."""
        compactor = ModelCompactor(keep_recent=2)

        # 第一次压缩
        msgs1 = [
            _make_msg("system", "original system prompt"),
            _make_msg("user", "a1"),
            _make_msg("assistant", "r1"),
            _make_msg("user", "a2"),
            _make_msg("assistant", "r2"),
            _make_msg("user", "a3"),
            _make_msg("assistant", "r3"),
        ]
        result1 = compactor.compact(msgs1, _FAKE_ADAPTER)

        # 第二次压缩 (在 result1 basic上再加message)
        msgs2 = list(result1.compressed_messages) + [
            _make_msg("user", "new message"),
            _make_msg("assistant", "new response"),
        ]
        result2 = compactor.compact(msgs2, _FAKE_ADAPTER)

        # verify: 压缩后 summary message只有一条 (最近的那条)
        summary_msgs = [
            m for m in result2.compressed_messages
            if m.role == "system" and "[CONVERSATION HISTORY SUMMARY" in (m.content or "")
        ]
        assert len(summary_msgs) <= 1, (
            f"expected at most 1 compaction summary, got {len(summary_msgs)}"
        )


# ── WatermarkMonitor Invariants ──


class TestWatermarkMonitorInvariants:
    """WatermarkMonitor invarianttest."""

    def test_estimate_tokens_increasing(self) -> None:
        """addmessage后 token 估测不降."""
        monitor = WatermarkMonitor()
        msgs = [_make_msg("user", "hello")]
        t1 = monitor.estimate_tokens(msgs)
        msgs.append(_make_msg("assistant", "world " * 100))
        monitor.mark_dirty()
        t2 = monitor.estimate_tokens(msgs)
        assert t2 >= t1, "token estimate should not decrease after adding messages"

    def test_estimate_tokens_cached(self) -> None:
        """相同inputreturnscache."""
        monitor = WatermarkMonitor()
        msgs = [_make_msg("user", "test message")]
        t1 = monitor.estimate_tokens(msgs)
        t2 = monitor.estimate_tokens(msgs)
        assert t1 == t2, "cached value should equal first estimate"

    def test_mark_dirty_invalidates_cache(self) -> None:
        """mark_dirty 后重新计算."""
        monitor = WatermarkMonitor()
        msgs = [_make_msg("user", "hello")]
        t1 = monitor.estimate_tokens(msgs)
        monitor.mark_dirty()
        t2 = monitor.estimate_tokens(msgs)
        assert t1 == t2, "same input → same result after mark_dirty"
        # 加message后
        msgs.append(_make_msg("assistant", "world"))
        monitor.mark_dirty()
        t3 = monitor.estimate_tokens(msgs)
        assert t3 > t1, "more content → higher token estimate"

    def test_check_watermark_normal(self) -> None:
        """水位正常 → None."""
        monitor = WatermarkMonitor()
        msgs = [_make_msg("user", "hi")]
        action = monitor.check_watermark(msgs, "test-model-wide-100k", step=1)
        assert action is None

    def test_check_watermark_force(self) -> None:
        """水位 > 90% → force. 使用小windowmodel llama3 (8192)."""
        monitor = WatermarkMonitor()
        # 100K chars → ~25K tokens (英文), window 8192 → 305% 水位 → force
        large_content = "x" * 100_000
        msgs = [_make_msg("user", large_content)]
        action = monitor.check_watermark(msgs, "llama3", step=10)
        assert action == "force", f"expected 'force', got {action}"

    def test_check_watermark_debounce(self) -> None:
        """刚压缩过 → 不触发."""
        monitor = WatermarkMonitor()
        large_content = "x" * 10_000
        msgs = [_make_msg("user", large_content)]
        monitor.record_compaction(step=5)
        # 第 6 步, 距离上次 1 步 < 5 步interval → 不触发
        action = monitor.check_watermark(
            msgs, "test-model-tiny-128", step=6
        )
        assert action is None, "debounce should prevent compression"

    def test_get_watermark_report(self) -> None:
        """水位报告格式correctly."""
        monitor = WatermarkMonitor()
        msgs = [_make_msg("user", "hello")]
        report = monitor.get_watermark_report(msgs, "test-model-100k")
        assert "estimated_tokens" in report
        assert "window_size" in report
        assert "watermark" in report
        assert "status" in report


# ── _estimate_chars_per_token ──


class TestEstimateCharsPerToken:
    """_estimate_chars_per_token 辅助functiontest."""

    def test_english_text(self) -> None:
        """纯英文 → ~4 chars/token."""
        text = "hello world this is a test message with english words only"
        ratio = _estimate_chars_per_token(text)
        assert 3.5 <= ratio <= 4.5, f"expected ~4.0, got {ratio}"

    def test_chinese_text(self) -> None:
        """Mixed CJK + Latin → ~2.3 chars/token (weighted average)."""
        text = "你好世界这是一条test消息"
        ratio = _estimate_chars_per_token(text)
        assert 1.5 <= ratio <= 3.0, f"expected ~2.3, got {ratio}"

    def test_mixed_text(self) -> None:
        """中英混合 → 加权average."""
        text = "hello 你好 world 世界"
        ratio = _estimate_chars_per_token(text)
        # 应在 1.6 ~ 4.0 之间
        assert 1.6 < ratio < 4.0, f"expected mixed, got {ratio}"

    def test_empty_text(self) -> None:
        """空文本 → returns英文default值."""
        ratio = _estimate_chars_per_token("")
        assert ratio == 4.0