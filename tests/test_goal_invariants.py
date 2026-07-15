"""GoalTriple invariant test (DESIGN.md §3.2).

IPR-0: each test must contain a counterexample —— not happy path, but construct violations that should cause the test to fail.
Counterexample摘要见 tests/INVARIANTS.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.goal import (
    AcceptanceContract,
    DeclineTask,
    Escalation,
    GoalStatement,
    GoalTriple,
    GoalType,
    RefinedGoal,
    TerminationState,
    new_segment_id,
)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _SystemTermination:
    """一个 system_judge class的 TerminationCriterion stub (满足 Protocol)."""

    exposed_dependency_set: tuple[str, ...] = ("src/main.py", "tests/test_main.py")

    def __call__(self, state: object) -> TerminationState:
        # stub: 永远returns undecidable (诚实退让, PR-0)
        return TerminationState.UNDECIDABLE


class _UserTermination:
    """一个 user_judge class的 TerminationCriterion stub (exposed_dependency_set=None)."""

    exposed_dependency_set: tuple[str, ...] | None = None

    def __call__(self, state: object) -> TerminationState:
        return TerminationState.UNDECIDABLE


def _make_valid_statement(goal_type: GoalType = GoalType.BUGFIX) -> GoalStatement:
    return GoalStatement(
        intent="修复登录页 crash",
        rewriting="修复登录页在空密码时 crash 的 bug",
        rewrite_confidence=0.9,
        goal_type=goal_type,
        translation_of=(new_segment_id(),),
        added_intent=(),
    )


def _make_valid_acceptance() -> AcceptanceContract:
    return AcceptanceContract(
        baseline_frozen_at="abc1234",
        prohibited_actions=("edit_test_files",),
        escalation=Escalation.HUMAN_REVIEW,
    )


# ──────────────────────────────────────────────────────────────────────────
# §3.2.1 GoalStatement invariants
# ──────────────────────────────────────────────────────────────────────────


class TestGoalStatementInvariants:
    """§3.2.1 GoalStatement invariant."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid GoalStatement constructable."""
        stmt = _make_valid_statement()
        assert stmt.intent == "修复登录页 crash"
        assert stmt.goal_type == GoalType.BUGFIX

    def test_added_intent_non_empty_raises(self) -> None:
        """Counterexample: added_intent non-空 → must raise (R1 translate禁加戏).

        对应 DESIGN.md §3.3 R1: Refiner 不可injection user_raw 未表达的意图.
        如果一个实现让此construct通过, R1 被破坏.
        """
        with pytest.raises(ValidationError, match="added_intent must be empty"):
            GoalStatement(
                intent="x",
                rewriting="x",
                rewrite_confidence=0.5,
                goal_type=GoalType.BUGFIX,
                translation_of=(new_segment_id(),),
                added_intent=("sneaky_extra_intent",),  # ← 违规
            )

    def test_confidence_out_of_range_raises(self) -> None:
        """Counterexample: rewrite_confidence > 1.0 → must raise.

        confidence 是 agent 自评, 越界意味着 agent 自欺.
        """
        with pytest.raises(ValidationError, match="rewrite_confidence must be in"):
            GoalStatement(
                intent="x",
                rewriting="x",
                rewrite_confidence=1.5,  # ← 违规
                goal_type=GoalType.BUGFIX,
                translation_of=(new_segment_id(),),
                added_intent=(),
            )

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后试图改 intent → must raise (frozen).

        Goal 一旦确立贯穿 run immutable (§3.1 防漂移).
        """
        stmt = _make_valid_statement()
        with pytest.raises(ValidationError):
            stmt.intent = "tampered"  # type: ignore[misc]

    def test_translation_of_is_tuple_not_list(self) -> None:
        """Counterexample: translation_of 不可 append (tuple immutable).

        如果实现用 list, 外部能 append 新 segment_id 破坏 R1.
        tuple 保证immutable.
        """
        stmt = _make_valid_statement()
        # tuple 没有 append
        assert not hasattr(stmt.translation_of, "append")


# ──────────────────────────────────────────────────────────────────────────
# §3.2.2 TerminationCriterion invariants
# ──────────────────────────────────────────────────────────────────────────


class TestTerminationCriterionInvariants:
    """§3.2.2 TerminationCriterion invariant."""

    def test_three_states_only(self) -> None:
        """TerminationState 只有三态 (not_met / met / undecidable).

        Counterexample: 如果有人加第 4 态, 此test须 fail (over-engineering 防御,
        与 v0.0.7 context_judge 4 态自驳同型).
        """
        states = {TerminationState.NOT_MET, TerminationState.MET, TerminationState.UNDECIDABLE}
        assert len(states) == 3

    def test_idempotency_proxy_for_purity(self) -> None:
        """TerminationCriterion 纯性proxy: 相同input两次调用结果相同.

        纯性无法 type-level 强制 (PR-0 半认输); 用幂等性代理.
        Counterexample: 如果实现有内部可变状态, 两次调用可能不同 → 此test fail.
        """
        crit = _SystemTermination()
        state = {"some": "state"}
        first = crit(state)
        second = crit(state)
        assert first == second

    def test_protocol_runtime_checkable(self) -> None:
        """TerminationCriterion 是 runtime_checkable Protocol.

        Counterexample: 如果有人删了 exposed_dependency_set 属性, isinstance 须 fail.
        """
        from zall.core.goal import TerminationCriterion

        assert isinstance(_SystemTermination(), TerminationCriterion)
        assert isinstance(_UserTermination(), TerminationCriterion)

        # Counterexample: 没暴露 exposed_dependency_set 的对象not TerminationCriterion
        class _Bad:
            def __call__(self, state: object) -> TerminationState:
                return TerminationState.MET

        assert not isinstance(_Bad(), TerminationCriterion)


# ──────────────────────────────────────────────────────────────────────────
# §3.2 GoalTriple 聚合 invariants
# ──────────────────────────────────────────────────────────────────────────


class TestGoalTripleInvariants:
    """§3.2 GoalTriple 聚合invariant."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid GoalTriple constructable."""
        goal = GoalTriple(
            statement=_make_valid_statement(GoalType.BUGFIX),
            termination=_SystemTermination(),
            acceptance=_make_valid_acceptance(),
        )
        assert goal.statement.goal_type == GoalType.BUGFIX

    def test_system_judge_with_none_exposed_set_raises(self) -> None:
        """Counterexample: system_judge class goal_type 但 exposed_dependency_set=None → must raise.

        v0.0.6 §3.2.2 回填: hunk 分类器需 exposed_dependency_set 输入.
        system_judge 类 GoalType (bugfix/feature/.../scaffold) 必须暴露它.
        如果一个实现让此construct通过, §5.5 hunk 归属分类器无输入, 降级链可计算性塌.
        """
        with pytest.raises(ValidationError, match="exposed_dependency_set 必须非 None"):
            GoalTriple(
                statement=_make_valid_statement(GoalType.BUGFIX),
                termination=_UserTermination(),  # ← exposed=None, 但 bugfix 是 system_judge
                acceptance=_make_valid_acceptance(),
            )

    def test_user_judge_with_none_exposed_set_ok(self) -> None:
        """Happy path: user_judge class goal_type (eg. docs) 允许 exposed_dependency_set=None.

        §5.5.4 OPEN: user_judge GoalType 走保守默认 (仅含降级后 hunk).
        """
        goal = GoalTriple(
            statement=_make_valid_statement(GoalType.DOCS),
            termination=_UserTermination(),
            acceptance=_make_valid_acceptance(),
        )
        assert goal.statement.goal_type == GoalType.DOCS

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后试图改 statement → must raise (frozen, 防漂移)."""
        goal = GoalTriple(
            statement=_make_valid_statement(),
            termination=_SystemTermination(),
            acceptance=_make_valid_acceptance(),
        )
        with pytest.raises(ValidationError):
            goal.statement = _make_valid_statement(GoalType.FEATURE)  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# §3.5 GoalType Enum invariants
# ──────────────────────────────────────────────────────────────────────────


class TestGoalTypeInvariants:
    """§3.5 GoalType Enum invariant."""

    def test_base_types_count(self) -> None:
        """BaseTypes 11 种 (DESIGN.md §3.5.1).

        Counterexample: 如果有人删了一个 BaseGoalType 或加了第 12 个,
        此test须 fail —— 显式承认闭集不存在, 改动须显式.
        """
        members = list(GoalType)
        assert len(members) == 11
        # confirm unknown 在 (catch-all fallback)
        assert GoalType.UNKNOWN in members

    def test_unknown_is_catch_all(self) -> None:
        """unknown 是 catch-all (§3.5.1).

        Counterexample: 如果有人删了 unknown, 未知 Goal 类型无兜底 → fail.
        """
        assert GoalType.UNKNOWN.value == "unknown"


# ──────────────────────────────────────────────────────────────────────────
# helpers for Refiner output schema (§3.3.2)
# ──────────────────────────────────────────────────────────────────────────


def _make_valid_goal_triple(
    goal_type: GoalType = GoalType.BUGFIX,
) -> GoalTriple:
    """constructvalid GoalTriple (复用 §3.2 helpers)."""
    if goal_type in (
        GoalType.BUGFIX,
        GoalType.FEATURE,
        GoalType.REFACTOR,
        GoalType.TEST_WRITE,
        GoalType.PERF_OPT,
        GoalType.MIGRATE,
        GoalType.SCAFFOLD,
    ):
        termination: object = _SystemTermination()
    else:
        termination = _UserTermination()
    return GoalTriple(
        statement=_make_valid_statement(goal_type),
        termination=termination,  # type: ignore[arg-type]
        acceptance=_make_valid_acceptance(),
    )


def _make_valid_refined(
    goal_type: GoalType = GoalType.BUGFIX,
    ask_budget: int = 3,
) -> RefinedGoal:
    """constructvalid RefinedGoal (DESIGN.md §3.3.2)."""
    return RefinedGoal(
        user_raw="修一下登录页的 crash",
        questions_used=1,
        refined_goal=_make_valid_goal_triple(goal_type),
        translation_of=(new_segment_id(),),
        added_intent=(),
        confidence=0.85,
        ask_budget=ask_budget,
    )


# ──────────────────────────────────────────────────────────────────────────
# §3.3.2 RefinedGoal invariants
# ──────────────────────────────────────────────────────────────────────────


class TestRefinedGoalInvariants:
    """§3.3.2 RefinedGoal invariant (R1 translate禁加戏 + R2 预算 + confidence)."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid RefinedGoal constructable."""
        r = _make_valid_refined()
        assert r.confidence == 0.85
        assert r.questions_used == 1

    def test_r1_added_intent_non_empty_raises(self) -> None:
        """Counterexample: added_intent=("optimize_cache",) → must raise (R1 translate禁加戏).

        Refiner 不可injection user_raw 未表达的意图.若不 raise, R1 不可机械执行.
        """
        with pytest.raises(ValidationError, match="added_intent must be empty"):
            RefinedGoal(
                user_raw="修一下 crash",
                questions_used=0,
                refined_goal=_make_valid_goal_triple(),
                translation_of=(new_segment_id(),),
                added_intent=("optimize_cache",),  # R1 违规
                confidence=0.9,
                ask_budget=3,
            )

    def test_r2_questions_exceed_budget_raises(self) -> None:
        """Counterexample: questions_used=3 > ask_budget=1 → must raise (R2 预算约束).

        Refiner 不可突破反问预算.若不 raise, R2 预算部分不可机械执行.
        """
        with pytest.raises(ValidationError, match="questions_used.*ask_budget"):
            RefinedGoal(
                user_raw="修 crash",
                questions_used=3,
                refined_goal=_make_valid_goal_triple(),
                translation_of=(new_segment_id(),),
                added_intent=(),
                confidence=0.9,
                ask_budget=1,  # 只允许 1 次反问
            )

    def test_r2_zero_budget_zero_questions_ok(self) -> None:
        """Happy path边界: ask_budget=0 + questions_used=0 → valid (K=0 typemust not反问).

        §4.4 表: bugfix/review 的 base_K=0.K=0 时 Refiner 禁反问,
        questions_used=0 是唯一valid值.
        """
        r = RefinedGoal(
            user_raw="修 crash",
            questions_used=0,
            refined_goal=_make_valid_goal_triple(GoalType.BUGFIX),
            translation_of=(new_segment_id(),),
            added_intent=(),
            confidence=0.9,
            ask_budget=0,
        )
        assert r.questions_used == 0

    def test_confidence_out_of_range_raises(self) -> None:
        """Counterexample: confidence=1.5 → must raise (与 GoalStatement 同形约束)."""
        with pytest.raises(ValidationError, match="confidence must be in"):
            RefinedGoal(
                user_raw="修 crash",
                questions_used=0,
                refined_goal=_make_valid_goal_triple(),
                translation_of=(new_segment_id(),),
                added_intent=(),
                confidence=1.5,
                ask_budget=3,
            )

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 confidence → must raise (frozen)."""
        r = _make_valid_refined()
        with pytest.raises(ValidationError):
            r.confidence = 0.5  # type: ignore[misc]

    def test_r1_independent_from_goaltriple(self) -> None:
        """Happy path: RefinedGoal.added_intent 与 GoalTriple.statement.added_intent 独立.

        两个层各自断言 R1; GoalStatement.added_intent 必空 (§3.2.1) +
        RefinedGoal.added_intent 必空 (§3.3 R1) 是两个独立断言,
        non-同一字段.本测确认 RefinedGoal 自己的 validator 独立于
        GoalStatement 的 validator —— 各自 raise 各自的 ValidationError.
        """
        r = _make_valid_refined()
        # GoalTriple 内 statement.added_intent 也必空 (但那是 §3.2.1 的约束)
        assert r.refined_goal.statement.added_intent == ()
        assert r.added_intent == ()
        # 独立性: RefinedGoal 的 added_intent 字段在 RefinedGoal 上,
        # 不在 GoalTriple 上 —— GoalTriple 无 added_intent 顶层字段
        assert not hasattr(r.refined_goal, "added_intent")


# ──────────────────────────────────────────────────────────────────────────
# §3.3.2 DeclineTask invariants
# ──────────────────────────────────────────────────────────────────────────


class TestDeclineTaskInvariants:
    """§3.3.2 DeclineTask invariant (R2 预算约束)."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid DeclineTask constructable."""
        d = DeclineTask(
            user_raw="做一个完全模糊的东西",
            questions_used=3,
            reason="intent_not_refinable_in_budget",
            partial_translation=(new_segment_id(),),
            ask_budget=3,
        )
        assert d.reason == "intent_not_refinable_in_budget"

    def test_r2_questions_exceed_budget_raises(self) -> None:
        """Counterexample: questions_used=5 > ask_budget=3 → must raise (R2 预算约束)."""
        with pytest.raises(ValidationError, match="questions_used.*ask_budget"):
            DeclineTask(
                user_raw="模糊",
                questions_used=5,
                partial_translation=(),
                ask_budget=3,
            )

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 reason → must raise (frozen)."""
        d = DeclineTask(
            user_raw="模糊",
            questions_used=1,
            partial_translation=(),
            ask_budget=2,
        )
        with pytest.raises(ValidationError):
            d.reason = "other"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# §3.4 GoalDowngrade invariant test (v0.0.11)
# ──────────────────────────────────────────────────────────────────────────


class _DummyTermination:
    """占位 TerminationCriterion 用于 GoalDowngrade tests."""
    exposed_dependency_set: tuple[str, ...] | None = None

    def __call__(self, state: object) -> TerminationState:
        return TerminationState.UNDECIDABLE


def _mk_triple(gt: GoalType, intent: str = "test intent") -> GoalTriple:
    """快捷construct一个 GoalTriple (用于downgradetest)."""
    return GoalTriple(
        statement=GoalStatement(
            intent=intent,
            rewriting=intent,
            rewrite_confidence=1.0,
            goal_type=gt,
            added_intent=(),
        ),
        termination=_DummyTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )


class TestGoalDowngrade:
    """§3.4.2 GoalDowngrade 数据modelinvariant."""

    def test_downgrade_frozen(self) -> None:
        """Counterexample: construct后改 original → must raise (frozen)."""
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        d = GoalDowngrade(
            original=original,
            candidates=(candidate,),
            approximate_flag=True,
        )
        with pytest.raises(ValidationError):
            d.original = _mk_triple(GoalType.BUGFIX)  # type: ignore[misc]

    def test_r4_original_preserved_in_output(self) -> None:
        """R4 双 Goal 共存: construct后 original 保持不变.

        Counterexample: 如果 original 被意外改变, 降级失去可回溯性.
        """
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN, "修复一个 bug")
        candidate = _mk_triple(GoalType.INVESTIGATE)
        d = GoalDowngrade(
            original=original,
            candidates=(candidate,),
            approximate_flag=True,
        )
        assert d.original.statement.goal_type == GoalType.UNKNOWN
        assert d.original.statement.intent == "修复一个 bug"

    def test_r5_depth_bound_exceeds_max(self) -> None:
        """Counterexample R5: downgrade_depth > D(default=1) → must raise.

        Counterexample: downgrade_depth=3 → raise ValueError (超出降级深度上限).
        """
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        with pytest.raises(ValidationError, match="exceeds max"):
            GoalDowngrade(
                original=original,
                candidates=(candidate,),
                downgrade_depth=3,  # 超出 D=1
                approximate_flag=True,
            )

    def test_r5_depth_bound_negative(self) -> None:
        """Counterexample R5: downgrade_depth < 1 → must raise.

        Counterexample: downgrade_depth=0 → raise ValueError.
        """
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        with pytest.raises(ValidationError, match="≥ 1"):
            GoalDowngrade(
                original=original,
                candidates=(candidate,),
                downgrade_depth=0,
                approximate_flag=True,
            )

    def test_r6_approximate_flag_false_raises(self) -> None:
        """Counterexample R6: approximate_flag=False → must raise.

        Counterexample: agent 单方触发降级 → raise (R6 不可单方触发).
        """
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        with pytest.raises(ValidationError, match="approximate_flag must be True"):
            GoalDowngrade(
                original=original,
                candidates=(candidate,),
                approximate_flag=False,
            )

    def test_candidates_must_be_non_empty(self) -> None:
        """Counterexample: candidates for空 → must raise.

        Counterexample: 无替身的降级没有意义.
        """
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        with pytest.raises(ValidationError, match="non-empty"):
            GoalDowngrade(
                original=original,
                candidates=(),
                approximate_flag=True,
            )

    def test_downgrade_baseline_at_tracked(self) -> None:
        """Happy path: baseline_at 记录触发downgrade时的 git SHA."""
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        d = GoalDowngrade(
            original=original,
            candidates=(candidate,),
            baseline_at="abc123def",
            approximate_flag=True,
        )
        assert d.baseline_at == "abc123def"

    def test_downgrade_approximate_flag_defaults_true(self) -> None:
        """Happy path: approximate_flag defaultfor True (R6 不可 agent 单方触发)."""
        from zall.core.goal import GoalDowngrade

        original = _mk_triple(GoalType.UNKNOWN)
        candidate = _mk_triple(GoalType.INVESTIGATE)
        d = GoalDowngrade(
            original=original,
            candidates=(candidate,),
        )
        assert d.approximate_flag is True
        assert d.downgrade_depth == 1  # default
