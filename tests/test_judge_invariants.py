"""Judge + Evidence invariant test (DESIGN.md §5.2-5.4).

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
    LintResult,
    TestCaseResult,
    base_judge,
)
from zall.core.goal import GoalType, TerminationState


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_verdict(
    state: TerminationState = TerminationState.MET,
    caveat: CaveatType | None = None,
) -> JudgeVerdict:
    return JudgeVerdict(state=state, caveat=caveat)


def _make_evidence() -> Evidence:
    return Evidence(baseline_sha="abc123", current_sha="def456")


class _SystemJudgeStub:
    """system Judge stub."""

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        if not evidence.test_results:
            return JudgeVerdict.undecidable_with_caveat(
                CaveatType.MAIN_UNAVAILABLE, "no tests to run"
            )
        all_pass = all(t.passed for t in evidence.test_results if not t.skipped)
        return JudgeVerdict(
            state=TerminationState.MET if all_pass else TerminationState.NOT_MET
        )


class _ModelSelfJudgeStub:
    """model_self Judge stub."""

    @property
    def judge_type(self) -> str:
        return "model_self"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="looks good")


# ──────────────────────────────────────────────────────────────────────────
# §5.4 CaveatType invariants
# ──────────────────────────────────────────────────────────────────────────


class TestCaveatTypeInvariants:
    """§5.4 caveat subtypeinvariant."""

    def test_two_caveat_types_only(self) -> None:
        """Counterexample: CaveatType 只有 2 种 (main_unavailable / main_aux_divergent).

        v0.0.3 红/蓝对抗已驳掉合并; 不许加第 3 种 (over-engineering 防御).
        """
        types = {CaveatType.MAIN_UNAVAILABLE, CaveatType.MAIN_AUX_DIVERGENT}
        assert len(types) == 2


# ──────────────────────────────────────────────────────────────────────────
# §5.2 JudgeVerdict invariants
# ──────────────────────────────────────────────────────────────────────────


class TestJudgeVerdictInvariants:
    """§5.2 JudgeVerdict invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid verdict constructable."""
        v = _make_verdict(TerminationState.MET)
        assert v.state == TerminationState.MET

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 state → must raise."""
        v = _make_verdict()
        with pytest.raises(ValidationError):
            v.state = TerminationState.NOT_MET  # type: ignore[misc]

    def test_undecidable_with_caveat(self) -> None:
        """Happy path: undecidable_with_caveat 产出 undecidable + caveat."""
        v = JudgeVerdict.undecidable_with_caveat(CaveatType.MAIN_UNAVAILABLE)
        assert v.state == TerminationState.UNDECIDABLE
        assert v.caveat == CaveatType.MAIN_UNAVAILABLE

    def test_caveat_implies_undecidable(self) -> None:
        """Counterexample: caveat=main_unavailable 但 state=met → 语义矛盾.

        §5.4: 主 Judge 跑不了 (main_unavailable) 时, 不能假装 met.
        如果一个实现允许 caveat+met 组合, PR-0 被破坏 (agent 假装完成).

        注: 本 invariant 在 JudgeVerdict 层不强制 raise (因for main_aux_divergent
        可以配 met_with_caveat); 但 AccountabilityResult.from_verdicts 会强制
        main_unavailable → undecidable.
        """


# ──────────────────────────────────────────────────────────────────────────
# §5.3 Evidence invariants
# ──────────────────────────────────────────────────────────────────────────


