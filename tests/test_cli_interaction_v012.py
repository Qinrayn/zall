"""v0.0.12 interaction layer overhaul test (对齐主stream agent 水准).

covers:
  1. §9.2.1/§9.2.5 Goal 锁定卡片 + 确认 (auto / interactive y / reject)
  2. §9.2.4 greylist `e`(改参) / `a`(本次允许), 且 `a` 不豁免 blacklist
  3. §9.2.3 edit_file 显示 diff 预览 (artifacts["diff"])
  4. §9.2.5 plan_mode (只读姿态): 写tool强制 greylist 需确认
  5. Goal 卡片渲染 + AgentLoop.goal 属性

IPR-0: 每个testincludes counterexamples.
"""

from __future__ import annotations

import io
from pathlib import Path

from zall.cli import app as app_mod
from zall.cli.render import render_goal_card
from zall.cli.responder import CliUserResponder
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import UserResponse, UserResponseType
from zall.core.loop import AgentLoop
from zall.core.model import ModelResponse, StopReason, ToolCall, ToolChoice
from zall.core.refiner import GoalRefiner
from zall.core.safety import Judgement, Rule, RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.tools.edit_file import EditFileTool


# ──────────────────────────────────────────────────────────────────────────
# test助手
# ──────────────────────────────────────────────────────────────────────────


class _FakeStdin:
    def isatty(self) -> bool:
        return False


class _FakeStdinTTY:
    def isatty(self) -> bool:
        return True


class _CwdMetaStub:
    cwd_path = "/tmp"
    git_branch = None
    git_remote = None


def _refined_goal(text: str = "do something") -> "GoalTriple":
    return GoalRefiner.refine(text, judge_mode="none").refined_goal


def _make_context() -> Context:
    return Context(user_raw="x", cwd_meta=_CwdMetaStub())


class _CountingResponder:
    """记录 greylist ask次数 (plan_mode integration tests用)."""

    def __init__(self) -> None:
        self.greylist_asks = 0

    def ask(self, action: Action, judgement: Judgement) -> UserResponse:
        if judgement.level == SafeLevel.GREYLIST:
            self.greylist_asks += 1
            return UserResponse(response_type=UserResponseType.ACCEPT)
        return UserResponse(response_type=UserResponseType.REJECT)


class _FakeWriteTool:
    """minimal write_file 替身 (只成功returns)."""

    @property
    def tool_id(self) -> str:
        return "write_file"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "write",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="written")


