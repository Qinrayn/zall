"""zall.core.tool_kind — ToolKind + ToolNamespace taxonomy enums.

Inspired by Grok Build's xai-grok-tools types/tool.rs taxonomy.
Provides semantic classification for all tools, replacing hardcoded
_WriteTools frozensets with first-class kind-based detection.

Usage:
    kind = ToolKind.READ
    kind.is_read_only()  # True
    kind.is_write()      # False

    ns = ToolNamespace.ZALL
    ns.value  # "zall"

IPR constraints:
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

from enum import Enum


class ToolKind(str, Enum):
    """Semantic tool kind — categorizes what a tool does at a high level.

    Each tool in the registry reports its kind. This enables:
      - Read/write classification (replaces _WRITE_TOOLS hardcoding)
      - Tool filtering by category (e.g., plan_mode blocks write kinds)
      - Cross-tool analytics and replay filtering
      - Plugin tools can declare their kind for correct classification

    Serializes to snake_case strings (e.g. "web_fetch", "list_dir").
    """

    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    EXECUTE = "execute"
    BATCH_EDIT = "batch_edit"
    SEARCH = "search"
    GLOB = "glob"
    LIST_DIR = "list_dir"
    WEB_FETCH = "web_fetch"
    WEB_SEARCH = "web_search"
    SUBAGENT = "subagent"
    PLAN = "plan"
    TODO = "todo"
    LSP = "lsp"
    CODE_GRAPH = "code_graph"
    READ_IMAGE = "read_image"
    SKILL = "skill"
    GIT_PROTECT = "git_protect"
    OTHER = "other"

    def is_read_only(self) -> bool:
        """Whether this kind only reads (no workspace or external mutation) by default."""
        return self in (
            ToolKind.READ,
            ToolKind.SEARCH,
            ToolKind.GLOB,
            ToolKind.LIST_DIR,
            ToolKind.LSP,
            ToolKind.CODE_GRAPH,
            ToolKind.READ_IMAGE,
            ToolKind.WEB_FETCH,
            ToolKind.WEB_SEARCH,
            ToolKind.PLAN,
            ToolKind.TODO,
            ToolKind.SKILL,
            ToolKind.GIT_PROTECT,
        )

    def is_write(self) -> bool:
        """Whether this kind modifies the filesystem or executes commands."""
        return self in (
            ToolKind.WRITE,
            ToolKind.EDIT,
            ToolKind.BATCH_EDIT,
            ToolKind.EXECUTE,
            ToolKind.SUBAGENT,
        )


class ToolNamespace(str, Enum):
    """Tool namespace — which toolset/harness a tool belongs to.

    Serializes to snake_case strings. Used for:
      - Distinguishing native tools from MCP/plugin tools
      - Multi-harness support (Codex, OpenCode compatibility)
      - Tool attribution in audit logs
    """

    ZALL = "zall"
    CODEX = "codex"
    OPENCODE = "opencode"
    MCP = "mcp"
    PLUGIN = "plugin"