"""§9.2.10 Subagent Authority inheritance protocol — invarianttest (v0.0.14).

IPR-0 style: 每条invariant配一个Counterexample (无inherit则偷渡).

核心命题 (DESIGN.md §9.2.10):
  subagent 必须**inherit parent 的 Authority 约束**, 否则 parent blacklist
  (eg. rm -rf) 后可 spawn subagent 绕道越界 → 偷渡.
  同时 subagent Authority 必须**更严格** (R6 不可单方触发, §3.4.3).

合并/优先级依据: context_judge 优先级链 DENY > GREY > WHITE (§4.2.1).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zall.core.action import Action
from zall.core.context import Context
from zall.core.goal import TerminationState
from zall.core.safety import (
    Rule,
    RuleSet,
    SafeLevel,
    context_judge,
)
from zall.core.tool import ToolRegistry
from zall.tools.spawn_subagent import (
    SpawnSubagentTool,
    _build_subagent_rules,
)


# ──────────────────────────────────────────────────────────────────────────
# test夹具
# ──────────────────────────────────────────────────────────────────────────


class _Cwd:
    """minimal cwd_meta (CwdMeta Protocol 形状), 仅供 Context construct."""

    cwd_path = "/tmp"
    git_branch = None
    git_remote = None


def _ctx() -> Context:
    return Context(user_raw="test", cwd_meta=_Cwd())


def _parent_rule(rule_id: str, tool_id: str, level: SafeLevel) -> Rule:
    return Rule(rule_id=rule_id, tool_id_pattern=tool_id, level=level)


def _parent_rs(*user_local: Rule, core_deny: tuple[Rule, ...] = ()) -> RuleSet:
    return RuleSet(
        core_deny_rules=core_deny,
        user_local_rules=tuple(user_local),
        domain_rules=(),
    )


def _level(rules: RuleSet, tool_id: str) -> SafeLevel:
    return context_judge(Action(tool_id=tool_id, args={}), _ctx(), rules).level


# ──────────────────────────────────────────────────────────────────────────
# 1. 过时 API 回归: constructdoes not raise (旧 _subagent_rules 用 Rule(tool_ids=...) / RuleSet(rules=...)
#    会 ValidationError 或静默空 RuleSet)
# ──────────────────────────────────────────────────────────────────────────


def test_build_rules_none_parent_no_error():
    """parent 未injection时退化for收紧rule, 且does not raiseexception (旧 API 会 ValidationError)."""
    rs = _build_subagent_rules(None)
    # spawn 必 blacklist (防recursive)
    assert _level(rs, "spawn_subagent") == SafeLevel.BLACKLIST
    # 写tool至少 greylist
    assert _level(rs, "bash") == SafeLevel.GREYLIST
    assert _level(rs, "write_file") == SafeLevel.GREYLIST
    assert _level(rs, "edit_file") == SafeLevel.GREYLIST


# ──────────────────────────────────────────────────────────────────────────
# 2. inherit parent blacklist (防绕道 — Counterexample: 无inherit则 subagent 不inherit)
# ──────────────────────────────────────────────────────────────────────────


def test_inherits_parent_blacklist_bash():
    """parent 把 bash 列 blacklist → subagent bash 仍 BLACKLIST (绕道防护)."""
    parent = _parent_rs(_parent_rule("p_ban_bash", "bash", SafeLevel.BLACKLIST))
    sub = _build_subagent_rules(parent)
    assert _level(sub, "bash") == SafeLevel.BLACKLIST


def test_inherits_parent_core_deny():
    """parent core_deny blacklist 危险tool → subagent 必inherit."""
    dangerous = Rule(
        rule_id="core_ban_danger",
        tool_id_pattern="danger_tool",
        level=SafeLevel.BLACKLIST,
    )
    parent = _parent_rs(core_deny=(dangerous,))
    sub = _build_subagent_rules(parent)
    assert _level(sub, "danger_tool") == SafeLevel.BLACKLIST


def test_inherits_parent_whitelist_readonly():
    """parent whitelist只读tool → subagent inherit WHITELIST (不过度收紧)."""
    parent = _parent_rs(_parent_rule("p_allow_read", "read_file", SafeLevel.WHITELIST))
    sub = _build_subagent_rules(parent)
    assert _level(sub, "read_file") == SafeLevel.WHITELIST


# ──────────────────────────────────────────────────────────────────────────
# 3. 更严格: parent whitelist 写tool → subagent 至少 GREYLIST (Counterexample: 旧无inherit=inherit whitelist)
# ──────────────────────────────────────────────────────────────────────────


def test_downgrades_parent_whitelist_bash():
    """parent whitelist bash → subagent GREYLIST (更严格, GREY > WHITE)."""
    parent = _parent_rs(_parent_rule("p_allow_bash", "bash", SafeLevel.WHITELIST))
    sub = _build_subagent_rules(parent)
    assert _level(sub, "bash") == SafeLevel.GREYLIST


def test_downgrades_parent_whitelist_write():
    """parent whitelist write_file → subagent GREYLIST."""
    parent = _parent_rs(
        _parent_rule("p_allow_write", "write_file", SafeLevel.WHITELIST)
    )
    sub = _build_subagent_rules(parent)
    assert _level(sub, "write_file") == SafeLevel.GREYLIST


# ──────────────────────────────────────────────────────────────────────────
# 4. recursive spawn 必禁 (Counterexample: parent whitelist spawn 也拦得住)
# ──────────────────────────────────────────────────────────────────────────


def test_bans_recursive_spawn_even_if_parent_whitelists():
    """parent whitelist spawn_subagent → subagent 仍 BLACKLIST (防authority升级)."""
    parent = _parent_rs(
        _parent_rule("p_allow_spawn", "spawn_subagent", SafeLevel.WHITELIST)
    )
    sub = _build_subagent_rules(parent)
    assert _level(sub, "spawn_subagent") == SafeLevel.BLACKLIST


# ──────────────────────────────────────────────────────────────────────────
# 5. execute wiring: 真正把inherit后的rule传给sub AgentLoop
# ──────────────────────────────────────────────────────────────────────────


class _FakeEgress:
    run_id = "fake"
    final_state = TerminationState.UNDECIDABLE
    step_count = 1
    total_tool_calls = 0
    total_model_calls = 1
    error = None


class _FakeAgentLoop:
    captured_rules: RuleSet | None = None

    def __init__(self, model, tools, rules, goal, context, user_responder, judge, max_steps):
        _FakeAgentLoop.captured_rules = rules
        self.recorder = SimpleNamespace(events=[])

    def run(self, system_prompt: str = "") -> _FakeEgress:
        return _FakeEgress()


def _make_tool_with_context(parent: RuleSet) -> SpawnSubagentTool:
    tool = SpawnSubagentTool()
    tool.set_context(model_provider=object(), tools=ToolRegistry(tools=()), rules=parent)
    return tool


def test_execute_wires_inherited_rules(monkeypatch: pytest.MonkeyPatch):
    """execute must把 _build_subagent_rules(parent) 传给sub AgentLoop (Counterexample: 旧代码没inherit)."""
    parent = _parent_rs(
        _parent_rule("p_allow_bash", "bash", SafeLevel.WHITELIST),
        _parent_rule("p_ban_spawn", "spawn_subagent", SafeLevel.WHITELIST),
    )
    monkeypatch.setattr(
        "zall.tools.spawn_subagent.AgentLoop", _FakeAgentLoop
    )
    tool = _make_tool_with_context(parent)
    result = tool.execute({"prompt": "investigate the parser"})
    # 调用成功 + rule确实被inherit并收紧
    assert result.success is True
    assert _FakeAgentLoop.captured_rules == _build_subagent_rules(parent)
    # 传给sub loop 的rule里 bash 被收紧for GREYLIST (而non-inherit的 WHITELIST)
    assert _level(_FakeAgentLoop.captured_rules, "bash") == SafeLevel.GREYLIST
    assert _level(_FakeAgentLoop.captured_rules, "spawn_subagent") == SafeLevel.BLACKLIST


def test_execute_empty_prompt_error():
    """空 prompt 须returnsfail + 明确error (不吞错, 不 spawn)."""
    parent = _parent_rs()
    tool = _make_tool_with_context(parent)
    result = tool.execute({"prompt": "   "})
    assert result.success is False
    assert "non-empty" in (result.error or "")


def test_execute_context_not_initialized():
    """未 set_context (model=None) → 明确报错, 不静默 spawn."""
    tool = SpawnSubagentTool()  # 未injection context
    result = tool.execute({"prompt": "x"})
    assert result.success is False
    assert "context not initialized" in (result.error or "")
