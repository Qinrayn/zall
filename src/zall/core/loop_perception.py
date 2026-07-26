"""zall.core.loop_perception — 感知步 (从 loop._run_step_body 抽取, PARADIGM Step 0 热循环瘦身)。

Corresponds to:
  §4.2.3 / §12.3 E1  Perception: 感知引擎更新状态估计 + anomaly 熔断 + 关键状态变化注入。

设计: 无状态自由函数, 接收 loop 实例 (与 executor.py / context_manager.py / loop_checkpoint.py
协作者模式一致)。此前内联在 AgentLoop._run_step_body (~95 行), 为给 loop.py 瘦身
(PARADIGM Step 0) 抽取为独立模块。**感知引擎默认 None → 直接返回, 不进热路径。**

不变量 (tests/test_perception_consumed_by_loop.py + test_perception_invariants.py 守护):
  - 感知引擎为 None → no-op (与原内联 `if self._perception_engine is not None` 一致)。
  - anomaly → 记 PERCEPTION_ANOMALY + 注入 nudge; 关键状态变化 → 注入摘要 (每步至多一条)。
  - 行为与抽取前等价 (纯搬运, 不改逻辑)。

IPR-3: stdlib + core 事件/模型/模板/verifiability, 无模型 SDK。
"""

from __future__ import annotations

import time
from typing import Any

from zall.core.loop_events import LoopEvent
from zall.core.model import Message
from zall.core.prompt_template import render as _render_template
from zall.core.verifiability import EventType


def run_perception(loop: Any) -> None:
    """执行一次感知步 (perceive → emit → anomaly nudge → state-change nudge → update snapshot)。

    loop 为 AgentLoop 实例。感知引擎为 None 时直接返回 (不进热路径)。
    """
    if loop._perception_engine is None:
        return

    loop._emit(LoopEvent(
        kind="step_progress",
        step=loop._step_count,
        payload={"phase": "perception", "message": "perceiving environment..."},
    ))
    state = loop._perception_engine.perceive()
    # 保存感知状态用于后续判断 (§12.3 E1)
    loop._last_perception_state = state
    loop._emit(LoopEvent(
        kind="perception_state",
        step=loop._step_count,
        payload={
            "confidence": state.confidence,
            "sources": list(state.sources),
            "anomaly": loop._perception_engine.anomaly(),
            "state_keys": list(state.state.keys()),
        },
    ))

    # §12.3 E1.1: anomaly 熔断 — 检测异常并注入
    if loop._perception_engine.anomaly():
        _state_keys = list(state.state.keys())
        _summary = f"confidence={state.confidence:.2f}, sources={list(state.sources)}, keys={_state_keys[:5]}"
        loop._recorder.append(
            event_id=f"perception_anomaly_{loop._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.PERCEPTION_ANOMALY,
            payload={
                "step": loop._step_count,
                "confidence": state.confidence,
                "sources": list(state.sources),
                "state_keys": _state_keys,
                "summary": _summary,
            },
        )
        _nudge = _render_template("perception_anomaly_nudge", summary=_summary)
        loop._append_message(Message(role="system", content=_nudge))
        loop._recorder.append(
            event_id=f"perception_anomaly_nudge_{loop._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.SYSTEM_INJECTION,
            payload={"reason": "perception_anomaly", "nudge": _nudge[:200]},
        )

    # §12.3 E1.2: 关键状态变化时注入紧凑摘要 (每步最多一条)
    # C1 fix: 传感器真实输出键是 "modified" (coding_sensors.py GitSensor) 和
    # "errors" (LSPSensor), 而非 "git_modified"/"lsp_errors"。旧键名导致本段
    # 永远读到默认值 0, 状态变化检测从未触发 (死代码)。
    _modified = state.state.get("modified", [])
    _modified_count = len(_modified) if isinstance(_modified, (list, tuple)) else 0
    _lsp_errors = state.state.get("errors", 0) or 0
    _current_snapshot = {
        "modified_count": _modified_count,
        "lsp_errors": _lsp_errors,
        "confidence": state.confidence,
    }
    if loop._prev_perception_snapshot is not None:
        _prev = loop._prev_perception_snapshot
        _changed = False
        # 条件: git modified 文件数变化
        if _current_snapshot.get("modified_count") != _prev.get("modified_count"):
            _changed = True
        # 条件: lsp_errors 从 0 变非 0
        elif (_prev.get("lsp_errors", 0) == 0
              and _current_snapshot.get("lsp_errors", 0) != 0):
            _changed = True
        # 条件: confidence 下降超过 0.3
        elif (_prev.get("confidence", 1.0) - _current_snapshot.get("confidence", 1.0)
              > 0.3):
            _changed = True

        if _changed:
            _diff_parts = []
            if _current_snapshot["modified_count"] != _prev["modified_count"]:
                _diff_parts.append(
                    f"git_modified:{_prev['modified_count']}->{_current_snapshot['modified_count']}"
                )
            if _current_snapshot["lsp_errors"] != _prev["lsp_errors"]:
                _diff_parts.append(
                    f"lsp_errors:{_prev['lsp_errors']}->{_current_snapshot['lsp_errors']}"
                )
            if _prev["confidence"] - _current_snapshot["confidence"] > 0.3:
                _diff_parts.append(
                    f"confidence:{_prev['confidence']:.2f}->{_current_snapshot['confidence']:.2f}"
                )
            _summary = " ".join(_diff_parts)
            _nudge = _render_template("perception_state_summary", summary=_summary)
            loop._append_message(Message(role="system", content=_nudge))
            loop._recorder.append(
                event_id=f"perception_change_{loop._step_count}",
                ts=int(time.time() * 1000),
                event_type=EventType.SYSTEM_INJECTION,
                payload={"reason": "perception_state_change", "nudge": _nudge[:200]},
            )
    # 更新上一步快照
    loop._prev_perception_snapshot = _current_snapshot
