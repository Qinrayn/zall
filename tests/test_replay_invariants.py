"""replay invariant test (§6.2 复现).

IPR-0: each test must contain a counterexample.

Protected core invariants (§6.2):
  1. ReplayAdapter.complete does not call real model (no HTTP requests)
  2. ReplayTool.execute does not touch filesystem (returns recorded output)
  3. replay 产出的 step_count / tool_calls 与原 meta 一致 (reproduced behavior)
  4. parse_timeline correctly解析 model_call + tool_call_end
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zall.cli.replay import (
    ReplayAdapter, ReplayTool, parse_timeline, replay_session, compare_egress,
)
from zall.core.model import ModelResponse, StopReason, ToolCall, ToolChoice


# ──────────────────────────────────────────────────────────────────────────
# ReplayAdapter
# ──────────────────────────────────────────────────────────────────────────


class TestReplayAdapter:
    def test_returns_recorded_responses_in_order(self) -> None:
        """Happy path: 按sequentialreturns recorded ModelResponse."""
        calls = [
            {"model": "fake", "stop_reason": "tool_use", "content": "echoing",
             "tool_calls": [{"id": "tc1", "tool_id": "echo", "args": {"text": "hi"}}]},
            {"model": "fake", "stop_reason": "stop", "content": "done", "tool_calls": []},
        ]
        adapter = ReplayAdapter(calls)
        r1 = adapter.complete([], [])
        assert r1.stop_reason == StopReason.TOOL_USE
        assert r1.content == "echoing"
        assert len(r1.tool_calls) == 1
        assert r1.tool_calls[0].tool_id == "echo"

        r2 = adapter.complete([], [])
        assert r2.stop_reason == StopReason.STOP
        assert r2.content == "done"

    def test_exhausted_returns_stop(self) -> None:
        """Happy path: recorded 用完 → STOP (防无限循环)."""
        adapter = ReplayAdapter([])
        r = adapter.complete([], [])
        assert r.stop_reason == StopReason.STOP

    def test_does_not_call_real_api(self) -> None:
        """Counterexample: ReplayAdapter.complete no HTTP requests (§6.2 does not call real model).

        如果 ReplayAdapter 内部调了 httpx, 这里 mock 会捕获.
        """
        calls = [{"model": "fake", "stop_reason": "stop", "content": "hi", "tool_calls": []}]
        adapter = ReplayAdapter(calls)
        # mock httpx.Client confirm不被调
        with patch("httpx.Client") as mock_client:
            adapter.complete([], [])
            assert not mock_client.called, "ReplayAdapter 不应调 httpx"


# ──────────────────────────────────────────────────────────────────────────
# ReplayTool
# ──────────────────────────────────────────────────────────────────────────


class TestReplayTool:
    def test_returns_recorded_output(self) -> None:
        """Happy path: returns recorded ToolResult output."""
        results = [
            {"tool_id": "echo", "success": True, "output": "echoed: hi", "error": None},
        ]
        tool = ReplayTool("echo", results)
        r = tool.execute({"text": "hi"})
        assert r.success
        assert r.output == "echoed: hi"

    def test_does_not_touch_filesystem(self) -> None:
        """Counterexample: ReplayTool.execute does not touch filesystem (§6.2 不真execute).

        如果 ReplayTool 内部调了 open/write, 这里能检测到.
        """
        tool = ReplayTool("write_file", [
            {"tool_id": "write_file", "success": True, "output": "wrote", "error": None},
        ])
        with patch("builtins.open", side_effect=AssertionError("ReplayTool 不应 open 文件")):
            r = tool.execute({"path": "/tmp/x", "content": "x"})
            # 没触发 AssertionError = 没调 open
            assert r.output == "wrote"


# ──────────────────────────────────────────────────────────────────────────
# parse_timeline
# ──────────────────────────────────────────────────────────────────────────


class TestParseTimeline:
    def test_parses_model_calls_and_tool_results(self, tmp_path: Path) -> None:
        """Happy path: correctlyparse model_call + tool_call_end."""
        # construct一个假 session
        session = tmp_path / "test_session"
        session.mkdir()
        timeline = [
            {"event_id": "m1", "ts": 1000, "event_type": "model_call",
             "payload": {"model": "fake", "stop_reason": "tool_use", "content": "echo",
                         "tool_calls": [{"id": "tc1", "tool_id": "echo", "args": {"text": "hi"}}],
                         "content_length": 4, "tool_calls_count": 1},
             "prev_hash": "0"*64, "hash": "a"*64},
            {"event_id": "t1_end", "ts": 1002, "event_type": "tool_call_end",
             "payload": {"tool_id": "echo", "success": True, "output": "echoed: hi",
                         "output_length": 11, "error": None},
             "prev_hash": "a"*64, "hash": "b"*64},
            {"event_id": "m2", "ts": 2000, "event_type": "model_call",
             "payload": {"model": "fake", "stop_reason": "stop", "content": "done",
                         "tool_calls": [], "content_length": 4, "tool_calls_count": 0},
             "prev_hash": "b"*64, "hash": "c"*64},
        ]
        with open(session / "timeline.jsonl", "w") as f:
            for ev in timeline:
                f.write(json.dumps(ev) + "\n")
        with open(session / "meta.json", "w") as f:
            json.dump({"run_id": "test", "final_state": "undecidable",
                       "step_count": 2, "tool_calls": 1, "model_calls": 2, "error": None}, f)

        parsed = parse_timeline(session)
        assert parsed is not None
        assert len(parsed.model_calls) == 2
        assert parsed.model_calls[0]["content"] == "echo"
        assert "echo" in parsed.tool_results
        assert len(parsed.tool_results["echo"]) == 1
        assert parsed.original_meta["final_state"] == "undecidable"

    def test_missing_timeline_returns_none(self, tmp_path: Path) -> None:
        """Counterexample: 无 timeline → None."""
        session = tmp_path / "empty"
        session.mkdir()
        (session / "meta.json").write_text("{}")
        assert parse_timeline(session) is None


# ──────────────────────────────────────────────────────────────────────────
# replay_session 端到端
# ──────────────────────────────────────────────────────────────────────────


class TestReplaySession:
    def test_replay_reproduces_steps_and_tools(self, tmp_path: Path) -> None:
        """Happy path: replay 产出的 step_count / tool_calls 与原一致 (reproduced behavior).

        §6.2: replay 复现结论, 不复现生成.
        step_count / tool_calls 一致 = 行for复现成功.
        final_state 不一定一致 (replay 用 NoOpJudge → 恒 undecidable).
        """
        # construct一个两步 session (tool_use → stop)
        session = tmp_path / "repl_test"
        session.mkdir()
        timeline = [
            {"event_id": "m1", "ts": 1000, "event_type": "model_call",
             "payload": {"model": "fake", "stop_reason": "tool_use", "content": "echo",
                         "tool_calls": [{"id": "tc1", "tool_id": "echo", "args": {"text": "hi"}}]},
             "prev_hash": "0"*64, "hash": "a"*64},
            {"event_id": "t1_end", "ts": 1002, "event_type": "tool_call_end",
             "payload": {"tool_id": "echo", "success": True, "output": "echoed: hi"},
             "prev_hash": "a"*64, "hash": "b"*64},
            {"event_id": "m2", "ts": 2000, "event_type": "model_call",
             "payload": {"model": "fake", "stop_reason": "stop", "content": "done",
                         "tool_calls": []},
             "prev_hash": "b"*64, "hash": "c"*64},
        ]
        with open(session / "timeline.jsonl", "w") as f:
            for ev in timeline:
                f.write(json.dumps(ev) + "\n")
        with open(session / "meta.json", "w") as f:
            json.dump({"run_id": "test", "final_state": "undecidable",
                       "step_count": 2, "tool_calls": 1, "model_calls": 2, "error": None}, f)

        result = replay_session(session)
        assert result is not None
        egress, meta = result

        # 行for复现: step_count + tool_calls 一致
        assert egress.step_count == meta["step_count"]
        assert egress.total_tool_calls == meta["tool_calls"]

    def test_compare_egress_detects_match(self) -> None:
        """Happy path: step+tool 一致 → reproduced=True."""
        from zall.core.loop_events import RunEgress
        from zall.core.goal import TerminationState
        eg = RunEgress(run_id="x", final_state=TerminationState.UNDECIDABLE,
                       step_count=2, total_tool_calls=1, total_model_calls=2)
        meta = {"final_state": "met", "step_count": 2, "tool_calls": 1}
        cmp = compare_egress(eg, meta)
        assert cmp["reproduced"] is True
        assert cmp["step_match"] is True
        assert cmp["tool_match"] is True

    def test_compare_egress_detects_divergence(self) -> None:
        """Counterexample: step 不一致 → reproduced=False."""
        from zall.core.loop_events import RunEgress
        from zall.core.goal import TerminationState
        eg = RunEgress(run_id="x", final_state=TerminationState.UNDECIDABLE,
                       step_count=3, total_tool_calls=1, total_model_calls=3)
        meta = {"final_state": "met", "step_count": 2, "tool_calls": 1}
        cmp = compare_egress(eg, meta)
        assert cmp["reproduced"] is False
        assert cmp["step_match"] is False
