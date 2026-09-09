"""工具重复调用梯度惩罚 (RepeatGuard, kimi dedup/repeat 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-REPEAT-1: 连击升级链 — streak 3→r1, 5→r2, 8→r3, 12→stop (force_stop 置位);
              换参数/换工具即断连击 (反例孪生); reset() 清零。
  I-REPEAT-2: e2e — 连击提醒追加在**工具结果内** (非独立消息);
              达 12 连击后回合优雅止损 (awaiting_input, 非 error terminal)。
  I-REPEAT-3: 同步内去重 — 同一步内完全相同的调用只真实执行一次,
              重复者得到占位结果且 tool_call 配对不破 (每 id 有 tool 消息)。
  I-REPEAT-4: 新回合 (add_user_message) 复位连击 (反例: 不复位则跨回合误罚)。
"""

from __future__ import annotations

import pytest

from zall.core.context import Context
from zall.core.gate import UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.model import ModelResponse, StopReason, ToolCall
from zall.core.repeat_guard import (
    FORCE_STOP_STREAK,
    RepeatGuard,
)
from zall.core.safety import RuleSet
from zall.core.tool import ToolRegistry, ToolResult


# ── fakes ──


class _EchoTool:
    __test__ = False
    tool_id = "echo"
    schema = {"type": "function", "function": {
        "name": "echo", "parameters": {"type": "object", "properties": {}}}}

    def __init__(self) -> None:
        self.executions = 0

    def execute(self, args: dict) -> ToolResult:
        self.executions += 1
        return ToolResult(success=True, output=f"echoed #{self.executions}")


class _Accept:
    __test__ = False

    def ask(self, action, judgement):
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _UserTermination:
    exposed_dependency_set = None

    def __call__(self, state: object) -> TerminationState:
        return TerminationState.UNDECIDABLE


class _Cwd:
    cwd_path = "/tmp/p"
    git_branch = "main"
    git_remote = "origin"


class _RepeatAdapter:
    """永远重复同一个 echo 调用, 直到脚本耗尽 (触发升级链)。"""

    __test__ = False

    def __init__(self, n_repeats: int) -> None:
        self._left = n_repeats

    @property
    def model_name(self) -> str:
        return "fake-repeat"

    def complete(self, messages, tools, tool_choice=None) -> ModelResponse:
        if self._left <= 0:
            return ModelResponse(content="giving up", stop_reason=StopReason.STOP)
        self._left -= 1
        return ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id=f"c{self._left}", tool_id="echo", args={"x": 1}),))


def _make_loop(adapter, tools: tuple) -> AgentLoop:
    goal = GoalTriple(
        statement=GoalStatement(
            intent="t", rewriting="t", rewrite_confidence=0.9,
            goal_type=GoalType.DOCS, translation_of=("seg1",), added_intent=(),
        ),
        termination=_UserTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=tools),
        rules=RuleSet(),
        goal=goal,
        context=Context(user_raw="t", cwd_meta=_Cwd()),
        user_responder=_Accept(),
    )


# ── I-REPEAT-1: 升级链单元不变量 ──


def test_streak_escalation_ladder() -> None:
    g = RepeatGuard()
    actions = []
    for _ in range(FORCE_STOP_STREAK):
        a, _txt = g.note_call("grep", {"pattern": "x"})
        actions.append(a)
    assert actions[:2] == ["none", "none"]
    assert actions[2] == "r1"            # 第 3 次
    assert actions[4] == "r2"            # 第 5 次
    assert actions[7] == "r3"            # 第 8 次
    assert actions[11] == "stop"         # 第 12 次
    assert g.force_stop is True


def test_streak_breaks_on_different_args() -> None:
    """反例孪生: 换参数/换工具即断连击, 永不升级。"""
    g = RepeatGuard()
    for i in range(20):
        a, txt = g.note_call("grep", {"pattern": f"x{i}"})
        assert a == "none" and txt is None
    assert g.force_stop is False
    # 换工具同理
    a1, _ = g.note_call("grep", {"p": 1})
    a2, _ = g.note_call("glob", {"p": 1})
    assert (a1, a2) == ("none", "none")


def test_reset_clears_streak() -> None:
    g = RepeatGuard()
    for _ in range(5):
        g.note_call("echo", {})
    g.reset()
    a, txt = g.note_call("echo", {})
    assert a == "none" and txt is None and g.streak == 1


def test_args_key_order_insensitive() -> None:
    """参数键序不同 = 同一调用 (规范化键)。"""
    g = RepeatGuard()
    g.note_call("t", {"a": 1, "b": 2})
    g.note_call("t", {"b": 2, "a": 1})
    assert g.streak == 2


# ── I-REPEAT-2: e2e 提醒进工具结果 + 强制止损 ──


def test_reminder_appended_inside_tool_result() -> None:
    adapter = _RepeatAdapter(n_repeats=3)
    loop = _make_loop(adapter, (_EchoTool(),))
    loop.run(system_prompt="s")
    tool_msgs = [m.content or "" for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 3
    assert "[repeat notice]" not in tool_msgs[0]      # 反例: 前 2 次无提醒
    assert "[repeat notice]" not in tool_msgs[1]
    assert "[repeat notice]" in tool_msgs[2]          # 第 3 次 r1 进结果内


def test_force_stop_ends_turn_gracefully() -> None:
    adapter = _RepeatAdapter(n_repeats=30)            # 远超 12, 由止损截断
    loop = _make_loop(adapter, (_EchoTool(),))
    egress = loop.run(system_prompt="s")
    # 优雅止损: awaiting_input 路径 → 正常 egress (非 MAX_STEPS/error)
    assert "MAX_STEPS" not in (egress.error or "")
    tool_msgs = [m.content or "" for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == FORCE_STOP_STREAK        # 恰好 12 次后停
    assert "[repeat dead-end]" in tool_msgs[-1]


# ── I-REPEAT-3: 同步内去重 ──


def test_same_step_dedup_executes_once() -> None:
    class _TwinCallAdapter:
        __test__ = False
        model_name = "fake-twin"
        calls = 0

        def complete(self, messages, tools, tool_choice=None) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
                        ToolCall(id="c1", tool_id="echo", args={"x": 1}),
                        ToolCall(id="c2", tool_id="echo", args={"x": 1}),
                        ToolCall(id="c3", tool_id="echo", args={"x": 2}),
                    ))
            return ModelResponse(content="done", stop_reason=StopReason.STOP)

    tool = _EchoTool()
    loop = _make_loop(_TwinCallAdapter(), (tool,))
    loop.run(system_prompt="s")
    # 相同调用只真实执行一次; 不同参数照常执行 (反例孪生)
    assert tool.executions == 2
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 3                        # 每个 tool_call 都有配对消息
    assert any("duplicate tool call" in (m.content or "") for m in tool_msgs)


# ── I-REPEAT-4: 新回合复位 ──


def test_new_turn_resets_streak() -> None:
    adapter = _RepeatAdapter(n_repeats=2)
    loop = _make_loop(adapter, (_EchoTool(),))
    loop.run(system_prompt="s")
    assert loop._repeat_guard.streak == 2
    loop.add_user_message("next turn")
    assert loop._repeat_guard.streak == 0             # 反例: 不复位则跨回合误罚


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
