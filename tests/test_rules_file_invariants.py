"""rules_file invariant tests (IPR-0).

Covers:
  - _default_safe_rules: correct structure and levels
  - load_rules: core_deny always present, default safe rules injected
  - _parse_toml_like: inline TOML parsing edge cases
  - RuleSet structure: three categories (core_deny, user_local, greylist_deny)
"""

from __future__ import annotations

import pytest

from zall.core.safety import RuleSet, SafeLevel
from zall.safety.rules_file import (
    _default_safe_rules,
    _parse_toml_like,
    _parse_level,
    load_rules,
)


class TestDefaultSafeRules:
    """_default_safe_rules() invariants."""

    def test_returns_list_of_rules(self) -> None:
        rules = _default_safe_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0

    def test_read_tools_are_whitelist(self) -> None:
        rules = _default_safe_rules()
        read_tools = {"read_file", "grep", "glob", "list_dir"}
        for r in rules:
            if r.tool_id_pattern in read_tools:
                assert r.level == SafeLevel.WHITELIST, (
                    f"{r.tool_id_pattern} should be whitelist"
                )

    def test_write_tools_are_greylist(self) -> None:
        rules = _default_safe_rules()
        write_tools = {"write_file", "edit_file"}
        for r in rules:
            if r.tool_id_pattern in write_tools:
                assert r.level == SafeLevel.GREYLIST, (
                    f"{r.tool_id_pattern} should be greylist"
                )

    def test_bash_is_whitelist(self) -> None:
        rules = _default_safe_rules()
        bash_rules = [r for r in rules if r.tool_id_pattern == "bash"]
        assert len(bash_rules) >= 1
        assert bash_rules[0].level == SafeLevel.WHITELIST

    def test_spawn_subagent_is_greylist(self) -> None:
        rules = _default_safe_rules()
        spawn_rules = [r for r in rules if r.tool_id_pattern == "spawn_subagent"]
        assert len(spawn_rules) >= 1
        assert spawn_rules[0].level == SafeLevel.GREYLIST


class TestLoadRules:
    """load_rules() invariants."""

    def test_returns_ruleset(self) -> None:
        rs = load_rules()
        assert isinstance(rs, RuleSet)

    def test_core_deny_always_present(self) -> None:
        rs = load_rules()
        assert len(rs.core_deny_rules) > 0

    def test_core_deny_includes_rm_rf(self) -> None:
        rs = load_rules()
        ids = {r.rule_id for r in rs.core_deny_rules}
        assert "core_deny_rm_rf_root" in ids

    def test_core_deny_includes_push_main(self) -> None:
        rs = load_rules()
        ids = {r.rule_id for r in rs.core_deny_rules}
        assert "core_deny_push_force_main" in ids

    def test_default_safe_rules_injected(self) -> None:
        rs = load_rules()
        user_ids = {r.rule_id for r in rs.user_local_rules}
        assert "default_allow_read" in user_ids
        assert "default_confirm_write" in user_ids

    def test_native_todo_whitelisted(self) -> None:
        rs = load_rules()
        todo_rules = [
            r for r in rs.user_local_rules
            if r.tool_id_pattern == "todo_list"
        ]
        assert len(todo_rules) >= 1
        assert todo_rules[0].level == SafeLevel.WHITELIST

    def test_greylist_deny_has_entries(self) -> None:
        rs = load_rules()
        assert len(rs.greylist_deny_rules) > 0

    def test_greylist_deny_includes_del(self) -> None:
        rs = load_rules()
        ids = {r.rule_id for r in rs.greylist_deny_rules}
        assert "greylist_deny_del_file" in ids


class TestParseTomlLike:
    """_parse_toml_like() parser invariants."""

    def test_empty_text(self) -> None:
        data = _parse_toml_like("")
        assert data == {"rules": []}

    def test_only_comments(self) -> None:
        data = _parse_toml_like("# just a comment\n# another one")
        assert data == {"rules": []}

    def test_single_rule_minimal(self) -> None:
        text = """
[[rules]]
id = "test1"
tool_id = "bash"
level = "blacklist"
"""
        data = _parse_toml_like(text)
        assert len(data["rules"]) == 1
        r = data["rules"][0]
        assert r["id"] == "test1"
        assert r["tool_id"] == "bash"
        assert r["level"] == "blacklist"

    def test_rule_with_inline_args(self) -> None:
        text = """
[[rules]]
id = "deny_push"
tool_id = "bash"
args = { command = "push" }
level = "blacklist"
"""
        data = _parse_toml_like(text)
        r = data["rules"][0]
        assert r["args"] == {"command": "push"}

    def test_rule_with_inline_context(self) -> None:
        text = """
[[rules]]
id = "deny_main_push"
tool_id = "bash"
args = { command = "push" }
context = { "cwd_meta.git_branch" = "main" }
level = "blacklist"
"""
        data = _parse_toml_like(text)
        r = data["rules"][0]
        assert r["context"] == {"cwd_meta.git_branch": "main"}

    def test_rule_with_multiline_args(self) -> None:
        text = """
[[rules]]
id = "deny_complex"
tool_id = "bash"
args = {
    command = "rm -rf /"
    extra = "test"
}
level = "blacklist"
"""
        data = _parse_toml_like(text)
        r = data["rules"][0]
        assert r["args"]["command"] == "rm -rf /"
        assert r["args"]["extra"] == "test"

    def test_multiple_rules(self) -> None:
        text = """
[[rules]]
id = "rule1"
tool_id = "read_file"
level = "whitelist"

[[rules]]
id = "rule2"
tool_id = "bash"
level = "blacklist"

[[rules]]
id = "rule3"
tool_id = "write_file"
level = "greylist"
"""
        data = _parse_toml_like(text)
        assert len(data["rules"]) == 3

    def test_quoted_string_values(self) -> None:
        text = """
[[rules]]
id = "test"
tool_id = "bash"
args = { command = "echo 'hello world'" }
level = "greylist"
"""
        data = _parse_toml_like(text)
        r = data["rules"][0]
        assert r["args"]["command"] == "echo 'hello world'"

    def test_level_parsing(self) -> None:
        assert _parse_level("whitelist") == SafeLevel.WHITELIST
        assert _parse_level("greylist") == SafeLevel.GREYLIST
        assert _parse_level("blacklist") == SafeLevel.BLACKLIST
        assert _parse_level("unknown") == SafeLevel.GREYLIST  # default fallback
        assert _parse_level("WHITELIST") == SafeLevel.WHITELIST  # case insensitive


class TestRulesFileNoFileDependency:
    """Tests that don't depend on actual filesystem rules.toml."""

    def test_load_rules_no_project_path(self) -> None:
        """load_rules without project path returns valid RuleSet."""
        rs = load_rules()
        assert isinstance(rs, RuleSet)
        # Should always have at least core_deny + some safe rules
        total = (
            len(rs.core_deny_rules)
            + len(rs.user_local_rules)
            + len(rs.greylist_deny_rules)
        )
        assert total > 0

    def test_core_deny_all_blacklist(self) -> None:
        """All core_deny rules must be BLACKLIST level."""
        rs = load_rules()
        for r in rs.core_deny_rules:
            assert r.level == SafeLevel.BLACKLIST, (
                f"core_deny rule '{r.rule_id}' is not BLACKLIST"
            )

    def test_greylist_deny_all_greylist(self) -> None:
        """All greylist_deny rules must be GREYLIST level."""
        rs = load_rules()
        for r in rs.greylist_deny_rules:
            assert r.level == SafeLevel.GREYLIST, (
                f"greylist_deny rule '{r.rule_id}' is not GREYLIST"
            )