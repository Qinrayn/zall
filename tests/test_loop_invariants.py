"""Agent Loop invariant + hello-world test (S0 capped at).

IPR-0: each test must contain a counterexample.
hello-world: fake adapter + fake tool run through minimal loop, 证明骨架可用.
"""

from __future__ import annotations

import pytest

from zall.core.accountability import (
    AccountabilityResult,
    CaveatType,
    Evidence,
    Judge,
    JudgeVerdict,
)
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import (
    UserResponder,
    UserResponse,
    UserResponseType,
)
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import (
    AgentLoop,
    AgentRunaway,
    RunEgress,
    ToolNotFound,
)
from zall.core.model import (
    Message,
    ModelAdapter,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.core.verifiability import EventType


# ──────────────────────────────────────────────────────────────────────────
# Fakes (hello-world basic设施)
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    """按脚本returns预设 ModelResponse 的 fake adapter.

    __test__ = False 防 pytest 误收.
    """

    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "fake-scripted"

    def complete(
        self,
        messages: list[Message],
        tools: list[dict],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        if self._call_index >= len(self._responses):
            # 脚本用完 → returns STOP 防无限循环
            return ModelResponse(content="script exhausted", stop_reason=StopReason.STOP)
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


class _EchoTool:
    """fake echo tool, returnsinputcontent."""

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "echo"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo back the input",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
        }

    def execute(self, args: dict) -> ToolResult:
        text = args.get("text", "")
        return ToolResult(success=True, output=f"echoed: {text}")


class _AutoAcceptResponder:
    """fake user responder, 对所有 greylist/blacklist 自动 ACCEPT."""

    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _AlwaysMetJudge:
    """fake Judge, 永远returns met."""

    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="all good")


class _AlwaysUndecidableJudge:
    """fake Judge, 永远returns undecidable."""

    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.UNDECIDABLE)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


def _make_goal() -> GoalTriple:
    """construct一个minimalvalid GoalTriple (用 user_judge type避免 exposed_dependency_set 要求)."""

    class _UserTermination:
        exposed_dependency_set = None

        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE

    return GoalTriple(
        statement=GoalStatement(
            intent="echo hello",
            rewriting="echo hello world",
            rewrite_confidence=0.9,
            goal_type=GoalType.DOCS,
            translation_of=("seg1",),
            added_intent=(),
        ),
        termination=_UserTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )


def _make_context() -> Context:
    return Context(user_raw="echo hello world", cwd_meta=_CwdMetaStub())


def _make_loop(
    adapter: _ScriptedAdapter,
    judge=None,
    observer=None,
) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),  # 空规则集 → 所有 tool 默认 greylist
        goal=_make_goal(),
        context=_make_context(),
        user_responder=_AutoAcceptResponder(),
        judge=judge,
        observer=observer,
    )


# ──────────────────────────────────────────────────────────────────────────
# hello-world: 完整循环跑通
# ──────────────────────────────────────────────────────────────────────────


class TestHelloWorld:
    """S0 capped at: fake adapter + fake tool 跑通完整循环."""

    def test_tool_then_stop(self) -> None:
        """Happy path: model先调 echo tool, 然后 STOP → RunEgress.

        这是 S0 骨架的minimal可用证明:
          Round 1: model → TOOL_USE (echo "hello")
          Round 2: model → STOP (done)
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="let me echo",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="echo", args={"text": "hello"}),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(
                content="done",
                stop_reason=StopReason.STOP,
            ),
        ])
        loop = _make_loop(adapter, judge=_AlwaysMetJudge())
        egress = loop.run()

        assert egress.final_state == TerminationState.MET
        assert egress.total_model_calls == 2
        assert egress.total_tool_calls == 1
        assert egress.error is None

        # RunRecorder 链完整
        assert loop.recorder.verify_chain() is True
        # 至少有 2 model_call + 1 gate_decision + 1 tool_start + 1 tool_end + 1 judge
        events = loop.recorder.events
        event_types = [e.event_type for e in events]
        assert EventType.MODEL_CALL in event_types
        assert EventType.TOOL_CALL_START in event_types
        assert EventType.TOOL_CALL_END in event_types
        assert EventType.JUDGE_RESULT in event_types

    def test_immediate_stop(self) -> None:
        """Happy path: modeldirectly STOP (不调tool) → RunEgress undecidable (无 judge 时)."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="I'm done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=None)
        egress = loop.run()

        # 无 Judge → undecidable (诚实退让, PR-0)
        assert egress.final_state == TerminationState.UNDECIDABLE
        assert egress.total_tool_calls == 0
        assert egress.total_model_calls == 1

    def test_judge_met(self) -> None:
        """Happy path: model STOP + Judge returns met → RunEgress met."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=_AlwaysMetJudge())
        egress = loop.run()
        assert egress.final_state == TerminationState.MET

    def test_judge_undecidable(self) -> None:
        """Happy path: model STOP + Judge returns undecidable → RunEgress undecidable."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="not sure", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=_AlwaysUndecidableJudge())
        egress = loop.run()
        assert egress.final_state == TerminationState.UNDECIDABLE


