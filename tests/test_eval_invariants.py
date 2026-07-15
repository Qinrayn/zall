"""Evaluation metrics invariant tests. (DESIGN.md §2)"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from zall.eval.metrics import (
    SessionSummary,
    compute_goal_achievement,
    compute_boundary_violation,
    compute_falsifiability,
    compute_reproducibility,
    compute_resource_efficiency,
    evaluate,
    format_report,
    load_session,
)

def _make_session(
    final_state: str = "met",
    steps: int = 3,
    tools: int = 1,
    models: int = 2,
    error: str | None = None,
    has_timeline: bool = True,
    add_override: bool = False,
    add_blacklist: bool = False,
) -> SessionSummary:
    """Helper to create test sessions."""
    timeline = None
    if has_timeline:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        events = [
            {"event_type": "model_call", "ts": 1000, "payload": {}},
            {"event_type": "gate_decision", "ts": 2000, "payload": {"level": "blacklist" if add_blacklist else "whitelist"}},
        ]
        if add_override:
            events.append({"event_type": "override", "ts": 3000, "payload": {"tool_id": "bash"}})
        events.append({"event_type": "tool_call_end", "ts": 4000, "payload": {"tool_id": "write_file"}})
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        f.close()
        timeline = Path(f.name)

    return SessionSummary(
        run_id="test", final_state=final_state,
        step_count=steps, tool_calls=tools, model_calls=models,
        error=error, timeline_path=timeline,
    )


class TestSessionLoader:
    def test_load_session(self) -> None:
        d = tempfile.mkdtemp()
        p = Path(d) / "meta.json"
        p.write_text(json.dumps({"run_id": "test123", "final_state": "met", "step_count": 5}))
        s = load_session(d)
        assert s is not None
        assert s.run_id == "test123"
        assert s.final_state == "met"

    def test_load_nonexistent(self) -> None:
        s = load_session("/nonexistent")
        assert s is None


class TestGoalAchievement:
    def test_all_met(self) -> None:
        ss = [_make_session("met") for _ in range(5)]
        m = compute_goal_achievement(ss)
        assert m.value == 1.0
        assert m.anti_value == 0.0

    def test_mixed(self) -> None:
        ss = [_make_session("met") for _ in range(3)] + [_make_session("undecidable")]
        m = compute_goal_achievement(ss)
        assert m.value == 0.75

    def test_empty(self) -> None:
        m = compute_goal_achievement([])
        assert m.value == 0.0


class TestBoundaryViolation:
    def test_clean(self) -> None:
        ss = [_make_session(add_blacklist=False, add_override=False)]
        m = compute_boundary_violation(ss)
        assert m.value == 0.0

    def test_violation(self) -> None:
        ss = [_make_session(add_override=True)]
        m = compute_boundary_violation(ss)
        assert m.value > 0.0


class TestFalsifiability:
    def test_all_met(self) -> None:
        ss = [_make_session("met") for _ in range(3)]
        m = compute_falsifiability(ss)
        assert m.value == 1.0

    def test_all_undecidable(self) -> None:
        ss = [_make_session("undecidable") for _ in range(3)]
        m = compute_falsifiability(ss)
        assert m.value == 0.0


class TestReproducibility:
    def test_met_with_timeline(self) -> None:
        ss = [_make_session("met", has_timeline=True)]
        m = compute_reproducibility(ss)
        assert m.value == 1.0

    def test_no_timeline(self) -> None:
        ss = [_make_session("met", has_timeline=False)]
        m = compute_reproducibility(ss)
        assert m.value == 0.0


class TestResourceEfficiency:
    def test_efficient(self) -> None:
        ss = [_make_session("met", steps=1, tools=0, models=1)]
        m = compute_resource_efficiency(ss)
        assert m.value > 0.8

    def test_no_met_sessions(self) -> None:
        ss = [_make_session("undecidable")]
        m = compute_resource_efficiency(ss)
        assert m.value == 0.0


class TestFullEvaluation:
    def test_evaluate(self) -> None:
        ss = [_make_session("met") for _ in range(3)]
        report = evaluate(ss)
        assert len(report.metrics) == 5
        assert report.health in ("healthy", "warning", "critical")

    def test_format(self) -> None:
        ss = [_make_session("met")]
        report = evaluate(ss)
        text = format_report(report)
        assert "Evaluation Report" in text
        assert "goal_achievement" in text

    def test_empty(self) -> None:
        report = evaluate([])
        assert report.health == "healthy"