"""OpenAICompat Adapter invariant tests.

IPR-0: each test must contain a counterexample.
real调用test用 skipif 守护 (无 ZALL_API_KEY 则跳过).
"""

from __future__ import annotations

import json
import os

import pytest

from zall.adapters.openai_compat import OpenAICompatAdapter
from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)


# ──────────────────────────────────────────────────────────────────────────
# Mock test (no real API calls)
# ──────────────────────────────────────────────────────────────────────────


class TestOpenAICompatAdapterParsing:
    """OpenAICompat Adapter parse逻辑test (mock, no real API calls)."""

    def test_parse_stop_response(self) -> None:
        """Happy path: finish_reason=stop → STOP, 无 tool_calls."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {
                    "message": {"content": "hello world", "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.STOP
        assert resp.content == "hello world"
        assert len(resp.tool_calls) == 0
        assert resp.usage["total"] == 15

    def test_parse_tool_calls_response(self) -> None:
        """Happy path: finish_reason=tool_calls → TOOL_USE, 有 tool_calls."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_001",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"text": "hello"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.TOOL_USE
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].tool_id == "echo"
        assert resp.tool_calls[0].args == {"text": "hello"}

    def test_parse_length_response(self) -> None:
        """Happy path: finish_reason=length → LENGTH."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {"message": {"content": "truncated", "role": "assistant"}, "finish_reason": "length"}
            ],
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.LENGTH

    def test_parse_content_filter_maps_to_stop(self) -> None:
        """Happy path: content_filter → STOP (不加第 4 态, 与 v0.0.7 一致)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {"message": {"content": "", "role": "assistant"}, "finish_reason": "content_filter"}
            ],
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.STOP

    def test_parse_unknown_finish_reason_maps_to_stop(self) -> None:
        """Happy path: 未知 finish_reason → STOP (保守, does not crash)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {"message": {"content": "x", "role": "assistant"}, "finish_reason": "weird_reason"}
            ],
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.STOP

    def test_parse_empty_choices(self) -> None:
        """Counterexample: 空 choices → STOP + "[empty response]" (does not crash)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {"choices": []}
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.STOP
        assert "empty" in resp.content.lower()

    def test_parse_invalid_json_arguments_fallback(self) -> None:
        """Counterexample: arguments notvalid JSON → fallback塞 __raw (PR-0 防御).

        v0.0.5 ACI design层提过: "GLM 偶尔把 tool 参数当字符串returns → 要 json 修复".
        本testverify: non-法 JSON 不让 Loop 崩, 而是塞 __raw.
        """
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_001",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": "not valid json {{{",  # ← non-法 JSON
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        resp = adapter._parse_response(data)
        assert resp.stop_reason == StopReason.TOOL_USE
        assert len(resp.tool_calls) == 1
        # args 含 __raw 键, 值是原始non-法字符串
        assert "__raw" in resp.tool_calls[0].args
        assert resp.tool_calls[0].args["__raw"] == "not valid json {{{"

    def test_parse_empty_arguments(self) -> None:
        """Happy path: arguments forempty string → parsefor空 dict."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_001",
                                "type": "function",
                                "function": {"name": "list_dir", "arguments": ""},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        resp = adapter._parse_response(data)
        assert len(resp.tool_calls) == 1
        # empty string json.loads("") 会fail, 走fallback
        assert "__raw" in resp.tool_calls[0].args or resp.tool_calls[0].args == {}


class _FakeStreamResp:
    """mock httpx streamingresponse (iter_lines yield SSE 行)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def status_code(self):
        return 200

    def iter_lines(self):
        return iter(self._lines)


