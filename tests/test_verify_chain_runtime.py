"""RunRecorder verify_chain 运行时自检 + CLI 命令 invariant tests.

IPR-0: each test must contain a counterexample.

Corresponds to:
  §12.1 Verifiability: 运行时链完整性自检 (MASTER.md §12.1 + §3.1.4)
  §6.1    RunRecorder verify_chain O(n) 链遍历
  IPR-3   core/ 只用 stdlib + pydantic/cryptography
"""

from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zall.core.goal import TerminationState
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.loop_events import RunEgress
from zall.core.model import (
    ModelResponse,
    StopReason,
    ToolCall,
)
from zall.core.verifiability import EventType, RunRecorder, TimelineEvent

# ── Reuse fakes from test_loop_invariants ──
from tests.test_loop_invariants import (
    _AutoAcceptResponder,
    _CwdMetaStub,
    _EchoTool,
    _make_context,
    _make_goal,
    _ScriptedAdapter,
    _AlwaysMetJudge,
)


def _make_loop(adapter: _ScriptedAdapter) -> AgentLoop:
    """minimal AgentLoop for test (借用了 test_loop_invariants 的 fakes)。"""
    return AgentLoop(
        model=adapter,
        tools=MagicMock(tools=(_EchoTool(),), schemas=[_EchoTool().schema]),
        rules=MagicMock(),
        goal=_make_goal(),
        context=_make_context(),
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(judge=_AlwaysMetJudge()),
    )


# ──────────────────────────────────────────────────────────────────────────
# §12.1 运行时自检不变量
# ──────────────────────────────────────────────────────────────────────────


class TestRunEndCallsVerifyChain:
    """run 结束时 verify_chain 被调用。"""

    def test_verify_chain_called_on_run_end(self) -> None:
        """Counterexample: 若 run 结束时未调 verify_chain, 则无法检测篡改。

        正常路径: model STOP → Judge met → run 结束 → verify_chain 被调用。
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        original = loop._recorder.verify_chain
        call_count = 0

        def _counting_verify() -> bool:
            nonlocal call_count
            call_count += 1
            return original()

        loop._recorder.verify_chain = _counting_verify
        egress = loop.run()

        assert call_count >= 1, "verify_chain must be called at least once during run()"
        assert egress.chain_warning is None  # 正常链无警告


class TestBrokenChainMarkedInEgress:
    """模拟 verify_chain 返回 False 时 RunEgress 含 chain_warning。"""

    def test_broken_chain_sets_chain_warning(self) -> None:
        """Counterexample: 链断裂时 chain_warning 为空 → 篡改不可发现。

        模拟 recorder 链断裂 → _check_chain_integrity 返回警告 → egress.chain_warning 非空。
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)

        def _broken_verify() -> bool:
            return False

        loop._recorder.verify_chain = _broken_verify
        egress = loop.run()

        assert egress.chain_warning is not None
        assert "FAILED" in egress.chain_warning
        assert "chain" in egress.chain_warning.lower()

    def test_broken_chain_records_chain_broken_event(self) -> None:
        """Counterexample: 链断裂但未记录 CHAIN_BROKEN 事件 → 审计轨迹缺失。

        模拟链断裂后, timeline 应包含一条 CHAIN_BROKEN 事件。
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)

        def _broken_verify() -> bool:
            return False

        loop._recorder.verify_chain = _broken_verify
        egress = loop.run()

        # 检查 timeline 中是否有 CHAIN_BROKEN 事件
        chain_broken_events = [
            ev for ev in loop.recorder.events
            if ev.event_type == EventType.CHAIN_BROKEN
        ]
        assert len(chain_broken_events) == 1
        assert chain_broken_events[0].payload.get("step") is not None

    def test_broken_chain_does_not_raise(self) -> None:
        """Counterexample: 链断裂抛异常 → run 崩溃, 违反"检测不阻止"原则。

        链断裂时 run 仍应返回 egress, 不 raise。
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)

        def _broken_verify() -> bool:
            return False

        loop._recorder.verify_chain = _broken_verify
        egress = loop.run()

        assert egress is not None
        assert egress.final_state == TerminationState.MET


