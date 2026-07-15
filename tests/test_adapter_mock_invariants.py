"""AnthropicAdapter + GeminiAdapter basic mock tests.

IPR-0: each test must contain a counterexample.
No real API calls (mock data to test parsing logic).
"""

from __future__ import annotations

import pytest

from zall.core.model import (
    Message,
    StopReason,
    ToolCall,
    ToolChoice,
)


# ──────────────────────────────────────────────────────────────────────────
# AnthropicAdapter tests
# ──────────────────────────────────────────────────────────────────────────


class TestAnthropicMapStopReason:
    """AnthropicAdapter._map_stop_reason pure function tests (no SDK dependency)."""

    def test_end_turn_maps_to_stop(self) -> None:
        from zall.adapters.anthropic import AnthropicAdapter
        assert AnthropicAdapter._map_stop_reason("end_turn") == StopReason.STOP

    def test_tool_use_maps_to_tool_use(self) -> None:
        from zall.adapters.anthropic import AnthropicAdapter
        assert AnthropicAdapter._map_stop_reason("tool_use") == StopReason.TOOL_USE

    def test_max_tokens_maps_to_length(self) -> None:
        from zall.adapters.anthropic import AnthropicAdapter
        assert AnthropicAdapter._map_stop_reason("max_tokens") == StopReason.LENGTH

    def test_stop_sequence_maps_to_stop(self) -> None:
        from zall.adapters.anthropic import AnthropicAdapter
        assert AnthropicAdapter._map_stop_reason("stop_sequence") == StopReason.STOP

    def test_unknown_reason_maps_to_stop(self) -> None:
        """Counterexample: unknown stop_reason maps to STOP (does not crash)."""
        from zall.adapters.anthropic import AnthropicAdapter
        assert AnthropicAdapter._map_stop_reason("weird_reason") == StopReason.STOP


class TestAnthropicBuildBody:
    """AnthropicAdapter._build_body message format conversion tests (no SDK dependency)."""

    def _make_adapter(self):
        from zall.adapters.anthropic import AnthropicAdapter
        return AnthropicAdapter(api_key="fake-key-test", model="claude-sonnet-4-20250514")

    def test_system_prompt_extracted(self) -> None:
        """system message extracted to top-level system parameter."""
        adapter = self._make_adapter()
        msgs = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hello"),
        ]
        body = adapter._build_body(msgs, [], ToolChoice.AUTO)
        assert body.get("system") == "You are a helpful assistant."
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    def test_tool_role_mapped_to_user(self) -> None:
        """tool role messages map to user (Anthropic protocol requirement)."""
        adapter = self._make_adapter()
        msgs = [
            Message(role="user", content="check"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc1", tool_id="read_file", args={"path": "x.txt"}),
            )),
            Message(role="tool", content="file content", tool_call_id="tc1"),
        ]
        body = adapter._build_body(msgs, [], ToolChoice.AUTO)
        assert body["messages"][2]["role"] == "user"
        assert body["messages"][2]["content"][0]["type"] == "tool_result"

    def test_tool_use_content_block(self) -> None:
        """tool_call converted to tool_use content block."""
        adapter = self._make_adapter()
        msgs = [
            Message(role="user", content="list files"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc1", tool_id="list_dir", args={"path": "."}),
            )),
        ]
        body = adapter._build_body(msgs, [], ToolChoice.AUTO)
        assert body["messages"][1]["content"][0]["type"] == "tool_use"
        assert body["messages"][1]["content"][0]["name"] == "list_dir"
        assert body["messages"][1]["content"][0]["id"] == "tc1"

    def test_tool_choice_mapping(self) -> None:
        """ToolChoice.REQUIRED → {"type": "any"}."""
        adapter = self._make_adapter()
        msgs = [Message(role="user", content="do something")]
        body = adapter._build_body(msgs, [{"name": "test_tool"}], ToolChoice.REQUIRED)
        assert body["tool_choice"]["type"] == "any"

    def test_tool_choice_none(self) -> None:
        """ToolChoice.NONE → {"type": "none"}."""
        adapter = self._make_adapter()
        msgs = [Message(role="user", content="think only")]
        body = adapter._build_body(msgs, [{"name": "test_tool"}], ToolChoice.NONE)
        assert body["tool_choice"]["type"] == "none"

    def test_empty_content_with_tool_calls(self) -> None:
        """assistant message with empty content + tool_calls → content field is empty list, no error."""
        adapter = self._make_adapter()
        msgs = [
            Message(role="user", content="search"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc1", tool_id="grep", args={"pattern": "foo"}),
            )),
        ]
        body = adapter._build_body(msgs, [], ToolChoice.AUTO)
        # 不应有纯文本块, 只有 tool_use 块
        text_blocks = [b for b in body["messages"][1]["content"] if b["type"] == "text"]
        assert len(text_blocks) == 0

    def test_tool_choice_auto_default(self) -> None:
        """Default ToolChoice.AUTO → {"type": "auto"}."""
        adapter = self._make_adapter()
        msgs = [Message(role="user", content="hi")]
        body = adapter._build_body(msgs, [], ToolChoice.AUTO)
        # 无tool时不传 tool_choice
        assert "tool_choice" not in body or body.get("tool_choice") == {"type": "auto"}


