"""zall.core.accountability — Judge + Evidence (DESIGN.md §5.2-5.4).

Corresponds to:
  §5.2   Judge subject三options: system | user | model_self
         base_judge 表: GoalType -> (main, aux)
  §5.3   Evidence: baseline_sha / current_sha / diff / test_results / lint_results / external
  §5.4   consistency: 主 undecidable → 辅cannot override; divergent → met_with_caveat
         caveat subtype: main_unavailable / main_aux_divergent (v0.0.3 立)

IPR constraints:
  IPR-0: invariant tests at tests/test_judge_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §5.2-5.4
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from zall.core.goal import GoalType, TerminationState


# ──────────────────────────────────────────────────────────────────────────
# §5.4 CaveatType (v0.0.3 立的两个subtype)
# ──────────────────────────────────────────────────────────────────────────


class CaveatType(str, Enum):
    """caveat subtype (DESIGN.md §5.4, v0.0.3 立)。

    两个看似都会触发 met_with_caveat 的事件, 实质独立:

    main_unavailable:
        主 Judge 当前不可用 (eg. system 无test可跑)。
        缺的是**判定**。对策: 补判据。

    main_aux_divergent:
        主/辅 Judge 都能跑但结论divergent。
        缺的是**一致**。对策: 调 Goal / 加 Validate Channel。

    不允许把两个事件合并成单一 caveat —— 会让 Verifiability 失去诊断能力。
    """

    MAIN_UNAVAILABLE = "main_unavailable"
    MAIN_AUX_DIVERGENT = "main_aux_divergent"


# ──────────────────────────────────────────────────────────────────────────
# §5.2 JudgeVerdict (Judge 的产出)
# ──────────────────────────────────────────────────────────────────────────


class JudgeVerdict(BaseModel):
    """单个 Judge 的产出 (DESIGN.md §5.2)。

    统一three-state (与 §3.2.2 TerminationState 一致):
        not_met / met / undecidable

    caveat (§5.4):
        单个 Judge 只填 main_unavailable (自己跑不了时)。
        main_aux_divergent 由上层多 Judge 编排填 (单个 Judge 不判"divergent")。

    report:
        自然语言报告, 给 audit 用, **不参与判定**。
        (与 §6.3 audit_warning 同型: 报告是证据, 不是判据)

    IPR-0 不变量:
        - frozen
        - caveat=main_unavailable 时, state 应为 undecidable
          (主 Judge 跑不了, 不能假装 met)
    """

    model_config = ConfigDict(frozen=True)

    state: TerminationState
    caveat: CaveatType | None = None
    report: str = ""

    @classmethod
    def undecidable_with_caveat(cls, caveat: CaveatType, report: str = "") -> JudgeVerdict:
        """construct一个带 caveat 的 undecidable verdict。

        §5.4: 主 Judge = undecidable → 辅 Judge cannot override改 met。
        所以 caveat 类 verdict 必须是 undecidable, 不能是 met。
        """
        return cls(state=TerminationState.UNDECIDABLE, caveat=caveat, report=report)


# ──────────────────────────────────────────────────────────────────────────
# §5.3 Evidence
# ──────────────────────────────────────────────────────────────────────────


class TestCaseResult(BaseModel):
    """单条test结果。"""

    __test__ = False  # pytest 不要把这个数据类当test类收集

    model_config = ConfigDict(frozen=True)

    test_id: str
    passed: bool
    skipped: bool = False


class LintResult(BaseModel):
    """单条 lint 结果。"""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    ok: bool
    warning: bool = False


class Evidence(BaseModel):
    """验收evidence (DESIGN.md §5.3)。

    IPR-0 不变量:
        - frozen
        - baseline_sha / current_sha 非空 (git 提交指纹)

    已知 OPEN:
        - external: dict[str, Any] placeholder —— 具体形态见 §6.5 schema
          (user_confirm / model_self_check / data_snapshot), deferred 收紧。
          与 Action.args 同型: 不假装能做到做不到的事。
    """

    model_config = ConfigDict(frozen=True)

    baseline_sha: str
    current_sha: str
    diff: str = ""  # structured_diff 的字符串形式 (deferred 收紧为结构化)
    test_results: tuple[TestCaseResult, ...] = ()
    lint_results: tuple[LintResult, ...] = ()
    external: dict[str, Any] = {}  # §6.5 schema, deferred 收紧

    @staticmethod
    def __no_tool_history__() -> bool:
        """Evidence 不携带 tool 调用历史 (§4.3 核心斩断呼应)。

        Evidence 是"状态快照 + test/lint 结果", 不是"agent 做过什么的日志"。
        """
        return True


# ──────────────────────────────────────────────────────────────────────────
# §5.2 Judge Protocol
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Judge(Protocol):
    """judgmentsubjectprotocol (DESIGN.md §5.2)。

    三options: system | user | model_self
    各自如何得到 verdict 不同 (实现层细节), 接口统一。

    纯性: 相同 evidence 两次调用结果相同 (幂等性代理, 与 §3.2.2 同型)。

    IPR-0 不变量:
        - 返回 JudgeVerdict (three-state + caveat + report)
        - caveat=main_unavailable 时, state 必为 undecidable
    """

    def __call__(self, evidence: Evidence) -> JudgeVerdict: ...


    @property
    def judge_type(self) -> str:
        """标识 Judge type: "system" | "user" | "model_self"。

        用于 §5.2 base_judge 表的 main/aux 配对。
        """
        ...


# ──────────────────────────────────────────────────────────────────────────
# §5.2 base_judge 表 (GoalType -> main/aux)
# ──────────────────────────────────────────────────────────────────────────


# coding agent 领域的 base_judge defaultcomposite (DESIGN.md §5.2 表 2)
# (main_judge_type, aux_judge_type)
_BASE_JUDGE_TABLE: dict[GoalType, tuple[str, str]] = {
    GoalType.BUGFIX: ("system", "model_self"),
    GoalType.FEATURE: ("system", "model_self"),
    GoalType.REFACTOR: ("system", "model_self"),
    GoalType.TEST_WRITE: ("system", "user"),
    GoalType.DOCS: ("user", "model_self"),
    GoalType.PERF_OPT: ("system", "model_self"),
    GoalType.REVIEW: ("model_self", "user"),
    GoalType.INVESTIGATE: ("model_self", "user"),
    GoalType.MIGRATE: ("system", "model_self"),
    GoalType.SCAFFOLD: ("system", "user"),
    GoalType.UNKNOWN: ("user", "model_self"),
}


def base_judge(goal_type: GoalType) -> tuple[str, str]:
    """query GoalType 的default (main_judge_type, aux_judge_type)。

    对应 DESIGN.md §5.2 表 2。SETTLED for coding agent, fornow 可扩展。

    Counterexample: 如果有人查询一个不存在的 GoalType, 须 raise KeyError。
    """
    if goal_type not in _BASE_JUDGE_TABLE:
        raise KeyError(
            f"GoalType {goal_type} 不在 base_judge 表中; "
            f"ExtendedGoalType 须走 fallback_to 继承 (§3.5)"
        )
    return _BASE_JUDGE_TABLE[goal_type]


# ──────────────────────────────────────────────────────────────────────────
# §5.4 多 Judge consistency编排
# ──────────────────────────────────────────────────────────────────────────


class AccountabilityResult(BaseModel):
    """多 Judge 编排的最终结果 (DESIGN.md §5.4)。

    §5.4 consistency规则:
        - 主 Judge = undecidable → 辅 Judge cannot override改 met (保 PR-0)
        - 主/辅divergent → met_with_caveat / not_met_with_signal
        - caveat 必须在 RunEgress 报

    状态升级:
        主 met + 辅 met → met
        主 met + 辅 not_met/undecidable → met_with_caveat (main_aux_divergent)
        主 not_met + 辅 任意 → not_met (主判否决, 辅不能救)
        主 undecidable + 辅 met → undecidable (辅cannot override) + caveat (main_aux_divergent)
        主 undecidable + 辅 not_met/undecidable → undecidable
        主 = main_unavailable caveat → undecidable + caveat (main_unavailable)

    IPR-0 不变量:
        - frozen
    """

    model_config = ConfigDict(frozen=True)

    state: TerminationState
    caveat: CaveatType | None = None
    main_verdict: JudgeVerdict
    aux_verdict: JudgeVerdict | None = None

    @classmethod
    def from_verdicts(
        cls, main: JudgeVerdict, aux: JudgeVerdict | None = None
    ) -> AccountabilityResult:
        """根据主/辅 verdict 计算最终结果 (§5.4 consistencyrule)。

        纯函数: 不调外部服务, 不引模型。
        """
        # 主 Judge 带 main_unavailable caveat → undecidable
        if main.caveat == CaveatType.MAIN_UNAVAILABLE:
            return cls(
                state=TerminationState.UNDECIDABLE,
                caveat=CaveatType.MAIN_UNAVAILABLE,
                main_verdict=main,
                aux_verdict=aux,
            )

        # 无辅 Judge → 直接用主
        if aux is None:
            return cls(
                state=main.state,
                caveat=main.caveat,
                main_verdict=main,
                aux_verdict=None,
            )

        # §5.4: 主 undecidable → 辅cannot override改 met
        if main.state == TerminationState.UNDECIDABLE:
            if aux.state == TerminationState.MET:
                # 辅说 met 但主说 undecidable → divergent, 但主优先, 仍 undecidable
                return cls(
                    state=TerminationState.UNDECIDABLE,
                    caveat=CaveatType.MAIN_AUX_DIVERGENT,
                    main_verdict=main,
                    aux_verdict=aux,
                )
            # 辅也 not_met/undecidable → 一致 undecidable
            return cls(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                main_verdict=main,
                aux_verdict=aux,
            )

        # 主 met + 辅 met → met
        if main.state == TerminationState.MET:
            if aux.state == TerminationState.MET:
                return cls(
                    state=TerminationState.MET,
                    caveat=None,
                    main_verdict=main,
                    aux_verdict=aux,
                )
            # 主 met + 辅 not_met/undecidable → met_with_caveat
            return cls(
                state=TerminationState.MET,
                caveat=CaveatType.MAIN_AUX_DIVERGENT,
                main_verdict=main,
                aux_verdict=aux,
            )

        # 主 not_met + 辅 任意 → not_met (主判否决, 辅不能救)
        return cls(
            state=TerminationState.NOT_MET,
            caveat=None,
            main_verdict=main,
            aux_verdict=aux,
        )
