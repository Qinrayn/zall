"""zall.core.eval — Evaluation from timeline (DESIGN.md §3 + §6 Phase 1).

Phase 1 评估体系落地: 从 timeline.jsonl 直接计算评估指标。

对应:
  §3 评估体系: goal_achievement_rate + timeline_integrity_rate
  §6.1 RunRecorder: timeline 是审计轨迹, 可用于评估

核心函数:
  compute_goal_achievement_rate(loop) — 从 timeline 中 goal_statement + judge_result 计算
  compute_timeline_integrity_rate(loop) — 验证 timeline 链完整性 + 事件顺序
  evaluate_from_timeline(loop) — 综合评估

IPR constraints:
  IPR-0: invariant tests at tests/test_eval_invariants.py
         test_eval_command_produces_metrics_from_timeline (反例: 无 timeline → eval 输出空)
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from zall.core.goal import TerminationState
from zall.core.verifiability import EventType, TimelineEvent


# ── 评估结果数据结构 ──


@dataclass
class CoreEvalMetric:
    """单个评估指标。"""

    name: str
    value: float
    anti_name: str
    anti_value: float
    notes: list[str] = field(default_factory=list)


@dataclass
class CoreEvalReport:
    """综合评估报告。"""

    run_id: str
    metrics: list[CoreEvalMetric]
    summary: str
    health: str  # "healthy" | "warning" | "critical"


# ── Timeline 加载器 ──


def load_timeline(session_dir: str | Path) -> list[TimelineEvent] | None:
    """从 session 目录加载 timeline.jsonl。"""
    p = Path(session_dir)
    timeline_path = p / "timeline.jsonl"
    if not timeline_path.exists():
        return None

    events: list[TimelineEvent] = []
    with open(timeline_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "metadata":
                    continue  # G12: 首行版本头, 非事件
                # 从 dict 重建 TimelineEvent
                event = TimelineEvent(
                    event_id=data["event_id"],
                    ts=data["ts"],
                    event_type=EventType(data["event_type"]),
                    payload=data.get("payload", {}),
                    prev_hash=data.get("prev_hash", "0" * 64),
                )
                events.append(event)
            except (json.JSONDecodeError, KeyError, ValueError):
                # 跳过损坏的行 (诚实退让)
                continue

    return events if events else None


# ── Metric 1: Goal Achievement Rate (§3) ──


def compute_goal_achievement_rate(events: list[TimelineEvent]) -> CoreEvalMetric:
    """从 timeline 计算 goal achievement rate。

    算法:
      1. 找 GOAL_STATEMENT 事件 (目标声明)
      2. 找 JUDGE_RESULT 事件 (判定结果)
      3. 若 judge_result.state == met → 达成
      4. 若无 judge_result, 用最后一条事件的隐含状态

    Counterexample: 如果没有 GOAL_STATEMENT 事件 (Refiner 未接入),
    本指标诚实返回 undecidable (0.0), 不假装达成。
    """
    goal_statement = _find_event(events, EventType.GOAL_STATEMENT)
    judge_result = _find_event(events, EventType.JUDGE_RESULT)

    if goal_statement is None:
        # 没有 goal statement → 无法评估 (诚实退让)
        return CoreEvalMetric(
            name="goal_achievement",
            value=0.0,
            anti_name="undecidable_rate",
            anti_value=1.0,
            notes=["no GOAL_STATEMENT event in timeline — cannot assess goal achievement"],
        )

    if judge_result is None:
        # 无 judge 结果 → 看 termination
        final_state = _infer_final_state(events)
        value = 1.0 if final_state == TerminationState.MET else 0.0
        anti_value = 1.0 - value
        note = "no JUDGE_RESULT event — inferred from termination"
        return CoreEvalMetric(
            name="goal_achievement",
            value=value,
            anti_name="not_met_rate",
            anti_value=anti_value,
            notes=[note],
        )

    # 有 judge 结果
    state_str = judge_result.payload.get("state", "")
    try:
        final_state = TerminationState(state_str)
    except ValueError:
        final_state = TerminationState.UNDECIDABLE

    value = 1.0 if final_state == TerminationState.MET else 0.0
    anti_value = 1.0 - value

    notes = []
    if final_state == TerminationState.MET:
        notes.append("goal achieved (judged met)")
    elif final_state == TerminationState.NOT_MET:
        notes.append("goal not met (judged not_met)")
    else:
        notes.append("goal undecidable (honest retreat)")

    return CoreEvalMetric(
        name="goal_achievement",
        value=value,
        anti_name="not_met_rate",
        anti_value=anti_value,
        notes=notes,
    )


# ── Metric 2: Timeline Integrity (§6.1) ──


def compute_timeline_integrity_rate(events: list[TimelineEvent]) -> CoreEvalMetric:
    """验证 timeline 链完整性 + 事件顺序。

    检查:
      1. 链式哈希: 每条 prev_hash == 前一条 compute_hash()
      2. 事件顺序: tool_call_start 必须在 tool_call_end 之前
      3. goal_statement + user_confirm 必须在第一条 tool_call_start 之前 (Phase 1 不变量)

    Counterexample: 如果 timeline 被篡改 (哈希链断裂) 或
    goal_statement 在 tool_call_start 之后, 本测试 fail。
    """
    if not events:
        return CoreEvalMetric(
            name="timeline_integrity",
            value=0.0,
            anti_name="tamper_detected",
            anti_value=1.0,
            notes=["empty timeline"],
        )

    checks_passed = 0
    total_checks = 3

    # 检查 1: 链式哈希
    chain_ok = _verify_chain_hash(events)
    if chain_ok:
        checks_passed += 1

    # 检查 2: 事件顺序 (tool_call_start before tool_call_end)
    order_ok = _verify_event_order(events)
    if order_ok:
        checks_passed += 1

    # 检查 3: Phase 1 不变量 — goal_statement + user_confirm 在第一条 tool_call_start 之前
    phase1_ok = _verify_phase1_invariants(events)
    if phase1_ok:
        checks_passed += 1

    value = checks_passed / total_checks
    anti_value = 1.0 - value

    notes = []
    if not chain_ok:
        notes.append("WARNING: timeline chain hash broken — possible tampering")
    if not order_ok:
        notes.append("WARNING: event order violated (tool_call_end before tool_call_start)")
    if not phase1_ok:
        notes.append("WARNING: Phase 1 invariant broken (goal_statement/user_confirm not before first tool_call_start)")

    return CoreEvalMetric(
        name="timeline_integrity",
        value=value,
        anti_name="tamper_detected",
        anti_value=anti_value,
        notes=notes,
    )


def _verify_chain_hash(events: list[TimelineEvent]) -> bool:
    """验证链式哈希完整性。"""
    prev = "0" * 64
    for event in events:
        if event.prev_hash != prev:
            return False
        prev = event.compute_hash()
    return True


def _verify_event_order(events: list[TimelineEvent]) -> bool:
    """验证 tool_call_start 在 tool_call_end 之前。"""
    tool_starts = [e for e in events if e.event_type == EventType.TOOL_CALL_START]
    tool_ends = [e for e in events if e.event_type == EventType.TOOL_CALL_END]

    if not tool_starts and not tool_ends:
        return True  # 无工具调用, 无顺序问题
    if not tool_starts or not tool_ends:
        return False  # 只有开始或只有结束

    # 每条 tool_call_start 必须有对应的 tool_call_end 且时间戳更大
    # 简化: 第一个 start 必须在第一个 end 之前
    first_start = min(e.ts for e in tool_starts)
    first_end = min(e.ts for e in tool_ends)
    return first_start < first_end


def _verify_phase1_invariants(events: list[TimelineEvent]) -> bool:
    """验证 Phase 1 不变量: goal_statement + user_confirm 在第一条 tool_call_start 之前。"""
    goal_stmt = _find_event(events, EventType.GOAL_STATEMENT)
    user_confirm = _find_event(events, EventType.USER_CONFIRM)
    tool_call_start = _find_event(events, EventType.TOOL_CALL_START)

    # 如果没有 tool_call_start, 不变量自动满足 (无工具调用)
    if tool_call_start is None:
        return True

    # Phase 1 不变量: goal_statement 和 user_confirm 必须在第一条 tool_call_start 之前
    if goal_stmt is None or goal_stmt.ts > tool_call_start.ts:
        return False
    if user_confirm is None or user_confirm.ts > tool_call_start.ts:
        return False
    return True


def _find_event(events: list[TimelineEvent], event_type: EventType) -> TimelineEvent | None:
    """找第一条指定类型的事件。"""
    for event in events:
        if event.event_type == event_type:
            return event
    return None


def _infer_final_state(events: list[TimelineEvent]) -> TerminationState:
    """从 timeline 推断最终状态 (无 judge 时的 fallback)。"""
    # 看最后几条事件推断
    reversed_events = list(reversed(events))
    for event in reversed_events:
        payload = event.payload
        if event.event_type == EventType.JUDGE_RESULT:
            state_str = payload.get("state", "undecidable")
            try:
                return TerminationState(state_str)
            except ValueError:
                return TerminationState.UNDECIDABLE

    # 没有 judge, 看是否有 error
    for event in reversed_events:
        if event.event_type == EventType.MODEL_CALL:
            error = event.payload.get("error")
            if error:
                return TerminationState.UNDECIDABLE

    # 默认 undecidable (诚实退让)
    return TerminationState.UNDECIDABLE


# ── 综合评估 ──


def evaluate_from_timeline(session_dir: str | Path) -> CoreEvalReport | None:
    """从 session timeline 进行综合评估。

    若 timeline 不可用, 返回 None (诚实退让)。
    """
    events = load_timeline(session_dir)
    if events is None:
        return None

    metrics = [
        compute_goal_achievement_rate(events),
        compute_timeline_integrity_rate(events),
    ]

    # Overall health
    warnings = [m for m in metrics if any("WARNING" in n for n in m.notes)]
    errors = [m for m in metrics if any("CRITICAL" in n for n in m.notes)]

    if errors:
        health = "critical"
    elif warnings:
        health = "warning"
    else:
        health = "healthy"

    run_id = events[0].event_id if events else "unknown"

    summary = f"Evaluated run {run_id}: "
    summary += f"{health} ({len(warnings)} warnings, {len(errors)} errors)"

    return CoreEvalReport(
        run_id=run_id,
        metrics=metrics,
        summary=summary,
        health=health,
    )


def format_core_eval_report(report: CoreEvalReport) -> str:
    """格式化核心评估报告为文本。"""
    lines = []
    lines.append("=" * 50)
    lines.append("  zall — Core Evaluation Report")
    lines.append(f"  Run: {report.run_id}")
    lines.append(f"  Health: {report.health.upper()}")
    lines.append("=" * 50)
    for m in report.metrics:
        metric_line = f"  {m.name:25s}  {m.value:6.0%}  |  {m.anti_name:25s}  {m.anti_value:6.0%}"
        lines.append(metric_line)
        for n in m.notes:
            lines.append(f"    {n}")
    lines.append("=" * 50)
    return "\n".join(lines)


__all__ = [
    "CoreEvalMetric",
    "CoreEvalReport",
    "load_timeline",
    "compute_goal_achievement_rate",
    "compute_timeline_integrity_rate",
    "evaluate_from_timeline",
    "format_core_eval_report",
]