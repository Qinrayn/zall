"""zall.cli.commands.git — Git commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /git, /commit

IPR constraints:
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from zall.cli.commands._common import _CATEGORY_TOOLS, slash_command

# Extracted from _legacy.py lines 1254-1400
@slash_command("/git", description="git operations", category=_CATEGORY_TOOLS)
def cmd_git(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if not arg:
        arg = "status --short"
    parts = arg.split()
    subcmd = parts[0] if parts else "status"

    SAFE_SUBCOMMANDS = {
        "status", "diff", "log", "branch", "commit", "push", "pull",
        "fetch", "stash", "add", "checkout",
    }

    if subcmd not in SAFE_SUBCOMMANDS and not subcmd.startswith("-"):
        out.write(f"  \u26a0 '{subcmd}' is not in the safe subcommand list \u2014 use with care\n")

    try:
        check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if check.returncode != 0:
            out.write("  (not a git repository)\n")
            return "handled"

        if subcmd == "commit":
            msg = " ".join(parts[1:]) if len(parts) > 1 else ""
            if msg:
                result = subprocess.run(
                    ["git", "commit", "-m", msg],
                    capture_output=True, text=True, timeout=30,
                )
            else:
                out.write("  usage: /git commit <message>  (or use /git add first)\n")
                return "handled"
        elif subcmd == "log":
            n = parts[1] if len(parts) > 1 else "10"
            if not n.isdigit():
                out.write("  usage: /git log [N]  where N is a positive integer\n")
                return "handled"
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                capture_output=True, text=True, timeout=10,
            )
        elif subcmd in ("push", "pull", "fetch"):
            result = subprocess.run(
                ["git", subcmd, *parts[1:]],
                capture_output=True, text=True, timeout=120,
            )
        elif subcmd == "diff":
            result = subprocess.run(
                ["git", "diff", *parts[1:]],
                capture_output=True, text=True, timeout=10,
            )
        else:
            result = subprocess.run(
                ["git", *parts],
                capture_output=True, text=True, timeout=30,
            )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            if stdout:
                out.write(f"  {stdout}\n")
            else:
                out.write(f"  \u2713 {subcmd} (no output)\n")
        else:
            out.write(f"  \u2717 {subcmd}: {stderr or stdout}\n")

    except subprocess.TimeoutExpired:
        out.write(f"  \u2717 {subcmd}: timed out\n")
    except (FileNotFoundError, OSError) as e:
        out.write(f"  \u2717 git unavailable: {e}\n")
    return "handled"


@slash_command("/commit", description="smart git commit", category=_CATEGORY_TOOLS)
def cmd_commit(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    try:
        check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if check.returncode != 0:
            out.write("  (not a git repository)\n")
            return "handled"

        # check是否有变化
        has_changes = subprocess.run(
            ["git", "diff", "--quiet"], capture_output=True, timeout=5,
        )
        has_staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], capture_output=True, timeout=5,
        )
        if has_changes.returncode == 0 and has_staged.returncode == 0:
            out.write("  nothing to commit (working tree clean)\n")
            return "handled"

        # 显示未暂存和已暂存的change
        files_result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        modified_files = [f.strip() for f in files_result.stdout.split("\n") if f.strip()]

        staged_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        staged_files = [f.strip() for f in staged_result.stdout.split("\n") if f.strip()]

        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        branch = branch_result.stdout.strip()

        if arg:
            message = arg
        else:
            file_count = len(modified_files) + len(staged_files)
            extensions: dict[str, int] = {}
            for f in modified_files + staged_files:
                ext = os.path.splitext(f)[1] or "(no ext)"
                extensions[ext] = extensions.get(ext, 0) + 1
            ext_summary = ", ".join(
                f"{count} {ext}" for ext, count in
                sorted(extensions.items(), key=lambda x: -x[1])[:3]
            )
            message = f"update {file_count} file(s) ({ext_summary})"

        # 预览change
        out.write(f"  branch: {branch}\n")
        if staged_files:
            out.write(f"  staged: {len(staged_files)} file(s)\n")
        if modified_files:
            out.write(f"  unstaged: {len(modified_files)} file(s)\n")
        out.write(f"  msg:    {message}\n\n")

        # 显示 unstaged diff digest
        if modified_files:
            diff_result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=10,
            )
            if diff_result.stdout.strip():
                for line in diff_result.stdout.strip().split("\n"):
                    out.write(f"    {line}\n")
                out.write("\n")

        # confirm: 列出要 stage + commit 的file
        if modified_files:
            out.write("  The following unstaged files will be auto-staged:\n")
            for f in modified_files[:10]:
                out.write(f"    + {f}\n")
            if len(modified_files) > 10:
                out.write(f"    ... and {len(modified_files) - 10} more\n")
            out.write("  Proceed? [y/N]: ")
            out.flush()
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                out.write("  \u2717 cancelled\n")
                return "handled"
        else:
            out.write("  (only staged files, no auto-stage needed)\n")

        # execute stage + commit
        if modified_files:
            out.write("  staging modified files...\n")
            subprocess.run(["git", "add", "-A"], capture_output=True, timeout=10)

        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            out.write(f"  \u2713 committed: {message}\n")
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and "file" in line and "changed" in line:
                    out.write(f"    {line}\n")
        else:
            out.write(f"  \u2717 commit failed: {result.stderr.strip()}\n")
    except subprocess.TimeoutExpired:
        out.write("  \u2717 commit timed out\n")
    except (FileNotFoundError, OSError) as e:
        out.write(f"  \u2717 git unavailable: {e}\n")
    return "handled"

