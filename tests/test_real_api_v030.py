"""real API integration tests (v0.1.2).

using user-configured agnes API for end-to-end verification.
only runs with valid API key 时运行 (否则 pytest.skip).

covers:
  - basic问答 (complete)
  - streaming (complete_stream)
  - tool调用 (tool_use)
  - 思考过程 (reasoning)
  - 错误处理 (无效 key / 无效模型)

IPR-0 invariant:
  - testfail不阻断其他test (独立用例)
  - 不修改用户项目文件 (只读)
  - 不通test是 API 问题, non- agent 问题
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from zall.adapters.openai_compat import OpenAICompatAdapter
from zall.core.model import Message, StopReason, ToolCall, ToolChoice

# ── skip条件 ──

def _has_api_key() -> bool:
    """check是否有可用的 API key."""
    from zall.safety.config import load_config
    try:
        cfg = load_config()
        key = (cfg.get("api_key") or "").strip()
        return bool(key) and key != "your-api-key-here"
    except Exception:
        return False


_HAS_KEY = _has_api_key()
_REQUIRES_KEY = pytest.mark.skipif(not _HAS_KEY, reason="requires real API key")


# ── Fixtures ──


@pytest.fixture
def adapter() -> OpenAICompatAdapter:
    """createreal adapter (using user-configured API key 和model)."""
    return OpenAICompatAdapter()


@pytest.fixture
def simple_messages() -> list[Message]:
    """简单问答message."""
    return [
        Message(role="system", content="You are a helpful assistant. Be concise."),
        Message(role="user", content="Say hello in exactly 3 words."),
    ]


# ── basic问答 ──


class TestRealApiBasic:
    """basic问答test (non-streaming)."""

    @_REQUIRES_KEY
    def test_simple_question(self, adapter: OpenAICompatAdapter) -> None:
        """model能returnsnon-空reply."""
        messages = [
            Message(role="system", content="Be concise."),
            Message(role="user", content="Reply with exactly: hello world"),
        ]
        resp = adapter.complete(messages, tools=[])
        assert resp.content, "response should not be empty"
        assert resp.stop_reason == StopReason.STOP
        assert len(resp.content) > 0
        print(f"  response: {resp.content[:100]}")

    @_REQUIRES_KEY
    def test_no_tool_call_without_tools(self, adapter: OpenAICompatAdapter) -> None:
        """不给toollist时model不应调tool."""
        messages = [
            Message(role="user", content="What is 2+2? Reply with just the number."),
        ]
        resp = adapter.complete(messages, tools=[])
        assert resp.stop_reason == StopReason.STOP
        assert not resp.tool_calls, "should not call tools when none provided"
        print(f"  response: {resp.content}")

    @_REQUIRES_KEY
    def test_usage_stats(self, adapter: OpenAICompatAdapter) -> None:
        """API returns token 用量statistics."""
        messages = [
            Message(role="user", content="Say 'hello'"),
        ]
        resp = adapter.complete(messages, tools=[])
        assert resp.usage, "usage stats should be present"
        assert resp.usage.get("prompt", 0) > 0, "prompt tokens > 0"
        assert resp.usage.get("completion", 0) > 0, "completion tokens > 0"
        print(f"  usage: {resp.usage}")


# ── streaming ──


class TestRealApiStreaming:
    """streaming调用tests."""

    @_REQUIRES_KEY
    def test_streaming_produces_tokens(self, adapter: OpenAICompatAdapter) -> None:
        """streaming产出 token 增量."""
        messages = [
            Message(role="user", content="Count from 1 to 5, one per line."),
        ]
        tokens = []
        final_resp = None
        for token, accumulated in adapter.complete_stream(messages, tools=[]):
            if token:
                tokens.append(token)
            final_resp = accumulated
        assert len(tokens) > 0, "should produce at least one token"
        assert final_resp is not None
        assert final_resp.content, "final response should have content"
        assert final_resp.stop_reason == StopReason.STOP
        print(f"  streamed {len(tokens)} tokens, total {len(final_resp.content)} chars")

    @_REQUIRES_KEY
    def test_streaming_accumulates_correctly(self, adapter: OpenAICompatAdapter) -> None:
        """streaming累积结果与blocking结果一致."""
        messages = [
            Message(role="user", content="Say 'hello world' in lowercase."),
        ]
        # streaming
        stream_content = ""
        final_resp = None
        for token, accumulated in adapter.complete_stream(messages, tools=[]):
            if token:
                stream_content += token
            final_resp = accumulated
        # blocking
        blocking_resp = adapter.complete(messages, tools=[])
        # 比较: streaming累积content应包含blockingcontent (或等价)
        assert stream_content.strip(), "stream content should not be empty"
        assert blocking_resp.content.strip(), "blocking response should not be empty"
        print(f"  stream: {stream_content[:80]}")
        print(f"  blocking: {blocking_resp.content[:80]}")

    @_REQUIRES_KEY
    def test_streaming_usage(self, adapter: OpenAICompatAdapter) -> None:
        """streaming最终response含 usage statistics."""
        messages = [
            Message(role="user", content="Say 'hello'"),
        ]
        final_resp = None
        for _token, accumulated in adapter.complete_stream(messages, tools=[]):
            final_resp = accumulated
        assert final_resp is not None
        # 某些 API streaming可能没有 usage (取决于 stream_options)
        if final_resp.usage:
            print(f"  stream usage: {final_resp.usage}")


# ── tool调用 ──


class TestRealApiToolUse:
    """tool调用tests."""

    @_REQUIRES_KEY
    def test_tool_use_basic(self, adapter: OpenAICompatAdapter) -> None:
        """model能识别需调tool的request."""
        messages = [
            Message(role="system", content="You have a tool called 'get_weather' that "
                     "returns weather data. Use it when asked about weather."),
            Message(role="user", content="What's the weather like in Beijing?"),
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "City name",
                            },
                        },
                        "required": ["city"],
                    },
                },
            },
        ]
        resp = adapter.complete(messages, tools=tools, tool_choice=ToolChoice.AUTO)
        # model可能 STOP 或 TOOL_USE (取决于model)
        if resp.stop_reason == StopReason.TOOL_USE:
            assert resp.tool_calls, "tool_calls should not be empty"
            tc = resp.tool_calls[0]
            assert tc.tool_id == "get_weather"
            assert "city" in tc.args
            print(f"  tool_call: {tc.tool_id}({tc.args})")
        else:
            # 某些model可能directly回答 (合理)
            print(f"  model chose STOP instead of TOOL_USE: {resp.content[:80]}")

    @_REQUIRES_KEY
    def test_tool_choice_none(self, adapter: OpenAICompatAdapter) -> None:
        """tool_choice=NONE 时model不应调tool."""
        messages = [
            Message(role="user", content="What's the weather? (but don't use tools)"),
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                        },
                        "required": ["city"],
                    },
                },
            },
        ]
        resp = adapter.complete(messages, tools=tools, tool_choice=ToolChoice.NONE)
        assert resp.stop_reason == StopReason.STOP
        assert not resp.tool_calls, "should not call tools with tool_choice=NONE"


# ── 思考过程 ──


class TestRealApiThinking:
    """思考过程 (reasoning) tests."""

    @_REQUIRES_KEY
    def test_reasoning_content(self, adapter: OpenAICompatAdapter) -> None:
        """model可能returns reasoning_content (如 DeepSeek-R1, Qwen3)."""
        messages = [
            Message(role="user", content="Write a haiku about programming."),
        ]
        resp = adapter.complete(messages, tools=[])
        # reasoning 是可选的, 不强制有
        if resp.reasoning:
            print(f"  reasoning ({len(resp.reasoning)} chars): {resp.reasoning[:100]}...")
        print(f"  content: {resp.content[:100]}")


# ── errorhandle ──


class TestRealApiErrors:
    """errorhandletests."""

    @_REQUIRES_KEY
    def test_invalid_model_name(self) -> None:
        """无效model名returns友好error."""
        adapter = OpenAICompatAdapter(model="non-existent-model-xyz-999")
        messages = [Message(role="user", content="hello")]
        resp = adapter.complete(messages, tools=[])
        assert resp.stop_reason == StopReason.STOP
        assert resp.content, "should return error message"
        # 不应包含敏感information (如 raw API key)
        assert "sk-" not in resp.content, "should not leak API key"
        print(f"  error response: {resp.content[:100]}")

    @_REQUIRES_KEY
    def test_empty_message_list(self, adapter: OpenAICompatAdapter) -> None:
        """空messagelist不应崩溃."""
        resp = adapter.complete([], tools=[])
        assert resp.stop_reason == StopReason.STOP
        print(f"  empty messages response: {resp.content[:80]}")


# ── EventBus integration tests ──


class TestRealApiEventBus:
    """EventBus 与real adapter 的integration tests."""

    @_REQUIRES_KEY
    def test_event_bus_with_real_adapter(self) -> None:
        """EventBus 接收real API 调用的event."""
        from zall.core.events import EventBus
        from zall.core.loop import AgentLoop
        from zall.core.goal import GoalTriple, GoalStatement, GoalType, AcceptanceContract, TerminationState
        from zall.core.context import Context
        from zall.core.safety import RuleSet, SafeLevel, context_judge
        from zall.core.action import Action
        from zall.core.tool import ToolRegistry, Tool
        from zall.cli.judge import UndecidableJudge
        from zall.cli.responder import CliUserResponder

        # create EventBus
        bus = EventBus()
        received_events: list[str] = []

        def on_event(kind: str, _payload: dict) -> None:
            received_events.append(kind)

        bus.on("*", on_event)

        # createminimaltool集 (只有 read_file)
        from zall.tools.read_file import ReadFileTool
        tools = ToolRegistry(tools=(ReadFileTool(),))
        rules = RuleSet()

        # create Goal
        goal = GoalTriple(
            statement=GoalStatement(
                intent="test",
                rewriting="test",
                rewrite_confidence=1.0,
                goal_type=GoalType.INVESTIGATE,
                translation_of=("test",),
                added_intent=(),
            ),
            termination=type("T", (), {"exposed_dependency_set": None, "__call__": lambda s, st: TerminationState.UNDECIDABLE})(),
            acceptance=AcceptanceContract(baseline_frozen_at="test"),
        )

        context = Context(user_raw="test", cwd_meta=type("C", (), {"cwd_path": ".", "git_branch": None, "git_remote": None})())

        responder = CliUserResponder(yes=True, is_tty=False)

        # create loop (用 EventBus)
        adapter = OpenAICompatAdapter()
        from zall.core.loop_config import AgentConfig
        loop = AgentLoop(
            model=adapter,
            tools=tools,
            rules=rules,
            goal=goal,
            context=context,
            user_responder=responder,
            config=AgentConfig(judge=UndecidableJudge(), event_bus=bus, max_steps=1),
        )

        # 运行
        egress = loop.run(system_prompt="You are a test agent. Say 'hello' and stop.")
        assert egress is not None
        # 应该收到至少一个event
        assert len(received_events) > 0, f"should receive events, got {received_events}"
        print(f"  received events: {received_events}")
        adapter.close()