class TestValidChainNoWarning:
    """verify_chain True 时 RunEgress 无 chain_warning。"""

    def test_valid_chain_no_warning(self) -> None:
        """Counterexample: 链完整但 chain_warning 非空 → 误报。

        正常 run 结束时链完整, egress.chain_warning 应为 None。
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        egress = loop.run()

        assert egress.chain_warning is None, "valid chain must not produce warning"
        assert loop.recorder.verify_chain() is True


# ──────────────────────────────────────────────────────────────────────────
# CLI /verify 命令测试
# ──────────────────────────────────────────────────────────────────────────


class TestCliVerifyCommand:
    """/verify 命令输出完整性。"""

    def test_cli_verify_with_active_loop(self) -> None:
        """Counterexample: /verify 输出不含 chain 状态 → 用户无法复核。

        使用当前 loop 的 recorder 验证, 输出应含 "valid" 或 "BROKEN"。
        """
        from zall.cli.commands.system import cmd_verify

        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop.run()  # 跑完, recorder 有事件

        out = StringIO()
        result = cmd_verify("", out, loop=loop)
        output = out.getvalue()

        assert result == "handled"
        assert "valid" in output or "BROKEN" in output
        assert "chain:" in output
        assert "events:" in output
        assert "tail_hash:" in output

    def test_cli_verify_outputs_broken(self) -> None:
        """Counterexample: 链断裂时 /verify 输出 BROKEN 而非 valid。

        篡改后链断裂, /verify 应输出 "BROKEN"。
        """
        from zall.cli.commands.system import cmd_verify

        # 构造一个 recorder 并手动篡改
        recorder = RunRecorder("test_run")
        recorder.append("ev1", int(time.time() * 1000), EventType.MODEL_CALL, {"model": "test"})
        recorder.append("ev2", int(time.time() * 1000), EventType.TOOL_CALL_START, {"tool_id": "echo"})
        # 篡改: 修改第一条事件
        recorder._events[0] = TimelineEvent(
            event_id="ev1_tampered",
            ts=recorder._events[0].ts,
            event_type=EventType.MODEL_CALL,
            payload={"model": "tampered"},
            prev_hash=recorder._events[0].prev_hash,
        )
        recorder._events_cache = None

        assert recorder.verify_chain() is False  # 确认篡改有效

        mock_loop = MagicMock()
        mock_loop.recorder = recorder
        mock_loop.run_id = "test_run"

        out = StringIO()
        result = cmd_verify("", out, loop=mock_loop)
        output = out.getvalue()

        assert result == "handled"
        assert "BROKEN" in output

    def test_cli_verify_no_run_id_uses_latest(self) -> None:
        """Counterexample: 不传 run_id 时不用最近 session → 用户困惑。

        模拟 _find_latest_session 返回假 session, 验证输出含 chain 状态。
        """
        from zall.cli.commands.system import cmd_verify, _load_timeline_events, _find_latest_session

        # 构造假 timeline.jsonl 内容
        recorder = RunRecorder("fake_run")
        recorder.append("ev1", 1000, EventType.MODEL_CALL, {"model": "test"})
        recorder.append("ev2", 1001, EventType.TOOL_CALL_START, {"tool_id": "echo"})

        fake_events = []
        for ev in recorder.events:
            fake_events.append({
                "event_id": ev.event_id,
                "ts": ev.ts,
                "event_type": ev.event_type.value,
                "payload": ev.payload,
                "prev_hash": ev.prev_hash,
            })

        fake_session_dir = Path("/tmp/fake_session")
        fake_session_dir_mock = MagicMock()
        fake_session_dir_mock.name = "fake_run_1234"

        with patch("zall.cli.commands.system._find_latest_session",
                   return_value=fake_session_dir_mock):
            with patch("zall.cli.commands.system._load_timeline_events",
                       return_value=fake_events):
                with patch("zall.cli.commands.system._get_anchor_status",
                           return_value="not anchored"):
                    out = StringIO()
                    result = cmd_verify("", out, loop=None)
                    output = out.getvalue()

        assert result == "handled"
        assert "valid" in output or "BROKEN" in output
        assert "events:" in output

    def test_cli_verify_active_loop_overrides_session(self) -> None:
        """Counterexample: 有活跃 loop 时 /verify 用 session 而非 loop → 数据不一致。

        有活跃 loop 时, 即使不传 run_id, 也应用当前 loop 的 recorder。
        """
        from zall.cli.commands.system import cmd_verify

        recorder = RunRecorder("active_run")
        recorder.append("ev1", 1000, EventType.MODEL_CALL, {"model": "test"})

        mock_loop = MagicMock()
        # 直接用真实 recorder (它有 events/tail_hash/verify_chain), 不要 mock 这些
        mock_loop.recorder = recorder

        out = StringIO()
        result = cmd_verify("", out, loop=mock_loop)
        output = out.getvalue()

        assert result == "handled"
        assert "active_run" in output or "valid" in output


# ──────────────────────────────────────────────────────────────────────────
# RunEgress 结构不变量
# ──────────────────────────────────────────────────────────────────────────


class TestRunEgressChainWarning:
    """RunEgress.chain_warning 字段不变量。"""

    def test_chain_warning_field_exists(self) -> None:
        """Counterexample: RunEgress 无 chain_warning 字段 → 运行时无法标记篡改。

        验证 RunEgress 模型定义包含 chain_warning 可选字段。
        """
        egress = RunEgress(
            run_id="test",
            final_state=TerminationState.UNDECIDABLE,
            step_count=0,
            total_tool_calls=0,
            total_model_calls=0,
            chain_warning=None,
        )
        assert egress.chain_warning is None

        egress_with_warning = RunEgress(
            run_id="test",
            final_state=TerminationState.UNDECIDABLE,
            step_count=0,
            total_tool_calls=0,
            total_model_calls=0,
            chain_warning="chain broken",
        )
        assert egress_with_warning.chain_warning == "chain broken"

    def test_chain_warning_default_none(self) -> None:
        """Counterexample: chain_warning 默认值非 None → 兼容性断裂。

        确保现有代码不传 chain_warning 时默认为 None。
        """
        egress = RunEgress(
            run_id="test",
            final_state=TerminationState.UNDECIDABLE,
            step_count=0,
            total_tool_calls=0,
            total_model_calls=0,
        )
        assert egress.chain_warning is None