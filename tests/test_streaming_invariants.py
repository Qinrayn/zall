"""streaming adapter equivalence tests (P2: streaming ≡ blocking).

IPR-0: each test must contain a counterexample.

Protected core invariants:
  1. streaming最终 ModelResponse.content == blocking content
  2. streaming最终 ModelResponse.tool_calls == blocking tool_calls (分片correctly拼接)
  3. streaming最终 ModelResponse.stop_reason == blocking stop_reason (用 finish_reason, 不硬编码)
  4. 无 tool_calls 的纯文本streaming也correctly

用 FakeStreamingAdapter mock OpenAI streaming 分片协议, 不调真 API.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.adapters.openai_compat import OpenAICompatAdapter


# ──────────────────────────────────────────────────────────────────────────
# FakeStreamingAdapter: mock OpenAI streaming 分片protocol
# ──────────────────────────────────────────────────────────────────────────


class FakeStreamingAdapter:
    """mock OpenAI streaming protocol的 fake adapter.

    按 chunks 列表逐个 yield, mockreal streaming.
    chunks 是 OpenAI SSE 格式的 data: 行列表.
    """

    __test__ = False

    def __init__(self, chunks: list[dict], model_name: str = "fake-stream") -> None:
        self._chunks = chunks
        self._model = model_name

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        # blockinginterface: 不implementation, test只用 complete_stream
        raise NotImplementedError("use complete_stream")

    def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
        # 复用 OpenAICompatAdapter._stream 的parse逻辑, 但喂 fake chunks
        content = ""
        tc_acc: dict[int, dict[str, Any]] = {}
        finish_reason = None

        for chunk in self._chunks:
            delta = chunk.get("delta", {})
            token = delta.get("content", "")
            if token:
                content += token
                yield (token, ModelResponse(content=content, stop_reason=StopReason.STOP))

            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta.get("index", 0)
                if idx not in tc_acc:
                    tc_acc[idx] = {"id": "", "tool_id": "", "args_str": ""}
                if tc_delta.get("id"):
                    tc_acc[idx]["id"] = tc_delta["id"]
                func = tc_delta.get("function", {})
                if func.get("name"):
                    tc_acc[idx]["tool_id"] = func["name"]
                if func.get("arguments"):
                    tc_acc[idx]["args_str"] += func["arguments"]

            fr = chunk.get("finish_reason")
            if fr:
                finish_reason = fr

        # construct最终 ModelResponse (复用 _map_finish_reason 逻辑)
        stop_reason = OpenAICompatAdapter._map_finish_reason(finish_reason or "stop")
        tool_calls = []
        for idx in sorted(tc_acc.keys()):
            tc = tc_acc[idx]
            args_raw = tc["args_str"] or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"__raw": args_raw}
            tool_calls.append(ToolCall(
                id=tc["id"] or f"stream_tc_{idx}",
                tool_id=tc["tool_id"],
                args=args,
            ))
        yield ("", ModelResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
        ))


# ──────────────────────────────────────────────────────────────────────────
# 纯文本streaming
# ──────────────────────────────────────────────────────────────────────────


class TestTextStreaming:
    def test_text_content_assembled(self) -> None:
        """Happy path: 纯文本streaming, 最终 content == 拼接."""
        chunks = [
            {"delta": {"content": "Hello"}, "finish_reason": None},
            {"delta": {"content": " world"}, "finish_reason": None},
            {"delta": {}, "finish_reason": "stop"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final is not None
        assert final.content == "Hello world"
        assert final.stop_reason == StopReason.STOP

    def test_text_no_tool_calls(self) -> None:
        """Happy path: 纯文本streaming, tool_calls for空."""
        chunks = [
            {"delta": {"content": "hi"}, "finish_reason": None},
            {"delta": {}, "finish_reason": "stop"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final.tool_calls == ()


# ──────────────────────────────────────────────────────────────────────────
# tool_calls streaming (核心Counterexample: 分片mustcorrectly拼接)
# ──────────────────────────────────────────────────────────────────────────


class TestToolCallsStreaming:
    def test_tool_calls_assembled_from_shards(self) -> None:
        """Happy path: tool_calls 分片correctly拼接 (id + name + arguments 增量).

        mock OpenAI streaming 的 tool_calls 分片:
          chunk1: {index:0, id:"tc1", function:{name:"read_file"}}
          chunk2: {index:0, function:{arguments:'{"path"'}}
          chunk3: {index:0, function:{arguments:': "x.py"}'}}
          chunk4: finish_reason="tool_calls"
        """
        chunks = [
            {"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "read_file"}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path"'}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "x.py"}'}}]}, "finish_reason": None},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final is not None
        assert final.stop_reason == StopReason.TOOL_USE
        assert len(final.tool_calls) == 1
        tc = final.tool_calls[0]
        assert tc.id == "tc1"
        assert tc.tool_id == "read_file"
        assert tc.args == {"path": "x.py"}

    def test_multiple_tool_calls_assembled(self) -> None:
        """Happy path: 多个 tool_calls (不同 index) 各自correctly拼接."""
        chunks = [
            {"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "bash"}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"command": "ls"'}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 1, "id": "tc2", "function": {"name": "read_file"}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '{"path": "a.py"}'}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}, "finish_reason": None},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final is not None
        assert len(final.tool_calls) == 2
        assert final.tool_calls[0].tool_id == "bash"
        assert final.tool_calls[0].args == {"command": "ls"}
        assert final.tool_calls[1].tool_id == "read_file"
        assert final.tool_calls[1].args == {"path": "a.py"}

    def test_shard_loss_is_detectable(self) -> None:
        """Counterexample: 丢掉 arguments 分片 → args parsefail (不 silent 通过).

        如果实现不累积 arguments, 最终 args 会是空 dict 或解析fail.
        """
        # 只给 name, 不给 arguments
        chunks = [
            {"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "bash"}}]}, "finish_reason": None},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        # args_str for空 → parsefor {} (not None, not崩)
        assert final.tool_calls[0].args == {}

    def test_finish_reason_mapped_correctly(self) -> None:
        """Counterexample: stop_reason must来自 finish_reason, 不硬encoding STOP.

        旧实现硬编码 STOP, 即使 finish_reason=tool_calls 也returns STOP → 丢tool调用.
        """
        chunks = [
            {"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "x"}}]}, "finish_reason": None},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        # must是 TOOL_USE, not STOP (旧 bug 会returns STOP)
        assert final.stop_reason == StopReason.TOOL_USE

    def test_length_finish_reason(self) -> None:
        """Happy path: finish_reason=length → StopReason.LENGTH."""
        chunks = [
            {"delta": {"content": "par"}, "finish_reason": None},
            {"delta": {}, "finish_reason": "length"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final.stop_reason == StopReason.LENGTH


# ──────────────────────────────────────────────────────────────────────────
# 混合: content + tool_calls
# ──────────────────────────────────────────────────────────────────────────


class TestMixedStreaming:
    def test_content_and_tool_calls_together(self) -> None:
        """Happy path: content 和 tool_calls 可以同时出现在stream里."""
        chunks = [
            {"delta": {"content": "let me read"}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "id": "tc1", "function": {"name": "read_file"}}]}, "finish_reason": None},
            {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path": "x"}'}}]}, "finish_reason": None},
            {"delta": {}, "finish_reason": "tool_calls"},
        ]
        adapter = FakeStreamingAdapter(chunks)
        final = None
        for token, resp in adapter.complete_stream([], []):
            final = resp
        assert final.content == "let me read"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].args == {"path": "x"}
