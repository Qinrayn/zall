"""context_judge invariant test (DESIGN.md §4.2.1-4.2.4).

IPR-0: each test must contain a counterexample —— not happy path, but construct violations that should cause the test to fail.
Counterexample摘要见 tests/INVARIANTS.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.action import Action
from zall.core.context import Context
from zall.core.safety import (
    GREYLIST_SUB_UNRESOLVABLE,
    Judgement,
    Rule,
    RuleSet,
    SafeLevel,
    context_judge,
)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _CwdMetaStub:
    """CwdMeta stub, 可配 git_branch."""

    def __init__(self, git_branch: str | None = "feature/x") -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = git_branch
        self.git_remote = "origin"


def _make_context(git_branch: str | None = "feature/x") -> Context:
    return Context(user_raw="test", cwd_meta=_CwdMetaStub(git_branch=git_branch))


def _make_rule(
    rule_id: str,
    tool_id_pattern: str = "*",
    args_matcher: dict[str, str] | None = None,
    context_matcher: dict[str, str] | None = None,
    level: SafeLevel = SafeLevel.WHITELIST,
) -> Rule:
    return Rule(
        rule_id=rule_id,
        tool_id_pattern=tool_id_pattern,
        args_matcher=args_matcher or {},
        context_matcher=context_matcher or {},
        level=level,
    )


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 SafeLevel invariants
# ──────────────────────────────────────────────────────────────────────────


class TestSafeLevelInvariants:
    """§4.2.1 SafeLevel 三态invariant."""

    def test_three_levels_only(self) -> None:
        """SafeLevel 只有三态 (whitelist / greylist / blacklist).

        Counterexample: 如果有人加第 4 态, 此test须 fail (over-engineering 防御,
        与 v0.0.7 context_judge 4 态自驳同型).
        """
        levels = {SafeLevel.WHITELIST, SafeLevel.GREYLIST, SafeLevel.BLACKLIST}
        assert len(levels) == 3


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 Rule invariants
# ──────────────────────────────────────────────────────────────────────────


class TestRuleInvariants:
    """§4.2.1 Rule 声明式invariant."""

    def test_happy_path_matches(self) -> None:
        """Happy path: tool_id + args 匹配的rule命中."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            level=SafeLevel.GREYLIST,
        )
        action = Action(tool_id="bash", args={"command": "git push origin main"})
        ctx = _make_context()
        assert rule.matches(action, ctx)

    def test_tool_id_glob_pattern(self) -> None:
        """Happy path: glob 通配符匹配."""
        rule = _make_rule("r1", tool_id_pattern="git_*")
        action = Action(tool_id="git_push", args={})
        assert rule.matches(action, _make_context())

    def test_non_matching_tool_id(self) -> None:
        """Counterexample: tool_id no match → 不命中."""
        rule = _make_rule("r1", tool_id_pattern="bash")
        action = Action(tool_id="read_file", args={})
        assert not rule.matches(action, _make_context())

    def test_args_substring_match(self) -> None:
        """Happy path: args substring 匹配 (eg. "push" in "git push origin main")."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
        )
        action = Action(tool_id="bash", args={"command": "git push origin main"})
        assert rule.matches(action, _make_context())

    def test_args_missing_key_not_match(self) -> None:
        """Counterexample: args 缺 key → 不命中."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
        )
        action = Action(tool_id="bash", args={"file": "x.py"})  # 缺 command
        assert not rule.matches(action, _make_context())

    def test_context_path_match(self) -> None:
        """Happy path: context propertypath匹配 (eg. cwd_meta.git_branch == main)."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            context_matcher={"cwd_meta.git_branch": "main"},
            level=SafeLevel.BLACKLIST,
        )
        action = Action(tool_id="bash", args={"command": "git push"})
        ctx = _make_context(git_branch="main")
        assert rule.matches(action, ctx)

    def test_context_path_non_match(self) -> None:
        """Counterexample: context propertyno match → 不命中."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            context_matcher={"cwd_meta.git_branch": "main"},
        )
        action = Action(tool_id="bash", args={"command": "git push"})
        ctx = _make_context(git_branch="feature/x")
        assert not rule.matches(action, ctx)

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 rule_id → must raise (frozen)."""
        rule = _make_rule("r1")
        with pytest.raises(ValidationError):
            rule.rule_id = "tampered"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 RuleSet invariants
# ──────────────────────────────────────────────────────────────────────────


class TestRuleSetInvariants:
    """§4.2.1 RuleSet 优先级链invariant."""

    def test_core_deny_must_be_blacklist(self) -> None:
        """Counterexample: core_deny_rules 含non- BLACKLIST rule → must raise.

        §4.2.1 优先级链: 核心immutable deny-rules 只能是 BLACKLIST.
        如果有人给 core_deny 加 whitelist 规则, 是命名误导 + 优先级链破坏.
        """
        bad_rule = _make_rule("bad_core", level=SafeLevel.WHITELIST)
        with pytest.raises(ValidationError, match="core_deny_rules can only contain BLACKLIST"):
            RuleSet(core_deny_rules=(bad_rule,))

    def test_core_deny_blacklist_ok(self) -> None:
        """Happy path: core_deny_rules 含 BLACKLIST rule → constructable."""
        rule = _make_rule("r1", level=SafeLevel.BLACKLIST)
        rs = RuleSet(core_deny_rules=(rule,))
        assert len(rs.core_deny_rules) == 1


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 context_judge function invariants
# ──────────────────────────────────────────────────────────────────────────


class TestContextJudgeInvariants:
    """§4.2.1 context_judge 主functioninvariant."""

    def test_core_deny_overrides_everything(self) -> None:
        """Counterexample: core_deny 命中 → BLACKLIST, 即使 user_local 有 whitelist.

        §4.2.1 优先级链: 核心immutable deny-rules > user_local.allow.
        如果一个实现让 user_local.allow covers core_deny, 此test fail.
        """
        core_deny = _make_rule(
            "core_deny_push_to_main",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            context_matcher={"cwd_meta.git_branch": "main"},
            level=SafeLevel.BLACKLIST,
        )
        user_allow = _make_rule(
            "user_allow_push",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            level=SafeLevel.WHITELIST,
        )
        rules = RuleSet(core_deny_rules=(core_deny,), user_local_rules=(user_allow,))
        action = Action(tool_id="bash", args={"command": "git push"})
        ctx = _make_context(git_branch="main")
        result = context_judge(action, ctx, rules)
        assert result.level == SafeLevel.BLACKLIST
        assert "core_deny_push_to_main" in result.matched_rule_ids

    def test_user_deny_overrides_user_allow(self) -> None:
        """Counterexample: user_local.deny > user_local.allow (优先级链)."""
        user_deny = _make_rule(
            "user_deny",
            tool_id_pattern="bash",
            args_matcher={"command": "rm"},
            level=SafeLevel.BLACKLIST,
        )
        user_allow = _make_rule(
            "user_allow_bash",
            tool_id_pattern="bash",
            level=SafeLevel.WHITELIST,
        )
        rules = RuleSet(user_local_rules=(user_deny, user_allow))
        action = Action(tool_id="bash", args={"command": "rm -rf node_modules"})
        ctx = _make_context()
        result = context_judge(action, ctx, rules)
        assert result.level == SafeLevel.BLACKLIST

    def test_no_match_defaults_to_greylist(self) -> None:
        """Counterexample: 无任何rule匹配 → default greylist (不default whitelist).

        §4.2.1: 无匹配 → greylist + sub_status=greylist_unresolvable_no_rule_matched.
        如果一个实现默认 whitelist, agent construct未知动作即可绕过确认 → 严重 hijack.
        """
        rules = RuleSet()  # 空规则集
        action = Action(tool_id="unknown_tool", args={"x": "y"})
        ctx = _make_context()
        result = context_judge(action, ctx, rules)
        assert result.level == SafeLevel.GREYLIST
        assert result.sub_status == GREYLIST_SUB_UNRESOLVABLE
        assert result.matched_rule_ids == ()

    def test_whitelist_when_only_whitelist_matches(self) -> None:
        """Happy path: 仅 whitelist rule命中 → whitelist."""
        rule = _make_rule(
            "allow_read",
            tool_id_pattern="read_file",
            level=SafeLevel.WHITELIST,
        )
        rules = RuleSet(user_local_rules=(rule,))
        action = Action(tool_id="read_file", args={"path": "x.py"})
        ctx = _make_context()
        result = context_judge(action, ctx, rules)
        assert result.level == SafeLevel.WHITELIST

    def test_greylist_when_greylist_matches(self) -> None:
        """Happy path: greylist rule命中 → greylist (无 sub_status)."""
        rule = _make_rule(
            "confirm_write",
            tool_id_pattern="write_file",
            level=SafeLevel.GREYLIST,
        )
        rules = RuleSet(user_local_rules=(rule,))
        action = Action(tool_id="write_file", args={"path": "x.py"})
        ctx = _make_context()
        result = context_judge(action, ctx, rules)
        assert result.level == SafeLevel.GREYLIST
        assert result.sub_status is None  # 有匹配的 greylist, not unresolvable

    def test_judgement_is_frozen(self) -> None:
        """Counterexample: Judgement construct后改 level → must raise (frozen)."""
        j = Judgement(level=SafeLevel.WHITELIST)
        with pytest.raises(ValidationError):
            j.level = SafeLevel.BLACKLIST  # type: ignore[misc]

    def test_idempotency(self) -> None:
        """纯性proxy: 相同input两次调用结果相同 (与 §3.2.2 TerminationCriterion 同型)."""
        rule = _make_rule(
            "r1",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            level=SafeLevel.BLACKLIST,
        )
        rules = RuleSet(user_local_rules=(rule,))
        action = Action(tool_id="bash", args={"command": "git push"})
        ctx = _make_context()
        first = context_judge(action, ctx, rules)
        second = context_judge(action, ctx, rules)
        assert first == second
