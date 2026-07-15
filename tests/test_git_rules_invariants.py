"""git_protect + rules_file invariant tests."""

from __future__ import annotations

import os
import tempfile

import pytest

from zall.tools.git_protect import GitProtect
from zall.safety.rules_file import load_rules, _parse_toml_like
from zall.core.safety import SafeLevel


class TestGitProtect:
    def test_is_git_repo(self) -> None:
        """zall project itself is git repo."""
        g = GitProtect()
        # may not be git repo (depends on current directory)
        # 至少does not crash溃
        result = g.is_git_repo()
        assert result in (True, False)

    def test_no_crash_on_non_git(self) -> None:
        """non- git directorydoes not crash溃."""
        g = GitProtect(cwd=tempfile.gettempdir())
        assert g.checkpoint() is None
        assert g.checkpoint_count == 0
        assert g.rollback() is False


class TestRulesFileParser:
    def test_parse_simple(self) -> None:
        text = """
[[rules]]
id = "allow_read"
tool_id = "read_file"
level = "whitelist"

[[rules]]
id = "deny_push"
tool_id = "bash"
level = "blacklist"
"""
        data = _parse_toml_like(text)
        assert len(data["rules"]) == 2
        assert data["rules"][0]["id"] == "allow_read"
        assert data["rules"][1]["level"] == "blacklist"

    def test_parse_with_args(self) -> None:
        text = """
[[rules]]
id = "deny_push"
tool_id = "bash"
args = {
    command = "push"
}
level = "blacklist"
"""
        data = _parse_toml_like(text)
        assert data["rules"][0]["args"] == {"command": "push"}

    def test_load_rules_no_file(self) -> None:
        """无rulefile时returnsdefault RuleSet (核心 deny + out-of-the-boxsecuritydefault).

        改进: 无 rules.toml 时injection 8 条默认 (read/grep/glob/list/bash 白名单,
        write/edit/spawn greylist), 不再每次都问 (用户实测痛点).
        v0.0.10: core_deny 从 3 条扩展到 9 条 (增 windows/unix 危险命令保护).
        v0.0.11: user_local_rules 7→8 (新增 spawn_subagent GREYLIST).
        v0.0.13: native_allow_todo 无条件追加 (todo_list 显示型tool whitelist, +1).
        """
        rs = load_rules(project_path="/nonexistent", user_path="/nonexistent")
        assert len(rs.core_deny_rules) >= 31  # 不硬编码精确值: 新规则增加时不应使testfail
        assert len(rs.user_local_rules) == 9  # 8 默认 + native_allow_todo (v0.0.13)

    def test_load_rules_default_location(self) -> None:
        """default位置load (does not crash溃, 无论file是否存在)."""
        rs = load_rules()
        assert len(rs.core_deny_rules) >= 31  # 不硬编码精确值: 新规则增加时不应使testfail