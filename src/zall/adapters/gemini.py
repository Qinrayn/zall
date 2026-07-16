"""zall.adapters.gemini — Google Gemini API adapter (with streaming).

Design:
  - Translates between zall's model-agnostic Message/ToolCall/ModelResponse
    and Google's Generative AI SDK format.
  - Supports both streaming and non-streaming.
  - Uses google-generativeai SDK (adapters/ may import SDKs per IPR-3).

Gemini API quirks:
  - Function declarations are part of the GenerationConfig, not a separate param.
  - Tool calls are called "function calls" with structured args.
  - System instruction is separate from messages.
  - Different finish_reason values: STOP, MAX_TOKENS, SAFETY, RECITATION, OTHER.
"""

from __future__ import annotations

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


class GeminiAdapter:
    """Google Gemini API adapter (non-streaming + streaming).

    Uses GOOGLE_API_KEY env var or ~/.zall/config.toml [auth] section.
    Model defaults to 'gemini-2.5-pro-exp-03-25' or GOOGLE_MODEL env var.
    """

    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        cfg = load_config()
        self._api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or cfg.get("api_key", "")
        )
        self._model = (
            model
            or os.environ.get("GOOGLE_MODEL")
            or "gemini-2.5-pro-exp-03-25"
        )
        self._timeout = timeout

        if not self._api_key:
            raise ValueError(
                "Google API key required — set GOOGLE_API_KEY env var "
                "or add api_key to ~/.zall/config.toml"
            )

    @property
    def model_name(self) -> str:
        return self._model

    def close(self) -> None:
        """释放 HTTP 资源 (当前无持久化 client, 留interface供未来使用)。"""
        pass

    # ── Non-streaming ──

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)  # type: ignore[attr-defined]
        model = genai.GenerativeModel(  # type: ignore[attr-defined]
            model_name=self._model,
            system_instruction=self._extract_system(messages),
            tools=self._build_tools(tools) if tools else None,
        )

        # Build history + current message
        history, current_msg = self._split_messages(messages)
        chat = model.start_chat(history=history)  # type: ignore[arg-type]

        try:
            resp = chat.send_message(
                current_msg,
                generation_config=genai.types.GenerationConfig(
                    candidate_count=1,
                ),
            )
        except Exception as e:
            return ModelResponse(
                content=f"[Gemini API error: {e}]",
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
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)  # type: ignore[attr-defined]
        model = genai.GenerativeModel(  # type: ignore[attr-defined]
            model_name=self._model,
            system_instruction=self._extract_system(messages),
            tools=self._build_tools(tools) if tools else None,
        )

        history, current_msg = self._split_messages(messages)
        chat = model.start_chat(history=history)  # type: ignore[arg-type]

        content = ""
        reasoning = ""
        finish_reason = None
        function_calls: list[dict[str, Any]] = []

        try:
            stream = chat.send_message(
                current_msg,
                stream=True,
                generation_config=genai.types.GenerationConfig(
                    candidate_count=1,
                ),
            )

            for chunk in stream:
                if not chunk.candidates:
                    continue

                candidate = chunk.candidates[0]
                if candidate.content is None:
                    continue
                if hasattr(candidate, "finish_reason") and candidate.finish_reason:
                    finish_reason = candidate.finish_reason.name

                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        content += part.text
                        yield (part.text, ModelResponse(
                            content=content, reasoning=reasoning,
                            stop_reason=StopReason.STOP,
                        ))
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        try:
                            args = dict(fc.args) if hasattr(fc, "args") else {}
                        except Exception:
                            args = {}
                        function_calls.append({
                            "name": fc.name,
                            "args": args,
                        })

        except GeneratorExit:
            pass
        except Exception as e:
            yield ("", ModelResponse(
                content=f"[Gemini stream error: {e}]",
                stop_reason=StopReason.STOP,
            ))
            return

        stop_reason = self._map_stop_reason(finish_reason or "STOP")
        tool_calls = tuple(
            ToolCall(
                id=f"fc_{i}",
                tool_id=fc["name"],
                args=fc.get("args", {}),
            )
            for i, fc in enumerate(function_calls)
        )

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        yield ("", ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        ))

    # ── Helpers ──

    def _extract_system(self, messages: list[Message]) -> str | None:
        """Extract system prompts as a single string (Gemini system_instruction)."""
        parts = [m.content for m in messages if m.role == "system" and m.content]
        return "\n".join(parts) if parts else None

    def _split_messages(
        self, messages: list[Message]
    ) -> tuple[list[dict[str, Any]], str]:
        """Split messages into history (list) and current user message (str).

        Gemini's chat interface requires:
          - history: list of {"role": "user"/"model", "parts": [...]}
          - The "user" messages and "model" responses interleaved
          - A final user message sent separately
        """

        # Filter out system messages
        non_system = [m for m in messages if m.role != "system"]

        if not non_system:
            return [], ""

        # The last user message is the current one
        last_is_user = non_system[-1].role == "user"
        history_msgs = non_system[:-1] if last_is_user else non_system

        history: list[dict[str, Any]] = []
        for m in history_msgs:
            role = "user" if m.role in ("user", "tool") else "model"
            parts: list[dict[str, Any]] = []

            if m.content:
                parts.append({"text": m.content})

            if m.tool_calls:
                for tc in m.tool_calls:
                    parts.append({
                        "function_call": {
                            "name": tc.tool_id,
                            "args": dict(tc.args),
                        },
                    })

            if m.role == "tool":
                # tool_result → function_response
                # name must match the original function_call.name (= tool_id), not tool_call_id
                parts.append({
                    "function_response": {
                        "name": m.tool_id or "unknown",
                        "response": {"result": m.content},
                    },
                })

            if parts:
                history.append({"role": role, "parts": parts})

        current_msg = ""
        if last_is_user and non_system:
            current_msg = non_system[-1].content

        return history, current_msg

    def _build_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert zall tool schemas to Gemini FunctionDeclaration format."""
        gemini_tools = []
        for t in tools:
            gemini_tools.append({
                "function_declarations": [{
                    "name": t.get("tool_id", t.get("name", "unknown")),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", t.get("parameters", {})),
                }],
            })
        return gemini_tools

    def _parse_response(self, resp: Any) -> ModelResponse:
        """Parse Gemini response into zall ModelResponse."""
        content = ""
        reasoning = ""
        tool_calls: list[ToolCall] = []
        stop_reason = StopReason.STOP

        if not resp.candidates:
            return ModelResponse(
                content="[Gemini returned no candidates]",
                stop_reason=StopReason.STOP,
            )

        candidate = resp.candidates[0]
        if candidate.content is None:
            return ModelResponse(
                content="[Gemini returned empty content]",
                stop_reason=StopReason.STOP,
            )
        if hasattr(candidate, "finish_reason") and candidate.finish_reason:
            stop_reason = self._map_stop_reason(candidate.finish_reason.name)

        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                content += part.text
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                try:
                    args = dict(fc.args) if hasattr(fc, "args") else {}
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(
                    id=f"fc_{len(tool_calls)}",
                    tool_id=fc.name,
                    args=args,
                ))

        if stop_reason == StopReason.TOOL_USE and not tool_calls:
            stop_reason = StopReason.STOP

        # Extract usage if available
        usage = {}
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            usage = {
                "prompt": getattr(resp.usage_metadata, "prompt_token_count", 0),
                "completion": getattr(resp.usage_metadata, "candidates_token_count", 0),
                "total": (getattr(resp.usage_metadata, "prompt_token_count", 0) +
                         getattr(resp.usage_metadata, "candidates_token_count", 0)),
            }

        return ModelResponse(
            content=content, reasoning=reasoning,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason, raw={}, usage=usage,
        )

    @staticmethod
    def _map_stop_reason(reason: str) -> StopReason:
        mapping = {
            "STOP": StopReason.STOP,
            "MAX_TOKENS": StopReason.LENGTH,
            "SAFETY": StopReason.STOP,
            "RECITATION": StopReason.STOP,
            "OTHER": StopReason.STOP,
            "FINISH_REASON_UNSPECIFIED": StopReason.STOP,
        }
        return mapping.get(reason, StopReason.STOP)