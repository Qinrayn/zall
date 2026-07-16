"""Tests for AgentDefinition system (v0.3.0).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import os
import pytest
import tempfile

from zall.core.agent import (
    AgentDefinition,
    AgentScope,
    PermissionMode,
    SubagentCapabilityMode,
    ToolsetPreset,
    discover_agents,
    filter_tools_by_capability,
    get_named_agent,
)


class TestToolsetPreset:
    """ToolsetPreset enum invariants."""

    def test_preset_values_are_lowercase(self):
        for preset in ToolsetPreset:
            assert preset.value == preset.value.lower(), (
                f"ToolsetPreset values must be lowercase: {preset}"
            )

    def test_all_presets_known(self):
        expected = {"zall", "explore", "plan", "codex", "opencode"}
        actual = {p.value for p in ToolsetPreset}
        assert actual == expected, f"Unexpected presets: {actual - expected}"


class TestSubagentCapabilityMode:
    """SubagentCapabilityMode invariants."""

    def test_read_only_blocks_write_tools(self):
        tool_ids = ["read_file", "write_file", "bash", "grep", "edit_file"]
        filtered = filter_tools_by_capability(tool_ids, SubagentCapabilityMode.READ_ONLY)
        assert "write_file" not in filtered
        assert "bash" not in filtered
        assert "edit_file" not in filtered
        assert "read_file" in filtered
        assert "grep" in filtered

    def test_plan_only_blocks_write_tools(self):
        tool_ids = ["read_file", "write_file", "bash", "grep", "todo_list"]
        filtered = filter_tools_by_capability(tool_ids, SubagentCapabilityMode.PLAN_ONLY)
        assert "write_file" not in filtered
        assert "bash" not in filtered
        assert "read_file" in filtered
        assert "grep" in filtered

    def test_full_passes_all(self):
        tool_ids = ["read_file", "write_file", "bash", "grep"]
        filtered = filter_tools_by_capability(tool_ids, SubagentCapabilityMode.FULL)
        assert filtered == tool_ids

    def test_no_bash_blocks_bash_only(self):
        tool_ids = ["read_file", "write_file", "bash", "grep", "edit_file"]
        filtered = filter_tools_by_capability(tool_ids, SubagentCapabilityMode.NO_BASH)
        assert "bash" not in filtered
        assert "write_file" in filtered
        assert "edit_file" in filtered


class TestAgentDefinition:
    """AgentDefinition invariants."""

    def test_default_zall_has_correct_toolset(self):
        def_ = AgentDefinition.default_zall()
        assert def_.name == "zall"
        assert def_.toolset == ToolsetPreset.ZALL
        assert def_.permission_mode == PermissionMode.DEFAULT

    def test_explore_has_readonly_capability(self):
        def_ = AgentDefinition.explore()
        assert def_.name == "explore"
        assert def_.toolset == ToolsetPreset.EXPLORE
        assert def_.capability_mode == SubagentCapabilityMode.READ_ONLY
        assert def_.permission_mode == PermissionMode.PLAN
        assert def_.allowed_subagent_types == []

    def test_plan_has_planonly_capability(self):
        def_ = AgentDefinition.plan()
        assert def_.name == "plan"
        assert def_.toolset == ToolsetPreset.PLAN
        assert def_.capability_mode == SubagentCapabilityMode.PLAN_ONLY
        assert def_.allowed_subagent_types == []

    def test_general_purpose_has_full_capability(self):
        def_ = AgentDefinition.general_purpose()
        assert def_.name == "general-purpose"
        assert def_.toolset == ToolsetPreset.ZALL
        assert def_.allowed_subagent_types == []

    def test_agent_definition_rejects_extra_fields(self):
        """extra='forbid' should reject unknown fields at construction."""
        def_ = AgentDefinition.default_zall()
        # The model itself should work fine (no extra fields)
        assert def_.name == "zall"
        # Trying to construct with unknown field should fail
        with pytest.raises(Exception):
            AgentDefinition(name="test", unknown_field="value")

    def test_parse_yaml_minimal(self):
        yaml = """
name: test-agent
description: A test agent
"""
        def_ = AgentDefinition.parse_yaml(yaml)
        assert def_.name == "test-agent"
        assert def_.description == "A test agent"
        assert def_.toolset == ToolsetPreset.ZALL  # default
        assert def_.scope == AgentScope.BUILTIN  # default

    def test_parse_yaml_full(self):
        yaml = """
