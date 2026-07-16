"""zall.tools.glob — Find files by name pattern (ACI design).

ACI Design notes:
  - 用 pathlib.glob 封装 (无外部依赖)
  - 支持 ** 递归通配 (pathlib 行为)
  - 限制结果数 (防大输出污染 context)
  - 跳过 `.git/`, `node_modules/` 等噪声目录 (性能优化)
  - 返回路径列表 (相对或绝对, 模型易解析)

IPR constraints:
  IPR-0: invariant tests at tests/test_glob_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import itertools
from typing import Any

from zall.core.tool import ToolResult
from zall._util import is_noise
from zall._util.path import resolve_path

MAX_RESULTS = 500  # 最大结果数 (prevents context pollution)


class GlobTool:
    """Find files by name pattern tool (ACI design)。

    IPR-0 不变量:
        - 限制结果数 (超过 MAX_RESULTS 截断 + 提示)
        - 无匹配时 success=True, output="(no matches)" (不是 error)
        - 返回路径列表 (每行一个)

    schema 设计:
        pattern: 必填, glob 模式 (eg. "**/*.py", "*.txt")
        path:    可选, 搜索根目录 (默认当前目录)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "glob"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "glob",
                "description": (
                    "Find files matching a glob pattern. "
                    "Supports ** for recursive matching. "
                    "Returns a list of paths, one per line. "
                    "Results are capped at 500 to prevent context pollution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern (e.g. '**/*.py', '*.txt', 'src/**/*.md')",
                        },
                        "path": {
                            "type": "string",
                            "description": "Root directory to search in (default: current directory)",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(
                success=False,
                output="[ERROR: pattern argument is required]",
                error="pattern required",
            )

        path_str = args.get("path") or "."
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

        try:
            # Use islice to avoid materializing the full sorted list before capping
            results = list(itertools.islice(
                (p for p in sorted(path.glob(pattern)) if not is_noise(p)),
                MAX_RESULTS + 1
            ))
        except (OSError, ValueError) as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: glob failed: {e}]",
                error=str(e),
            )

        if not results:
            return ToolResult(
                success=True,
                output="(no matches)",
                artifacts={"match_count": 0},
            )

        truncated = len(results) > MAX_RESULTS
        results = results[:MAX_RESULTS]
        output = "\n".join(str(r) for r in results)
        if truncated:
            output += f"\n... [truncated at {MAX_RESULTS} results]"
        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "match_count": len(results),
                "truncated": truncated,
            },
        )
