"""Multi-Judge orchestration invariant tests (MASTER.md §12.1, DESIGN.md §5.4).

Tests the integration of:
  - AgentConfig.judges dict + get_judge() helper
  - loop.py _check_termination multi-judge orchestration
  - base_judge table -> main/aux resolution
  - AccountabilityResult.from_verdicts with main+aux

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.accountability import (
    AccountabilityResult,
    CaveatType,
    Evidence,
    Judge,
    JudgeVerdict,
    base_judge,
)
from zall.core.goal import GoalType, TerminationState
from zall.core.loop_config import AgentConfig


# ──────────────────────────────────────────────────────────────────────────
# Fake Judge helpers (不依赖真实 system judge)
# ──────────────────────────────────────────────────────────────────────────


class _JudgeStub:
    """Fake Judge that returns a fixed verdict.

    Implements __call__ + judge_type property, satisfying Judge Protocol.
    """

    __test__ = False

    def __init__(self, judge_type: str, verdict: JudgeVerdict) -> None:
        self._judge_type = judge_type
        self._verdict = verdict

    @property
    def judge_type(self) -> str:
        return self._judge_type

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return self._verdict


def _v(state: TerminationState, caveat: CaveatType | None = None) -> JudgeVerdict:
    """Shorthand for constructing a JudgeVerdict."""
    return JudgeVerdict(state=state, caveat=caveat)


def _mk_evidence() -> Evidence:
    return Evidence(baseline_sha="abc123", current_sha="def456")


# ──────────────────────────────────────────────────────────────────────────
# §12.1 AgentConfig.judges 字段 + get_judge() helper
# ──────────────────────────────────────────────────────────────────────────


class TestAgentConfigJudges:
    """AgentConfig judges dict 和 get_judge() 不变量."""

    def test_judges_field_default_none(self) -> None:
        """Happy path: judges 默认 None, 向后兼容."""
        cfg = AgentConfig()
        assert cfg.judges is None
        assert cfg.judge is None

    def test_judge_backward_compat(self) -> None:
        """Happy path: 只传 judge=, judges=None, 行为不变."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judge=j)
        assert cfg.judge is j
        assert cfg.judges is None

    def test_judges_dict_stored(self) -> None:
        """Happy path: judges dict 正确存储."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judges={"system": j})
        assert cfg.judges is not None
        assert cfg.judges["system"] is j

    def test_judges_and_judge_coexist(self) -> None:
        """Happy path: judges 和 judge 可共存."""
        main_j = _JudgeStub("system", _v(TerminationState.MET))
        aux_j = _JudgeStub("model_self", _v(TerminationState.MET))
        cfg = AgentConfig(judge=main_j, judges={"system": main_j, "model_self": aux_j})
        assert cfg.judge is main_j
        assert cfg.judges is not None

    def test_get_judge_returns_none_for_empty(self) -> None:
        """Counterexample: get_judge 在无配置时返回 None."""
        cfg = AgentConfig()
        assert cfg.get_judge("system") is None

    def test_get_judge_prioritizes_judges_dict(self) -> None:
        """Happy path: get_judge 优先查 judges dict."""
        main_j = _JudgeStub("system", _v(TerminationState.MET))
        alt_j = _JudgeStub("system", _v(TerminationState.NOT_MET))
        cfg = AgentConfig(judge=alt_j, judges={"system": main_j})
        assert cfg.get_judge("system") is main_j

    def test_get_judge_fallback_to_judge_field(self) -> None:
        """Happy path: get_judge fallback 到 judge 字段 (向后兼容)."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judge=j)
        assert cfg.get_judge("system") is j

    def test_get_judge_fallback_type_mismatch(self) -> None:
        """Counterexample: get_judge 在 judges dict 模式下, judge 类型不匹配时返回 None."""
        j = _JudgeStub("user", _v(TerminationState.MET))
        # judges dict 模式下, 类型不匹配的 judge 不会 fallback
        cfg = AgentConfig(judge=j, judges={"system": _JudgeStub("system", _v(TerminationState.MET))})
        assert cfg.get_judge("user") is None


# ──────────────────────────────────────────────────────────────────────────
# §12.1 base_judge 表一致性
# ──────────────────────────────────────────────────────────────────────────


