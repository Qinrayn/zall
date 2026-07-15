"""GoalRefiner invariant test (DESIGN.md §3.3 minimal runnable).

IPR-0: each test must contain a counterexample —— not happy path, but construct violations that should cause the test to fail.

covers:
  §3.3 R1  翻译禁加戏: RefinedGoal.added_intent 必空 (Refiner 层独立断言)
  §3.3 R2  反问预算: questions_used(=0) ≤ ask_budget(=0)
  §3.3     confidence ∈ [0.0, 1.0]
  §3.5     关键词分类器: 命中类型 / UNKNOWN
  §5.2     base_judge 表驱动 exposed_dependency_set (system→(), 否则 None)
  fallback  _refine_goal 异常does not crash (守 IPR-0 Counterexample)
Counterexample摘要见 tests/INVARIANTS.md.
"""

from __future__ import annotations

import pytest

from zall.core.accountability import base_judge
from zall.core.goal import GoalType
from zall.core.refiner import GoalRefiner, _PlaceholderTermination, _classify_goal_type, _split_segments


# ──────────────────────────────────────────────────────────────────────────
# §3.3 R1 / R2 / confidence: RefinedGoal 机械约束 (经 Refiner 产出)
# ──────────────────────────────────────────────────────────────────────────


def test_refiner_r1_added_intent_empty():
    """R1 translate禁加戏: Refiner 产出的 RefinedGoal.added_intent 必空."""
    r = GoalRefiner.refine("修复登录页 crash", judge_mode="none")
    assert r.added_intent == ()
    # GoalTriple 内层 statement 也必空 (两层独立断言)
    assert r.refined_goal.statement.added_intent == ()


def test_refiner_r2_questions_within_budget():
    """R2 反问预算: questions_used(=0) ≤ ask_budget(=0)."""
    r = GoalRefiner.refine("实现用户导出功能", judge_mode="none")
    assert r.questions_used == 0
    assert r.ask_budget == 0
    assert r.questions_used <= r.ask_budget


def test_refiner_confidence_in_range():
    """confidence ∈ [0.0, 1.0]."""
    r = GoalRefiner.refine("随便聊聊这个项目的架构", judge_mode="none")
    assert 0.0 <= r.confidence <= 1.0


# ──────────────────────────────────────────────────────────────────────────
# §3.5 关键词分class器
# ──────────────────────────────────────────────────────────────────────────


def test_classify_bugfix():
    assert _classify_goal_type("修复登录页 crash") == GoalType.BUGFIX
    assert _classify_goal_type("fix the null pointer bug") == GoalType.BUGFIX


def test_classify_feature():
    assert _classify_goal_type("新增导出 CSV 功能") == GoalType.FEATURE


def test_classify_refactor():
    assert _classify_goal_type("重构 utils 模块") == GoalType.REFACTOR


def test_classify_unknown():
    """识别不到 → UNKNOWN (诚实低置信, 不阻断)."""
    assert _classify_goal_type("你觉得今天天气如何") == GoalType.UNKNOWN


def test_refiner_unknown_confidence_low():
    """UNKNOWN → confidence 0.5 (诚实低置信, 留给 Judge fallback)."""
    r = GoalRefiner.refine("你觉得今天天气如何", judge_mode="none")
    assert r.refined_goal.statement.goal_type == GoalType.UNKNOWN
    assert r.confidence == 0.5


def test_refiner_system_mode_forces_bugfix():
    """judge_mode=system → 强制 BUGFIX + confidence 0.9 (与旧 _make_goal 行for一致)."""
    r = GoalRefiner.refine("实现用户导出功能", judge_mode="system")
    assert r.refined_goal.statement.goal_type == GoalType.BUGFIX
    assert r.confidence == 0.9


# ──────────────────────────────────────────────────────────────────────────
# §5.2 base_judge 表驱动 exposed_dependency_set (Refiner 真正消费 §5.2)
# ──────────────────────────────────────────────────────────────────────────


def test_refiner_exposed_driven_by_base_judge_system():
    """main=system 的 GoalType (如 bugfix) → exposed_dependency_set=()."""
    r = GoalRefiner.refine("修复登录页 crash", judge_mode="none")
    gt = r.refined_goal.statement.goal_type
    main, _ = base_judge(gt)
    if main == "system":
        assert r.refined_goal.termination.exposed_dependency_set == ()
    else:
        assert r.refined_goal.termination.exposed_dependency_set is None


