"""zall.tools.grep — Search file contents (ACI design).

ACI Design notes:
  - 优先用 ripgrep (rg), 退化到纯 Python re (无外部依赖时仍可用)
  - 返回 file:line:match 格式 (与 grep -rn 一致, 模型易解析)
  - 限制匹配数 (防大输出污染 context)
  - 支持正则 / 固定字符串 / 大小写忽略
  - 默认递归当前目录

IPR constraints:
  IPR-0: invariant tests at tests/test_grep_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: only stdlib + subprocess, no model SDK
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from zall.core.tool import ToolResult
from zall._util import is_binary, NOISE_DIRS
from zall._util.path import resolve_path

MAX_MATCHES = 200  # 最大匹配数 (prevents context pollution)
_MAX_REGEX_TIMEOUT = 5  # seconds, prevents ReDoS in Python fallback


class GrepTool:
    """Search file contents tool (ACI design)。

    IPR-0 不变量:
        - 优先 ripgrep, 退化到 re (纯 Python)
        - 匹配超过 MAX_MATCHES 截断 + 提示
        - 返回 file:line:match 格式
        - 无匹配时 success=True, output="(no matches)" (不是 error)

    schema 设计:
        pattern:    必填, 搜索模式 (正则)
        path:       可选, 搜索路径 (默认当前目录)
        fixed:      可选, True 时按固定字符串搜索 (不解释正则)
        ignore_case: 可选, True 时忽略大小写
        max_results: 可选, 最大匹配数 (默认 200)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "grep"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search file contents using regex. Returns matches in 'file:line:match' format. "
                    "Uses ripgrep if available, falls back to pure Python. "
                    "Results are capped at 200 matches to prevent context pollution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "The regex pattern to search for",
                        },
                        "path": {
                            "type": "string",
                            "description": "File or directory to search (default: current directory)",
                        },
                        "fixed": {
                            "type": "boolean",
                            "description": "If True, treat pattern as fixed string (default: False)",
                            "default": False,
                        },
                        "ignore_case": {
                            "type": "boolean",
                            "description": "If True, ignore case (default: False)",
                            "default": False,
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum matches to return (default: {MAX_MATCHES})",
                            "default": MAX_MATCHES,
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
        fixed = args.get("fixed", False)
        ignore_case = args.get("ignore_case", False)
        max_results = args.get("max_results", MAX_MATCHES)
        if not isinstance(max_results, int) or max_results < 1:
            max_results = MAX_MATCHES
        max_results = min(max_results, MAX_MATCHES)

        path = resolve_path(path_str)

        if not path.exists():
            return ToolResult(
                success=False,
                output=f"[ERROR: path not found: {path}]",
                error=f"path not found: {path}",
            )

        # 优先 ripgrep
        rg = shutil.which("rg")
        if rg:
            return self._grep_rg(rg, pattern, path, fixed, ignore_case, max_results)
        return self._grep_python(pattern, path, fixed, ignore_case, max_results)

    def _grep_rg(
        self, rg: str, pattern: str, path: Path, fixed: bool,
        ignore_case: bool, max_results: int,
    ) -> ToolResult:
        """用 ripgrep search。"""
        cmd = [rg, "--line-number", "--no-heading", "--color=never"]
        if fixed:
            cmd.append("--fixed-strings")
        if ignore_case:
            cmd.append("--ignore-case")
        cmd.extend(["--max-count", str(max_results), pattern, str(path)])

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, encoding="utf-8",
                errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError):
            # rg 失败 → 退化到 Python
            return self._grep_python(pattern, path, fixed, ignore_case, max_results)

        stdout = proc.stdout or ""
        # rg exit code 0=有匹配, 1=无匹配, >1=error
        if proc.returncode > 1:
            return self._grep_python(pattern, path, fixed, ignore_case, max_results)

        lines = stdout.rstrip("\n").split("\n") if stdout.strip() else []
        if not lines:
            return ToolResult(
                success=True,
                output="(no matches)",
                artifacts={"match_count": 0, "engine": "rg"},
            )

        truncated = len(lines) > max_results
        lines = lines[:max_results]
        output = "\n".join(lines)
        if truncated:
            output += f"\n... [truncated at {max_results} matches]"
        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "match_count": len(lines),
                "engine": "rg",
                "truncated": truncated,
            },
        )

    def _grep_python(
        self, pattern: str, path: Path, fixed: bool,
        ignore_case: bool, max_results: int,
    ) -> ToolResult:
        """纯 Python re search (ripgrep 不可用时的退化)。"""
        flags = re.IGNORECASE if ignore_case else 0
        try:
            if fixed:
                regex = re.compile(re.escape(pattern), flags)
            else:
                regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: invalid regex pattern: {e}]",
                error=f"invalid regex: {e}",
            )

        def _run_search() -> tuple[list[str], int]:
            """Run the search, return (matches, files_searched)."""
            local_matches: list[str] = []
            local_files_searched = 0

            def _search_file(fpath: Path) -> None:
                nonlocal local_files_searched
                try:
                    # Use system preferred encoding (e.g., cp936 on Chinese Windows, not hardcoded UTF-8)
                    import locale as _locale
                    _sys_enc = _locale.getpreferredencoding(False) or "utf-8"
                    with open(fpath, "r", encoding=_sys_enc, errors="replace") as f:
                        local_files_searched += 1
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = fpath
                                local_matches.append(f"{rel}:{lineno}:{line.rstrip()}")
                                if len(local_matches) >= max_results:
                                    return
                except OSError:
                    pass

            if path.is_file():
                _search_file(path)
            else:
                # P2 fix: 用 os.walk + 提前剪枝替代 rglob("*")
                # rglob 会遍历所有条目再filter, os.walk 可在进入noisedirectory前剪枝 dirs
                for root, dirs, files in os.walk(path):
                    # 提前剪枝: 不recursive进入noisedirectory (.git, node_modules, __pycache__ 等)
                    dirs[:] = [d for d in dirs if d not in NOISE_DIRS]
                    for fname in files:
                        fpath = Path(root) / fname
                        if not is_binary(fpath):
                            _search_file(fpath)
                            if len(local_matches) >= max_results:
                                break
                    if len(local_matches) >= max_results:
                        break

            return local_matches, local_files_searched

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_search)
                matches, files_searched = future.result(timeout=_MAX_REGEX_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return ToolResult(
                success=False,
                output=f"[ERROR: regex search timed out after {_MAX_REGEX_TIMEOUT}s - pattern may cause catastrophic backtracking]",
                error="regex timeout",
            )

        if not matches:
            return ToolResult(
                success=True,
                output="(no matches)",
                artifacts={"match_count": 0, "engine": "python", "files_searched": files_searched},
            )

        truncated = len(matches) >= max_results
        output = "\n".join(matches)
        if truncated:
            output += f"\n... [truncated at {max_results} matches]"
        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "match_count": len(matches),
                "engine": "python",
                "files_searched": files_searched,
                "truncated": truncated,
            },
        )