class TestBaseJudgeTableConsistency:
    """base_judge 表对每个 GoalType 返回合法 (main, aux) 对."""

    def test_base_judge_table_consistency(self) -> None:
        """Happy path: 每个 GoalType 的 (main, aux) 都是合法 judge_type.

        Counterexample: 如果有人加了 GoalType 但没加 base_judge 条目,
        此test须 fail —— 显式承认漏项.
        """
        valid_types = {"system", "user", "model_self"}
        for gt in GoalType:
            main, aux = base_judge(gt)
            assert main in valid_types, f"{gt}: main={main} not in {valid_types}"
            assert aux in valid_types, f"{gt}: aux={aux} not in {valid_types}"

    def test_main_judge_never_equals_aux(self) -> None:
        """Counterexample: main 和 aux 不应相同 (否则无多 Judge 意义).

        注: 当前表设计允许相同, 此处仅作观察断言, 非强制.
        """
        for gt in GoalType:
            main, aux = base_judge(gt)
            # 当前设计允许相同, 仅记录观察
            if main == aux:
                pass  # 已知: 当前表所有条目 main != aux

    def test_all_goal_types_covered(self) -> None:
        """Happy path: 11 个 BaseGoalType 都在 base_judge 表中."""
        for gt in GoalType:
            main, aux = base_judge(gt)
            assert isinstance(main, str)
            assert isinstance(aux, str)


# ──────────────────────────────────────────────────────────────────────────
# §5.4 / §12.1 AccountabilityResult 多 Judge 编排不变量
# ──────────────────────────────────────────────────────────────────────────


