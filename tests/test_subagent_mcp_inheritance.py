"""§9.2.11 sub agent inherit MCP tool — implementation tests (includes counterexamples, IPR-0).

covers:
  1. _build_subagent_tools: sub agent inherit parent 的 MCP tool (含 mcp__ 命名空间)
  2. 排除 spawn_subagent 自身 (防递归嵌套)
  3. invariant: sub agent tool集 ⊆ parent (只减不增, 不凭空获得新能力)
  4. Counterexample: sub agent 拿不到 spawn_subagent → 不能再次生成sub agent
"""

from __future__ import annotations

from typing import Any

from zall.core.tool import ToolRegistry
from zall.mcp.tool import MCPTool
from zall.tools.spawn_subagent import (
    SpawnSubagentTool,
    _build_subagent_tools,
)


class _FakeClient:
    def call_tool(self, name: str, arguments: dict) -> object:
        from zall.core.tool import ToolResult

        return ToolResult(success=True, output=f"ok:{name}")

    def close(self) -> None:
        pass


class _FakeTool:
    """minimal Tool implementation (仅满足 Tool Protocol: tool_id / schema / execute)."""

    def __init__(self, tid: str) -> None:
        self._tid = tid

    @property
    def tool_id(self) -> str:
        return self._tid

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self._tid, "parameters": {}},
        }

    def execute(self, args: dict[str, Any]) -> object:
        from zall.core.tool import ToolResult

        return ToolResult(success=True, output="")


def _build_parent_registry() -> ToolRegistry:
    """parent 含 native tool + spawn_subagent + 一个 MCP tool."""
    mcp = MCPTool(
        server_name="filesystem",
        spec={"name": "read", "description": "read", "inputSchema": {}},
        client=_FakeClient(),
    )
    return ToolRegistry(
        tools=(
            _FakeTool("bash"),
            _FakeTool("read_file"),
            _FakeTool("edit_file"),
            SpawnSubagentTool(),
            mcp,
        )
    )


class TestSubagentMCPInheritance:
    def test_subagent_inherits_mcp_tools(self) -> None:
        parent = _build_parent_registry()
        sub = _build_subagent_tools(parent)
        # sub agent must拿到 parent 的 MCP tool (§9.2.11 核心诉求)
        assert "mcp__filesystem__read" in sub.tool_ids
        mcp_tool = sub.get("mcp__filesystem__read")
        assert isinstance(mcp_tool, MCPTool)

    def test_spawn_subagent_excluded(self) -> None:
        parent = _build_parent_registry()
        sub = _build_subagent_tools(parent)
        # sub agent 不得再生成sub agent (防无限嵌套)
        assert "spawn_subagent" not in sub.tool_ids
        assert sub.get("spawn_subagent") is None

    def test_invariants_subset_only_shrinks(self) -> None:
        parent = _build_parent_registry()
        sub = _build_subagent_tools(parent)
        parent_ids = set(parent.tool_ids)
        sub_ids = set(sub.tool_ids)
        # invariant: sub ⊆ parent (只减不增)
        assert sub_ids <= parent_ids
        # spawn 被排除 → sub比 parent 恰好少一个
        assert len(sub_ids) == len(parent_ids) - 1
        # native tool全部inherit
        assert {"bash", "read_file", "edit_file"} <= sub_ids

    def test_returns_tool_registry(self) -> None:
        parent = _build_parent_registry()
        sub = _build_subagent_tools(parent)
        assert isinstance(sub, ToolRegistry)
