"""zall.tools.edit_file — Exact string replacement (ACI design).

ACI Design notes:
  - old_string must match uniquely, 否则给出所有匹配位置让模型自选加上下文
  - 替换为 new_string
  - 返回 diff 摘要 (几行替换 / 几行新增)

IPR constraints:
  IPR-0: invariant tests at tests/test_edit_file_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

from typing import Any

from zall._util.path import resolve_path

from zall.core.tool import ToolResult
from zall.tools._diff import unified_diff as _unified_diff


class EditFileTool:
    """String replacementtool (ACI design)。

    IPR-0 不变量:
        - old_string must match uniquely → 否则返回所有匹配位置 + 上下文
        - 替换用 new_string
        - 文件不存在 → 友好错误

    schema 设计:
        path:       必填, 文件路径
        old_string: 必填, 要替换的原始字符串 (必须唯一匹配)
        new_string: 必填, 替换后的字符串
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "edit_file"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Perform exact string replacement in a file. "
                    "The old_string must match exactly once in the file; "
                    "if it matches multiple times or not at all, "
                    "the edit fails and the matching locations are reported."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to edit (absolute or relative to cwd)",
                        },
                        "old_string": {
                            "type": "string",
                            "description": "The exact string to replace (must be unique in the file)",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "The replacement string",
                        },
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path_str = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")

        if not path_str:
            return ToolResult(
                success=False, output="[ERROR: path is required]", error="path required"
            )

        path = resolve_path(path_str)

        try:
            from zall._util import read_text_file
            content = read_text_file(path)
        except FileNotFoundError:
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
        except IsADirectoryError:
            return ToolResult(
                success=False,
                output=f"[ERROR: not a file: {path}]",
                error=f"not a file: {path}",
            )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot read {path}: {e}]",
                error=str(e),
            )

        if not old:
            return ToolResult(
                success=False,
                output="[ERROR: old_string cannot be empty]",
                error="old_string empty",
            )

        # find所有匹配位置
        count = content.count(old)
        if count == 0:
            return ToolResult(
                success=False,
                output=f"[ERROR: old_string not found in {path}]\n\n"
                f"Hint: the file has {len(content)} characters. "
                f"Make sure the old_string exactly matches the content, "
                f"including whitespace and indentation.",
                error="old_string not found",
            )

        if count > 1:
            # 给出所有匹配位置及context
            lines = content.split("\n")
            locations = []
            for i, line in enumerate(lines, 1):
                if old in line:
                    locations.append(f"  Line {i}: {line.strip()[:80]}")
            return ToolResult(
                success=False,
                output=f"[ERROR: old_string matched {count} times in {path}. "
                f"The match must be unique.\n\n"
                f"Matching locations:\n"
                + "\n".join(locations[:20])
                + ("\n  ..." if len(locations) > 20 else "")
                + "\n\nHint: include more surrounding lines in old_string to make it unique, "
                + "or use grep to find a more specific anchor.",
                error="multiple matches",
                artifacts={"match_count": count, "locations": locations},
            )

        # 唯一匹配 → replace
        new_content = content.replace(old, new, 1)
        try:
            # v2 fix: 使用唯一临时file名, 避免concurrentwrite竞态
            from zall._util.file import atomic_write
            atomic_write(path, new_content)
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot write {path}: {e}]",
                error=str(e),
            )

        old_lines = old.count("\n") + 1
        new_lines = new.count("\n") + 1

        # v0.0.12: 产出 bounded unified diff, 供 §9.2.3 tool调用展示 (diff 预览)
        diff = _unified_diff(old, new)

        return ToolResult(
            success=True,
            output=f"Replaced {old_lines} line(s) with {new_lines} line(s) in {path}",
            artifacts={
                "path": str(path),
                "old_lines": old_lines,
                "new_lines": new_lines,
                "old_string": old[:500],   # 截断: diff 展示用, 防 timeline 膨胀
                "new_string": new[:500],
                "diff": diff,              # v0.0.12: 完整 diff (仅 observer 用, 不进 timeline)
            },
        )


# _unified_diff now imported from zall.tools._diff (v0.1.1 refactor R2)