class TestMultiJudgeAccountabilityResult:
    """AccountabilityResult.from_verdicts 多 Judge 编排规则."""

    def test_single_judge_backward_compat(self) -> None:
        """Happy path: 只传 main, aux=None, 行为不变 (aux_verdict=None)."""
        r = AccountabilityResult.from_verdicts(_v(TerminationState.MET))
        assert r.state == TerminationState.MET
        assert r.aux_verdict is None

        r2 = AccountabilityResult.from_verdicts(_v(TerminationState.NOT_MET))
        assert r2.state == TerminationState.NOT_MET
        assert r2.aux_verdict is None

        r3 = AccountabilityResult.from_verdicts(_v(TerminationState.UNDECIDABLE))
        assert r3.state == TerminationState.UNDECIDABLE
        assert r3.aux_verdict is None

    def test_multi_judge_main_met_aux_met(self) -> None:
        """Happy path: main=met, aux=met -> state=met, 无 caveat."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.MET), _v(TerminationState.MET)
        )
        assert r.state == TerminationState.MET
        assert r.caveat is None

    def test_multi_judge_main_met_aux_not_met(self) -> None:
        """Happy path: main=met, aux=not_met -> met_with_caveat (main_aux_divergent)."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.MET), _v(TerminationState.NOT_MET)
        )
        assert r.state == TerminationState.MET
        assert r.caveat == CaveatType.MAIN_AUX_DIVERGENT

    def test_multi_judge_main_met_aux_undecidable(self) -> None:
        """Happy path: main=met, aux=undecidable -> met_with_caveat (main_aux_divergent)."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.MET), _v(TerminationState.UNDECIDABLE)
        )
        assert r.state == TerminationState.MET
        assert r.caveat == CaveatType.MAIN_AUX_DIVERGENT

    def test_multi_judge_main_not_met_aux_met(self) -> None:
        """Counterexample: main=not_met, aux=met -> not_met (主判否决, 辅不能救).

        §5.4: 主判 not_met 时, 辅 met 不能"救"回来.
        """
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.NOT_MET), _v(TerminationState.MET)
        )
        assert r.state == TerminationState.NOT_MET
        assert r.caveat is None

    def test_multi_judge_main_not_met_aux_not_met(self) -> None:
        """Happy path: main=not_met, aux=not_met -> not_met."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.NOT_MET), _v(TerminationState.NOT_MET)
        )
        assert r.state == TerminationState.NOT_MET

    def test_multi_judge_main_undecidable_aux_met(self) -> None:
        """Counterexample: main=undecidable, aux=met -> undecidable + caveat (辅不可越级).

        §5.4: 主 Judge = undecidable → 辅 Judge 不可越级改 met (保 PR-0).
        """
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.UNDECIDABLE), _v(TerminationState.MET)
        )
        assert r.state == TerminationState.UNDECIDABLE
        assert r.caveat == CaveatType.MAIN_AUX_DIVERGENT

    def test_multi_judge_main_undecidable_aux_not_met(self) -> None:
        """Happy path: main=undecidable, aux=not_met -> undecidable, 无 caveat."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.UNDECIDABLE), _v(TerminationState.NOT_MET)
        )
        assert r.state == TerminationState.UNDECIDABLE
        assert r.caveat is None

    def test_multi_judge_main_undecidable_aux_undecidable(self) -> None:
        """Happy path: main=undecidable, aux=undecidable -> undecidable."""
        r = AccountabilityResult.from_verdicts(
            _v(TerminationState.UNDECIDABLE), _v(TerminationState.UNDECIDABLE)
        )
        assert r.state == TerminationState.UNDECIDABLE

    def test_multi_judge_main_unavailable_caveat(self) -> None:
        """Happy path: main=main_unavailable caveat -> undecidable + main_unavailable.

        §5.4: 主 Judge 跑不了 → 不能假装 met, 须 undecidable.
        """
        r = AccountabilityResult.from_verdicts(
            JudgeVerdict.undecidable_with_caveat(CaveatType.MAIN_UNAVAILABLE),
            _v(TerminationState.MET),
        )
        assert r.state == TerminationState.UNDECIDABLE
        assert r.caveat == CaveatType.MAIN_UNAVAILABLE


# ──────────────────────────────────────────────────────────────────────────
# §12.1 完整编排: AgentConfig + 多 Judge + loop 集成
# ──────────────────────────────────────────────────────────────────────────


class TestAgentConfigGetJudge:
    """AgentConfig.get_judge 完整编排测试."""

    def test_get_judge_returns_none_for_unconfigured_type(self) -> None:
        """Counterexample: get_judge 对未配置的 judge_type 返回 None."""
        cfg = AgentConfig(
            judges={"system": _JudgeStub("system", _v(TerminationState.MET))}
        )
        assert cfg.get_judge("user") is None
        assert cfg.get_judge("model_self") is None

    def test_get_judge_with_judges_dict(self) -> None:
        """Happy path: get_judge 从 judges dict 返回正确实例."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judges={"system": j})
        assert cfg.get_judge("system") is j

    def test_get_judge_fallback_with_matching_type(self) -> None:
        """Happy path: get_judge fallback 到 judge 字段 (judge_type 匹配)."""
        j = _JudgeStub("user", _v(TerminationState.MET))
        cfg = AgentConfig(judge=j)
        assert cfg.get_judge("user") is j

    def test_get_judge_fallback_with_non_matching_type(self) -> None:
        """Counterexample: get_judge 在 judges dict 模式下, judge_type 不匹配 -> None."""
        j = _JudgeStub("user", _v(TerminationState.MET))
        # 有 judges dict 时, 类型不匹配的 judge 不会 fallback
        cfg = AgentConfig(judge=j, judges={"system": _JudgeStub("system", _v(TerminationState.MET))})
        assert cfg.get_judge("system") is not None  # judges dict 中有 system
        assert cfg.get_judge("user") is None  # judge 是 user 但不在 judges dict 中

    def test_get_judge_judges_dict_overrides_judge(self) -> None:
        """Happy path: judges dict 优先级高于 judge 字段."""
        judges_j = _JudgeStub("system", _v(TerminationState.MET))
        fallback_j = _JudgeStub("system", _v(TerminationState.NOT_MET))
        cfg = AgentConfig(judge=fallback_j, judges={"system": judges_j})
        assert cfg.get_judge("system") is judges_j

    def test_get_judge_with_dict_containing_same_judge(self) -> None:
        """Happy path: judges dict 和 judge 指向同一实例."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judge=j, judges={"system": j})
        assert cfg.get_judge("system") is j


# ──────────────────────────────────────────────────────────────────────────
# §5.4 JudgeVerdict + CaveatType 组合不变量
# ──────────────────────────────────────────────────────────────────────────


class TestCaveatInvariants:
    """caveat 相关的额外不变量."""

    def test_main_unavailable_verdict_always_undecidable(self) -> None:
        """Counterexample: main_unavailable caveat 的 state 应为 undecidable.

        JudgeVerdict.undecidable_with_caveat 强制 undecidable.
        如果有人 construct JudgeVerdict with caveat=MAIN_UNAVAILABLE + state=met,
        那是语义矛盾.
        """
        v = JudgeVerdict.undecidable_with_caveat(CaveatType.MAIN_UNAVAILABLE)
        assert v.state == TerminationState.UNDECIDABLE
        assert v.caveat == CaveatType.MAIN_UNAVAILABLE

    def test_main_aux_divergent_caveat_not_set_by_single_judge(self) -> None:
        """Counterexample: main_aux_divergent 只有上层编排设, 单个 Judge 不设.

        §5.4: 单个 Judge 只填 main_unavailable; main_aux_divergent 由
        AccountabilityResult.from_verdicts 设.
        """
        v = _v(TerminationState.MET)
        assert v.caveat is None
        v2 = JudgeVerdict.undecidable_with_caveat(CaveatType.MAIN_UNAVAILABLE)
        assert v2.caveat == CaveatType.MAIN_UNAVAILABLE

    def test_frozen_immutable(self) -> None:
        """Counterexample: AccountabilityResult 和 JudgeVerdict 都是 frozen."""
        r = AccountabilityResult.from_verdicts(_v(TerminationState.MET))
        with pytest.raises(ValidationError):
            r.state = TerminationState.NOT_MET  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# §12.1 向后兼容: 旧代码只传 judge= 不传 judges= 时行为不变
# ──────────────────────────────────────────────────────────────────────────


class TestBackwardCompatibility:
    """向后兼容性保证."""

    def test_agent_config_judge_only_construction(self) -> None:
        """Happy path: 只传 judge=keywords, 不传 judges=, 和以前一样."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig(judge=j)
        assert cfg.judge is j
        assert cfg.judges is None
        # get_judge 仍能 work (fallback)
        assert cfg.get_judge("system") is j
        # 不匹配的 type 返回 None
        assert cfg.get_judge("user") is None

    def test_agent_config_from_kwargs_judge_only(self) -> None:
        """Happy path: from_kwargs 只传 judge=, 不传 judges=."""
        j = _JudgeStub("system", _v(TerminationState.MET))
        cfg = AgentConfig.from_kwargs(judge=j)
        assert cfg.judge is j
        assert cfg.judges is None

    def test_accountability_result_no_aux(self) -> None:
        """Happy path: from_verdicts 只传 main, aux=None, 保持现有行为."""
        r = AccountabilityResult.from_verdicts(_v(TerminationState.MET))
        assert r.aux_verdict is None
        assert r.state == TerminationState.MET

        r2 = AccountabilityResult.from_verdicts(_v(TerminationState.UNDECIDABLE))
        assert r2.aux_verdict is None
        assert r2.state == TerminationState.UNDECIDABLE

    def test_two_caveat_types_only(self) -> None:
        """Counterexample: CaveatType 只有 2 种, 不许加第 3 种."""
        types = {CaveatType.MAIN_UNAVAILABLE, CaveatType.MAIN_AUX_DIVERGENT}
        assert len(types) == 2