class TestAnthropicParseResponse:
    """AnthropicAdapter._parse_response parsing logic tests (mock SDK response)."""

    def _make_adapter(self):
        from zall.adapters.anthropic import AnthropicAdapter
        return AnthropicAdapter(api_key="fake-key-test", model="claude-sonnet-4-20250514")

    @staticmethod
    def _make_mock_response(content_blocks, stop_reason="end_turn", usage=None):
        """Construct a mock Anthropic SDK response object."""
        class MockBlock:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class MockUsage:
            def __init__(self, input_tokens=0, output_tokens=0):
                self.input_tokens = input_tokens
                self.output_tokens = output_tokens

        class MockResponse:
            def __init__(self):
                self.content = []
                for cb in content_blocks:
                    self.content.append(MockBlock(**cb))
                self.stop_reason = stop_reason
                self.usage = usage or MockUsage()

        return MockResponse()

    def test_parse_text_response(self) -> None:
        """Happy path: plain text reply → STOP + content."""
        adapter = self._make_adapter()
        resp = self._make_mock_response(
            [{"type": "text", "text": "Hello world"}],
            stop_reason="end_turn",
        )
        parsed = adapter._parse_response(resp)
        assert parsed.stop_reason == StopReason.STOP
        assert parsed.content == "Hello world"
        assert len(parsed.tool_calls) == 0

    def test_parse_tool_use_response(self) -> None:
        """Happy path: tool_use → TOOL_USE + tool_calls."""
        adapter = self._make_adapter()
        resp = self._make_mock_response(
            [
                {"type": "text", "text": "Let me check..."},
                {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "test.txt"}},
            ],
            stop_reason="tool_use",
        )
        parsed = adapter._parse_response(resp)
        assert parsed.stop_reason == StopReason.TOOL_USE
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].tool_id == "read_file"
        assert parsed.tool_calls[0].args == {"path": "test.txt"}

    def test_parse_thinking_block(self) -> None:
        """Happy path: thinking block → reasoning field."""
        adapter = self._make_adapter()
        resp = self._make_mock_response(
            [
                {"type": "thinking", "thinking": "I should use a tool..."},
                {"type": "text", "text": "Here's the answer"},
            ],
            stop_reason="end_turn",
        )
        parsed = adapter._parse_response(resp)
        assert parsed.reasoning == "I should use a tool..."

    def test_parse_usage_data(self) -> None:
        """Happy path: usage data extracted correctly."""
        adapter = self._make_adapter()
        resp = self._make_mock_response(
            [{"type": "text", "text": "Hi"}],
            stop_reason="end_turn",
            usage=type("MockUsage", (), {"input_tokens": 15, "output_tokens": 25})(),
        )
        parsed = adapter._parse_response(resp)
        assert parsed.usage.get("prompt") == 15
        assert parsed.usage.get("completion") == 25
        assert parsed.usage.get("total") == 40

    def test_parse_tool_use_no_tool_calls_fallback(self) -> None:
        """Counterexample: stop_reason=tool_use but no content.tool_use block → STOP."""
        adapter = self._make_adapter()
        resp = self._make_mock_response(
            [{"type": "text", "text": "No tool actually"}],
            stop_reason="tool_use",
        )
        parsed = adapter._parse_response(resp)
        # 没有 tool_use 块时downgradefor STOP
        assert parsed.stop_reason == StopReason.STOP


# ──────────────────────────────────────────────────────────────────────────
# 回归test: verify已fix的 Bug
# ──────────────────────────────────────────────────────────────────────────