# ──────────────────────────────────────────────────────────────────────────
# Counterexampletest
# ──────────────────────────────────────────────────────────────────────────


class TestLoopCounterExamples:
    """Counterexample: 违规场景须correctlyhandle."""

    def test_tool_use_without_tool_calls_raises(self) -> None:
        """Counterexample: stop_reason=TOOL_USE 但 tool_calls 空 → hallucination, 须报错.

        PR-0: 模型说"我调了tool"但实际没产 ToolCall → 幻觉.
        Loop 不允许这种"假装调了tool"的情况继续.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="I ran grep and found: file.py:10: bug",
                tool_calls=(),  # 空! 幻觉!
                stop_reason=StopReason.TOOL_USE,
            ),
        ])
        loop = _make_loop(adapter, judge=None)
        egress = loop.run()
        # hallucination被捕获, not silent 通过
        assert egress.error is not None
        assert "hallucinated" in egress.error.lower() or "tool_calls is empty" in egress.error

    def test_tool_not_found(self) -> None:
        """Counterexample: model调用未register的 tool_id → 报错.

        如果 Loop 让未注册tool静默通过, agent 调任意 tool_id 都不报错 → hijack.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="calling unknown tool",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="nonexistent_tool", args={}),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=None)
        egress = loop.run()
        assert egress.error is not None

    def test_recorder_chain_intact_after_run(self) -> None:
        """Counterexample: 跑完 Loop 后 timeline 链must完整.

        如果 Loop 在记录过程中断了链, verify_chain 须 False.
        这条test证明链没断 (Happy path); 反向是: 如果有人篡改 events, 会 False.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(
                content="echo",
                tool_calls=(
                    ToolCall(id="tc1", tool_id="echo", args={"text": "hi"}),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=_AlwaysMetJudge())
        loop.run()
        assert loop.recorder.verify_chain() is True

    def test_runegress_frozen(self) -> None:
        """Counterexample: RunEgress construct后改 final_state → must raise."""
        from pydantic import ValidationError

        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=None)
        egress = loop.run()
        with pytest.raises(ValidationError):
            egress.final_state = TerminationState.MET  # type: ignore[misc]

    def test_runegress_downgrade_fields_default(self) -> None:
        """Happy path: RunEgress default无downgrade字段 (original_goal=None, downgrade_depth=0).

        Counterexample: 如果没有降级但 original_goal non- None → 误报降级.
        """
        adapter = _ScriptedAdapter([
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, judge=None)
        egress = loop.run()
        assert egress.original_goal is None
        assert egress.candidate_goals == ()
        assert egress.downgrade_depth == 0
        assert egress.final_claim != ""  # default claim is set

    def test_pr0_scan_clean_content(self) -> None:
        """Happy path: 无false造tooloutput的正常reply不触发 PR-0 warning.

        Counterexample: 如果正常文本被误判for幻觉, 会干扰正常对话.
        """
        from zall.core.loop import AgentLoop as AL
        result = AL._scan_hallucinated_content(
            "分析完成.代码已更新，建议运行 pytest verify."
        )
        assert result == ()

    def test_pr0_scan_fake_bash_prompt(self) -> None:
        """Happy path: false造 bash prompt 被 PR-0 scanner 检出.

        Counterexample: 如果 fake_bash_prompt 不被检测, 模型可以伪造tool输出不被发现.
        v0.0.22 P1 Bug 10: 正则收紧for白名单命令前缀 (sudo/apt/pip/npm/git/python/node/cd/ls/cat/cp/mv/rm/mkdir/chmod/echo),
        不再匹配任意 $ xxx (避免误判对话中的 $ 变量引用).
        """
        from zall.core.loop import AgentLoop as AL
        result = AL._scan_hallucinated_content(
            "让我执行 $ sudo apt install foo\n结果:\nReading package lists...\nDone"
        )
        assert "fake_bash_prompt" in result

    def test_pr0_scan_fake_file_delimiter(self) -> None:
        """Happy path: false造file分隔符被 PR-0 scanner 检出.

        Counterexample: 如果 fake_file_delimiter 不被检测, 模型可以伪造文件内容.
        """
        from zall.core.loop import AgentLoop as AL
        result = AL._scan_hallucinated_content(
            "文件内容如下:\n--- BEGIN FILE ---\ndef main():\n    pass\n--- END FILE ---"
        )
        assert "fake_file_delimiter" in result

    def test_pr0_scan_multiple_hallucinations(self) -> None:
        """Happy path: 多个false造pattern可同时被检出.

        Counterexample: 如果只调第一个匹配就退出, 漏掉其他伪造.
        v0.0.22 P1 Bug 10: bash prompt 正则改用白名单命令前缀 (sudo/apt/pip/npm/git/python/node/cd/ls/cat/cp/mv/rm/mkdir/chmod/echo).
        """
        from zall.core.loop import AgentLoop as AL
        result = AL._scan_hallucinated_content(
            """让我execute: $ cat main.py
    --- BEGIN FILE ---
    def main():
        return 0
    --- END FILE ---
    文件 1.2KB written."""
        )
        assert "fake_bash_prompt" in result
        assert "fake_file_delimiter" in result

    def test_runegress_has_downgrade_fields(self) -> None:
        """Happy path: RunEgress 包含 §3.4.5 downgrade字段 (S1 完整version).

        Counterexample: 如果缺少 original_goal/final_claim 字段, §3.4.5 报告义务未满足.
        """
        egress = RunEgress(
            run_id="r1",
            final_state=TerminationState.UNDECIDABLE,
            step_count=1,
            total_tool_calls=0,
            total_model_calls=1,
            original_goal=None,
            candidate_goals=(),
            downgrade_depth=0,
            final_claim="test claim",
        )
        assert egress.original_goal is None
        assert egress.final_claim == "test claim"
        assert egress.downgrade_depth == 0


# ──────────────────────────────────────────────────────────────────────────
# v0.0.21 空 STOP backoff: model空reply → nudge retry一次
# ──────────────────────────────────────────────────────────────────────────


class TestEmptyStopNudge:
    """弱model对探索性task常'空 stop'; nudge 给它一次用tool/实质回答的机会."""

    def test_empty_stop_then_substantive_answer(self) -> None:
        """Happy path: 空 STOP → nudge → model给实质回答."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.STOP),
            ModelResponse(content="done after nudge", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        result = loop.step()
        assert result.kind == "awaiting_input"
        assert result.content == "done after nudge"
        # nudge injection了 system message (含 "empty" prompt)
        assert any(m.role == "system" and "empty" in (m.content or "").lower()
                   for m in loop._messages)
        assert adapter._call_index == 2  # 初始 + nudge 重试, 共 2 次

    def test_empty_stop_then_tool_use(self) -> None:
        """Happy path: nudge 后model改用tool → 正常executetool, 不误当 STOP terminate."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.STOP),
            ModelResponse(
                content="", stop_reason=StopReason.TOOL_USE,
                tool_calls=(ToolCall(id="t1", tool_id="echo", args={"text": "x"}),),
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        r1 = loop.step()  # 空 STOP → nudge → TOOL_USE → 执行 echo
        assert r1.kind == "tool_used"
        r2 = loop.step()  # STOP done
        assert r2.kind == "awaiting_input"
        assert r2.content == "done"

    def test_persistent_empty_stop_no_infinite_loop(self) -> None:
        """Counterexample: nudge 后仍空 STOP → 诚实returns空, 只retry 1 次 (不无限循环)."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.STOP),
            ModelResponse(content="", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        result = loop.step()
        assert result.kind == "awaiting_input"
        assert adapter._call_index == 2  # 1 次初始 + 1 次 nudge, not无限

    def test_nudge_emits_single_model_call_render(self) -> None:
        """Counterexample (Bug E): nudge retry不得双重渲染 model_call.

        旧实现第一次空调用的 model_call 事件被渲染成 "(empty)", nudge 重试又渲染
        一次 → 用户看到两行 "(empty)".修复后第一次空回复不广播 model_call 渲染,
        只渲染重试结果一次.
        """
        events: list = []
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.STOP),
            ModelResponse(content="done after nudge", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, observer=lambda ev: events.append(ev))
        loop.step()
        model_call_events = [e for e in events if e.kind == "model_call"]
        assert len(model_call_events) == 1  # not 2 (Bug E)
        # 且渲染的是retry结果 (non-空), not第一次的空reply
        assert model_call_events[0].payload["content"] == "done after nudge"
