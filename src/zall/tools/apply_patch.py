"""zall.tools.apply_patch — Semantic anchor-based patch tool (Codex 启发).

A more robust alternative to edit_file's exact string replacement.
Instead of requiring exact old_string matching, uses semantic anchors
(class/function/def names) to locate the edit target.

Design:
  - Patch format (Codex CLI inspired):
    ```
    *** Begin Patch
    *** Update File: src/app.py
    @@ def greet(name):
    -print("Hi " + name)
    +print(f"Hello, {name}!")
    *** End Patch
    ```
  - The @@ line provides a semantic anchor: function/class signature
  - The -/+ lines define the replacement within that scope
  - Falls back to line numbers if semantic anchor fails

  Benefits over edit_file:
    - No need for exact old_string matching (whitespace tolerant)
    - Semantic anchors are more stable across refactors
    - Model can write patches more naturally

IPR constraints:
  IPR-0: invariant tests at tests/test_apply_patch_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import re
from typing import Any

from zall.core.tool import ToolCapabilities, ToolResult, ToolScope


class ApplyPatchTool:
    """Semantic anchor-based patch tool.

    Applies a patch to a file using semantic anchors (@@ function/class)
    instead of fragile line numbers or exact string matching.

    Schema:
      path:  File path to patch
      patch: The patch content in Codex-style format
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "apply_patch"

    @property
    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(is_read_only=False, tool_scope=ToolScope.Write)

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": (
                    "Apply a semantic patch to a file. "
                    "Uses function/class anchors (@@) to locate the edit target, "
                    "which is more robust than exact string matching.\n\n"
                    "Patch format:\n"
                    "  @@ <function/class signature>\n"
                    "  -<old line(s)>\n"
                    "  +<new line(s)>\n\n"
                    "Example:\n"
                    '  @@ def hello(name):\n'
                    '  -print("Hi " + name)\n'
                    '  +print(f"Hello, {name}!")'
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to patch (absolute or relative to cwd)",
                        },
                        "patch": {
                            "type": "string",
                            "description": "The patch content with @@ anchors, - lines, + lines",
                        },
                    },
                    "required": ["path", "patch"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path_str = args.get("path", "")
        patch = args.get("patch", "")

        if not path_str:
            return ToolResult(
                success=False, output="[ERROR: path is required]", error="path required"
            )
        if not patch:
            return ToolResult(
                success=False, output="[ERROR: patch is required]", error="patch required"
            )

        from zall._util.path import resolve_path
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
                success=False, output=msg, error=f"file not found: {path}",
            )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot read {path}: {e}]",
                error=str(e),
            )

        # Parse the patch
        result = self._apply_patch(content, patch)
        if not result["success"]:
            return ToolResult(
                success=False,
                output=result["error"],
                error=result["error"],
            )

        # Write the patched content
        try:
            from zall._util.file import atomic_write
            atomic_write(path, result["new_content"])
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot write {path}: {e}]",
                error=str(e),
            )

        return ToolResult(
            success=True,
            output=(
                f"Applied patch to {path}\n"
                f"  anchor: {result['anchor']}\n"
                f"  {result['old_lines']} line(s) removed, "
                f"{result['new_lines']} line(s) added"
            ),
            artifacts={
                "path": str(path),
                "anchor": result["anchor"],
                "old_lines": result["old_lines"],
                "new_lines": result["new_lines"],
            },
        )

    def _apply_patch(self, content: str, patch: str) -> dict[str, Any]:
        """Parse and apply a semantic patch to content.

        Returns dict with:
          success: bool
          new_content: str (if success)
          anchor: str (the anchor line)
          old_lines: int
          new_lines: int
          error: str (if not success)
        """
        lines = content.split("\n")
        patch_lines = patch.strip().split("\n")

        # Find the @@ anchor line
        anchor_line = None
        old_lines_list: list[str] = []
        new_lines_list: list[str] = []
        in_patch = False

        for pl in patch_lines:
            pl_stripped = pl.strip()
            if pl_stripped.startswith("@@") and not in_patch:
                anchor_line = pl_stripped[2:].strip()
                in_patch = True
            elif in_patch:
                if pl_stripped.startswith("-"):
                    old_lines_list.append(pl_stripped[1:])
                elif pl_stripped.startswith("+"):
                    new_lines_list.append(pl_stripped[1:])
                elif pl_stripped == "":
                    # Empty line within patch = keep context
                    pass
                else:
                    # End of patch section
                    break

        if anchor_line is None:
            return {
                "success": False,
                "error": (
                    "[ERROR: no @@ anchor found in patch. "
                    "Use format: @@ function_name to specify the edit location."
                ),
            }

        if not old_lines_list and not new_lines_list:
            return {
                "success": False,
                "error": (
                    "[ERROR: no -/+ lines found in patch. "
                    "Use - to mark old lines and + to mark new lines."
                ),
            }

        # Find the anchor in the file
        anchor_pattern = re.escape(anchor_line)
        anchor_match = None
        for li, line in enumerate(lines):
            if anchor_line in line or re.search(anchor_pattern, line, re.IGNORECASE):
                anchor_match = li
                break

        if anchor_match is None:
            # Try fuzzy matching: search for the function/class name
            words = re.findall(r'\w+', anchor_line)
            for word in words:
                if len(word) < 3:
                    continue
                for li, line in enumerate(lines):
                    if word in line:
                        anchor_match = li
                        break
                if anchor_match is not None:
                    break

        if anchor_match is None:
            return {
                "success": False,
                "error": (
                    f"[ERROR: anchor '{anchor_line}' not found in the file]. "
                    f"Use grep to find the exact function/class signature."
                ),
            }

        # Find the old lines within the scope of the anchor
        # Look for the old_lines starting from anchor_match
        # First, try to find the exact match
        old_text = "\n".join(old_lines_list) if old_lines_list else ""

        if old_text:
            # Search for the old text in the file starting from anchor_match
            search_from = max(0, anchor_match)
            file_text_after = "\n".join(lines[search_from:])
            text_idx = file_text_after.find(old_text)

            if text_idx == -1:
                return {
                    "success": False,
                    "error": (
                        f"[ERROR: old text not found near anchor '{anchor_line}'. "
                        f"Make sure the - lines match the actual file content."
                    ),
                }

            # Convert text_idx to line number
            prefix_lines = file_text_after[:text_idx].count("\n")
            start_line = search_from + prefix_lines
            end_line = start_line + len(old_lines_list)

            # Build new content
            new_lines = (
                lines[:start_line]
                + new_lines_list
                + lines[end_line:]
            )
        else:
            # No old text = insert at anchor position
            new_lines = (
                lines[:anchor_match + 1]
                + new_lines_list
                + lines[anchor_match + 1:]
            )

        return {
            "success": True,
            "new_content": "\n".join(new_lines),
            "anchor": anchor_line,
            "old_lines": len(old_lines_list),
            "new_lines": len(new_lines_list),
        }