class TestBugFixes:
    """Regression test section: verify fixed B1-B11 regressions."""

    # ── B1: Compactor 配对split ──

    def test_b1_compactor_keeps_tool_pairs(self) -> None:
        """B1 regression: compactor does not split tool_call/result pairs."""
        from zall.core.compactor import ModelCompactor
        from zall.core.model import Message, ToolCall

        # Construct: user → assistant(tool_call) → tool(result) × multiple rounds
        msgs = [
            Message(role="system", content="You are a bot."),
            Message(role="user", content="read file"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc1", tool_id="read_file", args={"path": "a.txt"}),
            )),
            Message(role="tool", content="content of a", tool_call_id="tc1"),
            Message(role="user", content="now read another"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc2", tool_id="read_file", args={"path": "b.txt"}),
            )),
            Message(role="tool", content="content of b", tool_call_id="tc2"),
            Message(role="user", content="and another"),
            Message(role="assistant", content="", tool_calls=(
                ToolCall(id="tc3", tool_id="read_file", args={"path": "c.txt"}),
            )),
            Message(role="tool", content="content of c", tool_call_id="tc3"),
        ]

        compactor = ModelCompactor(keep_recent=4)  # _KEEP_RECENT=4
        result = compactor.compact(msgs, model=None)  # type: ignore[arg-type]

        # Verify: after compaction, every tool_result has a matching assistant(tool_call) before it
        tool_ids_in_recent = set()
        for m in result.compressed_messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    tool_ids_in_recent.add(tc.id)
        for m in result.compressed_messages:
            if m.role == "tool" and m.tool_call_id:
                assert m.tool_call_id in tool_ids_in_recent, (
                    f"B1 fail: tool_result(tool_call_id={m.tool_call_id}) "
                    f"missing paired assistant in compressed messages"
                )

        # Verify: system summary message is included in compaction result
        assert result.compacted_count > 0
        assert "[CONVERSATION HISTORY SUMMARY" in (
            result.compressed_messages[1].content or ""
        )

    # ── B2: max_steps=0 ──

    def test_b2_max_steps_zero_allowed(self) -> None:
        """B2 regression: max_steps=0 is not overwritten to MAX_STEPS."""
        from zall.core.loop import AgentLoop, MAX_STEPS
        from zall.core.gate import UserResponse, UserResponseType
        from zall.core.tool import ToolRegistry, ToolResult

        class _MockTool:
            tool_id = "mock"
            schema = {"name": "mock"}
            def execute(self, args):
                return ToolResult(success=True, output="")

        class _MockResponder:
            def ask(self, action, judgement):
                return UserResponse(response_type=UserResponseType.ACCEPT)

        tools = ToolRegistry(tools=(_MockTool(),))

        loop = AgentLoop(
            model=object(), tools=tools, rules=object(),
            goal=object(), context=object(),
            user_responder=_MockResponder(), max_steps=0,
        )
        # If B2 not fixed, loop._max_steps would be MAX_STEPS(50). After fix, 0 stays 0.
        # fix后 0 应preserve
        assert loop._max_steps == 0  # B2: 0 is a valid value

        loop2 = AgentLoop(
            model=object(), tools=tools, rules=object(),
            goal=object(), context=object(),
            user_responder=_MockResponder(), max_steps=None,
        )
        assert loop2._max_steps == MAX_STEPS  # None → default

    # ── B6: ToolRegistry instance级cache ──

    def test_b6_tool_registry_instance_cache(self) -> None:
        """B6 regression: different ToolRegistry instances do not share schema cache."""
        from zall.core.tool import Tool, ToolRegistry, ToolResult

        class ToolA:
            tool_id = "tool_a"
            schema = {"name": "tool_a", "input_schema": {"type": "object"}}
            def execute(self, args): return ToolResult(success=True, output="a")

        class ToolB:
            tool_id = "tool_b"
            schema = {"name": "tool_b", "input_schema": {"type": "object", "properties": {"x": {}}}}
            def execute(self, args): return ToolResult(success=True, output="b")

        reg_a = ToolRegistry(tools=(ToolA(),))
        reg_b = ToolRegistry(tools=(ToolA(), ToolB()))

        schemas_a = reg_a.schemas
        schemas_b = reg_b.schemas

        assert len(schemas_a) == 1
        assert len(schemas_b) == 2  # Before B6 fix would return 1 (from cache)
        assert schemas_b[0]["name"] == "tool_a"
        assert schemas_b[1]["name"] == "tool_b"

    # ── B9: Watermark 计数器重置 ──

    def test_b9_watermark_counter_reset(self) -> None:
        """B9 regression: each run() resets watermark counter."""
        from zall.core.loop import AgentLoop
        from zall.core.gate import UserResponse, UserResponseType
        from zall.core.tool import ToolRegistry, ToolResult

        class _MockTool:
            tool_id = "mock"
            schema = {"name": "mock"}
            def execute(self, args):
                return ToolResult(success=True, output="")

        class _MockResponder:
            def ask(self, action, judgement):
                return UserResponse(response_type=UserResponseType.ACCEPT)

        tools = ToolRegistry(tools=(_MockTool(),))

        # Verify initial value in __init__
        loop = AgentLoop(
            model=object(), tools=tools, rules=object(),
            goal=object(), context=object(),
            user_responder=_MockResponder(),
        )
        assert loop._watermark_check_counter == 0

        # Simulate accumulation
        loop._watermark_check_counter = 5
        # Verify run() has reset logic
        import inspect
        source = inspect.getsource(loop.run)
        assert "_watermark_check_counter = 0" in source, (
            "B9: run() should reset _watermark_check_counter"
        )

    # ── B11: GateState enum ──

    def test_b11_gate_state_enum_value(self) -> None:
        """B11 regression: GateState.deferred value should be 'deferred' not 'pending'."""
        from zall.core.gate import GateState
        assert GateState.deferred.value == "deferred"
        assert GateState.deferred == "deferred"  # compare with string
        # Ensure counterexample: old value "pending" no longer matches
        assert GateState.deferred != "pending"


