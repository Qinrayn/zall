"""zall.core.refiner — Goal Refiner (DESIGN.md §3.3–§3.4, minimal runnable).

Corresponds to:
  §3.3   Goal Refiner: user_raw -> RefinedGoal | DeclineTask
  §3.3 R1 翻译禁加戏 (added_intent 必空)
  §3.3 R2 反问预算 (最小版 ask_budget=0, 不反问)
  §3.4   GoalDowngrade: 降级机制 (suggest_downgrade + candidate_goals)
  §3.5  GoalType 分类 (11 BaseTypes)
  §5.2  base_judge 表驱动 exposed_dependency_set (Refiner 真正消费 §5.2)

Minimal runnable scope (PR-1 不许错误增量):
  - 不调模型 (PR-3 模型无关): 用纯关键词分类器落 GoalType, 零 SDK import。
  - 不反问 (R2 语义引导不可机械检测, 走 §6.3 audit_warning, 本版不碰):
    ask_budget=0, 一律 RefinedGoal, UNKNOWN 不 Decline。
  - 不改写 (rewriting = user_raw, confidence 命中 0.9 / 未命中 0.5):
    与既有 _make_goal 行为一致, 零回归。
  - translation_of 仅做"切分", 不是"加意图": 按句 / 逗号切分每段作为可回指
    segment, added_intent 永远空 (R1 不破)。segment_id 精确形态 deferred (goal.py)。
  - suggest_downgrade (v0.0.11): 当 UNKNOWN GoalType 时, 生成更窄的 candidate goals

IPR constraints:
  IPR-0: invariant tests at tests/test_refiner_invariants.py, includesCounterexample
  IPR-1: 本文件对应 DESIGN.md §3.3 (minimal) + §3.4 + §3.5 + §5.2
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from zall.core.accountability import base_judge
from zall.core.goal import (
    AcceptanceContract,
    GoalDowngrade,
    GoalStatement,
    GoalTriple,
    GoalType,
    RefinedGoal,
    TerminationCriterion,
    TerminationState,
)

# ──────────────────────────────────────────────────────────────────────────
# §3.5 关键词分class器 (纯function, 零modeldependency)
# sequential = 优先级; 第一个命中的type胜出。
# 仅覆盖 11 BaseTypes 中可机械识别的子集; 识别不到 → UNKNOWN (诚实低置信)。
# ──────────────────────────────────────────────────────────────────────────

# P2 fix: CJK 子串匹配 + English \b 词边界匹配
_GOAL_TYPE_CJK_KEYWORDS: dict[GoalType, tuple[str, ...]] = {
    GoalType.BUGFIX: ("修复", "错误", "报错", "崩溃"),
    GoalType.FEATURE: ("新增", "实现", "支持", "功能"),
    GoalType.REFACTOR: ("重构", "清理", "重命名"),
    GoalType.TEST_WRITE: ("测试", "覆盖"),
    GoalType.DOCS: ("文档", "注释"),
    GoalType.PERF_OPT: ("性能", "优化", "慢", "加速"),
    GoalType.REVIEW: ("审查", "评审", "检查代码"),
    GoalType.INVESTIGATE: ("调查", "排查", "为什么", "诊断"),
    GoalType.MIGRATE: ("迁移", "升级依赖"),
    GoalType.SCAFFOLD: ("脚手架", "初始化项目"),
}

_GOAL_TYPE_EN_KEYWORDS: dict[GoalType, tuple[str, ...]] = {
    GoalType.BUGFIX: ("bug", "fix", "crash", "defect"),
    GoalType.FEATURE: ("feature", "add", "implement"),
    GoalType.REFACTOR: ("refactor", "cleanup", "rename"),
    GoalType.TEST_WRITE: ("test", "pytest", "unittest"),
    GoalType.DOCS: ("docs", "readme", "docstring", "comment"),
    GoalType.PERF_OPT: ("perf", "optimize", "slow"),
    GoalType.REVIEW: ("review",),
    GoalType.INVESTIGATE: ("investigate", "why", "diagnose"),
    GoalType.MIGRATE: ("migrate", "upgrade", "port"),
    GoalType.SCAFFOLD: ("scaffold", "boilerplate"),
}

_GOAL_TYPE_EN_PATTERNS: dict[GoalType, tuple[re.Pattern[str], ...]] = {
    gt: tuple(re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in kws)
    for gt, kws in _GOAL_TYPE_EN_KEYWORDS.items()
}

_GOAL_TYPE_KEYWORDS: dict[GoalType, tuple[str, ...]] = {
    gt: _GOAL_TYPE_CJK_KEYWORDS.get(gt, ()) + _GOAL_TYPE_EN_KEYWORDS.get(gt, ())
    for gt in GoalType
}

# 分class优先级 (靠前的先匹配)
_GOAL_TYPE_PRIORITY: tuple[GoalType, ...] = (
    GoalType.BUGFIX,
    GoalType.REFACTOR,
    GoalType.TEST_WRITE,
    GoalType.DOCS,
    GoalType.PERF_OPT,
    GoalType.MIGRATE,
    GoalType.SCAFFOLD,
    GoalType.FEATURE,
    GoalType.REVIEW,
    GoalType.INVESTIGATE,
)


def _classify_goal_type(user_raw: str) -> GoalType:
    """纯关键词分class (§3.5)。

    P2 fix: English 用 \\b 词边界匹配, CJK 用子串匹配。
    防 "_apitest" 误匹配 "test"。
    """
    text = user_raw.lower()
    for gt in _GOAL_TYPE_PRIORITY:
        for kw in _GOAL_TYPE_CJK_KEYWORDS.get(gt, ()):
            if kw in text:
                return gt
        for pat in _GOAL_TYPE_EN_PATTERNS.get(gt, ()):
            if pat.search(text):
                return gt
    return GoalType.UNKNOWN


def _split_segments(user_raw: str) -> tuple[str, ...]:
    """把 user_raw 切分为可回指 segment (§3.3 translation_of)。

    切分 ≠ 加意图: 仅按 [。.!?！？;；\n] 或逗号切, 去空白, 丢空段。
    added_intent 永远空 (R1 不破); 每段都是 user_raw 原有子句。
    segment_id 精确形态 deferred (goal.py new_segment_id placeholder)。
    """
    parts = re.split(r"[。.!?！？;；\n]+|,|，", user_raw)
    segs = [p.strip() for p in parts if p.strip()]
    # 整句也被preserve为可回指 (若未切出多段, 退化为整句)
    return tuple(segs) if segs else (user_raw,)


@dataclass
class _PlaceholderTermination:
    """占位 TerminationCriterion (judgment实际走 Judge, 不是这里)。

    与既有 _make_goal 占位同语义: 永远 UNDECIDABLE, 由 Judge 兜底。
    Refiner 只决定"目标态分类", 不决定"完成" (PR-0 诚实退让不破)。

    M11: 非 frozen dataclass — exposed_dependency_set 须可写以满足
    TerminationCriterion Protocol 的 settable variable 要求。
    """

    exposed_dependency_set: tuple[str, ...] | None

    def __call__(self, state: object) -> TerminationState:
        return TerminationState.UNDECIDABLE


class GoalRefiner:
    """Goal Refiner (§3.3 minimal runnable)。

    方法:
        refine(user_raw, *, judge_mode) -> RefinedGoal
            纯函数, 零模型依赖。
            总是返回 RefinedGoal (最小版不 Decline, UNKNOWN 也走 RefinedGoal)。
            异常 (理论上不应发生) 由调用点 fallback 到 _make_goal 兜底。

    IPR-0 不变量 (落 RefinedGoal validator):
        - added_intent 必空 (R1)
        - questions_used(=0) ≤ ask_budget(=0) (R2)
        - confidence ∈ [0.0, 1.0]
    """

    ASK_BUDGET: int = 0  # 最小版不反问 (R2 语义引导 deferred)

    @classmethod
    def refine(cls, user_raw: str, *, judge_mode: str) -> RefinedGoal:
        """user_raw -> RefinedGoal (§3.3, 最小version)。

        judge_mode: "system" | "user" | "none"
            - "system": 强制 BUGFIX 类 (覆盖分类, 与既有 _make_goal 行为一致)
              exposed_dependency_set=()
            - 其他: 走关键词分类; system_judge 类 (§5.2 main=system) → exposed=(),
              user/model_self 类 → exposed=None
        """
        if judge_mode == "system":
            goal_type = GoalType.BUGFIX
            confidence = 0.9
        else:
            goal_type = _classify_goal_type(user_raw)
            # 命中已知type → 0.9; UNKNOWN → 0.5 (诚实低置信)
            confidence = 0.9 if goal_type is not GoalType.UNKNOWN else 0.5

        # §5.2 base_judge 表驱动 exposed_dependency_set
        # main=system 的class须暴露 (); 其他可 None
        main_judge, _aux = base_judge(goal_type)
        exposed: tuple[str, ...] | None = () if main_judge == "system" else None

        # P2 fix: rewrite_confidence 不应恒为 1.0 (无改写 ≠ 完全可信)
        # 根据 user_raw 长度给合理值: 短description模糊 → 0.7, 长description较明确 → 0.95
        _raw_len = len(user_raw.strip())
        if _raw_len < 20:
            rewrite_conf = 0.7
        elif _raw_len > 80:
            rewrite_conf = 0.95
        else:
            rewrite_conf = 0.7 + (_raw_len - 20) / 60.0 * 0.25

        goal = GoalTriple(
            statement=GoalStatement(
                intent=user_raw,
                rewriting=user_raw,  # 最小版不改写
                rewrite_confidence=rewrite_conf,  # P2 fix: 长度感知置信度
                goal_type=goal_type,
                translation_of=_split_segments(user_raw),
                added_intent=(),  # R1 必空
            ),
            termination=_PlaceholderTermination(exposed),
            acceptance=AcceptanceContract(baseline_frozen_at="cli_run"),
        )

        return RefinedGoal(
            user_raw=user_raw,
            questions_used=0,  # R2: 不反问
            refined_goal=goal,
            translation_of=_split_segments(user_raw),
            added_intent=(),  # R1 必空 (Refiner 层独立断言)
            confidence=confidence,
            ask_budget=cls.ASK_BUDGET,
        )

    # ── §3.4 GoalDowngrade: downgrade建议 (v0.0.11) ──

    # downgrade候选表: 当原始 GoalType 太宽泛不可单一定时, 推荐更窄的候选type
    # KEY = 原始 GoalType, VALUE = 推荐的downgrade候选 GoalType list (有优先级)
    _DOWNGRADE_CANDIDATES: dict[GoalType, tuple[GoalType, ...]] = {
        GoalType.UNKNOWN: (
            GoalType.INVESTIGATE,
            GoalType.BUGFIX,
            GoalType.FEATURE,
        ),
        GoalType.INVESTIGATE: (
            GoalType.BUGFIX,
            GoalType.PERF_OPT,
            GoalType.REFACTOR,
        ),
        GoalType.REVIEW: (
            GoalType.BUGFIX,
            GoalType.REFACTOR,
            GoalType.TEST_WRITE,
        ),
        GoalType.MIGRATE: (
            GoalType.REFACTOR,
            GoalType.TEST_WRITE,
        ),
    }

    @classmethod
    def suggest_downgrade(
        cls,
        original: GoalTriple,
        *,
        baseline_git_sha: str = "",
    ) -> GoalDowngrade | None:
        """§3.4: 为过宽的不可judgment Goal 建议downgrade候选 (§3.4.2)。

        Args:
            original: Refiner 产出的原始 GoalTriple (e.g. UNKNOWN)
            baseline_git_sha: 触发降级时的 git SHA

        Returns:
            GoalDowngrade 若原始类型在候选表中;
            None 若该类型无降级候选 (不适用降级机制, 走 Decline)

        IPR-0 不变量:
          - 返回的 GoalDowngrade 中 original 保持不变 (R4)
          - candidates 不包含 original goal_type (降级到新类型, 不是同类型)
          - downgrade_depth ≤ D
          - approximate_flag 必 True (R6)
        """
        original_type = original.statement.goal_type

        # 只对表中有downgrade候选的type触发downgrade
        candidate_types = cls._DOWNGRADE_CANDIDATES.get(original_type)
        if candidate_types is None:
            return None  # 无降级候选, 走 Decline / 不适用

        user_raw = original.statement.intent
        segments = _split_segments(user_raw)

        candidates: list[GoalTriple] = []
        seen_types: set[GoalType] = set()

        for gt in candidate_types:
            if gt == original_type:
                continue  # 降级必须到新类型
            if gt in seen_types:
                continue
            seen_types.add(gt)

            # 为每个 candidate construct GoalTriple
            main_judge, _aux = base_judge(gt)
            exposed: tuple[str, ...] | None = () if main_judge == "system" else None

            candidate = GoalTriple(
                statement=GoalStatement(
                    intent=user_raw,  # 保持用户原话 (§3.2.1 immutable)
                    rewriting=user_raw,
                    rewrite_confidence=0.5,  # 降级是近似, 置信度偏低
                    goal_type=gt,
                    translation_of=segments,
                    added_intent=(),  # R1 必空
                ),
                termination=_PlaceholderTermination(exposed),
                acceptance=AcceptanceContract(
                    baseline_frozen_at=baseline_git_sha or "downgrade",
                ),
            )
            candidates.append(candidate)

        if not candidates:
            return None

        return GoalDowngrade(
            original=original,
            candidates=tuple(candidates),
            downgrade_depth=1,  # 第一层降级
            approximate_flag=True,  # R6
            baseline_at=baseline_git_sha,
        )
