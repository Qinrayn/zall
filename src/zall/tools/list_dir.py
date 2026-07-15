"""zall.tools.list_dir — List directory tree (ACI design).

ACI Design notes:
  - 返回树状目录结构 (模型对"目录里有什么"敏感)
  - 限深 (防巨型目录污染 context)
  - 标记文件/目录 (用 / 后缀区分目录)
  - 跳过常见噪声目录 (.git, __pycache__, node_modules, .venv)

IPR constraints:
  IPR-0: invariant tests at tests/test_list_dir_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zall.core.tool import Tool, ToolResult
from zall._util import NOISE_DIRS
from zall._util.path import resolve_path

MAX_DEPTH = 3  # 最大深度 (prevents context pollution)
MAX_ENTRIES = 500  # 最大条目数

# skip这些directory (噪声, 不展示)
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
              ".egg-info"}


class ListDirTool:
    """List directory tree tool (ACI design)。

    IPR-0 不变量:
        - 限深 (默认 3, 最大 5)
        - 限制条目数 (超过 MAX_ENTRIES 截断)
        - 跳过噪声目录 (.git / __pycache__ / node_modules / ...)
        - 目录用 / 后缀标记 (模型易区分)

    schema 设计:
        path:   必填, 目录路径
        depth:  可选, 最大深度 (默认 3, 最大 5)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "list_dir"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": (
                    "List directory contents as a tree. "
                    "Directories are marked with a trailing /. "
                    "Skips noise directories (.git, __pycache__, node_modules, etc). "
                    "Depth is limited to prevent context pollution (default: 3, max: 5)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path to list (absolute or relative to cwd)",
                        },
                        "depth": {
                            "type": "integer",
                            "description": f"Maximum tree depth (default: {MAX_DEPTH}, max: 5)",
                            "default": MAX_DEPTH,
                        },
                    },
                    "required": ["path"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path_str = args.get("path", "")
        if not path_str:
            return ToolResult(
                success=False,
                output="[ERROR: path argument is required]",
                error="path required",
            )

        depth = args.get("depth", MAX_DEPTH)
        if not isinstance(depth, int) or depth < 1:
            depth = MAX_DEPTH
        depth = min(depth, 5)

        path = resolve_path(path_str)

        if not path.exists():
            return ToolResult(
                success=False,
                output=f"[ERROR: path not found: {path}]",
                error=f"path not found: {path}",
            )
        if not path.is_dir():
            return ToolResult(
                success=False,
                output=f"[ERROR: not a directory: {path}]",
                error=f"not a directory: {path}",
            )

        lines: list[str] = []
        entry_count = 0
        truncated = False

        def _walk(p: Path, prefix: str, cur_depth: int) -> None:
            nonlocal entry_count, truncated
            if truncated:
                return
            if cur_depth > depth:
                return
            try:
                # O4: compute is_dir once per entry to avoid redundant stat calls
                raw_entries = [(e.is_dir(), e.name.lower(), e) for e in p.iterdir()]
            except OSError:
                return

            raw_entries.sort(key=lambda x: (not x[0], x[1]))
            for is_dir, _, entry in raw_entries:
                if is_dir and (entry.name in _SKIP_DIRS or entry.name in NOISE_DIRS):
                    continue
                if entry_count >= MAX_ENTRIES:
                    truncated = True
                    return
                entry_count += 1
                if is_dir:
                    lines.append(f"{prefix}{entry.name}/")
                    _walk(entry, prefix + "  ", cur_depth + 1)
                else:
                    lines.append(f"{prefix}{entry.name}")

        _walk(path, "", 1)

        output = "\n".join(lines) if lines else "(empty directory)"
        if truncated:
            output += f"\n... [truncated at {MAX_ENTRIES} entries]"
        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "entry_count": entry_count,
                "truncated": truncated,
            },
        )
