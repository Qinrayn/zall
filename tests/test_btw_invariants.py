"""/btw 侧问 (kimi soul/btw.py 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-BTW-1: 主上下文纹丝不动 — 侧问前后 loop.messages 完全一致 (身份级)。
  I-BTW-2: prompt cache 对齐 — 请求前缀 == 主对话消息 + 相同工具 schema。
  I-BTW-3: DenyAll 二轮语义 — 第一轮误调工具 → 喂回拒绝结果重试;
           第二轮仍调工具 → 放弃并提示 (反例: 不无限循环)。
  I-BTW-4: 无活跃对话 / 空问题 → 友好提示不崩 (反例)。
"""

from __future__ import annotations

import io

import pytest

from zall.cli.commands import handle_slash
from zall.core.model import Message, ModelResponse, StopReason, ToolCall


class _SideAdapter:
    """记录收到的请求; 按脚本返回。"""

    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[list[Message], list[dict]]] = []

    @property
    def model_name(self) -> str:
        return "fake-side"

    def complete(self, messages, tools, tool_choice=None) -> ModelResponse:
        self.requests.append((list(messages), list(tools)))
        return self._responses.pop(0)


class _FakeRegistry:
    __test__ = False

    def __init__(self) -> None:
        class _T:
            tool_id = "echo"
            schema = {"type": "function", "function": {
                "name": "echo", "parameters": {"type": "object", "properties": {}}}}
        self.tools = (_T(),)


class _FakeLoop:
    __test__ = False

    def __init__(self, adapter) -> None:
        self._msgs = [
            Message(role="system", content="sys"),
            Message.user("main task"),
            Message.assistant(content="working on it"),
        ]
        self.model_adapter = adapter
        self._tools = _FakeRegistry()

    @property
    def messages(self) -> list[Message]:
        return list(self._msgs)


def _run_btw(loop, question: str = "what did we decide?") -> str:
    buf = io.StringIO()
    handle_slash(f"/btw {question}", {}, buf, loop)
    return buf.getvalue()


# ── I-BTW-1 + I-BTW-2 ──


def test_btw_answers_without_touching_main_context() -> None:
    adapter = _SideAdapter([
        ModelResponse(content="we decided X", stop_reason=StopReason.STOP),
    ])
    loop = _FakeLoop(adapter)
    before = [(m.role, m.content) for m in loop.messages]
    out = _run_btw(loop)
    assert "we decided X" in out
    after = [(m.role, m.content) for m in loop.messages]
    assert after == before                       # 主上下文纹丝不动
    # cache 对齐: 请求前缀 == 主对话消息, 工具 schema 同列表
    sent_msgs, sent_tools = adapter.requests[0]
    assert [(m.role, m.content) for m in sent_msgs[:3]] == before
    assert sent_msgs[3].role == "user" and "side question" in sent_msgs[3].content
    assert sent_tools and sent_tools[0]["function"]["name"] == "echo"


# ── I-BTW-3: DenyAll 二轮 ──


def test_btw_denies_tool_call_then_second_chance() -> None:
    adapter = _SideAdapter([
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id="c1", tool_id="echo", args={}),)),
        ModelResponse(content="fine, the answer is Y", stop_reason=StopReason.STOP),
    ])
    loop = _FakeLoop(adapter)
    out = _run_btw(loop)
    assert "the answer is Y" in out
    # 第二轮请求里包含拒绝的 tool 结果 (喂回)
    second_msgs, _ = adapter.requests[1]
    assert any(m.role == "tool" and "disabled for side questions" in (m.content or "")
               for m in second_msgs)


def test_btw_gives_up_after_two_tool_turns() -> None:
    """反例: 两轮都调工具 → 放弃并提示, 不无限循环、不执行任何工具。"""
    tc = ToolCall(id="c1", tool_id="echo", args={})
    adapter = _SideAdapter([
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(tc,)),
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(tc,)),
    ])
    loop = _FakeLoop(adapter)
    out = _run_btw(loop)
    assert "kept trying to call tools" in out
    assert len(adapter.requests) == 2            # 恰好两轮


# ── I-BTW-4: 边界 ──


def test_btw_requires_active_conversation() -> None:
    buf = io.StringIO()
    handle_slash("/btw hello", {}, buf, None)    # 无 loop
    assert "needs an active conversation" in buf.getvalue()


def test_btw_empty_question_shows_usage() -> None:
    adapter = _SideAdapter([])
    loop = _FakeLoop(adapter)
    buf = io.StringIO()
    handle_slash("/btw", {}, buf, loop)
    assert "usage" in buf.getvalue()
    assert adapter.requests == []                # 反例: 不发请求


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
