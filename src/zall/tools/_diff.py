"""Shared diff utilities for edit_file and batch_edit tools.

Extracted from edit_file.py and batch_edit.py (v0.1.1 refactor R2).
"""

from __future__ import annotations

import difflib


def unified_diff(old: str, new: str, context: int = 3) -> str:
    """Generate a bounded unified diff string.

    Same function previously duplicated in edit_file.py and batch_edit.py.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(old_lines, new_lines, n=context)
    )
    # Keep at most 50 lines of diff to avoid context pollution
    if len(diff_lines) > 50:
        diff_lines = diff_lines[:50] + ["... (diff truncated at 50 lines)\n"]
    return "".join(diff_lines)