class _ScriptAdapter:
    """按脚本returnspreset response的 fake adapter (不调真 API)."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-v012"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        r = self._responses[self._idx]
        self._idx += 1
        return r


def _write_then_stop_script() -> list[ModelResponse]:
    return [
        ModelResponse(
            content="writing",
            tool_calls=(ToolCall(id="t1", tool_id="write_file",
                                 args={"path": "a.txt", "content": "b"}),),
            stop_reason=StopReason.TOOL_USE,
        ),
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ]


# ──────────────────────────────────────────────────────────────────────────
# §9.2.1 / §9.2.5 Goal lockconfirm
# ──────────────────────────────────────────────────────────────────────────


class TestGoalConfirm:
    def test_yes_mode_auto_confirms(self) -> None:
        """Happy path: --yes pattern自动confirm Goal (不blocking)."""
        buf = io.StringIO()
        goal = _refined_goal("fix the bug")
        ok = app_mod._confirm_goal(buf, goal, judge_mode="none", yes=True)
        assert ok is True
        assert "Goal" in buf.getvalue()

    def test_non_interactive_auto_confirms(self, monkeypatch) -> None:
        """Happy path: non-交互 (stdin non- TTY) 自动confirm (zall 'task' 一次性 / test)."""
        buf = io.StringIO()
        monkeypatch.setattr("sys.stdin", _FakeStdin())
        goal = _refined_goal("fix the bug")
        ok = app_mod._confirm_goal(buf, goal, judge_mode="none", yes=False)
        assert ok is True
        assert "Goal" in buf.getvalue()

    def test_interactive_y_accepts(self, monkeypatch) -> None:
        """Happy path: 交互式input y → confirm."""
        buf = io.StringIO()
        monkeypatch.setattr("sys.stdin", _FakeStdinTTY())
        seq = iter(["y"])
        goal = _refined_goal("fix the bug")
        ok = app_mod._confirm_goal(
            buf, goal, judge_mode="none", yes=False,
            input_fn=lambda _: next(seq),
        )
        assert ok is True

    def test_interactive_empty_rejects(self, monkeypatch) -> None:
        """Counterexample: 交互式空input → reject (defaultsecurity)."""
        buf = io.StringIO()
        monkeypatch.setattr("sys.stdin", _FakeStdinTTY())
        seq = iter([""])
        goal = _refined_goal("fix the bug")
        ok = app_mod._confirm_goal(
            buf, goal, judge_mode="none", yes=False,
            input_fn=lambda _: next(seq),
        )
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────
# §9.2.4 greylist `e` (改参) / `a` (本次允许)
# ──────────────────────────────────────────────────────────────────────────


class TestGreylistEditAlways:
    def test_edit_bash_returns_modify(self) -> None:
        """Happy path: greylist `e` → 让用户改 command, returns MODIFY + 新 Action."""
        seq = iter(["e", "echo hello"])
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: next(seq), print_fn=lambda _: None,
        )
        action = Action(tool_id="bash", args={"command": "rm -rf /tmp/x"})
        resp = r.ask(action, Judgement(level=SafeLevel.GREYLIST))
        assert resp.response_type == UserResponseType.MODIFY
        assert resp.modified_action is not None
        assert resp.modified_action.args["command"] == "echo hello"

    def test_always_allows_session(self) -> None:
        """Happy path: greylist `a` 允许后, 同tool再次调用自动通过 (不重复问)."""
        seq = iter(["a"])
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: next(seq), print_fn=lambda _: None,
        )
        action = Action(tool_id="bash", args={"command": "ls"})
        resp1 = r.ask(action, Judgement(level=SafeLevel.GREYLIST))
        assert resp1.response_type == UserResponseType.ACCEPT
        # 第二次相同tool → session允许, 不再问 (ask_fn 不被消费)
        resp2 = r.ask(action, Judgement(level=SafeLevel.GREYLIST))
        assert resp2.response_type == UserResponseType.ACCEPT

    def test_always_does_not_bypass_blacklist(self) -> None:
        """Counterexample: `a` (greylist) 不豁免 blacklist —— blacklist 仍需显式 override 理由."""
        seq = iter(["a", ""])  # 'a' 给 greylist; 空理由给 blacklist → reject
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: next(seq), print_fn=lambda _: None,
        )
        grey = Action(tool_id="bash", args={"command": "ls"})
        r.ask(grey, Judgement(level=SafeLevel.GREYLIST))  # 消费 'a', 加入 allow 集
        black = Action(tool_id="rm_tool", args={"command": "rm -rf"})
        resp = r.ask(black, Judgement(level=SafeLevel.BLACKLIST))
        # 'a' 不影响 blacklist; 空 override 理由 → REJECT
        assert resp.response_type == UserResponseType.REJECT


# ──────────────────────────────────────────────────────────────────────────
# §9.2.3 edit_file diff 预览
# ──────────────────────────────────────────────────────────────────────────


class TestEditDiff:
    def test_edit_file_emits_diff(self, tmp_path: Path) -> None:
        """Happy path: edit_file 成功replace → artifacts 含 bounded unified diff."""
        p = tmp_path / "f.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        tool = EditFileTool()
        res = tool.execute({
            "path": str(p),
            "old_string": "line2",
            "new_string": "lineTWO",
        })
        assert res.success
        assert "diff" in res.artifacts
        diff = res.artifacts["diff"]
        assert "-line2" in diff
        assert "+lineTWO" in diff
        # bounded: ≤ ~40 行 (此例远小于)
        assert len(diff.splitlines()) < 40


# ──────────────────────────────────────────────────────────────────────────
# §9.2.5 plan_mode (只读姿态)
# ──────────────────────────────────────────────────────────────────────────


class TestPlanMode:
    def test_plan_mode_forces_write_greylist(self) -> None:
        """Happy path: plan_mode=True 时, 原本 whitelist 的写tool被强制 greylist (需confirm)."""
        wl_rule = Rule(
            rule_id="wl_write", tool_id_pattern="write_file",
            level=SafeLevel.WHITELIST,
        )
        responder = _CountingResponder()
        loop = AgentLoop(
            model=_ScriptAdapter(_write_then_stop_script()),
            tools=ToolRegistry(tools=(_FakeWriteTool(),)),
            rules=RuleSet(user_local_rules=(wl_rule,)),
            goal=_refined_goal("add a new feature endpoint"),
            context=_make_context(),
            user_responder=responder,
            plan_mode=True,
        )
        loop.run(system_prompt="")
        # 写tool经 greylist 走了confirm (被问到)
        assert responder.greylist_asks >= 1

    def test_plan_mode_off_write_whitelisted_no_ask(self) -> None:
        """Counterexample: plan_mode=False 时, whitelist 写tooldirectlyexecute (不confirm)."""
        wl_rule = Rule(
            rule_id="wl_write", tool_id_pattern="write_file",
            level=SafeLevel.WHITELIST,
        )
        responder = _CountingResponder()
        loop = AgentLoop(
            model=_ScriptAdapter(_write_then_stop_script()),
            tools=ToolRegistry(tools=(_FakeWriteTool(),)),
            rules=RuleSet(user_local_rules=(wl_rule,)),
            goal=_refined_goal("add a new feature endpoint"),
            context=_make_context(),
            user_responder=responder,
            plan_mode=False,
        )
        loop.run(system_prompt="")
        # non- plan pattern: whitelist directlyexecute, 不应被问
        assert responder.greylist_asks == 0


# ──────────────────────────────────────────────────────────────────────────
# Goal 卡片渲染 + loop.goal property
# ──────────────────────────────────────────────────────────────────────────


class TestGoalCardAndProperty:
    def test_render_goal_card_non_tty(self) -> None:
        """Happy path: non- TTY downgrade纯文本, 含type/intent/terminate判据."""
        buf = io.StringIO()
        goal = _refined_goal("fix the login bug")
        render_goal_card(goal, "none", buf)
        out = buf.getvalue()
        assert "Goal" in out
        assert "bugfix" in out
        assert "fix the login bug" in out

    def test_loop_goal_property(self) -> None:
        """Happy path: AgentLoop.goal 暴露lock的 Goal (UX 只读seam)."""
        goal = _refined_goal()
        loop = AgentLoop(
            model=_ScriptAdapter(_write_then_stop_script()),
            tools=ToolRegistry(tools=(_FakeWriteTool(),)),
            rules=RuleSet(),
            goal=goal,
            context=_make_context(),
            user_responder=_CountingResponder(),
        )
        assert loop.goal is goal
