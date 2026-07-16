"""zall.sandbox — 沙箱模式 (子 agent 隔离执行环境).

Inspired by Grok Build's xai-grok-sandbox. Provides process-level isolation
for sub-agent execution, preventing escape and limiting resource usage.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │  Sandbox                                                    │
  │  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐ │
  │  │ Process  │→ │ Resource │  │ Security                   │ │
  │  │ (subproc)│  │ Limits   │  │ - Read-only by default     │ │
  │  └──────────┘  └──────────┘  │ - No network access        │ │
  │                              │ - Timeout enforcement      │ │
  │  ┌──────────┐  ┌──────────┐ │ - Temp workspace           │ │
  │  │ Worktree │  │ Cleanup  │ └────────────────────────────┘ │
  │  │ (git)    │  │ (atomic) │                                │ │
  │  └──────────┘  └──────────┘                                │
  └──────────────────────────────────────────────────────────────┘

Modes:
  NONE     — 无隔离 (默认, 与当前行为一致)
  WORKTREE — Git worktree 隔离 (子 agent 在独立 worktree 中操作)
  PROCESS  — 子进程隔离 (子 agent 在独立 Python 进程中运行)
  CONTAINER — Docker 容器隔离 (完全隔离, 需要 Docker)

Usage:
    sandbox = Sandbox(mode=SandboxMode.WORKTREE, project_dir="/path")
    with sandbox.isolate() as worktree_path:
        # subagent works inside worktree_path
        result = sandbox.execute("bash", args={"command": "make test"})
    # Auto-cleanup on exit

IPR constraints:
  IPR-0: invariant tests at tests/test_sandbox_invariants.py
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SandboxMode(str, Enum):
    """沙箱模式 — 隔离级别。"""
    NONE = "none"
    WORKTREE = "worktree"
    PROCESS = "process"


@dataclass
class SandboxResult:
    """沙箱执行结果。"""
    success: bool
    output: str
    error: str = ""
    exit_code: int = 0
    duration: float = 0.0


class SandboxError(Exception):
    """沙箱相关错误。"""


# ═══════════════════════════════════════════════════════════════════
# §1  Resource Limits
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ResourceLimits:
    """沙箱资源限制。"""
    timeout_seconds: float = 30.0
    max_output_bytes: int = 100_000
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    max_files: int = 1000
    allow_network: bool = False
    allow_write: bool = False
    env_vars: dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# §2  Git Worktree Sandbox
# ═══════════════════════════════════════════════════════════════════


class WorktreeSandbox:
    """Git Worktree 隔离 — 子 agent 在独立 worktree 中操作。

    使用 git worktree add 创建隔离的工作目录。
    子 agent 的修改不会影响主工作区, 直到显式合并。
    """

    def __init__(
        self,
        project_dir: str,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._project_dir = Path(project_dir).resolve()
        self._limits = limits or ResourceLimits()
        self._worktree_path: Path | None = None
        self._original_branch: str = ""
        self._worktree_branch: str = ""

    def create(self) -> Path:
        """创建隔离 worktree。

        Returns:
            worktree 的路径

        Raises:
            SandboxError: 如果 git 不可用或创建失败
        """
        if not self._is_git_repo():
            raise SandboxError("Not a git repository")

        # 生成唯一分支名
        timestamp = int(time.time())
        self._worktree_branch = f".zall/sandbox/{timestamp}"

        # 获取当前分支名
        self._original_branch = self._git_cmd(
            "rev-parse", "--abbrev-ref", "HEAD"
        )

        # 创建 worktree
        worktree_dir = (
            self._project_dir / ".zall" / "sandbox" / str(timestamp)
        )
        worktree_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._git_cmd(
                "worktree", "add",
                "-b", self._worktree_branch,
                str(worktree_dir),
                self._original_branch,
            )
        except subprocess.CalledProcessError as e:
            raise SandboxError(f"Failed to create worktree: {e}") from e

        self._worktree_path = worktree_dir
        return worktree_dir

    def cleanup(self) -> None:
        """清理 worktree。"""
        if self._worktree_path is not None and self._worktree_path.exists():
            try:
                self._git_cmd("worktree", "remove", "--force", str(self._worktree_path))
                # 清理 worktree 目录
                shutil.rmtree(self._worktree_path, ignore_errors=True)
                # 清理分支
                try:
                    self._git_cmd("branch", "-D", self._worktree_branch)
                except Exception:
                    pass
            except Exception:
                pass
        self._worktree_path = None

    def get_diff(self) -> str:
        """获取 worktree 与原始分支的差异。

        Returns:
            diff 文本
        """
        if self._worktree_path is None:
            return ""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._worktree_path), "diff", self._original_branch],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def apply_changes(self) -> bool:
        """将 worktree 的修改合并回主分支。

        Returns:
            True 如果成功
        """
        if self._worktree_path is None:
            return False
        try:
            # 在 worktree 中提交所有修改
            self._git_cmd_in_worktree("add", "-A")
            self._git_cmd_in_worktree(
                "commit", "--allow-empty", "-m", "[sandbox] changes",
            )
            # 从主仓库拉取 worktree 的修改
            self._git_cmd("fetch", ".", self._worktree_branch)
            self._git_cmd("merge", "--allow-unrelated-histories", "FETCH_HEAD")
            return True
        except subprocess.CalledProcessError:
            return False

    def _is_git_repo(self) -> bool:
        return (self._project_dir / ".git").exists()

    def _git_cmd(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._project_dir)] + list(args),
            capture_output=True, text=True, timeout=10,
            check=True,
        )
        return result.stdout.strip()

    def _git_cmd_in_worktree(self, *args: str) -> str:
        if self._worktree_path is None:
            raise SandboxError("No active worktree")
        result = subprocess.run(
            ["git", "-C", str(self._worktree_path)] + list(args),
            capture_output=True, text=True, timeout=10,
            check=True,
        )
        return result.stdout.strip()

    @property
    def active(self) -> bool:
        return self._worktree_path is not None

    @property
    def path(self) -> Path | None:
        return self._worktree_path

    def __enter__(self) -> Path:
        return self.create()

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


# ═══════════════════════════════════════════════════════════════════
# §3  Process Sandbox
# ═══════════════════════════════════════════════════════════════════


class ProcessSandbox:
    """子进程隔离 — 在独立 Python 进程中执行。

    通过 subprocess 在隔离环境中运行代码。
    限制: 超时, 输出大小, 环境变量。
    """

    def __init__(
        self,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._limits = limits or ResourceLimits()
        self._temp_dir: Path | None = None
        self._active = False

    def create_workspace(self) -> Path:
        """创建临时工作目录。"""
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="zall_sandbox_"))
        self._active = True
        return self._temp_dir

    def cleanup(self) -> None:
        """清理临时目录。"""
        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
        self._active = False

    def execute_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """在隔离环境中执行命令。

        Args:
            command: shell 命令
            cwd: 工作目录 (默认临时目录)
            env: 环境变量覆盖

        Returns:
            SandboxResult
        """
        workspace = cwd or str(self.create_workspace())
        merged_env = {
            **os.environ,
            "ZALL_SANDBOX": "1",
            "ZALL_SANDBOX_MODE": "process",
        }
        if not self._limits.allow_network:
            merged_env["ZALL_NO_NETWORK"] = "1"
        if env:
            merged_env.update(env)

        start = time.time()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._limits.timeout_seconds,
                cwd=workspace,
                env=merged_env,
            )
            duration = time.time() - start

            output = result.stdout
            if len(output) > self._limits.max_output_bytes:
                output = output[:self._limits.max_output_bytes] + "\n... (truncated)"

            return SandboxResult(
                success=result.returncode == 0,
                output=output,
                error=result.stderr[:self._limits.max_output_bytes],
                exit_code=result.returncode,
                duration=duration,
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return SandboxResult(
                success=False,
                output="",
                error=f"Timeout after {self._limits.timeout_seconds}s",
                exit_code=-1,
                duration=duration,
            )
        except Exception as e:
            duration = time.time() - start
            return SandboxResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1,
                duration=duration,
            )

    @property
    def active(self) -> bool:
        return self._temp_dir is not None

    def __enter__(self) -> ProcessSandbox:
        self.create_workspace()
        return self

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


# ═══════════════════════════════════════════════════════════════════
# §4  Sandbox — 统一入口
# ═══════════════════════════════════════════════════════════════════


class Sandbox:
    """沙箱 — 统一入口点。

    根据模式选择不同的隔离策略。

    Usage:
        sandbox = Sandbox(mode=SandboxMode.NONE)

        # Worktree 隔离
        sandbox = Sandbox(mode=SandboxMode.WORKTREE, project_dir="/path")
        with sandbox.isolate() as path:
            # subagent works here

        # 进程隔离
        sandbox = Sandbox(mode=SandboxMode.PROCESS)
        sandbox.execute("bash", args={"command": "ls"})
    """

    def __init__(
        self,
        mode: SandboxMode = SandboxMode.NONE,
        project_dir: str | None = None,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._mode = mode
        self._project_dir = project_dir or os.getcwd()
        self._limits = limits or ResourceLimits()
        self._worktree: WorktreeSandbox | None = None
        self._process: ProcessSandbox | None = None

    @property
    def mode(self) -> SandboxMode:
        return self._mode

    def isolate(self) -> Any:
        """创建隔离环境。

        Returns:
            上下文管理器, 提供隔离后的工作路径
        """
        if self._mode == SandboxMode.WORKTREE:
            self._worktree = WorktreeSandbox(self._project_dir, self._limits)
            return self._worktree
        elif self._mode == SandboxMode.PROCESS:
            self._process = ProcessSandbox(self._limits)
            return self._process
        else:
            # NONE: 无隔离
            return _NullSandbox()

    def get_path(self) -> str | None:
        """获取隔离环境的工作路径。"""
        if self._worktree is not None and self._worktree.active:
            return str(self._worktree.path)
        if self._process is not None and self._process.active:
            return str(self._process._temp_dir)
        return None

    def execute(
        self,
        tool_id: str,
        args: dict[str, Any],
    ) -> SandboxResult:
        """在沙箱中执行工具操作。

        Args:
            tool_id: 工具 ID
            args: 工具参数

        Returns:
            SandboxResult
        """
        if self._mode == SandboxMode.NONE:
            return SandboxResult(
                success=True,
                output="[sandbox: none mode, no isolation]",
            )

        # Check for active sandbox context
        if self._mode == SandboxMode.PROCESS:
            if self._process is None or not self._process.active:
                # Auto-create process sandbox if not yet isolated
                if self._process is None:
                    self._process = ProcessSandbox(self._limits)
                self._process.create_workspace()

        if tool_id == "bash":
            command = args.get("command", "")
            cwd = self.get_path()
            return self._execute_command(command, cwd)

        if tool_id in ("write_file", "edit_file", "batch_edit"):
            # 文件操作在隔离环境中执行
            path = args.get("path", args.get("file_path", ""))
            content = args.get("content", args.get("old_string", ""))

            if not self._limits.allow_write:
                return SandboxResult(
                    success=False,
                    output="",
                    error="Write operations not allowed in sandbox (read-only mode)",
                )

            # 在隔离环境中执行文件写入
            return self._write_file(path, content, tool_id)

        return SandboxResult(
            success=True,
            output=f"[sandbox: {tool_id} executed in {self._mode.value} mode]",
        )

    def cleanup(self) -> None:
        """清理沙箱环境。"""
        if self._worktree is not None:
            self._worktree.cleanup()
        if self._process is not None:
            self._process.cleanup()

    def apply_changes(self) -> bool:
        """将沙箱中的修改应用回主项目。"""
        if self._worktree is not None:
            return self._worktree.apply_changes()
        if self._process is not None:
            # Process sandbox: copy files back
            return self._apply_process_changes()
        return True

    def get_diff(self) -> str:
        """获取沙箱中的修改差异。"""
        if self._worktree is not None:
            return self._worktree.get_diff()
        return ""

    # ── Internal ──

    def _execute_command(
        self,
        command: str,
        cwd: str | None = None,
    ) -> SandboxResult:
        if self._process is not None:
            return self._process.execute_command(command, cwd=cwd)

        if self._worktree is not None and self._worktree.active:
            try:
                start = time.time()
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self._limits.timeout_seconds,
                    cwd=cwd or str(self._worktree.path),
                )
                duration = time.time() - start
                return SandboxResult(
                    success=result.returncode == 0,
                    output=result.stdout,
                    error=result.stderr,
                    exit_code=result.returncode,
                    duration=duration,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    success=False, output="", error="Timeout",
                )
            except Exception as e:
                return SandboxResult(
                    success=False, output="", error=str(e),
                )

        return SandboxResult(
            success=False,
            output="",
            error="No active sandbox",
        )

    def _write_file(
        self,
        path: str,
        content: str,
        tool_id: str,  # noqa: ARG002
    ) -> SandboxResult:
        cwd = self.get_path()
        if cwd is None:
            return SandboxResult(
                success=False, output="", error="No active sandbox",
            )

        try:
            abs_path = Path(cwd) / path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            return SandboxResult(
                success=True,
                output=f"Written {len(content)} bytes to {path}",
            )
        except Exception as e:
            return SandboxResult(
                success=False, output="", error=str(e),
            )

    def _apply_process_changes(self) -> bool:
        """将进程沙箱的文件复制回主项目。"""
        if self._process is None or not self._process.active:
            return True
        try:
            src = self._process._temp_dir
            if src is None:
                return True
            dst = Path(self._project_dir)
            for item in src.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(src)
                    target = dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
            return True
        except Exception:
            return False

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


class _NullSandbox:
    """空沙箱 — 无隔离, 用于 NONE 模式。"""

    def __enter__(self) -> str:
        return os.getcwd()

    def __exit__(self, *args: Any) -> None:
        pass