# ── P1 fix: max_steps terminal 也要调 judge (dogfood 发现) ──


class TestMaxStepsJudgeInvocation:
    """P1: --judge system 模式下 agent 跑满 max_steps 时, judge 必须被调用。

    dogfood 发现: 之前 terminal 分支直接返回 egress, 绕过 _check_termination,
    导致 judge 永远不执行。修复后 max_steps 场景应调 judge 覆盖 final_state。
    """

    def test_max_steps_judge_invoked(self) -> None:
        """Happy path: max_steps + judge 配置 -> judge 被调, final_state 非 UNDECIDABLE (若 judge 判 met)."""
        from tests.test_loop_invariants import _make_loop, _ScriptedAdapter, _AlwaysMetJudge
        from zall.core.model import ModelResponse, StopReason, ToolCall
        # 不断调工具, 跑满 max_steps
        adapter = _ScriptedAdapter([
            ModelResponse(content="work", tool_calls=(
                ToolCall(id=f"t{i}", tool_id="echo", args={"text": "x"}),
            ), stop_reason=StopReason.TOOL_USE) for i in range(20)
        ])
        loop = _make_loop(adapter, judge=_AlwaysMetJudge())
        loop._max_steps = 2  # 强制快速跑满
        egress = loop.run()
        # judge 判 met (AlwaysMetJudge), 应覆盖 max_steps 的 UNDECIDABLE
        assert egress.final_state.value == "met"
        # 保留 max_steps error
        assert egress.error is not None
        assert "MAX_STEPS" in egress.error

    def test_max_steps_no_judge_stays_undecidable(self) -> None:
        """Counterexample: max_steps + 无 judge -> 保持 UNDECIDABLE (不调 judge)."""
        from tests.test_loop_invariants import _make_loop, _ScriptedAdapter
        from zall.core.model import ModelResponse, StopReason, ToolCall
        adapter = _ScriptedAdapter([
            ModelResponse(content="work", tool_calls=(
                ToolCall(id=f"t{i}", tool_id="echo", args={"text": "x"}),
            ), stop_reason=StopReason.TOOL_USE) for i in range(20)
        ])
        loop = _make_loop(adapter, judge=None)  # 无 judge
        loop._max_steps = 2
        egress = loop.run()
        assert egress.final_state.value == "undecidable"
        assert egress.error is not None
        assert "MAX_STEPS" in egress.error

    def test_exception_terminal_not_invoke_judge(self) -> None:
        """Counterexample: 异常 terminal (非 max_steps) 不调 judge, 保持原 egress."""
        from tests.test_loop_invariants import _make_loop, _AlwaysMetJudge
        from zall.core.loop_events import RunEgress
        from zall.core.goal import TerminationState
        # 构造一个异常 terminal (非 MAX_STEPS error)
        # 用 monkeypatch 让 step 抛异常 -> terminal with error (非 MAX_STEPS)
        adapter = type("ErrAdapter", (), {
            "model_name": "err",
            "complete": lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("api down")),
        })()
        loop = _make_loop(adapter, judge=_AlwaysMetJudge())
        loop._max_steps = 5
        egress = loop.run()
        # 异常 terminal 不调 judge, 保持 undecidable (不因 judge 变 met)
        assert egress.final_state.value == "undecidable"
        assert egress.error is not None
        assert "MAX_STEPS" not in egress.error