class _FakeClient:
    """mock httpx.Client (只implementation stream)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def stream(self, method, url, json=None, headers=None, timeout=None):
        # v0.0.22 P0-2: real stream path加分层 timeout, mock accept同名parameter以compatiblesign
        return _FakeStreamResp(self._lines)

    def close(self):
        pass


class TestOpenAICompatAdapterStreaming:
    """streaming SSE parsetest (mock, no real API calls).

    v0.0.21d 回归: 旧实现把 tool_calls/finish_reason 处理误嵌套在 if rtoken (reasoning)
    branch内 → 无 reasoning 的模型streaming tool_calls 永远丢失 → agent streaming模式不调tool.
    """

    def test_stream_tool_calls_without_reasoning(self) -> None:
        """Counterexample (v0.0.21d bug): 无 reasoning 的streaming tool_calls must被捕获.

        delta 全无 reasoning_content → 旧实现 if rtoken 不进入 → tool_calls 丢弃.
        """
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        sse_lines = [
            'data: {"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","function":{"name":"bash"}}]}}]}',
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\":"}}]}}]}',
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"mkdir x\\"}"}}]}}]}',
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
        adapter._client = _FakeClient(sse_lines)  # type: ignore[attr-defined]

        final = None
        for _token, resp in adapter.complete_stream(messages=[Message.user("x")], tools=[]):
            final = resp
        assert final is not None
        assert final.stop_reason == StopReason.TOOL_USE
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].tool_id == "bash"
        assert final.tool_calls[0].args == {"command": "mkdir x"}

    def test_stream_finish_reason_without_reasoning(self) -> None:
        """Counterexample: 无 reasoning 的streaming finish_reason=stop must被捕获 (旧 bug 丢 → default stop)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        sse_lines = [
            'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}',
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
        adapter._client = _FakeClient(sse_lines)  # type: ignore[attr-defined]

        final = None
        for _token, resp in adapter.complete_stream(messages=[Message.user("x")], tools=[]):
            final = resp
        assert final is not None
        assert final.stop_reason == StopReason.STOP
        assert final.content == "hi"


class TestOpenAICompatAdapterMessageConversion:
    """OpenAICompat Adapter Message converttests."""

    def test_user_message_to_openai(self) -> None:
        """Happy path: user Message → OpenAI 格式."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        msg = Message.user("hello")
        m = adapter._msg_to_openai(msg)
        assert m["role"] == "user"
        assert m["content"] == "hello"

    def test_tool_result_message_to_openai(self) -> None:
        """Happy path: tool 结果 Message → OpenAI 格式 (含 tool_call_id)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        msg = Message.tool_result(tool_call_id="tc1", content="result text")
        m = adapter._msg_to_openai(msg)
        assert m["role"] == "tool"
        assert m["tool_call_id"] == "tc1"
        assert m["content"] == "result text"

    def test_assistant_with_tool_calls_to_openai(self) -> None:
        """Happy path: assistant + tool_calls → OpenAI 格式 (arguments 是 JSON 字符串)."""
        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        tc = ToolCall(id="tc1", tool_id="echo", args={"text": "hi"})
        msg = Message.assistant(content="running echo", tool_calls=(tc,))
        m = adapter._msg_to_openai(msg)
        assert m["role"] == "assistant"
        assert len(m["tool_calls"]) == 1
        assert m["tool_calls"][0]["function"]["name"] == "echo"
        # arguments 是 JSON 字符串
        args = json.loads(m["tool_calls"][0]["function"]["arguments"])
        assert args == {"text": "hi"}


class TestOpenAICompatAdapterConfig:
    """OpenAICompat Adapter configtests."""

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: api_key 从环境variable ZALL_API_KEY read."""
        monkeypatch.setenv("ZALL_API_KEY", "test_key_123")
        adapter = OpenAICompatAdapter(model='agnes-1.5-flash')
        assert adapter._api_key == "test_key_123"

    def test_api_base_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: api_base 从环境variable ZALL_API_BASE read."""
        monkeypatch.setenv("ZALL_API_KEY", "test_key_123")
        monkeypatch.setenv("ZALL_API_BASE", "https://custom.example.com/v1")
        adapter = OpenAICompatAdapter(model='agnes-1.5-flash')
        assert adapter._api_base == "https://custom.example.com/v1"

    def test_api_key_param_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: 显式 api_key parametercovers环境variable."""
        monkeypatch.setenv("ZALL_API_KEY", "env_key")
        monkeypatch.setenv("ZALL_MODEL", "fake-model")
        adapter = OpenAICompatAdapter(api_key="param_key")
        assert adapter._api_key == "param_key"

    def test_model_name_property(self) -> None:
        """Happy path: model_name propertyreturnsmodel名."""
        adapter = OpenAICompatAdapter(api_key="fake", model="glm-4-plus")
        assert adapter.model_name == "glm-4-plus"


class TestOpenAICompatAdapterProtocol:
    """OpenAICompat Adapter 满足 ModelAdapter Protocol."""

    def test_glm_adapter_is_model_adapter(self) -> None:
        """Happy path: OpenAICompatAdapter 满足 core.ModelAdapter Protocol."""
        from zall.core.model import ModelAdapter

        adapter = OpenAICompatAdapter(api_key="fake", model="fake-model")
        assert isinstance(adapter, ModelAdapter)


# ──────────────────────────────────────────────────────────────────────────
# real调用test (需要 ZALL_API_KEY, 否则skip)
# ──────────────────────────────────────────────────────────────────────────


_HAS_API_KEY = bool(os.environ.get("ZALL_API_KEY"))


@pytest.mark.skipif(not _HAS_API_KEY, reason="ZALL_API_KEY not set")
class TestOpenAICompatRealCall:
    """real GLM API 调用test (仅在有 API key 时跑).

    这些testverify OpenAICompatAdapter 能与real API 通信.
    不includes counterexamples —— Counterexample在 mock test中已covers.
    """

    def test_simple_completion(self) -> None:
        """real: 简单文本补全 → STOP."""
        adapter = OpenAICompatAdapter(model='agnes-1.5-flash')
        resp = adapter.complete(
            messages=[Message.user("回复一个字:好")],
            tools=[],
        )
        assert resp.stop_reason == StopReason.STOP
        assert len(resp.content) > 0
        assert resp.usage["total"] > 0

    def test_tool_call(self) -> None:
        """real: model调用tool → TOOL_USE (或软件downgrade STOP).

        某些模型 (如 agnes-1.5-flash) 可能returns finish_reason=tool_calls
        但无实际 tool_call 分片 → 适配器降级for STOP (v0.0.26 修复).
        两种结果都接受, 只要does not raise异常即可.
        """
        adapter = OpenAICompatAdapter(model='agnes-1.5-flash')
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo back the input text",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ]
        resp = adapter.complete(
            messages=[Message.user("请使用 echo tool, text 参数填 'hello zall'")],
            tools=tools,
            tool_choice=ToolChoice.REQUIRED,
        )
        # v0.0.26: accept两种结果 — TOOL_USE(model正常工作) 或 STOP(modelreturns空 tool_calls)
        assert resp.stop_reason in (StopReason.TOOL_USE, StopReason.STOP)
        if resp.stop_reason == StopReason.TOOL_USE:
            assert len(resp.tool_calls) >= 1
            assert resp.tool_calls[0].tool_id == "echo"
