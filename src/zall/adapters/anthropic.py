"""zall.adapters.anthropic — Anthropic Claude API adapter (with streaming).

Design:
  - Translates between zall's model-agnostic Message/ToolCall/ModelResponse
    and Anthropic's Messages API format.
  - Supports both streaming and non-streaming.
  - Uses anthropic SDK (adapters/ may import SDKs per IPR-3).

Anthropic API quirks:
  - Tool calls are content blocks (not a separate field like OpenAI).
  - "stop_reason" values: end_turn→STOP, tool_use→TOOL_USE, max_tokens→LENGTH.
  - System prompt is a separate parameter, not a message with role="system".
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.safety.config import load_config


def _map_anthropic_role(role: str) -> str:
    """Map zall Message roles to Anthropic API roles.

    Anthropic Messages API only accepts "user" and "assistant".
    zall tool results use role="tool" — must be remapped to "user"
    (Anthropic requires tool_result content blocks inside a user turn).
    """
    if role == "tool":
        return "user"
    if role not in ("user", "assistant"):
        return "user"  # fallback
    return role


class AnthropicAdapter:
    """Anthropic Claude API adapter (non-streaming + streaming).

    Uses ANTHROPIC_API_KEY env var or ~/.zall/config.toml [auth] section.
    """

    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        cfg = load_config()
        # Try Anthropic-specific key first, then fallback to generic api_key
        self._api_key = (
            api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or cfg.get("api_key", "")
        )
        self._model = model or os.environ.get("ANTHROPIC_MODEL") or cfg.get("model", "claude-sonnet-4-20250514")
        self._max_tokens = max_tokens
        self._timeout = timeout

        if not self._api_key:
            raise ValueError(
                "Anthropic API key required — set ANTHROPIC_API_KEY env var "
                "or add api_key to ~/.zall/config.toml"
            )

        import anthropic
        self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)

    def close(self) -> None:
        """Close the persistent Anthropic client."""
        self._client.close()

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
    ) -> Iterator[tuple[str, ModelResponse]]:
        yield from self._stream(messages, tools, tool_choice)

    # ── Internal ──

    def _build_body(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build Anthropic Messages API request body."""
        # Separate system prompt (Anthropic uses top-level system parameter)
        system_parts: list[str] = []
        api_messages: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue

            api_msg: dict[str, Any] = {
                "role": _map_anthropic_role(m.role),
                "content": [],
            }

            # Text content
            if m.content:
                api_msg["content"].append({"type": "text", "text": m.content})

            # Tool calls (assistant messages)
            if m.tool_calls:
                for tc in m.tool_calls:
                    # v0.1.1: 直接传 dict, 避免 json.dumps→json.loads 不必要往返
                    api_msg["content"].append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.tool_id,
                        "input": dict(tc.args),
                    })

            # Tool results (tool role messages)
            if m.role == "tool":
                # Anthropic uses tool_result content blocks
                api_msg["content"] = [{
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": m.content,
                }]

            # If content is empty and no tool_calls/tool_result, add empty text
            if not api_msg["content"]:
                api_msg["content"] = [{"type": "text", "text": ""}]

            api_messages.append(api_msg)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": self._max_tokens,
        }

        if system_parts:
            body["system"] = "\n".join(system_parts)

        # Convert zall tool schemas to Anthropic format
        if tools:
            anthropic_tools = []
            for t in tools:
                # zall tool schema: {tool_id, description, input_schema}
                anthropic_tools.append({
                    "name": t.get("tool_id", t.get("name", "unknown")),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", t.get("parameters", {})),
                })
            body["tools"] = anthropic_tools

            # Tool choice mapping
            tc_map = {
                ToolChoice.AUTO: {"type": "auto"},
                ToolChoice.REQUIRED: {"type": "any"},
                ToolChoice.NONE: {"type": "none"},
            }
            body["tool_choice"] = tc_map.get(tool_choice, {"type": "auto"})

        if stream:
            body["stream"] = True

        return body

    def _call(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
        stream: bool = False,
    ) -> ModelResponse:
        """Non-streaming API call."""
        body = self._build_body(messages, tools, tool_choice, stream=False)

        try:
            import anthropic
            resp = self._client.messages.create(**body)
        except anthropic.APIStatusError as e:
            return self._make_error_response(e.status_code, str(e))
        except Exception as e:
            return ModelResponse(
                content=f"[Anthropic API error: {e}]",
                stop_reason=StopReason.STOP,
                raw={"error": str(e)},
            )

        return self._parse_response(resp)

    def _stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
    ) -> Iterator[tuple[str, ModelResponse]]:
        """Streaming API call, yields (token_delta, accumulated_response)."""
        body = self._build_body(messages, tools, tool_choice, stream=True)

        content = ""
        reasoning = ""
        tool_calls_acc: list[dict[str, Any]] = []
        current_tool_block: dict[str, Any] | None = None
        finish_reason = None
        stop_reason = StopReason.STOP
        # B6 fix: stream式path捕获 usage 数据
        stream_usage: dict[str, int] = {"prompt": 0, "completion": 0}

        try:
            import anthropic
            with self._client.messages.stream(**body) as stream:
                for event in stream:
                    if event.type == "content_block_start":
                        # B5: 防御性check content_block 是否为 None
                        if event.content_block is None:
                            continue
                        if event.content_block.type == "tool_use":
                            current_tool_block = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input": "",
                            }
                        elif event.content_block.type == "thinking":
                            pass  # Thinking blocks are handled via raw stream

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            content += delta.text
                            yield (delta.text, ModelResponse(
                                content=content, reasoning=reasoning,
                                stop_reason=StopReason.STOP,
                            ))
                        elif delta.type == "thinking_delta":
                            reasoning += delta.thinking
                            yield (delta.thinking, ModelResponse(
                                content=content, reasoning=reasoning,
                                stop_reason=StopReason.STOP,
                            ))
                        elif delta.type == "input_json_delta":
                            if current_tool_block is not None:
                                current_tool_block["input"] += delta.partial_json

                    elif event.type == "content_block_stop":
                        if current_tool_block is not None:
                            tool_calls_acc.append(current_tool_block)
                            current_tool_block = None

                    elif event.type == "message_delta":
                        if event.delta.stop_reason:
                            finish_reason = event.delta.stop_reason
                        # B6 fix: 捕获stream式 usage 数据
                        if event.usage:
                            pu = getattr(event.usage, "input_tokens", None) or 0
                            cu = getattr(event.usage, "output_tokens", None) or 0
                            if pu:
                                stream_usage["prompt"] = pu
                            if cu:
                                stream_usage["completion"] = cu

                    elif event.type == "message_stop":
                        break

        except anthropic.APIStatusError as e:
            yield ("", self._make_error_response(e.status_code, str(e)))
            return
        except GeneratorExit:
            # Stream interrupted
            pass
        except Exception as e:
            yield ("", ModelResponse(
                content=f"[Anthropic stream error: {e}]",
                stop_reason=StopReason.STOP,
            ))
            return

        stop_reason = self._map_stop_reason(finish_reason or "end_turn")

        # Build tool calls from accumulated blocks
        tool_calls: list[ToolCall] = []
        for tb in tool_calls_acc:
            input_raw = tb.get("input", "{}")
            try:
                args = json.loads(input_raw) if isinstance(input_raw, str) else input_raw
            except (json.JSONDecodeError, TypeError):
                args = {"__raw": input_raw}
            tool_calls.append(ToolCall(
                id=tb.get("id", f"tc_{len(tool_calls)}"),
                tool_id=tb.get("name", ""),
                args=args,
            ))

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        yield ("", ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
            usage=stream_usage if any(stream_usage.values()) else {},
        ))

    def _parse_response(self, resp: Any) -> ModelResponse:
        """Parse Anthropic response into zall ModelResponse."""
        content = ""
        reasoning = ""
        tool_calls: list[ToolCall] = []
        stop_reason = self._map_stop_reason(resp.stop_reason or "end_turn")

        for block in resp.content:
            if block.type == "text":
                content += block.text
            elif block.type == "thinking":
                reasoning += block.thinking
            elif block.type == "tool_use":
                args = dict(block.input) if block.input else {}
                tool_calls.append(ToolCall(
                    id=block.id,
                    tool_id=block.name,
                    args=args,
                ))

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        usage = {}
        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "prompt": getattr(resp.usage, "input_tokens", 0),
                "completion": getattr(resp.usage, "output_tokens", 0),
                "total": (getattr(resp.usage, "input_tokens", 0) +
                         getattr(resp.usage, "output_tokens", 0)),
            }

        return ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason, raw={}, usage=usage,
        )

    def _make_error_response(self, status_code: int, body: str) -> ModelResponse:
        """User-friendly error response."""
        error_map = {
            401: "Anthropic API authentication failed. Check your ANTHROPIC_API_KEY.",
            403: "Anthropic API access denied. Your key may not have permission for this model.",
            404: f"Anthropic model '{self._model}' not found. Check model name.",
            429: "Anthropic API rate limit exceeded. Wait and try again.",
            500: "Anthropic API server error. Try again later.",
        }
        hint = error_map.get(status_code, f"Anthropic API error (HTTP {status_code})")
        return ModelResponse(
            content=f"[{hint}]",
            stop_reason=StopReason.STOP,
            raw={"status": status_code, "body": body[:500]},
        )

    @staticmethod
    def _map_stop_reason(reason: str) -> StopReason:
        mapping = {
            "end_turn": StopReason.STOP,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.LENGTH,
            "stop_sequence": StopReason.STOP,
        }
        return mapping.get(reason, StopReason.STOP)