def test_refiner_exposed_driven_by_base_judge_user():
    """main=user 的 GoalType (如 docs) → exposed_dependency_set=None."""
    r = GoalRefiner.refine("补一下 README 文档", judge_mode="none")
    gt = r.refined_goal.statement.goal_type
    main, _ = base_judge(gt)
    if main == "user":
        assert r.refined_goal.termination.exposed_dependency_set is None


# ──────────────────────────────────────────────────────────────────────────
# §3.3 translation_of 切分 (切分 ≠ 加intent)
# ──────────────────────────────────────────────────────────────────────────


def test_split_segments_basic():
    segs = _split_segments("修复登录页.再补一个test，最后写文档")
    # 至少包含原句sub句, 且non-空
    assert len(segs) >= 1
    assert all(s.strip() for s in segs)


def test_split_segments_single_fallback():
    """无分隔符 → 退化for整句 (仍可回指)."""
    segs = _split_segments("修复登录页crash")
    assert segs == ("修复登录页crash",)


def test_refiner_translation_of_is_split():
    """Refiner 的 translation_of 是切分结果, 且 added_intent 仍空 (R1 不破)."""
    r = GoalRefiner.refine("修复登录页.补test", judge_mode="none")
    assert r.translation_of  # non-空, 每段回指 user_raw
    assert r.added_intent == ()


# ──────────────────────────────────────────────────────────────────────────
# fallback: 守 IPR-0 Counterexample (construct层故障不得改变 RunEgress)
# ──────────────────────────────────────────────────────────────────────────


def test_refine_goal_fallback_on_refiner_broken(monkeypatch):
    """若 Refiner 抛exception, _refine_goal 须 fallback 到 _make_goal does not crash.

    IPR-0 Counterexample: construct层故障不得让 run/REPL 崩溃.
    """
    from zall.cli import app as cli_app

    def _boom(*a, **k):
        raise RuntimeError("refiner exploded")

    monkeypatch.setattr(cli_app.GoalRefiner, "refine", _boom)
    # 不应抛; 应fallback到minimal诚实construct
    goal = cli_app._refine_goal("修复登录页 crash", judge_mode="none")
    assert goal.statement.goal_type in (GoalType.UNKNOWN, GoalType.BUGFIX)


def test_refiner_always_returns_refined_goal_not_decline():
    """minimalversion: 一律 RefinedGoal, UNKNOWN 也不 Decline (不阻断 REPL)."""
    from zall.core.goal import DeclineTask

    r = GoalRefiner.refine("完全模糊的一句话", judge_mode="none")
    assert not isinstance(r, DeclineTask)
    assert isinstance(r, __import__("zall.core.goal", fromlist=["RefinedGoal"]).RefinedGoal)


# ──────────────────────────────────────────────────────────────────────────
# §3.4 GoalDowngrade: suggest_downgrade invariant test (v0.0.11)
# ──────────────────────────────────────────────────────────────────────────


def test_suggest_downgrade_unknown_has_candidates():
    """Happy path: UNKNOWN type有downgrade候选 (INVESTIGATE, BUGFIX, FEATURE).

    Counterexample: 如果 UNKNOWN 没有降级候选, 用户面对模糊意图没有出路.
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    original = GoalTriple(
        statement=GoalStatement(
            intent="帮我做点事",
            rewriting="帮我做点事",
            rewrite_confidence=0.5,
            goal_type=GoalType.UNKNOWN,
            added_intent=(),
        ),
        termination=_PlaceholderTermination(None),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )

    result = GoalRefiner.suggest_downgrade(original)
    assert result is not None
    assert result.original.statement.goal_type == GoalType.UNKNOWN
    assert len(result.candidates) == 3
    candidate_types = {c.statement.goal_type for c in result.candidates}
    assert GoalType.INVESTIGATE in candidate_types
    assert GoalType.BUGFIX in candidate_types
    assert GoalType.FEATURE in candidate_types


def test_suggest_downgrade_no_candidate_for_bugfix():
    """Happy path: BUGFIX type无downgrade候选 (已足够窄).

    Counterexample: 如果 BUGFIX 也有降级, 会无限降级 (违反 R5 降级深度上限).
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    original = GoalTriple(
        statement=GoalStatement(
            intent="修复登录页 crash",
            rewriting="修复登录页 crash",
            rewrite_confidence=0.9,
            goal_type=GoalType.BUGFIX,
            added_intent=(),
        ),
        termination=_PlaceholderTermination(()),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )

    result = GoalRefiner.suggest_downgrade(original)
    assert result is None  # BUGFIX 不需要降级


