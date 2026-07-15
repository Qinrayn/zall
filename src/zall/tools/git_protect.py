"""Git-native safety layer using git stash as automatic checkpoints.

Auto git stash create after every write_file / edit_file / bash write operation,
creating a temporary safety point. One-command rollback.

Design: git-as-source-of-truth, auto-stash after each change.
Does not commit to a branch — only uses stash as a lightweight safety net
(no git log pollution).

IPR constraints:
  IPR-0: invariant tests at tests/test_git_protect_invariants.py
  IPR-1: corresponds to DESIGN.md git-native hooks
  IPR-3: only stdlib + subprocess, no model SDK
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


class GitProtect:
    """Git-native safety layer.

    Auto git stash create after each write operation.
    Does not commit to a branch — only uses stash as a lightweight safety net.

    Usage:
        protector = GitProtect()
        protector.checkpoint()  # 在 write/edit 后调用
        protector.rollback()    # 回滚到上一个 checkpoint
        protector.list_checkpoints()  # 列出所有 checkpoint
    """

    __test__ = False

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._checkpoints: list[dict[str, Any]] = []
        self._checkpoint_file = self._cwd / ".zall" / "checkpoints.json"
        self._load_checkpoints()

    def _run_git(self, *args: str) -> str:
        """运行 git command, return stdout。"""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def is_git_repo(self) -> bool:
        """check是否在 git 仓库中。"""
        return self._run_git("rev-parse", "--git-dir") != ""

    def has_changes(self) -> bool:
        """check是否有未暂存的改动。"""
        try:
            result = subprocess.run(
                ["git", "diff", "--quiet"],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # git diff --quiet: returncode 0 = 无改动, 1 = 有改动
            return result.returncode != 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _load_checkpoints(self) -> None:
        """Load checkpoints from disk (if any)."""
        try:
            if self._checkpoint_file.exists():
                with open(self._checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._checkpoints = data
        except (OSError, json.JSONDecodeError):
            self._checkpoints = []

    def _save_checkpoints(self) -> None:
        """Persist checkpoints to disk."""
        try:
            self._checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(self._checkpoints, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def checkpoint(self, label: str = "") -> dict[str, Any] | None:
        """创建security点: git stash create + store, 记录 ref。

        store 将 ref 写入 stash reflog, 防止 git GC 回收。
        返回 checkpoint 元数据, 或在 git 不可用时返回 None。
        """
        if not self.is_git_repo():
            return None

        # 先 stash 当前改动
        stash_ref = self._run_git("stash", "create")
        if not stash_ref:
            # 没有改动, 不需要 stash
            return None

        # B1 fix: store 到 stash reflog (防止 ref 悬空被 GC)
        store_label = label or f"checkpoint_{len(self._checkpoints)}"
        self._run_git("stash", "store", "-m", store_label, stash_ref)

        cp = {
            "ref": stash_ref,
            "ts": int(time.time() * 1000),
            "label": store_label,
            "index": len(self._checkpoints),
        }
        self._checkpoints.append(cp)
        self._save_checkpoints()
        return cp

    def rollback(self, to_index: int | None = None) -> bool:
        """回滚到指定 checkpoint。

        to_index=None 时回滚到上一个 checkpoint。
        返回 True 表示回滚成功。

        v0.1.3: 回滚前先 git stash 保护当前未提交的改动, 防止数据丢失。
        """
        if not self._checkpoints:
            return False

        if to_index is None:
            # 回滾到上一个: 只有 1 个 checkpoint 时无法回滚 (无前一个)
            if len(self._checkpoints) < 2:
                return False
            to_index = len(self._checkpoints) - 2  # 回滾到上一个

        if to_index < 0 or to_index >= len(self._checkpoints):
            return False

        target = self._checkpoints[to_index]
        ref = target["ref"]

        # v0.1.3: 先 stash 当前脏工作区, 防止 git checkout -- . 丢失未commit改动
        self._run_git("stash", "push", "-m", "zall_rollback_safety", "--", ".")

        # 获取 stash 中改动的filelist (仅resume这些file, 不破坏用户的其他改动)
        stash_files = self._run_git("stash", "show", "--name-only", ref)
        if stash_files:
            only_files = [f for f in stash_files.split("\n") if f.strip()]
            # 只 checkout 这些file为 HEAD state (清除 stash 造成的未暂存改动)
            for f in only_files:
                self._run_git("checkout", "--", f)
        else:
            # 没有filelistfallback: 用 git checkout -- . 作为最后手段, 但先warning
            self._run_git("checkout", "--", ".")

        # 用 git stash apply resume, check returncode
        try:
            apply_result = subprocess.run(
                ["git", "stash", "apply", ref],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            success = apply_result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            success = False
        if success:
            # cleanup后续 checkpoint
            self._checkpoints = self._checkpoints[: to_index + 1]
            self._save_checkpoints()
            return True
        return False

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """列出所有 checkpoint。"""
        return list(self._checkpoints)

    @property
    def checkpoint_count(self) -> int:
        return len(self._checkpoints)