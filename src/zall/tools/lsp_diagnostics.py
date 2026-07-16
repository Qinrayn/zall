"""zall.tools.lsp_diagnostics — LSP 诊断工具 (v0.4.0).

Agent 可用此工具获取当前项目的代码诊断信息 (错误/警告)。
需要 LSP 服务器支持 (如 pyright, typescript-language-server 等)。

IPR constraints:
  IPR-0: invariant tests at tests/test_lsp_tool.py
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

from typing import Any

from zall.core.tool import ToolResult


_DESCRIPTION = (
    "Check for code errors and warnings in the project. "
    "Returns diagnostics (errors, warnings, hints) from the language server. "
    "Use this BEFORE running builds or tests to catch syntax/type errors early. "
    "The diagnostics are grouped by file and severity."
)


class LspDiagnosticsTool:
    """LSP 诊断工具 — 获取代码错误/警告。

    在 agent 运行过程中自动检查代码质量。
    使用 LSP 服务器 (如 pyright) 提供实时诊断。
    """

    __test__ = False

    def __init__(self, lsp_manager: Any | None = None) -> None:
        self._lsp = lsp_manager

    def set_lsp_manager(self, lsp_manager: Any) -> None:
        """注入 LspManager 实例 (CLI 层调用)。"""
        self._lsp = lsp_manager

    @property
    def tool_id(self) -> str:
        return "lsp_diagnostics"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "lsp_diagnostics",
                "description": _DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Optional: check diagnostics for a specific file only. "
                                "If omitted, returns diagnostics for all tracked files."
                            ),
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["error", "warning", "all"],
                            "description": (
                                "Filter by severity level. "
                                "'error' = errors only, "
                                "'warning' = warnings + errors, "
                                "'all' = all diagnostics (default)."
                            ),
                            "default": "all",
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """执行 LSP 诊断检查。"""
        if self._lsp is None:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "LSP manager not initialized. "
                    "LSP diagnostics are unavailable."
                ),
            )

        file_path = args.get("file_path", "")
        severity_filter = args.get("severity", "all")

        try:
            if file_path:
                # Single file
                self._lsp.open_file(file_path)
                diags = self._lsp.get_diagnostics(file_path)
                result = self._format_diagnostics({file_path: diags}, severity_filter)
            else:
                # All files
                all_diags = self._lsp.all_diagnostics
                result = self._format_diagnostics(all_diags, severity_filter)

            return ToolResult(
                success=True,
                output=result,
                artifacts={
                    "diagnostic_count": len(result),
                    "file_count": len(self._lsp.all_diagnostics) if not file_path else 1,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"LSP diagnostics failed: {e}",
            )

    def _format_diagnostics(
        self,
        all_diags: dict[str, list[Any]],
        severity_filter: str,
    ) -> str:
        """格式化诊断输出。"""
        lines: list[str] = []
        total_errors = 0
        total_warnings = 0

        for file_path in sorted(all_diags.keys()):
            diags = all_diags[file_path]
            file_errors = [
                d for d in diags
                if getattr(d, "severity_label", "") == "error"
            ]
            file_warnings = [
                d for d in diags
                if getattr(d, "severity_label", "") == "warning"
            ]

            # Filter
            if severity_filter == "error":
                filtered = file_errors
            elif severity_filter == "warning":
                filtered = file_errors + file_warnings
            else:
                filtered = diags

            if not filtered:
                continue

            total_errors += len(file_errors)
            total_warnings += len(file_warnings)

            lines.append(f"\n  {file_path}")
            lines.append(f"    {len(file_errors)} errors, {len(file_warnings)} warnings")

            for d in filtered:
                label = getattr(d, "severity_label", "?")
                msg = getattr(d, "message", "?")
                line_num = getattr(d, "line", 0) + 1
                col_num = getattr(d, "column", 0) + 1
                lines.append(f"      {label}:{line_num}:{col_num}: {msg}")

        if not lines:
            return "[No diagnostics — project looks clean]"

        summary = f"\nTotal: {total_errors} errors, {total_warnings} warnings"
        return summary + "".join(lines)


class LspHoverTool:
    """LSP Hover 工具 — 获取符号的悬停信息。

    Agent 可用此工具查询函数/变量/类的类型签名和文档。
    """

    __test__ = False

    def __init__(self, lsp_manager: Any | None = None) -> None:
        self._lsp = lsp_manager

    def set_lsp_manager(self, lsp_manager: Any) -> None:
        self._lsp = lsp_manager

    @property
    def tool_id(self) -> str:
        return "lsp_hover"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "lsp_hover",
                "description": (
                    "Get type information and documentation for a symbol at a "
                    "specific file position. Use this to understand function "
                    "signatures, variable types, and API documentation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file",
                        },
                        "line": {
                            "type": "integer",
                            "description": "Line number (1-indexed)",
                        },
                        "column": {
                            "type": "integer",
                            "description": "Column number (1-indexed)",
                        },
                    },
                    "required": ["file_path", "line", "column"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if self._lsp is None:
            return ToolResult(
                success=False, output="",
                error="LSP manager not initialized",
            )

        file_path = args.get("file_path", "")
        line = int(args.get("line", 1)) - 1  # 0-indexed
        column = int(args.get("column", 1)) - 1

        try:
            info = self._lsp.hover(file_path, line, column)
            if info is None:
                return ToolResult(
                    success=True,
                    output=f"[No hover info at {file_path}:{line + 1}:{column + 1}]",
                )
            return ToolResult(
                success=True,
                output=f"```{info.language}\n{info.content}\n```",
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )


class LspGotoDefinitionTool:
    """LSP 跳转定义工具 — 查询符号定义位置。"""

    __test__ = False

    def __init__(self, lsp_manager: Any | None = None) -> None:
        self._lsp = lsp_manager

    def set_lsp_manager(self, lsp_manager: Any) -> None:
        self._lsp = lsp_manager

    @property
    def tool_id(self) -> str:
        return "lsp_goto_definition"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "lsp_goto_definition",
                "description": (
                    "Find where a symbol is defined. "
                    "Given a file and position, returns the definition location. "
                    "Use this to navigate to function/class/variable definitions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Current file"},
                        "line": {"type": "integer", "description": "Line (1-indexed)"},
                        "column": {"type": "integer", "description": "Column (1-indexed)"},
                    },
                    "required": ["file_path", "line", "column"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if self._lsp is None:
            return ToolResult(
                success=False, output="",
                error="LSP manager not initialized",
            )

        file_path = args.get("file_path", "")
        line = int(args.get("line", 1)) - 1
        column = int(args.get("column", 1)) - 1

        try:
            locs = self._lsp.goto_definition(file_path, line, column)
            if not locs:
                return ToolResult(
                    success=True,
                    output=f"[No definition found at {file_path}:{line + 1}:{column + 1}]",
                )
            lines = ["[Definitions found:]"]
            for loc in locs:
                lines.append(f"  {loc.file_path}:{loc.line + 1}:{loc.column + 1}")
            return ToolResult(success=True, output="\n".join(lines))
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )