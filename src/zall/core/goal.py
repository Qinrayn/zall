"""zall.core.goal — Goal dimension code projection (DESIGN.md §3.2–§3.4).

Corresponds to:
  §3.2.1 GoalStatement  —— intent / rewriting / rewrite_confidence / goal_type
                           / translation_of / added_intent(必空, R1)
  §3.2.2 TerminationCriterion —— 纯函数, three-state, exposed_dependency_set (v0.0.6 回填)
  §3.2.3 AcceptanceContract   —— baseline_frozen_at / prohibited_actions / escalation
  §3.4   GoalDowngrade         —— 降级机制 (original + candidates + depth + approximate_flag)
  §3.4.3 DowngradeGateState    —— 降级闸门状态 (R4/R5/R6 刚性规则)
  §3.5   GoalType Enum        —— 11 BaseTypes + ExtendedGoalType (本文件only落 BaseTypes)

IPR constraints:
  IPR-0: invariant tests at tests/test_goal_invariants.py, includesCounterexample
  IPR-1: 本文件每段对应 DESIGN.md §3.2.x
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator


# ──────────────────────────────────────────────────────────────────────────
# §3.5 GoalType Enum (BaseTypes 11 种, for coding agent, SETTLED-fornow)
# ──────────────────────────────────────────────────────────────────────────


class GoalType(str, Enum):
    """coding agent 领域的 Base GoalType (DESIGN.md §3.5.1)。

    SETTLED-fornow: 已观察到的全集; 未来发现新类型须显式扩展 (ExtendedGoalType)。
    """

    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST_WRITE = "test_write"
    DOCS = "docs"
    PERF_OPT = "perf_opt"
    REVIEW = "review"
    INVESTIGATE = "investigate"
    MIGRATE = "migrate"
    SCAFFOLD = "scaffold"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────────────────────────────────────
# §3.2.2 TerminationCriterion (three-state + exposed_dependency_set)
# ──────────────────────────────────────────────────────────────────────────


class TerminationState(str, Enum):
    """three-stateTermination criterion (DESIGN.md §3.2.2)。

    three-state不是二态: undecidable 是诚实的终止, 比 false-positive met 更负责。
    PR-0 落地: agent 不能假装完成。
    """

    NOT_MET = "not_met"
    MET = "met"
    UNDECIDABLE = "undecidable"


@runtime_checkable
class TerminationCriterion(Protocol):
    """Termination criterionprotocol (DESIGN.md §3.2.2)。

    必须为**纯函数**: 输入=当前状态, 输出 ∈ {not_met, met, undecidable}。
    纯性无法在 type 层强制 (PR-0 半认输); 由 invariant test用幂等性代理。

    exposed_dependency_set (v0.0.6 回填):
        system_judge 类 GoalType 必填; user_judge 类 GoalType 可 None。
        交叉验证在 GoalTriple.validator 中做。
    """

    exposed_dependency_set: tuple[str, ...] | None

    def __call__(self, state: object) -> TerminationState: ...


# ──────────────────────────────────────────────────────────────────────────
# §3.2.3 AcceptanceContract
# ──────────────────────────────────────────────────────────────────────────


class Escalation(str, Enum):
    """Acceptance contract触发后的出路 (DESIGN.md §3.2.3)。"""

    HUMAN_REVIEW = "human_review"
    ABORT = "abort"


class AcceptanceContract(BaseModel):
    """Acceptance contract (DESIGN.md §3.2.3)。

    IPR-0 不变量:
        - baseline_frozen_at 不可空 (test基线冻结点)
        - prohibited_actions 用 tuple (不可变)
    """

    model_config = ConfigDict(frozen=True)

    baseline_frozen_at: str
    prohibited_actions: tuple[str, ...] = ()
    escalation: Escalation = Escalation.HUMAN_REVIEW


# ──────────────────────────────────────────────────────────────────────────
# §3.2.1 GoalStatement (includes v0.0.8 回填的 goal_type 字段)
# ──────────────────────────────────────────────────────────────────────────


class GoalStatement(BaseModel):
    """Goal statement (DESIGN.md §3.2.1, v0.0.8 回填 goal_type)。

    IPR-0 不变量 (R1 翻译禁加戏):
        - intent: 用户原话, immutable (frozen + str 天然不可变)
        - translation_of: 每条须可回指 user_raw 子句 (segment_id 形态 deferred)
        - added_intent: **必空** (validator 断言, 构造时传非空须 raise)
    """

    model_config = ConfigDict(frozen=True)

    intent: str
    rewriting: str
    rewrite_confidence: float
    goal_type: GoalType
    translation_of: tuple[str, ...] = ()
    added_intent: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _added_intent_must_be_empty(self) -> GoalStatement:
        """R1 translate禁加戏: added_intent must为空 (DESIGN.md §3.3 R1)。

        Counterexample: 构造时传 added_intent=("x",) → 须 raise ValueError。
        """
        if len(self.added_intent) > 0:
            raise ValueError(
                "added_intent must be empty (R1: 翻译禁加戏; "
                "Refiner 不可注入 user_raw 未表达的意图)"
            )
        return self

    @model_validator(mode="after")
    def _rewrite_confidence_range(self) -> GoalStatement:
        """rewrite_confidence ∈ [0.0, 1.0]。

        Counterexample: confidence=1.5 → 须 raise。
        """
        if not (0.0 <= self.rewrite_confidence <= 1.0):
            raise ValueError(
                f"rewrite_confidence must be in [0.0, 1.0], got {self.rewrite_confidence}"
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# §3.2 GoalTriple (三段式聚合)
# ──────────────────────────────────────────────────────────────────────────


# §5.2 base_judge 表中 main=system 的 GoalType 集合
# (用于 exposed_dependency_set 交叉validate)
_SYSTEM_JUDGE_GOAL_TYPES: frozenset[GoalType] = frozenset(
    {
        GoalType.BUGFIX,
        GoalType.FEATURE,
        GoalType.REFACTOR,
        GoalType.TEST_WRITE,
        GoalType.PERF_OPT,
        GoalType.MIGRATE,
        GoalType.SCAFFOLD,
    }
)


class GoalTriple(BaseModel):
    """Goal 三段式聚合 (DESIGN.md §3.2)。

    IPR-0 不变量:
        - frozen (不可重新赋值)
        - statement / acceptance 是 frozen pydantic model
        - termination 是 runtime_checkable Protocol (有 __call__ + exposed_dependency_set)
        - 交叉验证: system_judge 类 goal_type → termination.exposed_dependency_set 必非 None
                    user_judge 类 goal_type → 可 None
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    statement: GoalStatement
    termination: TerminationCriterion
    acceptance: AcceptanceContract

    @model_validator(mode="after")
    def _exposed_dependency_set_cross_check(self) -> GoalTriple:
        """v0.0.6 §3.2.2 回填的交叉validate。

        system_judge 类 GoalType (§5.2 表 main=system) 的 termination
        必须暴露 exposed_dependency_set (非 None)。

        Counterexample: goal_type=bugfix 但 termination.exposed_dependency_set=None
              → 须 raise (hunk 归属分类器无输入)。
        """
        if self.statement.goal_type in _SYSTEM_JUDGE_GOAL_TYPES:
            if self.termination.exposed_dependency_set is None:
                raise ValueError(
                    f"goal_type={self.statement.goal_type.value} 是 system_judge 类, "
                    f"termination.exposed_dependency_set 必须非 None "
                    f"(§3.2.2 v0.0.6 回填: hunk 分类器需此输入)"
                )
        return self


# ──────────────────────────────────────────────────────────────────────────
# §3.4 GoalDowngrade (downgrade机制)
# ──────────────────────────────────────────────────────────────────────────


class DowngradeGateState(str, Enum):
    """downgradegatestate (§3.4.4): 在 Refiner gate基础上extension。
    
    workflow:
      Refiner 耗尽预算 → 不可单一可判定 → suggest_downgrade()
      → DOWNGADE_PROPOSED → user 接受 → DOWNGADE_EXECUTING
                          → user 拒绝 → 走 Decline (ABORT)
    
    与 ConfirmGate 桥接: loop 看到 DeclineTask 时检查是否有 downgrade 建议,
    若有 → 进入降级闸门; 若无 → 按现有路径 Decline。
    """
    
    NO_DOWNGRADE = "no_downgrade"          # 不需要降级 (正常路径)
    DOWNGADE_PROPOSED = "downgrade_proposed"  # 建议降级, 等待 user 响应
    DOWNGADE_ACCEPTED = "downgrade_accepted"  # user 接受降级
    DOWNGADE_REJECTED = "downgrade_rejected"  # user 拒绝降级


class GoalDowngrade(BaseModel):
    """Goal downgrade (§3.4.2): preserve原始intent, 开出更窄的可judgment Goal 作为近似替身。
    
    触发条件: Refiner 耗尽 K 仍无法将用户意图转译为单一可判定 Goal。
    
    刚性规则:
      R4 双 Goal 共存: original 永不删除 (字段必非空)
      R5 降级深度上限: downgrade_depth ≤ D (默认 D=1)
      R6 不可单方触发: 只能由用户在闸门处触发 (approximate_flag=True)
    
    IPR-0 不变量:
      - original 非空 (R4)
      - candidates 长度 ≥ 1 (降级必须有替身)
      - downgrade_depth ∈ [1, D]
      - approximate_flag 必 True (R6 不可 agent 单方触发)
    """
    
    DEFAULT_MAX_DEPTH: int = 1  # 全局默认 D
    
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    
    original: GoalTriple
    candidates: tuple[GoalTriple, ...]
    downgrade_depth: int = 1
    approximate_flag: bool = True
    baseline_at: str = ""  # git SHA at downgrade trigger

    @model_validator(mode="after")
    def _candidates_non_empty(self) -> GoalDowngrade:
        """downgrademust有替身: candidates 至少 1 个。"""
        if len(self.candidates) == 0:
            raise ValueError("candidates must be non-empty (降级必须有替身 Goal)")
        return self
    
    @model_validator(mode="after")
    def _r5_depth_bound(self) -> GoalDowngrade:
        """R5 downgrade深度max: downgrade_depth ≤ D。"""
        if self.downgrade_depth < 1:
            raise ValueError(
                f"downgrade_depth must be ≥ 1, got {self.downgrade_depth}"
            )
        if self.downgrade_depth > self.DEFAULT_MAX_DEPTH:
            raise ValueError(
                f"downgrade_depth ({self.downgrade_depth}) exceeds max ({self.DEFAULT_MAX_DEPTH}) "
                f"(R5: 降级深度上限)"
            )
        return self
    
    @model_validator(mode="after")
    def _r6_approximate_flag(self) -> GoalDowngrade:
        """R6: approximate_flag must为 True (不可 agent 单方触发downgrade)。"""
        if not self.approximate_flag:
            raise ValueError(
                "approximate_flag must be True (R6: 不可 agent 单方触发, "
                "降级只能在用户闸门确认后设置)"
            )
        return self


# ──────────────────────────────────────────────────────────────────────────
# segment_id generator (translation_of 的元素; 形态 deferred, 本file给placeholder)
# ──────────────────────────────────────────────────────────────────────────


def new_segment_id() -> str:
    """生成一个 segment_id (形态 deferred; 当前用 uuid4 placeholder)。

    未来可换为 user_raw 的子句 hash + 偏移; 当前only保证唯一性。
    """
    return uuid4().hex


# ──────────────────────────────────────────────────────────────────────────
# §3.3 Goal Refiner output schema (RefinedGoal / DeclineTask)
# ──────────────────────────────────────────────────────────────────────────
# 形态 SETTLED (DESIGN.md §3.3.2); 运行前提 PENDING (context_permitted /
# ask_budget 来自 §4 Authority, §4 已 SETTLED 但 Refiner 尚未接 run)。
#
# 本file只落 output schema + R1/R2 预算约束的机械execute;
# R2 "问询禁引导" 的语义judgment不可机械检测 (走 §6.3 audit_warning), 不假装;
# R3 "translate即lock" 是gatestate machine, 属 ConfirmGate (§4.5), 不在此 primitive。


class RefineOutcome(BaseModel):
    """Refiner output的 Union 基class (DESIGN.md §3.3.2 Output)。

    RefinedGoal | DeclineTask 共享 user_raw 回指; 用 model_validator 在子类
    各自执行 R1/R2 约束。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    user_raw: str
    questions_used: int


class DeclineTask(RefineOutcome):
    """DeclineTask: Refiner 耗尽预算仍无法转译 (DESIGN.md §3.3.2)。

    IPR-0 不变量:
        - questions_used ≤ ask_budget (R2 预算约束)
        - reason 固定 "intent_not_refinable_in_budget"
    """

    reason: str = "intent_not_refinable_in_budget"
    partial_translation: tuple[str, ...] = ()
    ask_budget: int

    @model_validator(mode="after")
    def _questions_within_budget(self) -> DeclineTask:
        """R2 预算约束: questions_used ≤ ask_budget。

        Counterexample: ask_budget=1 但 questions_used=3 → 须 raise。
        """
        if self.questions_used > self.ask_budget:
            raise ValueError(
                f"questions_used ({self.questions_used}) > ask_budget "
                f"({self.ask_budget}): Refiner 突破了预算 (R2 预算约束)"
            )
        return self


class RefinedGoal(RefineOutcome):
    """RefinedGoal: Refiner 成功转译的产出 (DESIGN.md §3.3.2)。

    IPR-0 不变量:
        - R1: added_intent 必空 (翻译禁加戏; 与 GoalStatement.added_intent 同形约束,
          但 Refiner 层独立再断言一次, 因 RefinedGoal.added_intent 是 Refiner
          自己声称的 "我没加意图", 与 GoalTriple 内的 added_intent 是两个层)
        - R2 预算: questions_used ≤ ask_budget
        - confidence ∈ [0.0, 1.0] (与 GoalStatement 同形)
    """

    refined_goal: GoalTriple
    translation_of: tuple[str, ...] = ()
    added_intent: tuple[str, ...] = ()
    confidence: float
    ask_budget: int

    @model_validator(mode="after")
    def _r1_added_intent_must_be_empty(self) -> RefinedGoal:
        """R1 translate禁加戏 (DESIGN.md §3.3 R1): added_intent 必空。

        Counterexample: Refiner 产出 added_intent=("optimize_cache",) → 须 raise。
        与 GoalStatement._added_intent_must_be_empty 同形但独立: Refiner 层的
        added_intent 是 "Refiner 声称自己没加的意图", 与 GoalTriple 内 statement
        的 added_intent 是两个独立断言层。
        """
        if len(self.added_intent) > 0:
            raise ValueError(
                "added_intent must be empty (R1: 翻译禁加戏; "
                "Refiner 不可注入 user_raw 未表达的意图)"
            )
        return self

    @model_validator(mode="after")
    def _r2_questions_within_budget(self) -> RefinedGoal:
        """R2 预算约束 (DESIGN.md §3.3 R2): questions_used ≤ ask_budget。

        Counterexample: ask_budget=0 但 questions_used=1 → 须 raise。
        """
        if self.questions_used > self.ask_budget:
            raise ValueError(
                f"questions_used ({self.questions_used}) > ask_budget "
                f"({self.ask_budget}): Refiner 突破了反问预算 (R2)"
            )
        return self

    @model_validator(mode="after")
    def _confidence_range(self) -> RefinedGoal:
        """confidence ∈ [0.0, 1.0] (与 GoalStatement 同形)。

        Counterexample: confidence=1.5 → 须 raise。
        """
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        return self