def test_suggest_downgrade_candidates_exclude_original():
    """R4: downgrade候选不包含原始 GoalType (downgrademust到新type).

    Counterexample: 如果降级到同类型, 等于是没降级 → 欺骗用户.
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    original = GoalTriple(
        statement=GoalStatement(
            intent="调查for什么慢",
            rewriting="调查for什么慢",
            rewrite_confidence=1.0,
            goal_type=GoalType.INVESTIGATE,
            added_intent=(),
        ),
        termination=_PlaceholderTermination(()),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )

    result = GoalRefiner.suggest_downgrade(original)
    assert result is not None
    # INVESTIGATE 的候选不包含 INVESTIGATE 自身
    candidate_types = {c.statement.goal_type for c in result.candidates}
    assert GoalType.INVESTIGATE not in candidate_types


def test_suggest_downgrade_r4_original_not_mutated():
    """R4 双 Goal 共存: GoalRefiner returns的 original 未被修改.

    Counterexample: 如果 original 在降级过程中被修改, 失去可回溯性.
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    original = GoalTriple(
        statement=GoalStatement(
            intent="完全模糊的意图",
            rewriting="完全模糊的意图",
            rewrite_confidence=0.5,
            goal_type=GoalType.UNKNOWN,
            added_intent=(),
        ),
        termination=_PlaceholderTermination(None),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )

    result = GoalRefiner.suggest_downgrade(original)
    assert result is not None
    # original 的 GoalType 和 intent 都保持不变
    assert result.original.statement.goal_type == GoalType.UNKNOWN
    assert result.original.statement.intent == "完全模糊的意图"


def test_suggest_downgrade_baseline_sha_preserved():
    """Happy path: baseline_git_sha parameter被传递到 GoalDowngrade.baseline_at.

    Counterexample: 如果基线 SHA 丢失, 降级事件不可复现.
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    original = GoalTriple(
        statement=GoalStatement(
            intent="帮我看看",
            rewriting="帮我看看",
            rewrite_confidence=0.5,
            goal_type=GoalType.UNKNOWN,
            added_intent=(),
        ),
        termination=_PlaceholderTermination(None),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )

    result = GoalRefiner.suggest_downgrade(original, baseline_git_sha="abcdef123")
    assert result is not None
    assert result.baseline_at == "abcdef123"


def test_suggest_downgrade_with_different_types():
    """Happy path: verify其他有downgrade候选的type (REVIEW, MIGRATE, INVESTIGATE).

    Counterexample: 如果表里有某类型但 suggest_downgrade 不认, 是 bug.
    """
    from zall.core.goal import AcceptanceContract, GoalStatement, GoalTriple

    # REVIEW → [BUGFIX, REFACTOR, TEST_WRITE]
    review = GoalTriple(
        statement=GoalStatement(
            intent="review 这段代码", rewriting="review 这段代码",
            rewrite_confidence=1.0, goal_type=GoalType.REVIEW, added_intent=(),
        ),
        termination=_PlaceholderTermination(None),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )
    r = GoalRefiner.suggest_downgrade(review)
    assert r is not None
    review_types = {c.statement.goal_type for c in r.candidates}
    assert GoalType.BUGFIX in review_types
    assert GoalType.REFACTOR in review_types
    assert GoalType.TEST_WRITE in review_types

    # MIGRATE → [REFACTOR, TEST_WRITE]
    migrate = GoalTriple(
        statement=GoalStatement(
            intent="迁移这个项目", rewriting="迁移这个项目",
            rewrite_confidence=1.0, goal_type=GoalType.MIGRATE, added_intent=(),
        ),
        termination=_PlaceholderTermination(()),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )
    r2 = GoalRefiner.suggest_downgrade(migrate)
    assert r2 is not None
    migrate_types = {c.statement.goal_type for c in r2.candidates}
    assert GoalType.REFACTOR in migrate_types
    assert GoalType.TEST_WRITE in migrate_types
