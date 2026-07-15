"""zall.eval — 5-dimensional evaluation metrics (§2).

Analyzes session data (JSONL timeline + meta.json) and produces
a structured evaluation report with anti-metric pairs.

Each metric traces to DESIGN.md §1.2 (R-Metric A),
has per-GoalType breakdown (R-Metric B),
and pairs with an anti-metric (R-Metric C).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data structures ──

@dataclass
class SessionSummary:
    run_id: str
    final_state: str
    step_count: int
    tool_calls: int
    model_calls: int
    error: str | None
    timeline_path: Path | None


@dataclass
class MetricResult:
    name: str
    value: float
    anti_name: str
    anti_value: float
    per_goal_type: dict[str, float] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    session_id: str
    metrics: list[MetricResult]
    summary: str
    health: str  # "healthy" | "warning" | "critical"


@dataclass
class TimelineCounts:
    """Aggregated counts from timeline files, populated once and shared.

    Each metric function previously re-opened and re-iterated session files.
    These counts are built in a single pass and passed to metric functions.
    """
    total_tool_calls: int = 0
    blacklist_events: int = 0
    override_events: int = 0
    mutation_events: int = 0


# ── Session loader ──

def load_session(session_dir: str | Path) -> SessionSummary | None:
    """Load a single session from directory."""
    p = Path(session_dir)
    meta_path = p / "meta.json"
    timeline_path = p / "timeline.jsonl"
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    return SessionSummary(
        run_id=meta.get("run_id", p.name),
        final_state=meta.get("final_state", "?"),
        step_count=meta.get("step_count", 0),
        tool_calls=meta.get("tool_calls", 0),
        model_calls=meta.get("model_calls", 0),
        error=meta.get("error"),
        timeline_path=timeline_path if timeline_path.exists() else None,
    )


def load_all_sessions(base_dir: str | Path | None = None) -> list[SessionSummary]:
    """Load all sessions from ~/.zall/sessions/."""
    if base_dir is None:
        base_dir = Path.home() / ".zall" / "sessions"
    base = Path(base_dir)
    if not base.exists():
        return []
    sessions = []
    for d in sorted(base.iterdir()):
        if d.is_dir():
            s = load_session(d)
            if s:
                sessions.append(s)
    return sessions


# ── Metric 1: Goal Achievement (§2.1.1) ──

def compute_goal_achievement(sessions: list[SessionSummary]) -> MetricResult:
    """Goal achievement rate + decline_rate anti-metric."""
    total = len(sessions)
    if total == 0:
        return MetricResult("goal_achievement", 0.0, "decline_rate", 0.0)

    met = sum(1 for s in sessions if s.final_state == "met")
    declined = sum(1 for s in sessions if s.error == "declined")
    achievement = met / total  # total > 0 guaranteed by early return
    decline_rate = declined / total

    notes = []
    if achievement > 0.8 and decline_rate > 0.3:
        notes.append("WARNING: high achievement + high decline = Goodhart signal")
    elif achievement > 0.6:
        notes.append(f"OK: {achievement:.0%} achievement rate")

    return MetricResult(
        "goal_achievement", achievement,
        "decline_rate", decline_rate,
        notes=notes,
    )


# ── Metric 2: Boundary Violation (§2.1.2) ──

def compute_boundary_violation(sessions: list[SessionSummary], counts: TimelineCounts | None = None) -> MetricResult:
    """Boundary violation rate + proactivity anti-metric."""
    if counts is None:
        counts = _count_timeline_events(sessions)
    total_tool_calls = counts.total_tool_calls
    if total_tool_calls == 0:
        return MetricResult("boundary_violation", 0.0, "proactivity", 0.0)

    violation_rate = (counts.blacklist_events + counts.override_events) / total_tool_calls
    proactivity = 1.0 - violation_rate  # simple proxy

    notes = []
    if violation_rate > 0.3:
        notes.append("WARNING: high boundary violation rate")
    elif violation_rate > 0.1:
        notes.append(f"CAUTION: {violation_rate:.0%} violation rate")

    return MetricResult(
        "boundary_violation", violation_rate,
        "proactivity", proactivity,
        notes=notes,
    )


# ── Metric 3: Falsifiability (§2.1.3) ──

def compute_falsifiability(sessions: list[SessionSummary], counts: TimelineCounts | None = None) -> MetricResult:
    """Falsifiability rate + test_baseline_mutation anti-metric."""
    if counts is None:
        counts = _count_timeline_events(sessions)
    total = len(sessions)
    if total == 0:
        return MetricResult("falsifiability", 0.0, "baseline_mutation", 0.0)

    # Falsifiable = not undecidable (has a clear verdict)
    falsifiable = sum(1 for s in sessions if s.final_state != "undecidable")
    falsifiability_rate = falsifiable / total

    total_tool_calls_f = counts.total_tool_calls
    mutation_rate = counts.mutation_events / total_tool_calls_f if total_tool_calls_f > 0 else 0.0

    notes = []
    if falsifiability_rate > 0.8 and mutation_rate > 0.5:
        notes.append("WARNING: high falsifiability + high mutation rate")
    elif falsifiability_rate > 0.5:
        notes.append(f"OK: {falsifiability_rate:.0%} falsifiable")

    return MetricResult(
        "falsifiability", falsifiability_rate,
        "baseline_mutation", mutation_rate,
        notes=notes,
    )


# ── Metric 4: Reproducibility (§2.1.4) ──

def compute_reproducibility(sessions: list[SessionSummary]) -> MetricResult:
    """Reproducibility rate + tamper_detected anti-metric."""
    total = len(sessions)
    if total == 0:
        return MetricResult("reproducibility", 0.0, "tamper_detected", 0.0)

    # Reproducible = has timeline + met state
    reproducible = sum(1 for s in sessions if s.timeline_path and s.final_state == "met")
    reproducibility_rate = reproducible / total

    # Tamper detection: only cryptographically-significant errors
    crypto_keywords = ("hash", "chain", "verification", "tamper", "signature")
    tampered = sum(
        1 for s in sessions
        if s.error and any(kw in s.error.lower() for kw in crypto_keywords)
    )
    tamper_rate = tampered / total

    notes = []
    if reproducibility_rate < 0.3:
        notes.append("WARNING: low reproducibility rate")
    elif tamper_rate > 0.3:
        notes.append("CAUTION: high tamper detection rate")

    return MetricResult(
        "reproducibility", reproducibility_rate,
        "tamper_detected", tamper_rate,
        notes=notes,
    )


# ── Metric 5: Resource Efficiency (§2.1.5) ──

def compute_resource_efficiency(sessions: list[SessionSummary]) -> MetricResult:
    """Resource efficiency + shortcut_signal anti-metric.

    Only met sessions are counted (R-Metric A: cross met/not_met comparison is wrong).
    """
    met_sessions = [s for s in sessions if s.final_state == "met"]
    total = len(met_sessions)
    if total == 0:
        return MetricResult("resource_efficiency", 0.0, "shortcut_signal", 0.0)

    avg_steps = sum(s.step_count for s in met_sessions) / total
    avg_tools = sum(s.tool_calls for s in met_sessions) / total
    avg_model = sum(s.model_calls for s in met_sessions) / total

    # Efficiency score: lower is better (fewer steps/tools/models to achieve met)
    # Normalize: 1.0 = ideal (1 step, 0 tools, 1 model call)
    # v0.1.1: magic numbers parameterized
    _EFF_STEPS_DENOM = 5
    _EFF_TOOLS_DENOM = 5
    _EFF_MODEL_DENOM = 3
    ideal = 1.0
    actual = (avg_steps / _EFF_STEPS_DENOM) + (avg_tools / _EFF_TOOLS_DENOM) + (avg_model / _EFF_MODEL_DENOM)
    efficiency = max(0.0, min(1.0, ideal / max(actual, 0.1)))

    # Shortcut signal: sessions with met but no tool calls (suspicious)
    shortcuts = sum(1 for s in met_sessions if s.tool_calls == 0)
    shortcut_rate = shortcuts / total

    notes = []
    if efficiency > 0.8 and shortcut_rate > 0.5:
        notes.append("WARNING: high efficiency + high shortcut = suspicious")
    elif efficiency > 0.5:
        notes.append(f"OK: efficiency {efficiency:.0%}")

    return MetricResult(
        "resource_efficiency", efficiency,
        "shortcut_signal", shortcut_rate,
        notes=notes,
    )


# ── Full evaluation ──

def _count_timeline_events(sessions: list[SessionSummary]) -> TimelineCounts:
    """Single pass through all timeline files to aggregate counts.

    Each metric function previously re-opened and re-iterated session files.
    This shared pass populates all counts at once.
    """
    counts = TimelineCounts()
    for s in sessions:
        counts.total_tool_calls += s.tool_calls
        if not s.timeline_path or not s.timeline_path.exists():
            continue
        with open(s.timeline_path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    et = ev.get("event_type", "")
                    if et == "override":
                        counts.override_events += 1
                    elif et == "gate_decision":
                        lvl = ev.get("payload", {}).get("level", "")
                        if lvl == "blacklist":
                            counts.blacklist_events += 1
                    elif et == "tool_call_end":
                        payload = ev.get("payload", {})
                        tid = payload.get("tool_id", "")
                        if tid in ("write_file", "edit_file"):
                            counts.mutation_events += 1
                except json.JSONDecodeError:
                    pass
    return counts


def evaluate(sessions: list[SessionSummary]) -> EvalReport:
    """Run all 5 metrics and produce a report."""
    if not sessions:
        return EvalReport("(no sessions)", [], "No sessions to evaluate.", "healthy")

    # Single pass through timeline files for all metrics
    counts = _count_timeline_events(sessions)

    metrics = [
        compute_goal_achievement(sessions),
        compute_boundary_violation(sessions, counts),
        compute_falsifiability(sessions, counts),
        compute_reproducibility(sessions),
        compute_resource_efficiency(sessions),
    ]

    # Overall health
    warnings = [m for m in metrics if any("WARNING" in n for n in m.notes)]

    # Emit CRITICAL notes for out-of-range metric values
    for m in metrics:
        if m.value < 0.0 or m.value > 1.0:
            m.notes.append(f"CRITICAL: {m.name}={m.value:.4f} out of range [0.0, 1.0]")

    errors = [m for m in metrics if any("CRITICAL" in n for n in m.notes)]
    if errors:
        health = "critical"
    elif warnings:
        health = "warning"
    else:
        health = "healthy"

    summary = f"Evaluated {len(sessions)} session(s): "
    summary += f"{health} ({len(warnings)} warnings, {len(errors)} errors)"

    return EvalReport(
        session_id=f"{len(sessions)} sessions",
        metrics=metrics,
        summary=summary,
        health=health,
    )


def format_report(report: EvalReport) -> str:
    """Format report as a clean text table."""
    lines = []
    lines.append("=" * 50)
    lines.append("  zall — Evaluation Report")
    lines.append(f"  Sessions: {report.session_id}")
    lines.append(f"  Health: {report.health.upper()}")
    lines.append("=" * 50)
    for m in report.metrics:
        metric_line = f"  {m.name:25s}  {m.value:6.0%}  |  {m.anti_name:25s}  {m.anti_value:6.0%}"
        lines.append(metric_line)
        for n in m.notes:
            lines.append(f"    {n}")
    lines.append("=" * 50)
    return "\n".join(lines)