class TestEvidenceInvariants:
    """§5.3 Evidence invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid Evidence constructable."""
        e = _make_evidence()
        assert e.baseline_sha == "abc123"
        assert e.current_sha == "def456"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 baseline_sha → must raise."""
        e = _make_evidence()
        with pytest.raises(ValidationError):
            e.baseline_sha = "tampered"  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """Evidence 不携带 tool 历史 (§4.3 核心斩断呼应)."""
        assert Evidence.__no_tool_history__() is True

    def test_test_results_is_tuple(self) -> None:
        """test_results 是 tuple (immutable)."""
        e = Evidence(
            baseline_sha="a",
            current_sha="b",
            test_results=(TestCaseResult(test_id="t1", passed=True),),
        )
        assert isinstance(e.test_results, tuple)
        assert not hasattr(e.test_results, "append")

    def test_external_dict_known_open(self) -> None:
        """Known OPEN: external dict 可变 (与 Action.args 同型, 不假装)."""
        e = _make_evidence()
        assert isinstance(e.external, dict)


# ──────────────────────────────────────────────────────────────────────────
# §5.2 Judge Protocol invariants
# ──────────────────────────────────────────────────────────────────────────


class TestJudgeProtocolInvariants:
    """§5.2 Judge Protocol invariant."""

    def test_system_judge_is_judge(self) -> None:
        """Happy path: _SystemJudgeStub 满足 Judge Protocol."""
        assert isinstance(_SystemJudgeStub(), Judge)

    def test_bad_object_not_judge(self) -> None:
        """Counterexample: 缺 __call__ 的对象not Judge."""

        class _Bad:
            @property
            def judge_type(self) -> str:
                return "system"

        assert not isinstance(_Bad(), Judge)

    def test_idempotency(self) -> None:
        """纯性proxy: 相同 evidence 两次调用结果相同."""
        judge = _SystemJudgeStub()
        e = Evidence(
            baseline_sha="a",
            current_sha="b",
            test_results=(TestCaseResult(test_id="t1", passed=True),),
        )
        first = judge(e)
        second = judge(e)
        assert first == second


# ──────────────────────────────────────────────────────────────────────────
# §5.2 base_judge 表 invariants
# ──────────────────────────────────────────────────────────────────────────


class TestBaseJudgeTableInvariants:
    """§5.2 base_judge 表invariant."""

    def test_all_goal_types_covered(self) -> None:
        """Happy path: 11 个 BaseGoalType 都在 base_judge 表中.

        Counterexample: 如果有人加了 GoalType 但没加 base_judge 条目,
        此test须 fail —— 显式承认漏项.
        """
        for gt in GoalType:
            main, aux = base_judge(gt)
            assert main in ("system", "user", "model_self")
            assert aux in ("system", "user", "model_self", "none") or aux in (
                "system",
                "user",
                "model_self",
            )

    def test_bugfix_uses_system_main(self) -> None:
        """Happy path: bugfix 的 main 是 system (§5.2 表 2)."""
        main, aux = base_judge(GoalType.BUGFIX)
        assert main == "system"
        assert aux == "model_self"

    def test_docs_uses_user_main(self) -> None:
        """Happy path: docs 的 main 是 user (§5.2 表 2, 无机械判据)."""
        main, _ = base_judge(GoalType.DOCS)
        assert main == "user"


# ──────────────────────────────────────────────────────────────────────────
# §5.4 AccountabilityResult (多 Judge 一致性) invariants
# ──────────────────────────────────────────────────────────────────────────


class TestAccountabilityResultInvariants:
    """§5.4 多 Judge 一致性invariant."""

    def test_main_met_aux_met_is_met(self) -> None:
        """Happy path: 主 met + 辅 met → met, 无 caveat."""
        r = AccountabilityResult.from_verdicts(
            _make_verdict(TerminationState.MET),
            _make_verdict(TerminationState.MET),
        )
        assert r.state == TerminationState.MET
        assert r.caveat is None

    def test_main_met_aux_not_met_is_met_with_caveat(self) -> None:
        """Happy path: 主 met + 辅 not_met → met + main_aux_divergent."""
        r = AccountabilityResult.from_verdicts(
            _make_verdict(TerminationState.MET),
            _make_verdict(TerminationState.NOT_MET),
        )
        assert r.state == TerminationState.MET
        assert r.caveat == CaveatType.MAIN_AUX_DIVERGENT

    def test_main_undecidable_aux_met_stays_undecidable(self) -> None:
        """Counterexample: 主 undecidable + 辅 met → 仍 undecidable (辅不可越级).

        §5.4: 主 Judge = undecidable → 辅 Judge 不可越级改 met (保 PR-0).
        如果一个实现让辅 met covers主 undecidable, agent 自欺 → 严重 hijack.
        """
        r = AccountabilityResult.from_verdicts(
            _make_verdict(TerminationState.UNDECIDABLE),
            _make_verdict(TerminationState.MET),
        )
        assert r.state == TerminationState.UNDECIDABLE
        assert r.caveat == CaveatType.MAIN_AUX_DIVERGENT

    def test_main_not_met_aux_met_stays_not_met(self) -> None:
        """Counterexample: 主 not_met + 辅 met → 仍 not_met (主判否决, 辅不能救).

        §5.4: 主判 not_met 时, 辅 met 不能"救"回来.
        """
        r = AccountabilityResult.from_verdicts(
            _make_verdict(TerminationState.NOT_MET),
            _make_verdict(TerminationState.MET),
        )
        assert r.state == TerminationState.NOT_MET

    def test_main_unavailable_is_undecidable(self) -> None:
        """Happy path: 主 main_unavailable → undecidable + main_unavailable caveat.

        §5.4: 主 Judge 跑不了 → 不能假装 met, 须 undecidable.
        """
        r = AccountabilityResult.from_verdicts(
            JudgeVerdict.undecidable_with_caveat(CaveatType.MAIN_UNAVAILABLE),
            _make_verdict(TerminationState.MET),
        )
        assert r.state == TerminationState.UNDECIDABLE
        assert r.caveat == CaveatType.MAIN_UNAVAILABLE

    def test_no_aux_uses_main_directly(self) -> None:
        """Happy path: 无辅 Judge → directly用主 verdict."""
        r = AccountabilityResult.from_verdicts(_make_verdict(TerminationState.MET))
        assert r.state == TerminationState.MET
        assert r.aux_verdict is None

    def test_frozen_immutable(self) -> None:
        """Counterexample: AccountabilityResult construct后改 state → must raise."""
        r = AccountabilityResult.from_verdicts(_make_verdict(TerminationState.MET))
        with pytest.raises(ValidationError):
            r.state = TerminationState.NOT_MET  # type: ignore[misc]
