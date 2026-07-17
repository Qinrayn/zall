"""OpenAI-compatible API adapter (non-streaming + streaming).

Design:
  - Non-streaming: complete()
  - Streaming: complete_stream() yields token deltas
  - core/ must NOT import httpx (adapters/ can, per IPR-3)
  - Inherits BaseAdapter (shared error handling, retry)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.adapters.base import BaseAdapter


@dataclass
class _StreamState:
    """Accumulated state during streaming (extracted for exception recovery)."""

    content: str = ""
    reasoning: str = ""
    tc_acc: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str | None = None
    has_tool_delta: bool = False
    has_any_content: bool = False
    usage: dict[str, int] = field(default_factory=dict)


class OpenAICompatAdapter(BaseAdapter):
    """OpenAI-compatible API adapter (non-streaming + streaming)."""

    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(api_key, api_base, model, timeout)
        if not self._api_key:
            raise ValueError("API key required — set ZALL_API_KEY or add to ~/.zall/config.toml")
        if not self._model:
            raise ValueError("Model required — set ZALL_MODEL or add to ~/.zall/config.toml")
        # Reuse persistent HTTP client (connection pool) across REPL turns.
        self._client = httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        """Close the persistent HTTP client."""
        self._client.close()

    def __enter__(self) -> OpenAICompatAdapter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def model_name(self) -> str:
        return self._model

    # ── Non-streaming ──

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        return self._call(messages, tools, tool_choice, stream=False)

    # ── Streaming ──

    def complete_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> Any:
        """Streaming call, yields (token_delta, accumulated_response)."""
        yield from self._stream(messages, tools, tool_choice)

    def _build_body(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [self._msg_to_openai(m) for m in messages],
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice.value
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def _call(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
        stream: bool = False,
    ) -> ModelResponse:
        body = self._build_body(messages, tools, tool_choice, stream)
        base = self._api_base.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        # Reuse persistent client with exponential backoff retry.
        # Retries on network jitter / 429 / 5xx (max 5); 400/401/403/404 not retried.
        def _do_post() -> ModelResponse:
            try:
                resp = self._client.post(url, json=body, headers=headers,
                                         timeout=self._timeout)
            except httpx.ConnectError as e:
                return ModelResponse(
                    content=f"[API connection error: cannot reach {self._api_base}. "
                            f"Check your network or api_base setting. /doctor for details.]",
                    stop_reason=StopReason.STOP,
                    raw={"error": str(e)},
                )
            except httpx.TimeoutException as e:
                return ModelResponse(
                    content=f"[API timeout: {self._api_base} did not respond within "
                            f"{self._timeout}s. Try /model to switch to a faster model.]",
                    stop_reason=StopReason.STOP,
                    raw={"error": str(e)},
                )
            if resp.status_code != 200:
                # Capture retry_after header for rate-limit handling in with_retry.
                retry_after = resp.headers.get("retry-after", "0") if hasattr(resp, "headers") else "0"
                raw = {"status": resp.status_code, "retry_after": retry_after}
                return self.make_error_response(resp.status_code, resp.text, raw=raw)
            return self._parse_response(resp.json())

        return self.with_retry(_do_post, max_retries=5, base_delay=1.0, max_delay=60.0)

    def _stream(self, messages: list[Message], tools: list[dict[str, Any]], tool_choice: ToolChoice) -> Any:
        """Stream the response, yielding (token_delta, accumulated_response).

        Handles OpenAI streaming protocol:
          - delta.content: incremental text tokens
          - delta.reasoning_content / delta.reasoning: reasoning tokens
          - delta.tool_calls[].index/id/function.name: first chunk
          - delta.tool_calls[].function.arguments: incremental concatenation
          - choices[0].finish_reason: only in the last chunk (maps to stop_reason)

        Retry strategy:
          Initial connection is retried up to 3 times with exponential backoff + jitter.
          Once streaming has started, mid-stream errors are reported as partial responses
          (the server state is lost, so retrying mid-stream would repeat tool calls).
        """
        import random as _random

        body = self._build_body(messages, tools, tool_choice, stream=True)
        base = self._api_base.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        # Stream state shared across yields for exception recovery.
        state = _StreamState()

        # Reuse persistent client; `with` only closes the response stream, not the client.
        # Layered timeout: connect=10s, read={self._timeout}s (from config), write=10s, pool=5s.
        # The read timeout matches the overall adapter timeout so complex multi-step
        # reasoning tasks (e.g., writing a game) don't get cut off mid-stream.
        stream_timeout = httpx.Timeout(connect=10.0, read=self._timeout, write=10.0, pool=5.0)

        # Retry loop for the initial connection (not mid-stream).
        # Uses exponential backoff with jitter, inspired by xAI Grok Build's sampler.
        max_conn_retries = 3
        for conn_attempt in range(max_conn_retries):
            try:
                resp = self._client.stream("POST", url, json=body, headers=headers,
                                           timeout=stream_timeout)
                resp.__enter__()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                if conn_attempt < max_conn_retries - 1:
                    delay = min(1.0 * (2 ** conn_attempt), 30.0) * _random.uniform(0.75, 1.25)
                    import time as _time
                    _time.sleep(delay)
                    continue
                yield ("", self._make_stream_error(
                    f"cannot reach {self._api_base} after {max_conn_retries} attempts. "
                    f"Check your network or api_base setting.", e))
                return
            except Exception as e:
                if conn_attempt < max_conn_retries - 1:
                    delay = min(1.0 * (2 ** conn_attempt), 30.0) * _random.uniform(0.75, 1.25)
                    import time as _time
                    _time.sleep(delay)
                    continue
                yield ("", self._make_stream_error(
                    f"cannot connect to {self._api_base}. /doctor for details.", e))
                return
            break  # Connection succeeded

        try:
            with resp:
                if resp.status_code != 200:
                    # Streaming responses must be read() before .text is available.
                    err_body = resp.read().decode("utf-8", errors="replace") if hasattr(resp, "read") else resp.text
                    yield ("", self.make_error_response(resp.status_code, err_body))
                    return
                for line in resp.iter_lines():
                    chunk = self._parse_stream_line(line)
                    if chunk is None:
                        continue  # Empty / keepalive / [DONE] / corrupted JSON
                    data = chunk
                    # Usage may appear in any chunk (not just usage-only ones).
                    state.usage = self._capture_chunk_usage(data, state.usage)
                    # Skip chunks with no choices (usage-only).
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    # Process the choice: finish_reason + deltas.
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        state.finish_reason = choice["finish_reason"]
                    delta = choice.get("delta", {})
                    for event in self._process_stream_delta(delta, state):
                        yield event
        except httpx.ConnectError as e:
            yield ("", self._make_stream_error(
                f"cannot reach {self._api_base}. Check your network or api_base setting.", e))
            return
        except httpx.TimeoutException as e:
            yield ("", self._make_stream_error(
                f"{self._api_base} did not respond within {self._timeout}s. "
                f"Try /model to switch to a faster model.", e))
            return
        except GeneratorExit:
            # Stream interrupted (Ctrl-C) — do not yield
            # (PEP 342: GeneratorExit + yield = RuntimeError)
            return
        except Exception as e:
            # On unexpected errors, return whatever was accumulated so far.
            yield self._build_partial_response(state, e)
            return

        # Build the final ModelResponse.
        yield self._build_final_stream_response(state)

    # ── Streaming helper methods ──

    @staticmethod
    def _parse_stream_line(line: str) -> dict[str, Any] | None:
        """Parse a single SSE line, returning a JSON dict or None (skip)."""
        if not line:
            return None
        line = line.strip()
        if line.startswith(":") or line in ("data: [DONE]", "data:[DONE]"):
            return None
        if not line.startswith("data: "):
            return None
        try:
            return cast(dict[str, Any], json.loads(line[6:]))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _capture_chunk_usage(data: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        """Extract token usage from a chunk (may share a chunk with finish_reason)."""
        usage_raw = data.get("usage")
        if usage_raw:
            return {
                "prompt": usage_raw.get("prompt_tokens", 0),
                "completion": usage_raw.get("completion_tokens", 0),
                "total": usage_raw.get("total_tokens", 0),
            }
        return current

    def _process_stream_delta(self, delta: dict[str, Any], state: "_StreamState") -> Any:
        """Process a single delta block, update state, yield intermediate token events.

        Yield order: reasoning -> content (ensures reasoning is updated before
        content yield, so the accumulated response is consistent).
        """
        # Intermediate yields use StopReason.STOP as a snapshot marker.
        # The final stop_reason is set only in _build_final_stream_response.
        # loop.py's _call_model_stream uses the final resp's stop_reason,
        # so intermediate STOP values are safe (only used for token rendering).

        # 1. Reasoning tokens (processed before content).
        rtoken = delta.get("reasoning_content") or delta.get("reasoning", "")
        if rtoken:
            state.reasoning += rtoken
            state.has_any_content = True
            yield (rtoken, ModelResponse(
                content=state.content,
                reasoning=state.reasoning,
                stop_reason=StopReason.STOP,
            ))

        # 2. Content tokens.
        token = delta.get("content", "")
        if token:
            state.content += token
            state.has_any_content = True
            yield (token, ModelResponse(
                content=state.content,
                reasoning=state.reasoning,
                stop_reason=StopReason.STOP,
            ))

        # 3. Tool call fragment accumulation.
        tc_deltas = delta.get("tool_calls", [])
        if tc_deltas:
            state.has_tool_delta = True
            state.has_any_content = True
        for tc_delta in tc_deltas:
            idx = tc_delta.get("index", 0)
            if idx not in state.tc_acc:
                state.tc_acc[idx] = {"id": "", "tool_id": "", "args_str": ""}
            if tc_delta.get("id"):
                state.tc_acc[idx]["id"] = tc_delta["id"]
            func = tc_delta.get("function", {})
            if func.get("name"):
                state.tc_acc[idx]["tool_id"] = func["name"]
            if func.get("arguments"):
                state.tc_acc[idx]["args_str"] += func["arguments"]

    def _make_stream_error(self, msg: str, error: Exception) -> ModelResponse:
        """Build a streaming error response.
        
        Returns a plain ModelResponse (not a tuple) — the caller wraps it in
        a (token, response) pair when yielding.
        """
        return ModelResponse(
            content=f"[API error: {msg}]",
            stop_reason=StopReason.STOP,
            raw={"error": str(error)},
        )

    def _build_partial_response(self, state: "_StreamState", error: Exception) -> tuple[str, ModelResponse]:
        """Build a partial response on stream interruption."""
        if state.has_any_content:
            stop_reason = (
                StopReason.TOOL_USE
                if state.has_tool_delta and state.tc_acc
                else StopReason.STOP
            )
            tool_calls = self._build_tool_calls_from_acc(state.tc_acc)
            return ("", ModelResponse(
                content=state.content,
                reasoning=state.reasoning,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
                raw={"error": str(error)},
            ))
        return ("", ModelResponse(
            content=f"[API error: {error}. Try /doctor to check your config.]",
            stop_reason=StopReason.STOP,
            raw={"error": str(error)},
        ))

    def _build_final_stream_response(self, state: "_StreamState") -> tuple[str, ModelResponse]:
        """Build the final ModelResponse after a successful stream."""
        stop_reason = self._map_finish_reason(state.finish_reason or "stop")
        # Degradation: finish_reason=tool_calls but no tool_call deltas -> STOP
        if stop_reason == StopReason.TOOL_USE and not state.tc_acc:
            stop_reason = StopReason.STOP
        # No finish_reason set but tool_calls accumulated -> TOOL_USE
        if state.finish_reason is None and state.has_tool_delta and state.tc_acc:
            stop_reason = StopReason.TOOL_USE
        tool_calls = self._build_tool_calls_from_acc(state.tc_acc)
        return ("", ModelResponse(
            content=state.content,
            reasoning=state.reasoning,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
            usage=state.usage,
        ))

    def _msg_to_openai(self, msg: Message) -> dict[str, Any]:
        m: dict[str, Any] = {"role": msg.role}
        # B11: 纯tool调用message content 为 "" → 设 null (compatible严格 API)
        if msg.tool_calls:
            # OpenAI: assistant 带 tool_calls 时 content 可为 null
            if msg.content:
                m["content"] = msg.content
            else:
                m["content"] = None
        else:
            m["content"] = msg.content
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        # Standard OpenAI API uses tool_call_id for tool result mapping.
        # tool_id is not a standard field — not sent to the API.
        # Message model guarantees role="tool" has non-empty tool_call_id.
        if msg.tool_calls:
            m["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.tool_id,
                        "arguments": json.dumps(tc.args, ensure_ascii=False),
                    },
                }
                for tc in msg.tool_calls
            ]
        return m

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        choices = data.get("choices", [])
        if not choices:
            return ModelResponse(content="[empty]", stop_reason=StopReason.STOP, raw=data)
        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        # Non-streaming responses may also include reasoning in the message.
        reasoning = msg.get("reasoning_content") or msg.get("reasoning", "") or ""
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason = self._map_finish_reason(finish_reason)
        tool_calls = []
        for rtc in msg.get("tool_calls") or []:
            tc_id = rtc.get("id", "")
            func = rtc.get("function", {})
            tool_id = func.get("name", "")
            args_raw = func.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, TypeError):
                args = {"__raw": args_raw}
            tool_calls.append(ToolCall(id=tc_id, tool_id=tool_id, args=args))
        # Degradation: finish_reason=tool_calls but tool_calls list is empty -> STOP
        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP
        usage_raw = data.get("usage", {})
        usage = {
            "prompt": usage_raw.get("prompt_tokens", 0),
            "completion": usage_raw.get("completion_tokens", 0),
            "total": usage_raw.get("total_tokens", 0),
        }
        return ModelResponse(
            content=content, reasoning=reasoning, tool_calls=tuple(tool_calls),
            stop_reason=stop_reason, raw=data, usage=usage,
        )

    @staticmethod
    def _map_finish_reason(reason: str) -> StopReason:
        mapping = {
            "stop": StopReason.STOP,
            "tool_calls": StopReason.TOOL_USE,
            "function_call": StopReason.TOOL_USE,
            "length": StopReason.LENGTH,
            "content_filter": StopReason.STOP,
        }
        return mapping.get(reason, StopReason.STOP)

    @staticmethod
    def _build_tool_calls_from_acc(tc_acc: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
        """Build a ToolCall tuple from accumulated streaming tool_call fragments."""
        tool_calls = []
        for idx in sorted(tc_acc.keys()):
            tc = tc_acc[idx]
            args_raw = tc.get("args_str", "").strip() or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, TypeError):
                args = {"__raw": args_raw}
            tool_calls.append(ToolCall(
                id=tc.get("id") or f"stream_tc_{idx}",
                tool_id=tc.get("tool_id", ""),
                args=args,
            ))
        return tuple(tool_calls)
