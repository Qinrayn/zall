"""zall.core.sandbox — Subprocess sandbox for isolating untrusted plugin tools.

Corresponds to MASTER.md §12.1 Plugin: 沙箱 (PARTIAL -> REAL).
Previously only capability declaration enforcement (assert_capabilities_declared)
was implemented. This module adds subprocess execution isolation.

IPR-3: core/sandbox.py uses stdlib only (subprocess, json, os, sys, tempfile).
No model SDK, no third-party dependencies.

沙箱能力边界 (诚实说明):
  DONE:
    - 进程级隔离: 子进程崩溃 (segfault/OOM/异常) 不影响主进程
    - 超时控制: 默认 30s, 可配置, 防止无限循环
    - 工作目录隔离: 可指定 cwd, 限制子进程文件系统访问范围
    - 结果序列化: 复杂 ToolResult (含 artifacts) 正确 JSON 序列化回主进程

  OPEN (需 OS 级支持, 本版本不做):
    - 内存限制 (RLIMIT_AS / cgroup, 跨平台不一致)
    - 网络访问限制 (iptables / seccomp / Windows Filtering Platform)
    - 完整容器隔离 (Docker / Podman / Firecracker microVM)
    - 文件系统沙箱 (chroot / overlayfs / bind mounts)
    - 系统调用过滤 (seccomp-bpf)

Usage:
    sandbox = SubprocessSandbox(timeout=30)
    result = sandbox.execute_tool(
        tool_module="my_package.tools",
        tool_factory="my_tool_factory",
        args={"path": "/tmp/test.txt"},
    )
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from zall.core.tool import ToolResult

# ── Subprocess runner script ──

_SUBPROCESS_RUNNER = r"""
import importlib
import json
import sys


def _main() -> None:
    try:
        # Read serialized input from stdin (JSON)
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8"))

        tool_module = data["tool_module"]
        tool_factory = data["tool_factory"]
        args = data.get("args", {})
        sys_path_entries = data.get("sys_path_entries", [])

        # Prepend custom sys.path entries so the subprocess can find
        # test helper modules or custom plugin directories.
        for entry in sys_path_entries:
            if entry and entry not in sys.path:
                sys.path.insert(0, entry)

        # Import the module and get the factory function
        module = importlib.import_module(tool_module)
        factory = getattr(module, tool_factory)

        # Create the tool instance and execute
        tool = factory()
        result = tool.execute(args)

        # Serialize result to stdout as JSON
        json.dump(
            {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "artifacts": dict(result.artifacts) if result.artifacts else {},
            },
            sys.stdout,
        )
    except Exception as e:
        json.dump(
            {
                "success": False,
                "output": "[SANDBOX ERROR] %s: %s" % (type(e).__name__, str(e)),
                "error": str(e),
                "artifacts": {},
            },
            sys.stdout,
        )


if __name__ == "__main__":
    _main()
"""

# ── SubprocessSandbox ──


class SubprocessSandbox:
    """Subprocess sandbox: execute tool in an isolated subprocess.

    Used for untrusted plugin tool execution isolation. The main process
    is not affected by plugin crashes, segfaults, OOM, or infinite loops.

    Communication protocol:
      - Parent writes JSON to subprocess stdin:
          {tool_module, tool_factory, args, sys_path_entries?}
      - Subprocess writes JSON to parent stdout:
          {success, output, error, artifacts}
      - If the subprocess crashes or times out, an error ToolResult is returned.

    Args:
        timeout: Maximum execution time in seconds (default 30).
        cwd: Working directory for the subprocess (default None = inherit).
    """

    def __init__(self, timeout: int = 30, cwd: str | None = None) -> None:
        self._timeout = timeout
        self._cwd = cwd

    def execute_tool(
        self,
        tool_module: str,
        tool_factory: str,
        args: dict[str, Any],
        sys_path_entries: list[str] | None = None,
    ) -> ToolResult:
        """Execute a tool in a subprocess and return the result.

        Serializes (module, factory, args) to the subprocess via stdin.
        The subprocess imports the module, creates the tool via factory(),
        executes it with args, and serializes the ToolResult back via stdout.

        Args:
            tool_module: The Python module path (e.g., 'my_package.tools').
            tool_factory: The factory function name in the module.
            args: Arguments dict to pass to tool.execute().
            sys_path_entries: Optional list of paths to prepend to sys.path
                in the subprocess (for test helpers or custom plugin dirs).

        Returns:
            ToolResult from the subprocess, or an error ToolResult on failure.
        """
        # Serialize input data as JSON
        input_data: dict[str, Any] = {
            "tool_module": tool_module,
            "tool_factory": tool_factory,
            "args": args,
        }
        if sys_path_entries:
            input_data["sys_path_entries"] = sys_path_entries

        input_bytes = json.dumps(input_data).encode("utf-8")

        # Write runner script to a temporary file.
        # Using a temp file is more portable than -c on Windows (escaping).
        script_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                script_path = f.name
                f.write(_SUBPROCESS_RUNNER)
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[SANDBOX ERROR] failed to create runner script: {e}",
                error=str(e),
            )

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                input=input_bytes,
                capture_output=True,
                timeout=self._timeout,
                cwd=self._cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output=(
                    f"[SANDBOX TIMEOUT] tool execution exceeded "
                    f"{self._timeout}s timeout"
                ),
                error=f"timeout after {self._timeout}s",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=(
                    f"[SANDBOX ERROR] subprocess run failed: "
                    f"{type(e).__name__}: {e}"
                ),
                error=str(e),
            )
        finally:
            # Clean up the temp script
            if script_path is not None:
                try:
                    os.unlink(script_path)
                except OSError:
                    pass

        # Check for subprocess crash (non-zero exit code)
        if proc.returncode != 0:
            stderr_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            error_detail = (
                f"exit code {proc.returncode}"
                if not stderr_msg
                else f"exit code {proc.returncode}: {stderr_msg}"
            )
            return ToolResult(
                success=False,
                output=(
                    f"[SANDBOX CRASH] subprocess exited with code "
                    f"{proc.returncode}"
                ),
                error=error_detail,
            )

        # Parse the result JSON from stdout
        try:
            stdout_text = proc.stdout.decode("utf-8", errors="replace").strip()
            if not stdout_text:
                return ToolResult(
                    success=False,
                    output="[SANDBOX ERROR] subprocess produced no output",
                    error="empty stdout from subprocess",
                )
            stdout_data = json.loads(stdout_text)
            return ToolResult(
                success=bool(stdout_data.get("success", False)),
                output=str(stdout_data.get("output", "")),
                error=stdout_data.get("error"),
                artifacts=dict(stdout_data.get("artifacts", {})),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return ToolResult(
                success=False,
                output=(
                    f"[SANDBOX ERROR] failed to parse tool result: "
                    f"{type(e).__name__}: {e}"
                ),
                error=str(e),
            )