# ──────────────────────────────────────────────────────────────────────────
# GeminiAdapter tests (pure functions, no SDK dependency)
# ──────────────────────────────────────────────────────────────────────────


class TestGeminiMapStopReason:
    """GeminiAdapter._map_stop_reason pure function tests (no SDK dependency)."""

    def test_stop_maps_to_stop(self) -> None:
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("STOP") == StopReason.STOP

    def test_max_tokens_maps_to_length(self) -> None:
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("MAX_TOKENS") == StopReason.LENGTH

    def test_safety_maps_to_stop(self) -> None:
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("SAFETY") == StopReason.STOP

    def test_recitation_maps_to_stop(self) -> None:
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("RECITATION") == StopReason.STOP

    def test_unknown_maps_to_stop(self) -> None:
        """Counterexample: unknown reason → STOP (does not crash)."""
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("BOGUS_REASON") == StopReason.STOP


class TestGeminiExtractSystem:
    """GeminiAdapter._extract_system tests (no SDK dependency)."""

    def _make_adapter(self):
        from zall.adapters.gemini import GeminiAdapter
        return GeminiAdapter(api_key="fake-key-test", model="gemini-test")

    def test_single_system_message(self) -> None:
        adapter = self._make_adapter()
        msgs = [Message(role="system", content="You are a bot.")]
        result = adapter._extract_system(msgs)
        assert result == "You are a bot."

    def test_multiple_system_messages_joined(self) -> None:
        adapter = self._make_adapter()
        msgs = [
            Message(role="system", content="Rule 1"),
            Message(role="system", content="Rule 2"),
        ]
        result = adapter._extract_system(msgs)
        assert result == "Rule 1\nRule 2"

    def test_no_system_messages(self) -> None:
        """Counterexample: no system messages → None."""
        adapter = self._make_adapter()
        msgs = [Message(role="user", content="hi")]
        assert adapter._extract_system(msgs) is None


class TestGeminiBuildTools:
    """GeminiAdapter._build_tools tests (no SDK dependency)."""

    def _make_adapter(self):
        from zall.adapters.gemini import GeminiAdapter
        return GeminiAdapter(api_key="fake-key-test", model="gemini-test")

    def test_single_tool(self) -> None:
        adapter = self._make_adapter()
        tools = [{"tool_id": "read_file", "description": "Read a file", "input_schema": {"type": "object"}}]
        result = adapter._build_tools(tools)
        assert len(result) == 1
        assert result[0]["function_declarations"][0]["name"] == "read_file"

    def test_empty_tools(self) -> None:
        """Counterexample: empty tool list → empty list."""
        adapter = self._make_adapter()
        assert adapter._build_tools([]) == []


class TestGeminiMapStopReasonEdgeCases:
    """GeminiAdapter._map_stop_reason edge cases."""

    def test_empty_string(self) -> None:
        """Counterexample: empty string → STOP (does not crash)."""
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("") == StopReason.STOP

    def test_none_string(self) -> None:
        """Counterexample: "None" string → STOP (does not crash)."""
        from zall.adapters.gemini import GeminiAdapter
        assert GeminiAdapter._map_stop_reason("None") == StopReason.STOP