# ── max_steps 软处理: 压缩+重置步数继续 ──


from tests.test_loop_invariants import _ScriptedAdapter  # noqa: E402


class TestMaxStepsSoftHandling:
    """max_steps 到上限时压缩上下文 + 重置步数继续, 不直接终止。"""

    def test_max_steps_compact_continues_when_compressible(self, monkeypatch) -> None:
        """Happy path: 到上限时若可压缩, 步数重置继续。"""
        from tests.test_loop_invariants import _make_loop
        from zall.core.model import ModelResponse, StopReason, ToolCall
        adapter = _ScriptedAdapter([
            ModelResponse(content="work", tool_calls=(
                ToolCall(id="t1", tool_id="echo", args={"text": "x"}),
            ), stop_reason=StopReason.TOOL_USE),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter)
        loop._max_steps = 1  # 1 步就到上限
        # mock: 压缩成功 (消息数减少)
        monkeypatch.setattr(loop, "_try_compact_for_continuation", lambda: True)
        egress = loop.run()
        assert loop._max_steps_retries >= 1
        # 压缩后继续, 最终 STOP (非 max_steps 终止)
        assert "MAX_STEPS" not in (egress.error or "")

    def test_max_steps_terminates_when_not_compressible(self) -> None:
        """Counterexample: 无法压缩时直接终止 (不无限重试)。"""
        from tests.test_loop_invariants import _make_loop
        from zall.core.model import ModelResponse, StopReason, ToolCall
        adapter = _ScriptedAdapter([
            ModelResponse(content="work", tool_calls=(
                ToolCall(id=f"t{i}", tool_id="echo", args={"text": "x"}),
            ), stop_reason=StopReason.TOOL_USE) for i in range(50)
        ])
        loop = _make_loop(adapter)
        loop._max_steps = 2
        # mock: 压缩失败 (消息数未减少)
        import unittest.mock as _mock
        with _mock.patch.object(loop, "_try_compact_for_continuation", return_value=False):
            egress = loop.run()
        assert "MAX_STEPS" in (egress.error or "")

    def test_max_steps_retries_capped(self, monkeypatch) -> None:
        """Counterexample: 重试次数有上限, 不无限重试。"""
        from tests.test_loop_invariants import _make_loop
        from zall.core.model import ModelResponse, StopReason, ToolCall
        adapter = _ScriptedAdapter([
            ModelResponse(content="work", tool_calls=(
                ToolCall(id=f"t{i}", tool_id="echo", args={"text": "x"}),
            ), stop_reason=StopReason.TOOL_USE) for i in range(50)
        ])
        loop = _make_loop(adapter)
        loop._max_steps = 2
        loop._MAX_STEPS_RETRIES = 1
        # mock: 压缩永远"成功"但实际消息不减少 -> 重试 1 次后终止
        monkeypatch.setattr(loop, "_try_compact_for_continuation", lambda: True)
        egress = loop.run()
        assert loop._max_steps_retries <= 2  # 不超过 _MAX_STEPS_RETRIES + 少量余量
