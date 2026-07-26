"""zall.adapters.scripted — 脚本回放 adapter (G15, kimi _scripted_echo 对标).

用途 (E2E 设施):
  - 确定性回归: 把一段真实会话的模型响应录成 JSON 脚本, 离线精确回放 —
    全链路 (loop/工具/渲染/持久化) 测试不烧 API、不抖动。
  - 演示/冒烟: 无 key 环境跑通完整 agent 流程。

脚本格式 (JSON):
  {
    "loop": false,                    // 可选: 耗尽后从头循环 (默认 false)
    "responses": [
      {"content": "hi", "stop_reason": "stop"},
      {"tool_calls": [{"id": "t1", "tool_id": "read_file",
                       "args": {"path": "a.py"}}]},   // 省略 stop_reason → tool_use
      {"content": "done", "usage": {"prompt": 10, "completion": 5}}
    ]
  }

接入: model 写 "scripted:<path.json>" 或 env ZALL_SCRIPT=<path.json>
(cli.config._build_adapter 拦截)。

流式语义 ≡ 阻塞 (ModelAdapter Protocol 契约): complete_stream 逐块 yield
(delta, accumulated), 最终 response 与 complete() 等价。

IPR constraints:
  IPR-0: tests/test_scripted_chaos_invariants.py (含反例)
  IPR-3: 纯 stdlib + pydantic 模型 (无 SDK, 无网络)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)

# 流式回放的切块大小 (字符): 足够小以观察渐进渲染, 足够大不拖慢测试
_STREAM_CHUNK = 12

_EXHAUSTED_TEXT = "[scripted: script exhausted]"


def _parse_entry(entry: dict[str, Any]) -> ModelResponse:
    """单条脚本条目 → ModelResponse; 有 tool_calls 未写 stop_reason 时推断 TOOL_USE。"""
    calls = tuple(
        ToolCall(
            id=str(tc.get("id") or f"call_{i}"),
            tool_id=str(tc["tool_id"]),
            args=dict(tc.get("args") or {}),
        )
        for i, tc in enumerate(entry.get("tool_calls") or [])
    )
    raw_reason = entry.get("stop_reason")
    if raw_reason is None:
        reason = StopReason.TOOL_USE if calls else StopReason.STOP
    else:
        reason = StopReason(str(raw_reason).lower())
    return ModelResponse(
        content=str(entry.get("content") or ""),
        reasoning=str(entry.get("reasoning") or ""),
        tool_calls=calls,
        stop_reason=reason,
        usage={k: int(v) for k, v in (entry.get("usage") or {}).items()},
        raw={"scripted": True},
    )


class ScriptedAdapter:
    """按序回放预录响应; 耗尽后返回 STOP 收尾 (loop=True 则从头循环)。

    calls: 每次 complete/complete_stream 记录 (len(messages), tool 数) —
    供测试断言 loop 侧真实发送了什么。
    """

    __test__ = False

    def __init__(
        self,
        responses: list[dict[str, Any]],
        *,
        loop: bool = False,
        model: str = "scripted",
    ) -> None:
        if not responses:
            raise ValueError("scripted: responses 不能为空")
        # 构造期就解析全部条目 — 坏脚本立即失败, 不留到回放中途
        self._responses = [_parse_entry(e) for e in responses]
        self._loop = loop
        self._model = model
        self._cursor = 0
        self.calls: list[tuple[int, int]] = []

    @classmethod
    def from_file(cls, path: str | Path, *, model: str | None = None) -> "ScriptedAdapter":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "responses" not in data:
            raise ValueError(f"scripted: {path} 缺 'responses' 键")
        return cls(
            data["responses"],
            loop=bool(data.get("loop", False)),
            model=model or f"scripted:{Path(path).name}",
        )

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        self.calls.append((len(messages), len(tools)))
        return self._next()

    def complete_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> Iterator[tuple[str, ModelResponse]]:
        """逐块 yield (delta, accumulated); 最终 response ≡ complete()。"""
        self.calls.append((len(messages), len(tools)))
        final = self._next()
        content = final.content
        acc = ""
        for i in range(0, len(content), _STREAM_CHUNK):
            delta = content[i : i + _STREAM_CHUNK]
            acc += delta
            partial = ModelResponse(
                content=acc, stop_reason=StopReason.STOP, raw={"scripted": True}
            )
            yield delta, partial
        # 收尾帧: 完整等价响应 (含 tool_calls/usage/reasoning)
        yield "", final

    def _next(self) -> ModelResponse:
        if self._cursor >= len(self._responses):
            if self._loop:
                self._cursor = 0
            else:
                return ModelResponse(
                    content=_EXHAUSTED_TEXT,
                    stop_reason=StopReason.STOP,
                    raw={"scripted": True, "exhausted": True},
                )
        resp = self._responses[self._cursor]
        self._cursor += 1
        return resp

    # Protocol 兼容 no-op
    def set_retry_callback(self, cb: Any) -> None:
        pass

    def close(self) -> None:
        pass
