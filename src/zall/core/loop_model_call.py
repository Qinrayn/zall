"""zall.core.loop_model_call — 模型调用面 (从 AgentLoop 抽取, loop.py 瘦身续)。

Corresponds to:
  §6.1  model_call 记录点 (timeline 完整 ModelResponse, §6.2 replay 可复现)
  §9.2.12 思考过程分流 (reasoning/content 双通道 token 事件)
  A1    流式异常诚实传播 + 零产出降级非流式 (2026-07-26 chaos e2e)

设计: 无状态自由函数, 接收 loop 实例 (与 loop_perception / loop_checkpoint /
executor 协作者模式一致)。此前内联在 AgentLoop._call_model /
_call_model_stream (~184 行)。**纯搬运, 不改逻辑** — 行为等价由
test_loop_stream_invariants / test_stream_error_invariants 守护。

IPR-3: stdlib + core 事件/模型/verifiability, 无模型 SDK。
"""

from __future__ import annotations

import time
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger
from zall.core.loop_events import LoopEvent
from zall.core.model import ModelResponse, StopReason, ToolChoice
from zall.core.verifiability import EventType

_log = _get_zall_logger(__name__)


def call_model(loop: Any, *, emit_model_call: bool = True) -> ModelResponse:
    """调model, 记录到 RunRecorder。

    stream 分流 (P2):
      loop._stream=True 且 adapter 有 complete_stream → 流式分支
      否则 → 阻塞 complete() (P1 行为, 零变化)

    流式语义 ≡ 阻塞: 最终 ModelResponse 一致, 记录点一致,
    只是过程中逐 token 广播 model_token 事件给 observer。

    emit_model_call (v0.0.21c): 默认 True 广播 model_call 渲染事件;
      False 时只记 timeline + model_call_start (spinner), 不广播 model_call
      渲染。供 step() 的"首次调用"用 —— 防 nudge 重试时第一次空回复被渲染成
      "(empty)" 与重试结果双重显示。调用方在确认不需 nudge 后补发渲染。
    """
    # O2: use cached tool schemas (avoid rebuilding every model call)
    tool_schemas: list[dict[str, Any]] = loop._tool_schemas

    # Extension: on_before_model
    if loop._ext_registry is not None:
        loop._ext_registry.fire(
            "on_before_model",
            messages=loop._chat_state.messages,
            step=loop._step_count,
        )

    # §6.1 呈现层投影: 调model前broadcast model_call_start (让呈现层显示 spinner)
    # 纯 observer event, 不进 RunRecorder (start 不是auditevent, 完成才记)
    loop._emit(LoopEvent(
        kind="model_call_start",
        step=loop._step_count,
        payload={"model": loop._model.model_name},
    ))

    if loop._stream:
        resp = call_model_stream(loop, tool_schemas)
    else:
        resp = loop._model.complete(
            messages=loop._chat_state.messages,
            tools=tool_schemas,
            tool_choice=ToolChoice.AUTO,
        )

    # 记录 model_call event (stream式/blocking共用同一record point)
    # §6.2 replay 要求 timeline 存完整 ModelResponse (不只digest)
    # B2 fix: 同时存储真实 usage 数据, 供 /undo 校正使用
    loop._recorder.append(
        event_id=f"model_call_{loop._model_call_count}",
        ts=int(time.time() * 1000),
        event_type=EventType.MODEL_CALL,
        payload={
            "model": loop._model.model_name,
            "stop_reason": resp.stop_reason.value,
            "content_length": len(resp.content),
            "tool_calls_count": len(resp.tool_calls),
            # §6.2 replay 用: 完整response数据 (让 timeline reproducible)
            "content": resp.content,
            "reasoning": resp.reasoning,
            "reasoning_length": len(resp.reasoning),
            "tool_calls": [
                {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                for tc in resp.tool_calls
            ],
            # B2: 真实 usage 数据, 供 _recalc_usage_from_timeline 使用
            "usage": dict(resp.usage) if resp.usage else {},
        },
    )
    # §6.1 呈现层投影: 同一record pointbroadcast给 observer
    if emit_model_call:
        loop._emit_model_call_event(resp)

    return resp


def call_model_stream(loop: Any, tool_schemas: list[dict[str, Any]]) -> ModelResponse:
    """stream式调model, 逐 token broadcast, return最终 ModelResponse。

    语义 ≡ 阻塞: 最终返回的 ModelResponse 与 complete() 等价。
    过程中每个 token 通过 observer 广播 model_token 事件 (呈现层用)。
    RunRecorder 不记 token (那是呈现层, 不是审计轨迹)。
    """
    resp: ModelResponse | None = None
    # 思考过程分stream (§9.2.12): model先给 reasoning 再给 content。
    # 用长度增量judgment当前 token 属于哪条通道 (reasoning 阶段 content 不增长),
    # 不引入新interface (仍沿用 complete_stream 的 (token, accumulated) protocol)。
    prev_content_len = 0
    prev_reasoning_len = 0
    # Track tool call count to detect new tool call deltas
    prev_tool_call_count = 0
    try:
        for token, accumulated in loop._model.complete_stream(  # type: ignore[attr-defined]
            messages=loop._chat_state.messages,
            tools=tool_schemas,
            tool_choice=ToolChoice.AUTO,
        ):
            if token:
                reasoning = accumulated.reasoning
                if (len(reasoning) > prev_reasoning_len
                        and len(accumulated.content) == prev_content_len):
                    # 思考过程增量 → model_thinking (呈现层透明展示)
                    delta = reasoning[prev_reasoning_len:]
                    loop._emit(LoopEvent(
                        kind="model_thinking",
                        step=loop._step_count,
                        payload={"token": delta, "accumulated": reasoning},
                    ))
                    prev_reasoning_len = len(reasoning)
                else:
                    # content增量 → model_token (呈现层stream式显示)
                    loop._emit(LoopEvent(
                        kind="model_token",
                        step=loop._step_count,
                        payload={"token": token, "accumulated": accumulated.content},
                    ))
                    prev_content_len = len(accumulated.content)
            # Tool call delta: emit model_tool_call event so UI can show progress
            if accumulated.tool_calls and len(accumulated.tool_calls) > prev_tool_call_count:
                # Only emit when new tool calls appear (not on every token)
                prev_tool_call_count = len(accumulated.tool_calls)
                loop._emit(LoopEvent(
                    kind="model_tool_call",
                    step=loop._step_count,
                    payload={
                        "tool_calls": [
                            {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                            for tc in accumulated.tool_calls
                        ],
                    },
                ))
            resp = accumulated
    except GeneratorExit:
        # GeneratorExit must重抛 (Python generatorprotocol: 关闭信号不可吞)
        # 吞掉会破坏 with/finally cleanup链, 导致资源leak
        raise
    except Exception as _stream_exc:
        # v0.4.9 (A1): stream exception must be observable and honest.
        # Previously this was silently downgraded to an empty/partial STOP
        # response, so callers (step/UI/auto-retry) had no signal that
        # streaming failed. Now we log it, record it for introspection
        # (loop._last_stream_error), and let it propagate so step()'s
        # terminal handler emits a real, diagnosable error egress instead
        # of pretending the turn succeeded. This keeps the fail-safe
        # semantic (no crash, clean terminal) while making the failure
        # visible — matching the sync call path's behavior.
        loop._last_stream_error = _stream_exc
        _log.warning(
            "stream model call failed: %s: %s",
            type(_stream_exc).__name__, _stream_exc,
        )
        # 流式重试缺陷修复 (2026-07-26, chaos e2e 钓出): adapter 的
        # _with_retry 重试链只保护非流式 complete(), 各 adapter 的
        # complete_stream 是裸流 — 网络闪断直接 raise 会杀死长任务。
        # 分级恢复:
        #   零产出 (没有广播过任何 token/thinking/tool_call delta)
        #     → 降级到非流式 complete() (自动获得完整重试链),
        #       UI 不会有重复输出 (什么都还没显示过)。
        #   已有部分产出 → 维持 A1 诚实传播 (降级重打会造成
        #       半截内容 + 完整内容双重显示, 且截断处语义不可拼接)。
        emitted_any = (
            prev_content_len > 0
            or prev_reasoning_len > 0
            or prev_tool_call_count > 0
        )
        if emitted_any:
            raise
        _log.warning(
            "stream produced zero output — falling back to "
            "non-streaming complete() (with retry chain)",
        )
        # 可见性: 复用 retry 事件通道 (spinner 标签替换, 零闪烁)
        loop._emit(LoopEvent(kind="retry", step=loop._step_count, payload={
            "category": "stream_fallback",
            "delay": 0,
            "attempt": 1,
            "max_attempts": 1,
        }))
        return loop._model.complete(
            messages=loop._chat_state.messages,
            tools=tool_schemas,
            tool_choice=ToolChoice.AUTO,
        )
    # stream式结束, resp 是最终 ModelResponse (含完整 content + tool_calls + stop_reason)
    if resp is None:
        # stream式没产出任何东西 (exception) → downgrade为 STOP
        return ModelResponse(content="", stop_reason=StopReason.STOP)
    return resp


__all__ = ["call_model", "call_model_stream"]