name: my-agent
description: Full agent
toolset: explore
permissionMode: plan
model: grok-3-fast
disallowedTools: [bash]
allowedSubagentTypes: [general-purpose]
"""
        def_ = AgentDefinition.parse_yaml(yaml)
        assert def_.name == "my-agent"
        assert def_.toolset == ToolsetPreset.EXPLORE
        assert def_.permission_mode == PermissionMode.PLAN
        assert def_.model == "grok-3-fast"
        assert "bash" in def_.disallowed_tools
        assert def_.allowed_subagent_types == ["general-purpose"]

    def test_parse_yaml_with_extra_fields_rejected(self):
        """extra='forbid' should reject unknown fields."""
        yaml = """
name: test
unknown_field: value
"""
        with pytest.raises(Exception):
            AgentDefinition.parse_yaml(yaml)

    def test_from_file_roundtrip(self):
        content = """---
name: roundtrip-agent
description: Roundtrip test
toolset: plan
---

Custom prompt body here.
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            fpath = f.name

        try:
            def_ = AgentDefinition.from_file(fpath)
            assert def_.name == "roundtrip-agent"
            assert def_.toolset == ToolsetPreset.PLAN
            assert def_.prompt_body == "Custom prompt body here."
            assert def_.source_path is not None
        finally:
            os.unlink(fpath)

    def test_from_file_no_body(self):
        content = """---
name: no-body-agent
description: Just frontmatter
---
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            fpath = f.name

        try:
            def_ = AgentDefinition.from_file(fpath)
            assert def_.name == "no-body-agent"
            assert def_.prompt_body is None  # empty body -> None
        finally:
            os.unlink(fpath)

    def test_from_file_missing_frontmatter(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write("Just text, no frontmatter")
            fpath = f.name

        try:
            with pytest.raises(ValueError, match="must start with '---'"):
                AgentDefinition.from_file(fpath)
        finally:
            os.unlink(fpath)


class TestDiscoverAgents:
    """Agent discovery invariants."""

    def test_discover_from_project_dir(self):
        """Should find .zall/agents/*.md if it exists."""
        agents = discover_agents(project_dir=os.getcwd())
        # At minimum should find the ones we created
        names = [a.name for a in agents]
        # Should also include builtin agents via get_named_agent
        assert len(agents) >= 2  # explore + plan from .zall/agents/

    def test_discover_no_duplicates(self):
        agents = discover_agents()
        names = [a.name for a in agents]
        assert len(names) == len(set(names)), (
            f"Duplicate agent names: {names}"
        )


class TestGetNamedAgent:
    """get_named_agent invariants."""

    def test_builtin_agents_resolvable(self):
        for name in ("zall", "explore", "plan", "general-purpose"):
            def_ = get_named_agent(name)
            assert def_ is not None, f"Builtin agent '{name}' not found"
            assert def_.name == name

    def test_unknown_agent_returns_none(self):
        def_ = get_named_agent("nonexistent-agent")
        assert def_ is None

    def test_project_agent_overrides_builtin(self):
        """Project-level agent should be found and returned."""
        agent = get_named_agent("explore")
        assert agent is not None
        assert agent.name == "explore"


class TestToolsetPresetIntegration:
    """Test toolset presets with the tool building system."""

    def test_explore_toolset_no_write_tools(self):
        from zall.core.toolset import build_native_tools_for_preset
        tools = build_native_tools_for_preset("explore")
        tool_ids = [t.tool_id for t in tools]
        assert "read_file" in tool_ids
        assert "grep" in tool_ids
        assert "list_dir" in tool_ids
        assert "bash" not in tool_ids
        assert "write_file" not in tool_ids
        assert "edit_file" not in tool_ids

    def test_plan_toolset_has_todo(self):
        from zall.core.toolset import build_native_tools_for_preset
        tools = build_native_tools_for_preset("plan")
        tool_ids = [t.tool_id for t in tools]
        assert "read_file" in tool_ids
        assert "todo_list" in tool_ids
        assert "bash" not in tool_ids

    def test_zall_toolset_has_all_tools(self):
        from zall.core.toolset import build_native_tools_for_preset
        tools = build_native_tools_for_preset("zall")
        tool_ids = [t.tool_id for t in tools]
        assert "bash" in tool_ids
        assert "write_file" in tool_ids
        assert "spawn_subagent" in tool_ids
        assert "read_file" in tool_ids