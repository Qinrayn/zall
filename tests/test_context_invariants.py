"""Context invariant test (DESIGN.md §4.3).

IPR-0: each test must contain a counterexample.
Counterexample summary in tests/INVARIANTS.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.context import (
    Context,
    CwdMeta,
    DomainKnowledge,
    RunEgressSummary,
)
from zall.core.goal import GoalStatement, GoalType, new_segment_id


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _CwdMetaStub:
    """CwdMeta stub (满足 Protocol)."""

    cwd_path: str = "/home/user/project"
    git_branch: str | None = "feature/x"
    git_remote: str | None = "origin"


class _DomainKnowledgeStub:
    """DomainKnowledge stub (满足 Protocol)."""

    protected_branches: tuple[str, ...] = ("main", "master", "release/*")


class _RunEgressSummaryStub:
    """RunEgressSummary stub (满足 Protocol, minimalinterfacefor空)."""

    pass


def _make_valid_statement(goal_type: GoalType = GoalType.BUGFIX) -> GoalStatement:
    return GoalStatement(
        intent="修复登录页 crash",
        rewriting="修复登录页在空密码时 crash 的 bug",
        rewrite_confidence=0.9,
        goal_type=goal_type,
        translation_of=(new_segment_id(),),
        added_intent=(),
    )


def _make_valid_context() -> Context:
    return Context(
        user_raw="修一下登录页 crash",
        cwd_meta=_CwdMetaStub(),
    )


# ──────────────────────────────────────────────────────────────────────────
# §4.3 Context invariants
# ──────────────────────────────────────────────────────────────────────────


class TestContextInvariants:
    """§4.3 Context invariant."""

    def test_happy_path_constructs(self) -> None:
        """Happy path: valid Context constructable."""
        ctx = _make_valid_context()
        assert ctx.user_raw == "修一下登录页 crash"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后试图改 user_raw → must raise (frozen).

        Context 是某一时刻的快照, 改了 context_judge 结果不可复现.
        """
        ctx = _make_valid_context()
        with pytest.raises(ValidationError):
            ctx.user_raw = "tampered"  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """Context 不携带 tool 历史 (§4.3 核心斩断).

        history_level 仅含 GoalStatement + RunEgress 摘要,
        不含 tool_call_start / tool_call_end / tool_result 记录.
        """
        assert Context.__no_tool_history__() is True

    def test_history_level_has_no_tool_fields(self) -> None:
        """Counterexample: Context 的 history 字段not tool 调用记录.

        prior_goal_statements 是 GoalStatement (§3.2.1),
        prior_run_egress_summaries 是 RunEgressSummary (§3.4.5 摘要).
        两者都不携带 tool 调用记录 (§4.3 核心斩断).

        如果有人给 Context 加 tool_history 字段, 此test本身不会 fail,
        但 __no_tool_history__ 标记的删除会在 code review 暴露 (声明式审计).
        """
        ctx = Context(
            user_raw="x",
            cwd_meta=_CwdMetaStub(),
            prior_goal_statements=(_make_valid_statement(),),
            prior_run_egress_summaries=(_RunEgressSummaryStub(),),
        )
        # confirm history 字段type
        for stmt in ctx.prior_goal_statements:
            assert isinstance(stmt, GoalStatement)
        # tool_history 字段不存在 (核心斩断)
        assert not hasattr(ctx, "tool_history")
        assert not hasattr(ctx, "tool_calls")
        assert not hasattr(ctx, "tool_results")

    def test_user_explicit_artifacts_is_tuple(self) -> None:
        """user_explicit_artifacts 是 tuple (immutable).

        用户显式回灌的 artifact_id 列表, immutable (§4.3).
        """
        ctx = Context(
            user_raw="x",
            cwd_meta=_CwdMetaStub(),
            user_explicit_artifacts=("artifact_001", "artifact_002"),
        )
        assert isinstance(ctx.user_explicit_artifacts, tuple)
        assert not hasattr(ctx.user_explicit_artifacts, "append")

    def test_cwd_meta_protocol_checkable(self) -> None:
        """CwdMeta 是 runtime_checkable Protocol.

        Counterexample: 如果有人删了 cwd_path 属性, isinstance 须 fail.
        """
        assert isinstance(_CwdMetaStub(), CwdMeta)

        class _Bad:
            git_branch: str | None = "x"
            git_remote: str | None = "y"
            # 缺 cwd_path

        assert not isinstance(_Bad(), CwdMeta)

    def test_domain_knowledge_protocol_checkable(self) -> None:
        """DomainKnowledge 是 runtime_checkable Protocol."""
        assert isinstance(_DomainKnowledgeStub(), DomainKnowledge)

    def test_run_egress_summary_protocol_checkable(self) -> None:
        """RunEgressSummary 是 runtime_checkable Protocol (minimalinterfacefor空).

        任何对象都满足空 Protocol —— 这是有意的:
        RunEgress 未落码时, 此 Protocol 仅占位, 后续收紧.
        """
        assert isinstance(_RunEgressSummaryStub(), RunEgressSummary)
        assert isinstance(object(), RunEgressSummary)  # 空 Protocol 接受任何对象
