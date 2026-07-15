"""ModelAdapter interface invariant test (DESIGN.md PR-3 + §0 PR-0).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.model import (
    Message,
    ModelAdapter,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _FakeAdapter:
    """ModelAdapter stub, for testing."""

    __test__ = False

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(
        self,
        messages: list[Message],
        tools: list[dict],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        return ModelResponse(
            content="hello",
            stop_reason=StopReason.STOP,
            usage={"prompt": 10, "completion": 5},
        )


# ──────────────────────────────────────────────────────────────────────────
# StopReason invariants
# ──────────────────────────────────────────────────────────────────────────


class TestStopReasonInvariants:
    """StopReason 三态invariant."""

    def test_three_reasons_only(self) -> None:
        """Counterexample: StopReason 只有 3 种 (stop / tool_use / length).

        与 §3.2.2 TerminationState 三态哲学一致, 不许加第 4 种.
        """
        reasons = {StopReason.STOP, StopReason.TOOL_USE, StopReason.LENGTH}
        assert len(reasons) == 3


# ──────────────────────────────────────────────────────────────────────────
# ToolChoice invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolChoiceInvariants:
    """ToolChoice invariant."""

    def test_three_choices_only(self) -> None:
        """Counterexample: ToolChoice 只有 3 种 (auto / required / none)."""
        choices = {ToolChoice.AUTO, ToolChoice.REQUIRED, ToolChoice.NONE}
        assert len(choices) == 3


# ──────────────────────────────────────────────────────────────────────────
# ToolCall invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolCallInvariants:
    """ToolCall invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid ToolCall constructable."""
        tc = ToolCall(id="tc1", tool_id="bash", args={"command": "ls"})
        assert tc.id == "tc1"
        assert tc.tool_id == "bash"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 tool_id → must raise."""
        tc = ToolCall(id="tc1", tool_id="bash")
        with pytest.raises(ValidationError):
            tc.tool_id = "read_file"  # type: ignore[misc]

    def test_tool_calls_is_tuple_in_message(self) -> None:
        """Happy path: Message.tool_calls 是 tuple (immutable)."""
        tc = ToolCall(id="tc1", tool_id="bash")
        msg = Message.assistant(content="running bash", tool_calls=(tc,))
        assert isinstance(msg.tool_calls, tuple)
        assert not hasattr(msg.tool_calls, "append")


# ──────────────────────────────────────────────────────────────────────────
# Message invariants
# ──────────────────────────────────────────────────────────────────────────


class TestMessageInvariants:
    """Message invariant."""

    def test_user_message(self) -> None:
        """Happy path: user messageconstructable."""
        msg = Message.user("fix the bug")
        assert msg.role == "user"
        assert msg.content == "fix the bug"

    def test_assistant_message_with_tool_calls(self) -> None:
        """Happy path: assistant message带 tool_calls."""
        tc = ToolCall(id="tc1", tool_id="bash", args={"command": "ls"})
        msg = Message.assistant(content="running ls", tool_calls=(tc,))
        assert msg.role == "assistant"
        assert len(msg.tool_calls) == 1

    def test_tool_result_message(self) -> None:
        """Happy path: tool 结果回灌message."""
        msg = Message.tool_result(tool_call_id="tc1", content="file1.py\nfile2.py")
        assert msg.role == "tool"
        assert msg.tool_call_id == "tc1"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 content → must raise."""
        msg = Message.user("original")
        with pytest.raises(ValidationError):
            msg.content = "tampered"  # type: ignore[misc]

    def test_tool_result_without_call_id_raises(self) -> None:
        """Counterexample: role="tool" 但 tool_call_id=None → must raise (回灌歧义).

        §PR-0: 回灌须指明对应哪个 tool_call, 否则 Agent Loop 不知道
        把结果塞给哪个 ToolCall.
        """
        with pytest.raises(ValidationError, match="tool_call_id"):
            Message(role="tool", content="result", tool_call_id=None)


# ──────────────────────────────────────────────────────────────────────────
# ModelResponse invariants
# ──────────────────────────────────────────────────────────────────────────


class TestModelResponseInvariants:
    """ModelResponse invariant."""

    def test_stop_with_no_tool_calls_ok(self) -> None:
        """Happy path: stop_reason=STOP + 无 tool_calls → valid (纯文本reply)."""
        resp = ModelResponse(content="done", stop_reason=StopReason.STOP)
        assert resp.stop_reason == StopReason.STOP
        assert len(resp.tool_calls) == 0

    def test_tool_use_with_tool_calls_ok(self) -> None:
        """Happy path: stop_reason=TOOL_USE + 有 tool_calls → valid."""
        tc = ToolCall(id="tc1", tool_id="bash")
        resp = ModelResponse(
            content="", tool_calls=(tc,), stop_reason=StopReason.TOOL_USE
        )
        assert resp.stop_reason == StopReason.TOOL_USE
        assert len(resp.tool_calls) == 1

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 content → must raise."""
        resp = ModelResponse(content="x", stop_reason=StopReason.STOP)
        with pytest.raises(ValidationError):
            resp.content = "tampered"  # type: ignore[misc]

    def test_usage_dict_known_open(self) -> None:
        """Known OPEN: usage dict 可变 (与 Action.args 同型, 不假装)."""
        resp = ModelResponse(content="x", stop_reason=StopReason.STOP)
        assert isinstance(resp.usage, dict)

    def test_raw_dict_known_open(self) -> None:
        """Known OPEN: raw dict 可变 (调试用, 不参与逻辑, 不假装)."""
        resp = ModelResponse(content="x", stop_reason=StopReason.STOP)
        assert isinstance(resp.raw, dict)


# ──────────────────────────────────────────────────────────────────────────
# ModelAdapter Protocol invariants
# ──────────────────────────────────────────────────────────────────────────


class TestModelAdapterProtocolInvariants:
    """ModelAdapter Protocol invariant."""

    def test_fake_adapter_is_model_adapter(self) -> None:
        """Happy path: _FakeAdapter 满足 ModelAdapter Protocol."""
        assert isinstance(_FakeAdapter(), ModelAdapter)

    def test_bad_object_not_model_adapter(self) -> None:
        """Counterexample: 缺 complete 的对象not ModelAdapter."""

        class _Bad:
            @property
            def model_name(self) -> str:
                return "x"

        assert not isinstance(_Bad(), ModelAdapter)

    def test_complete_returns_model_response(self) -> None:
        """Happy path: complete returns ModelResponse."""
        adapter = _FakeAdapter()
        resp = adapter.complete(messages=[Message.user("hi")], tools=[])
        assert isinstance(resp, ModelResponse)
        assert resp.stop_reason == StopReason.STOP
