"""zall.adapters.ollama — Ollama local model adapter (with streaming).

Design:
  - Translates between zall's model-agnostic Message/ToolCall/ModelResponse
    and Ollama's chat API format.
  - Supports both streaming and non-streaming.
  - Uses ollama Python SDK (adapters/ may import SDKs per IPR-3).

Ollama API quirks:
  - Runs locally (default http://localhost:11434).
  - Tool calling is supported in newer models (llama3.1+, qwen2.5, etc.).
  - Messages format: role + content, with optional tool_calls.
  - Streaming yields dicts with "message" or "done" keys.
  - No native "reasoning" field — reasoning is shown in content.
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


class OllamaAdapter:
    """Ollama local model adapter (non-streaming + streaming).

    Uses OLLAMA_HOST env var (default http://localhost:11434) and OLLAMA_MODEL
    env var (default llama3.1) or config values.
    """

    __test__ = False

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        cfg = load_config()
        self._host = host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        self._model = (
            model
            or os.environ.get("OLLAMA_MODEL")
            or cfg.get("model", "llama3.1")
        )
        self._timeout = timeout

        import ollama
        self._client = ollama.Client(host=self._host)

    def close(self) -> None:
        """Close the persistent Ollama client (if it has a close method)."""
        if hasattr(self._client, "close"):
            self._client.close()  # type: ignore[no-untyped-call]

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
        body = self._build_body(messages, tools, tool_choice)

        try:
            import ollama
            resp = self._client.chat(**body)
        except ollama.ResponseError as e:
            return self._make_error_response(e.status_code, str(e))
        except Exception as e:
            return ModelResponse(
                content=f"[Ollama error: {e}. Is Ollama running at {self._host}?]",
                stop_reason=StopReason.STOP,
                raw={"error": str(e)},
            )

        return self._parse_response(resp)

    # ── Streaming ──

    def complete_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> Iterator[tuple[str, ModelResponse]]:
        body = self._build_body(messages, tools, tool_choice)

        content = ""
        reasoning = ""
        finish_reason = None
        tool_calls_acc: list[dict[str, Any]] = []

        try:
            import ollama
            stream = self._client.chat(**body, stream=True)
            for chunk in stream:
                msg = chunk.get("message", {})
                token = msg.get("content", "")
                if token:
                    content += token
                    yield (token, ModelResponse(
                        content=content, reasoning=reasoning,
                        stop_reason=StopReason.STOP,
                    ))

                # Tool calls — extract BEFORE done check (done chunk may carry tool_calls)
                tcs = msg.get("tool_calls", [])
                if tcs:
                    for tc in tcs:
                        fc = tc.get("function", tc)
                        tool_calls_acc.append({
                            "name": fc.get("name", ""),
                            "arguments": fc.get("arguments", {}),
                        })

                if chunk.get("done"):
                    finish_reason = chunk.get("done_reason", "stop")
                    break
        except GeneratorExit:
            pass
        except ollama.ResponseError as e:
            yield ("", self._make_error_response(e.status_code, str(e)))
            return
        except Exception as e:
            yield ("", ModelResponse(
                content=f"[Ollama stream error: {e}]",
                stop_reason=StopReason.STOP,
            ))
            return

        stop_reason = self._map_done_reason(finish_reason or "stop")
        tool_calls = tuple(
            ToolCall(id=f"ollama_{i}", tool_id=tc["name"], args=tc.get("arguments", {}))
            for i, tc in enumerate(tool_calls_acc)
        )

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        # M4: upgrade to TOOL_USE when tool_calls are present (done_reason may not carry it)
        if tool_calls:
            stop_reason = StopReason.TOOL_USE

        yield ("", ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        ))

    # ── Internal ──

    def _build_body(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice,
    ) -> dict[str, Any]:
        """Build Ollama chat API request body."""
        ollama_messages = []
        for m in messages:
            om: dict[str, Any] = {"role": m.role, "content": m.content}

            # Tool calls in assistant messages
            if m.tool_calls:
                om["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.tool_id,
                            "arguments": dict(tc.args),
                        }
                    }
                    for tc in m.tool_calls
                ]

            # Tool result messages — content 已在init化时setting (第 174 行)
            # v0.1.1: remove冗余 om["content"] = m.content
            if m.role == "tool":
                pass

            ollama_messages.append(om)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": ollama_messages,
        }

        # B12: tool_choice 控制 (Ollama 支持: "auto" / "required" / "none")
        if tool_choice == ToolChoice.REQUIRED and tools:
            body["tool_choice"] = "required"
        elif tool_choice == ToolChoice.NONE:
            body["tool_choice"] = "none"
        # ToolChoice.AUTO: 不设 (Ollama default行为)

        # Convert tool schemas to Ollama format
        if tools:
            ollama_tools = []
            for t in tools:
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("tool_id", t.get("name", "unknown")),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", t.get("parameters", {})),
                    },
                })
            body["tools"] = ollama_tools

        return body

    def _parse_response(self, resp: Any) -> ModelResponse:
        """Parse Ollama response into zall ModelResponse."""
        msg = resp.get("message", {})
        content = msg.get("content", "")
        reasoning = ""

        # Ollama doesn't have a separate reasoning field
        # Some models include 思考 in content, we detect and extract
        # (left as simple content for now)

        # Extract tool calls
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls", []):
            fc = tc.get("function", tc)
            name = fc.get("name", "")
            args = fc.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {"__raw": args}
            tool_calls.append(ToolCall(
                id=f"ollama_{len(tool_calls)}",
                tool_id=name,
                args=dict(args) if isinstance(args, dict) else {"__raw": str(args)},
            ))

        done_reason = resp.get("done_reason", "stop")
        stop_reason = self._map_done_reason(done_reason)

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        # Token usage
        usage = {}
        eval_count = resp.get("eval_count", 0)
        prompt_eval_count = resp.get("prompt_eval_count", 0)
        if eval_count or prompt_eval_count:
            usage = {
                "prompt": prompt_eval_count or 0,
                "completion": eval_count or 0,
                "total": (prompt_eval_count or 0) + (eval_count or 0),
            }

        return ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason, raw=dict(resp), usage=usage,
        )

    def _make_error_response(self, status_code: int, body: str) -> ModelResponse:
        """User-friendly error for Ollama issues."""
        if status_code == 0 or "Connection refused" in body:
            hint = (
                f"Cannot connect to Ollama at {self._host}. "
                "Make sure Ollama is running (ollama serve)."
            )
        elif status_code == 404:
            hint = f"Model '{self._model}' not found. Run: ollama pull {self._model}"
        else:
            hint = f"Ollama error (HTTP {status_code}): {body[:200]}"

        return ModelResponse(
            content=f"[{hint}]",
            stop_reason=StopReason.STOP,
            raw={"status": status_code, "body": body[:500]},
        )

    @staticmethod
    def _map_done_reason(reason: str) -> StopReason:
        mapping = {
            "stop": StopReason.STOP,
            "tool_calls": StopReason.TOOL_USE,
            "length": StopReason.LENGTH,
            "error": StopReason.STOP,
        }
        return mapping.get(reason, StopReason.STOP)