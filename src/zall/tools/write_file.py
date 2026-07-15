"""Write file tool.

Design:
  - read_file must be called before write (prevents blind overwrite)
  - create_only flag: when True, refuses to overwrite existing files
  - Auto-creates parent directories
  - Returns write summary (lines / bytes / path)

IPR constraints:
  IPR-0: invariant tests at tests/test_write_file_invariants.py
  IPR-1: corresponds to DESIGN.md file tool design
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zall._util.path import resolve_path

from zall.core.tool import Tool, ToolResult


class WriteFileTool:
    """Write file tool.

    Invariants:
        - read_file must be called before write (caller ensures, not enforced here)
        - create_only=True + file exists -> reject
        - Parent directory auto-created (agent won't fail on missing dirs)

    Schema:
        path:        required, file path
        content:     required, content to write
        create_only: optional, True = refuse to overwrite existing files
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "write_file"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "Write content to a file, creating the file if it does not exist. "
                    "Parent directories are created automatically. "
                    "If create_only=True, refuses to overwrite an existing file."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to write (absolute or relative to cwd)",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the file",
                        },
                        "create_only": {
                            "type": "boolean",
                            "description": "If True, refuse to overwrite an existing file (default: False)",
                            "default": False,
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path_str = args.get("path", "")
        content = args.get("content", "")
        create_only = args.get("create_only", False)

        if not path_str:
            return ToolResult(
                success=False,
                output="[ERROR: path argument is required]",
                error="path is required",
            )

        path = resolve_path(path_str)

        # create_only check
        if create_only and path.exists():
            return ToolResult(
                success=False,
                output=f"[ERROR: file already exists (create_only=True): {path}]",
                error=f"file already exists: {path}",
            )

        # Auto-create parent directory
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot create parent directory for {path}: {e}]",
                error=str(e),
            )

        # Write file
        old_exists = path.exists()
        try:
            from zall._util.file import atomic_write
            atomic_write(path, content)
        except PermissionError:
            return ToolResult(
                success=False,
                output=f"[ERROR: permission denied: {path}]",
                error=f"permission denied: {path}",
            )
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot write {path}: {e}]",
                error=str(e),
            )

        lines = content.count("\n")
        if content and not content.endswith("\n"):
            lines += 1
        byte_count = len(content.encode("utf-8"))

        return ToolResult(
            success=True,
            output=f"Wrote {lines} line(s), {byte_count} byte(s) to {path}",
            artifacts={
                "path": str(path),
                "lines": lines,
                "bytes": byte_count,
                "created": not old_exists,
            },
        )
