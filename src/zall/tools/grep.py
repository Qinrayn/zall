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

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from zall._util import NOISE_DIRS, is_binary
from zall._util.path import resolve_path
from zall.core.tool import ToolResult

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
    def capabilities(self):
        from zall.core.tool import ToolCapabilities, ToolScope
        return ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)


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
        # 敏感文件防护 (kimi grep_local 对标): rg 输出行按 "path:line:text" 前缀
        # 过滤 — grep API_KEY 不得把 .env 密钥行吐进上下文。
        from zall.safety.sensitive import is_sensitive_file
        kept: list[str] = []
        skipped: set[str] = set()
        for line in lines:
            # Windows 盘符 (C:) 含冒号: 取最后两个冒号之前为路径候选
            fpath = line
            parts = line.split(":")
            if len(parts) >= 3:
                for cut in range(len(parts) - 2, 0, -1):
                    cand = ":".join(parts[:cut])
                    if parts[cut].isdigit():
                        fpath = cand
                        break
            if fpath and is_sensitive_file(fpath):
                from pathlib import PurePath
                skipped.add(PurePath(fpath.replace("\\", "/")).name)
            else:
                kept.append(line)
        lines = kept
        sensitive_note = ""
        if skipped:
            uniq = sorted(skipped)
            sensitive_note = (
                f"\n[note: skipped {len(uniq)} sensitive file(s) "
                f"({', '.join(uniq[:5])}) to protect credentials]")
        if not lines:
            return ToolResult(
                success=True,
                output="(no matches)" + sensitive_note,
                artifacts={"match_count": 0, "engine": "rg"},
            )

        truncated = len(lines) > max_results
        lines = lines[:max_results]
        output = "\n".join(lines)
        if truncated:
            output += f"\n... [truncated at {max_results} matches]"
        output += sensitive_note
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

        _cancel = threading.Event()  # G14: 超时后通知搜索线程协作退出

        def _run_search() -> tuple[list[str], int, list[str]]:
            """Run the search, return (matches, files_searched, skipped_sensitive)."""
            local_matches: list[str] = []
            local_files_searched = 0
            # 敏感文件防护 (与 read_file/@ 引用同源): 命中行可能就是密钥本体
            # (grep API_KEY 直接把 .env 的密钥行吐进上下文), 整文件跳过。
            from zall.safety.sensitive import is_sensitive_file
            skipped_sensitive: list[str] = []

            def _search_file(fpath: Path) -> None:
                nonlocal local_files_searched
                if is_sensitive_file(str(fpath)):
                    skipped_sensitive.append(fpath.name)
                    return
                try:
                    # Use system preferred encoding (e.g., cp936 on Chinese Windows, not hardcoded UTF-8)
                    import locale as _locale
                    _sys_enc = _locale.getpreferredencoding(False) or "utf-8"
                    with open(fpath, "r", encoding=_sys_enc, errors="replace") as f:
                        local_files_searched += 1
                        for lineno, line in enumerate(f, 1):
                            if _cancel.is_set():
                                return
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
                    if _cancel.is_set():
                        break
                    # 提前剪枝: 不recursive进入noisedirectory (.git, node_modules, __pycache__ 等)
                    dirs[:] = [d for d in dirs if d not in NOISE_DIRS]
                    for fname in files:
                        if _cancel.is_set():
                            break
                        fpath = Path(root) / fname
                        if not is_binary(fpath):
                            _search_file(fpath)
                            if len(local_matches) >= max_results:
                                break
                    if len(local_matches) >= max_results:
                        break

            return local_matches, local_files_searched, skipped_sensitive

        # G14 fix: 不用 ThreadPoolExecutor — 其 with 退出时 shutdown(wait=True)
        # 会阻塞等待灾难性回溯的失控线程, timeout 保护形同虚设。
        # 改用 daemon 线程 + join(timeout) + 协作式停止标志:
        # 超时后主线程立即返回, 失控线程见 _cancel 尽快自行退出且不阻止进程退出。
        result_box: dict[str, Any] = {}

        def _worker() -> None:
            try:
                result_box["value"] = _run_search()
            except Exception as e:  # 防御: 异常不能静默吞掉
                result_box["error"] = e

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=_MAX_REGEX_TIMEOUT)
        if t.is_alive():
            _cancel.set()  # 通知失控线程尽快退出
            return ToolResult(
                success=False,
                output=f"[ERROR: regex search timed out after {_MAX_REGEX_TIMEOUT}s - pattern may cause catastrophic backtracking]",
                error="regex timeout",
            )
        if "error" in result_box:
            return ToolResult(
                success=False,
                output=f"[ERROR: search failed: {result_box['error']}]",
                error=str(result_box["error"]),
            )
        matches, files_searched, skipped_sensitive = result_box["value"]
        # 敏感文件警示 (kimi grep_local 对标): 告知模型跳过了哪些, 不露内容
        sensitive_note = ""
        if skipped_sensitive:
            uniq = sorted(set(skipped_sensitive))
            sensitive_note = (
                f"\n[note: skipped {len(uniq)} sensitive file(s) "
                f"({', '.join(uniq[:5])}) to protect credentials]")

        if not matches:
            return ToolResult(
                success=True,
                output="(no matches)" + sensitive_note,
                artifacts={"match_count": 0, "engine": "python", "files_searched": files_searched},
            )

        truncated = len(matches) >= max_results
        output = "\n".join(matches)
        if truncated:
            output += f"\n... [truncated at {max_results} matches]"
        output += sensitive_note
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
