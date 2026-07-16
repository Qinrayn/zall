"""zall.tools.read_file — Read file tool (ACI design).

Corresponds to:
  §4.2   8 个核心工具: read_file
  SWE-agent ACI 哲学: 工具接口为模型认知设计, 不为人类方便

ACI Design notes:
  - 返回line-numbered内容 (模型对"第 47 行"敏感)
  - 支持 offset / limit 分页 (不让模型一次吞下整文件)
  - auto-truncates beyond MAX_LINES with notice (prevents context pollution)
  - auto-detects binary files (does not crash)
  - file not found / permission denied → friendly error (不是 RuntimeError)

IPR constraints:
  IPR-0: invariant tests at tests/test_read_file_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import os
import itertools
from typing import Any

from zall.core.tool import ToolResult
from zall._util import is_binary
from zall._util.file import detect_text_encoding as _detect_encoding
from zall._util.path import resolve_path

# 单次最大行数 (超过此数truncate, prevents context pollution)
MAX_LINES = 2000


class ReadFileTool:
    """Read file tool (ACI design)。

    ACI design decisions:
        - 返回line-numbered → 模型能说"第 47 行有 bug", 而不是"大概在中间部分"
        - 支持 offset/limit → 模型能先读文件头了解结构, 再读特定段
        - 默认 limit=500 (v0.2.2: 从 100 提升至 500, 避免7次连续读取700行文件)
        - 超过 2000 行截断 → 不让模型一次吞下整个大文件 (prevents context pollution)
        - 二进制检测 → 模型不会尝试"读一张图片"然后困惑

    schema 设计:
        path:   必填, 文件路径 (相对或绝对)
        offset: 可选, 起始行号 (从 1 开始, 默认 1)
        limit:  可选, 最大行数 (默认 500, 最大 2000)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "read_file"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file from the local filesystem. "
                    "Returns line-numbered content. "
                    "Supports offset/limit for pagination (default: first 500 lines). "
                    "Small files (<2000 lines) can be read in one call with limit=2000. "
                    "If the file has more than 2000 lines, only the first 2000 are shown "
                    "with a truncation notice and a hint to continue reading. "
                    "Use grep tool first to find relevant line numbers, then read specific regions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to read (absolute or relative to cwd)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting line number (1-based, default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Maximum lines to read (default: 500, max: {MAX_LINES})",
                            "default": 500,
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
                error="path is required",
            )

        # parse offset / limit
        offset = args.get("offset", 1)
        limit = args.get("limit", 500)
        if not isinstance(offset, int) or offset < 1:
            offset = 1
        if not isinstance(limit, int) or limit < 1:
            limit = 500
        limit = min(limit, MAX_LINES)  # 防模型传 limit=999999
        lines: list[str] = []

        # parsepath
        path = resolve_path(path_str)

        # checkfile是否存在
        if not path.exists():
            from zall._util.path import suggest_similar_path
            suggestion = suggest_similar_path(path)
            msg = f"[ERROR: file not found: {path}]"
            if suggestion:
                msg += f"\n  Did you mean: {suggestion}?"
            return ToolResult(
                success=False,
                output=msg,
                error=f"file not found: {path}",
            )

        # check是否为file (不是directory)
        if not path.is_file():
            return ToolResult(
                success=False,
                output=f"[ERROR: not a file: {path}]",
                error=f"not a file: {path}",
            )

        # checkauthority
        try:
            if not os.access(path, os.R_OK):
                return ToolResult(
                    success=False,
                    output=f"[ERROR: permission denied: {path}]",
                    error=f"permission denied: {path}",
                )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot access {path}: {e}]",
                error=str(e),
            )

        # 检测二进制file (使用共享tool)
        if is_binary(path):
            return ToolResult(
                success=False,
                output=f"[ERROR: binary file detected: {path}. "
                f"read_file can only read text files.]",
                error="binary file detected",
            )

        # 读file (单次遍历, 不 seek 回头)
        try:
            start = max(0, offset - 1)  # 转为 0-based
            end = start + limit
            file_size = os.path.getsize(path)
            total_lines = 0
            exact_total = True
            lines = []
            file_enc = _detect_encoding(path)
            with open(path, "r", encoding=file_enc) as f:
                lines = list(itertools.islice(f, start, end))
                actual_end = start + len(lines)
                end = max(actual_end, start)
                # 尝试读下一行: 如果有 → file更大, 用估算; 没有 → 精确总行数 = end
                next_line = f.readline()
                if next_line:
                    exact_total = False
                    # 从sample估算average行宽 → 推算总行数 (避免全量扫描)
                    # B5 fix: sample<3行时估算不可靠, 用保守上界
                    if len(lines) >= 3:
                        sample_bytes = sum(len(line.encode(file_enc)) for line in lines)
                        avg_bytes_per_line = sample_bytes / len(lines)
                        total_lines = int(file_size / max(avg_bytes_per_line, 1))
                    else:
                        # sample太小: 用file大小 / 256 字节作为保守估算
                        total_lines = max(actual_end, int(file_size / 256))
                else:
                    total_lines = actual_end  # 精确总行数
        except UnicodeDecodeError as e:
            return ToolResult(
                success=False,
                output=(
                    f"[ERROR: cannot decode {path} with detected encoding '{file_enc}': {e}. "
                    f"The file may be in a different encoding. Try using a different tool "
                    f"to read this file.]"
                ),
                error=str(e),
            )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot read {path}: {e}]",
                error=str(e),
            )

        line_num_width = len(str(end))  # 行号宽度对齐

        # buildoutput
        output_parts = []
        for i, line in enumerate(lines, start=start + 1):
            output_parts.append(f"{i:>{line_num_width}} {line.rstrip()}")

        content = "\n".join(output_parts)

        # 附加contextinformation
        count_display = total_lines if exact_total else f"~{total_lines}"
        header = f"Lines {start + 1}-{end} of {count_display}"
        if total_lines > MAX_LINES and limit >= MAX_LINES and offset <= 1:
            header += (
                f"\n[Note: file has ~{total_lines} lines, showing first {MAX_LINES}. "
                f"Use offset to read more or use grep to search for specific content.]"
            )
        # v2: prompt下一段: 帮助model分页read
        if total_lines > end:
            next_offset = end + 1
            header += f"\n[next: read_file(path, offset={next_offset}) to continue]"
        output = f"{header}\n{'-' * 40}\n{content}"

        # v2: 标记file已read (filestatecache, 供后续 is_file_unchanged check)
        try:
            from zall._util.file_state import get_file_state_cache
            get_file_state_cache().mark_file_read(path)
        except Exception:
            pass

        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "path": str(path),
                "total_lines": total_lines,
                "lines_shown": end - start,
                "offset": offset,
            },
        )