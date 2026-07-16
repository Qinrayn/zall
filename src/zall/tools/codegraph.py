"""zall.tools.codegraph — CodeGraph 工具 (v0.4.0).

Agent 可用此工具搜索符号、获取文件大纲、跳转到定义。
需要 CodeGraph 索引支持。

IPR constraints:
  IPR-0: invariant tests at tests/test_codegraph_tool.py
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

from typing import Any

from zall.core.tool import ToolResult


class CodeGraphSearchTool:
    """CodeGraph 符号搜索工具 — 搜索代码中的符号定义。

    Agent 可用此工具快速定位类、函数、变量的定义位置。
    支持模糊匹配, 适合在大型代码库中导航。
    """

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        """注入 CodeGraph 实例 (CLI 层调用)。"""
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "codegraph_search"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "codegraph_search",
                "description": (
                    "Search for symbols (classes, functions, types) in the codebase. "
                    "Returns matching symbol names, kinds, file paths, and line numbers. "
                    "Use this to find where things are defined, understand code structure, "
                    "and discover related code. Supports fuzzy matching."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Symbol name or partial name to search for",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 20)",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if self._cg is None:
            return ToolResult(
                success=False, output="",
                error="CodeGraph not initialized. Run codegraph_index first.",
            )

        query = args.get("query", "")
        if not query.strip():
            return ToolResult(
                success=False, output="",
                error="query must be non-empty",
            )

        max_results = int(args.get("max_results", 20))

        try:
            symbols = self._cg.search(query, max_results=max_results)
            if not symbols:
                return ToolResult(
                    success=True,
                    output=f"[No symbols found matching '{query}']",
                )

            lines = [f"[Symbols matching '{query}' ({len(symbols)} found):]"]
            by_kind: dict[str, list[Any]] = {}
            for sym in symbols:
                kind = getattr(sym, "kind", "")
                kind_label = kind.value if hasattr(kind, "value") else str(kind)
                by_kind.setdefault(kind_label, []).append(sym)

            for kind in sorted(by_kind.keys()):
                syms = by_kind[kind]
                lines.append(f"\n  {kind}s ({len(syms)}):")
                for sym in syms:
                    loc = getattr(sym, "location", None)
                    if loc:
                        fn = getattr(loc, "file_path", "?")
                        ln = getattr(loc, "line", 0)
                        sig = getattr(sym, "signature", "")
                        sig_str = f" — {sig}" if sig else ""
                        lines.append(f"    {sym.name} @ {fn}:{ln}{sig_str}")
                    else:
                        lines.append(f"    {sym.name}")

            return ToolResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )


class CodeGraphOutlineTool:
    """CodeGraph 文件大纲工具 — 获取文件的结构概览。

    Agent 可用此工具快速了解一个文件包含哪些类、函数、方法。
    返回树形结构, 包括每个符号的行号和签名。
    """

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "codegraph_outline"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "codegraph_outline",
                "description": (
                    "Get the structural outline of a source file. "
                    "Returns all classes, functions, and their methods with line numbers. "
                    "Use this to understand a file's structure before reading it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the source file",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if self._cg is None:
            return ToolResult(
                success=False, output="",
                error="CodeGraph not initialized",
            )

        file_path = args.get("file_path", "")
        if not file_path.strip():
            return ToolResult(
                success=False, output="",
                error="file_path must be non-empty",
            )

        try:
            outline = self._cg.get_outline(file_path)
            if not outline:
                return ToolResult(
                    success=True,
                    output=f"[No symbols found in {file_path}]",
                )

            lines = [f"[Outline of {file_path}:]"]
            for entry in outline:
                name = entry.get("name", "?")
                kind = entry.get("kind", "?")
                line = entry.get("line", 0)
                sig = entry.get("signature", "")
                sig_str = f" — {sig}" if sig else ""
                lines.append(f"\n  {kind} {name} @ {line}{sig_str}")

                children = entry.get("children", [])
                for child in children:
                    c_name = child.get("name", "?")
                    c_kind = child.get("kind", "?")
                    c_line = child.get("line", 0)
                    c_sig = child.get("signature", "")
                    c_sig_str = f" — {c_sig}" if c_sig else ""
                    lines.append(f"    {c_kind} {c_name} @ {c_line}{c_sig_str}")

            return ToolResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )


class CodeGraphStatsTool:
    """CodeGraph 统计工具 — 查看索引状态。"""

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "codegraph_stats"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "codegraph_stats",
                "description": (
                    "Get statistics about the codebase index: "
                    "number of files indexed, symbols found, and indexing status. "
                    "Use this to check if the codebase is ready for code navigation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:  # noqa: ARG002
        if self._cg is None:
            return ToolResult(
                success=False, output="",
                error="CodeGraph not initialized",
            )

        try:
            stats = self._cg.get_stats()
            return ToolResult(
                success=True,
                output=(
                    f"[CodeGraph Index Status]\n"
                    f"  Status: {stats.get('status', 'unknown')}\n"
                    f"  Files indexed: {stats.get('file_count', 0)}\n"
                    f"  Symbols found: {stats.get('symbol_count', 0)}\n"
                    f"  Errors: {stats.get('error_count', 0)}\n"
                ),
                artifacts=stats,
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )


class CodeGraphIndexTool:
    """CodeGraph 索引工具 — 触发索引构建。"""

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "codegraph_index"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "codegraph_index",
                "description": (
                    "Build or refresh the codebase index. "
                    "This scans all source files and extracts symbols "
                    "(classes, functions, types) for fast searching. "
                    "Run this once at the start of a session if you need "
                    "code search capabilities."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:  # noqa: ARG002
        if self._cg is None:
            return ToolResult(
                success=False, output="",
                error="CodeGraph not initialized",
            )

        try:
            self._cg.build_index()
            stats = self._cg.get_stats()
            return ToolResult(
                success=True,
                output=(
                    f"[CodeGraph Index Built]\n"
                    f"  Files indexed: {stats.get('file_count', 0)}\n"
                    f"  Symbols found: {stats.get('symbol_count', 0)}\n"
                ),
                artifacts=stats,
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )