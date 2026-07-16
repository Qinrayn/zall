"""zall.tools.code_understanding — 代码理解工具 (v0.4.0).

将 CodeGraph 搜索 + 文件大纲 + 文件读取 组合为一步操作。
Agent 无需多次调用即可深入理解代码。

IPR constraints:
  IPR-0: invariant tests at tests/test_code_understanding.py
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

from typing import Any

from zall.core.tool import ToolResult


class CodeUnderstandingTool:
    """代码理解工具 — 组合搜索 + 大纲 + 读取。

    一步完成:
      1. 搜索相关符号
      2. 获取文件大纲
      3. 读取关键文件内容
      4. 返回综合分析

    Agent 用此工具替代多次 codegraph_search + read_file 调用。
    """

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "code_understanding"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "code_understanding",
                "description": (
                    "Deeply understand a piece of code in one call. "
                    "Given a symbol name or file path, this tool will: "
                    "1) Search for the symbol across the codebase, "
                    "2) Get the structural outline of relevant files, "
                    "3) Read the key file content. "
                    "Returns a comprehensive analysis. "
                    "Use this instead of multiple separate search/read calls."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": (
                                "Symbol name (e.g. 'MyClass') or file path "
                                "(e.g. 'src/main.py') to understand"
                            ),
                        },
                        "depth": {
                            "type": "string",
                            "enum": ["quick", "normal", "deep"],
                            "description": (
                                "Analysis depth: "
                                "'quick' — just search and outline, "
                                "'normal' — search + outline + read top file (default), "
                                "'deep' — search + outline + read all related files"
                            ),
                            "default": "normal",
                        },
                    },
                    "required": ["target"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if self._cg is None:
            return ToolResult(
                success=False, output="",
                error="CodeGraph not initialized. Run codegraph_index first.",
            )

        target = args.get("target", "").strip()
        if not target:
            return ToolResult(
                success=False, output="",
                error="target must be non-empty",
            )

        depth = args.get("depth", "normal")

        try:
            result = self._analyze(target, depth)
            return ToolResult(
                success=True,
                output=result["output"],
                artifacts=result.get("artifacts", {}),
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )

    def _analyze(self, target: str, depth: str) -> dict[str, Any]:
        """执行代码分析。"""
        artifacts: dict[str, Any] = {}
        parts: list[str] = []

        # 1. Try as file path first
        outline = self._cg.get_outline(target)
        if outline:
            return self._analyze_file(target, outline, depth, artifacts)

        # 2. Try as symbol name
        symbols = self._cg.search(target)
        if not symbols:
            # 3. Try as partial file path
            stats = self._cg.get_stats()
            parts.append(f"[Code Understanding: '{target}']\n")
            parts.append("  No symbols found matching this target.\n")
            if stats.get("status") == "indexed":
                parts.append(
                    f"  Project has {stats.get('file_count', 0)} files indexed.\n"
                    f"  Try a more specific symbol name or full file path."
                )
            else:
                parts.append(
                    "  Codebase not indexed. Run /codegraph index first."
                )
            return {"output": "".join(parts), "artifacts": artifacts}

        # Collect unique files
        files = {}
        for sym in symbols:
            loc = getattr(sym, "location", None)
            if loc:
                fp = getattr(loc, "file_path", "")
                if fp not in files:
                    files[fp] = []
                files[fp].append(sym)

        parts.append(f"[Code Understanding: '{target}']\n")
        parts.append(f"  Found {len(symbols)} symbols across {len(files)} files:\n")

        for fpath in sorted(files.keys()):
            syms = files[fpath]
            parts.append(f"\n  📄 {fpath}")
            parts.append(f"     {len(syms)} symbols matching '{target}'")

            # Show matching symbols
            for sym in syms:
                kind = getattr(sym, "kind", "")
                kind_label = kind.value if hasattr(kind, "value") else str(kind)
                loc = getattr(sym, "location", None)
                ln = getattr(loc, "line", 0) if loc else 0
                sig = getattr(sym, "signature", "")
                sig_str = f" — {sig[:100]}" if sig else ""
                parts.append(f"       {kind_label} {sym.name} @ {ln}{sig_str}")

            # Get file outline
            fo = self._cg.get_outline(fpath)
            if fo:
                other_syms = [e for e in fo if e["name"] != target]
                if other_syms:
                    parts.append("     Other symbols in this file:")
                    for e in other_syms[:5]:
                        parts.append(f"       {e['kind']} {e['name']} @ {e['line']}")

            # Read file content for normal/deep
            if depth in ("normal", "deep"):
                self._read_file_content(fpath, parts, depth == "deep")

        artifacts["file_count"] = len(files)
        artifacts["symbol_count"] = len(symbols)

        return {"output": "\n".join(parts), "artifacts": artifacts}

    def _analyze_file(
        self, file_path: str, outline: list[dict],
        depth: str, artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """分析单个文件。"""
        parts: list[str] = []
        parts.append(f"[Code Understanding: '{file_path}']\n")

        # File outline
        parts.append(f"\n  📄 Structure ({len(outline)} symbols):")
        for entry in outline:
            name = entry.get("name", "?")
            kind = entry.get("kind", "?")
            line = entry.get("line", 0)
            sig = entry.get("signature", "")
            sig_str = f" — {sig[:120]}" if sig else ""
            parts.append(f"    {kind} {name} @ {line}{sig_str}")

            children = entry.get("children", [])
            for child in children:
                c_name = child.get("name", "?")
                c_kind = child.get("kind", "?")
                c_line = child.get("line", 0)
                c_sig = child.get("signature", "")
                c_sig_str = f" — {c_sig[:80]}" if c_sig else ""
                parts.append(f"      {c_kind} {c_name} @ {c_line}{c_sig_str}")

        # Read file for normal/deep
        if depth in ("normal", "deep"):
            self._read_file_content(file_path, parts, depth == "deep")

        artifacts["file_count"] = 1
        artifacts["symbol_count"] = len(outline)

        return {"output": "\n".join(parts), "artifacts": artifacts}

    def _read_file_content(
        self, file_path: str, parts: list[str], full: bool,
    ) -> None:
        """读取文件内容。"""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                return

            if full:
                content = "".join(lines)
            else:
                # Show first 50 lines + last 10 lines
                head = "".join(lines[:50])
                tail = "".join(lines[-10:]) if len(lines) > 60 else ""
                if tail:
                    content = f"{head}\n... ({len(lines) - 60} lines omitted) ...\n{tail}"
                else:
                    content = "".join(lines)

            parts.append("\n  📝 Content:")
            for line in content.split("\n")[:60]:
                parts.append(f"  {line}")
            if len(content.split("\n")) > 60:
                parts.append("  ... (truncated)")
        except (OSError, IOError) as e:
            parts.append(f"\n  ⚠ Could not read file: {e}")