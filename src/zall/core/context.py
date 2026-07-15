"""zall.core.context — Context primitive (DESIGN.md §4.3).

Corresponds to:
  §4.3 Context = (task_level, history_level, domain_level, user_explicit_artifacts)
  §4.3 核心斩断: agent 不许偷偷拿跨 run 上下文; 用户可显式回灌, 被审计。
                history_level 不includes tool 历史。

IPR constraints:
  IPR-0: invariant tests at tests/test_context_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §4.3
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from zall.core.goal import GoalStatement


# ──────────────────────────────────────────────────────────────────────────
# 子结构 Protocol placeholder (concrete形态 deferred, 后续轮次落码)
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class CwdMeta(Protocol):
    """Current working directory metadata (read-only). Shape deferred.

    最小接口: cwd_path / git_branch / git_remote。
    后续可扩展 (eg. 工作目录文件数 / 权限 / ...)。
    """

    cwd_path: str
    git_branch: str | None
    git_remote: str | None


@runtime_checkable
class RunEgressSummary(Protocol):
    """RunEgress summaries for previous N runs (§3.4.5).

    具体形态 deferred (RunEgress primitive 未落码)。
    本 Protocol onlyplaceholder, 后续 RunEgress 落码时须满足此接口。
    """

    # placeholder: 最小interface为空, 后续extension
    ...


@runtime_checkable
class DomainKnowledge(Protocol):
    """Coding-agent domain knowledge. Shape deferred.

    最小接口: protected_branches (eg. ("main", "master", "release/*"))。
    后续可扩展 (eg. test_frameworks / linter_rules / ...)。
    """

    protected_branches: tuple[str, ...]


# ──────────────────────────────────────────────────────────────────────────
# §4.3 Context
# ──────────────────────────────────────────────────────────────────────────


class Context(BaseModel):
    """agent 运行时的contextsnapshot (DESIGN.md §4.3)。

    IPR-0 不变量:
        - frozen (不可重新赋值, 防止 context_judge 结果不可复现)
        - history_level 不includes tool 历史 (§4.3 核心斩断)
          → prior_goal_statements + prior_run_egress_summaries 是only有的 history 字段
          → 两者都不携带 tool 调用记录

    核心斩断 (§4.3):
        agent 不许偷偷拿跨 run 上下文; 用户可显式回灌, 被审计。
        This deliberately diverges from approaches that retain full session history.
        理由是防跨 run 上下文污染 (R-三维同推 风险对治)。

    已知 OPEN:
        - CwdMeta / RunEgressSummary / DomainKnowledge 的具体形态 deferred
        - user_explicit_artifacts 是 artifact_id 列表 (tuple[str, ...]),
          具体 artifact 结构 deferred
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # ── task_level (§4.3)
    user_raw: str
    cwd_meta: CwdMeta

    # ── history_level (§4.3) —— 不includes tool 历史 (核心斩断)
    prior_goal_statements: tuple[GoalStatement, ...] = ()
    prior_run_egress_summaries: tuple[RunEgressSummary, ...] = ()

    # ── domain_level (§4.3)
    domain: DomainKnowledge | None = None

    # ── user_explicit_artifacts (§4.3)
    user_explicit_artifacts: tuple[str, ...] = ()

    @staticmethod
    def __no_tool_history__() -> bool:
        """声明: Context 不携带 tool 调用历史 (§4.3 核心斩断)。

        history_level onlyincludes GoalStatement + RunEgress 摘要,
        不includes tool_call_start / tool_call_end / tool_result 记录。
        防跨 run 上下文污染。

        Counterexample: 如果有人给 Context 加 tool_history 字段, 此标记须被删除 ——
        但删除标记本身会在 code review 中暴露 (声明式审计)。
        """
        return True
