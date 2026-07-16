"""zall.tools.pty_executor — PTY-based bash executor for interactive commands.

Optional enhancement over PopenExecutor. Uses PTY (pseudo-terminal) for:
  - Streaming output in real-time (not just at end)
  - Handling interactive commands (sudo, ssh, passwd)
  - Graceful timeout via PTY signal

Falls back to PopenExecutor if PTY is unavailable.

Corresponds to:
  §4.2    Tool layer: pluggable executor strategy
  §7      Long-term property: tool replaceability

IPR constraints:
  IPR-0: invariants at tests/test_bash_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2
  IPR-3: stdlib only (pty is stdlib on POSIX)
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from zall.core.tool import ToolResult
from zall.tools.bash import (
    MAX_OUTPUT_BYTES,
    PopenExecutor,
    _preferred_encoding,
    _sanitize_env,
    _truncate_at_bytes_enc,
)

# Try to import PTY support
_HAS_PTY = False
if sys.platform != "win32":
    try:
        import pty  # noqa: F401
        import select  # noqa: F401
        import termios  # noqa: F401
        import struct  # noqa: F401
        import fcntl  # noqa: F401
        _HAS_PTY = True
    except ImportError:
        pass


class PtyExecutor:
    """PTY-based bash executor with real-time output streaming.

    Uses a pseudo-terminal (PTY) to execute commands, enabling:
      - Real-time stdout/stderr collection
      - Interactive command handling (sudo, ssh)
      - Graceful timeout via PTY signal

    Falls back to PopenExecutor if PTY is not available on the platform
    (Windows) or if PTY initialization fails.

    Note: This executor does NOT stream output back to the model
    incrementally (the tool protocol is synchronous). However, the
    PTY approach handles interactive prompts and provides better
    process isolation than subprocess.PIPE.
    """

    def __init__(self) -> None:
        self._fallback = PopenExecutor()

    def execute(
        self,
        command: str,
        timeout: int,
        cwd: str | None = None,
    ) -> ToolResult:
        """Execute a command via PTY, fallback to PopenExecutor.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            cwd: Working directory (None = current)

        Returns:
            ToolResult with combined stdout/stderr
        """
        if not _HAS_PTY:
            return self._fallback.execute(command, timeout, cwd=cwd)

        try:
            return self._pty_execute(command, timeout, cwd)
        except Exception:
            return self._fallback.execute(command, timeout, cwd=cwd)

    def _pty_execute(
        self,
        command: str,
        timeout: int,
        cwd: str | None = None,
    ) -> ToolResult:
        """Execute via PTY with real-time output collection."""
        import pty as pty_module  # noqa: F401
        import select as select_module  # noqa: F401
        import fcntl as fcntl_module  # noqa: F401

        start = time.monotonic()
        env = _sanitize_env()
        enc = _preferred_encoding()

        # Fork PTY
        pid, fd = pty_module.fork()

        if pid == 0:  # Child process
            # Set up environment
            for key, val in env.items():
                os.environ[key] = val
            if cwd:
                try:
                    os.chdir(cwd)
                except OSError:
                    pass
            # Execute the command via shell
            try:
                os.execle("/bin/sh", "sh", "-c", command, os.environ)
            except OSError:
                os._exit(1)

        # Parent process
        output_chunks: list[bytes] = []
        deadline = time.monotonic() + timeout

        try:
            # Set PTY to non-blocking
            fl = fcntl_module.fcntl(fd, fcntl_module.F_GETFL)
            fcntl_module.fcntl(fd, fcntl_module.F_SETFL, fl | os.O_NONBLOCK)

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Timeout: kill child process group
                    try:
                        os.killpg(os.getpgid(pid), 15)  # SIGTERM
                        time.sleep(0.1)
                        os.killpg(os.getpgid(pid), 9)   # SIGKILL
                    except OSError:
                        pass
                    os.close(fd)
                    _, status = os.waitpid(pid, 0)
                    duration = time.monotonic() - start
                    output_text = b"".join(output_chunks).decode(enc, errors="replace")
                    return ToolResult(
                        success=False,
                        output=f"[ERROR: command timed out after {timeout}s]\n"
                               f"{output_text}\n{duration:.1f}s elapsed",
                        error=f"timeout after {timeout}s",
                        artifacts={"duration": duration},
                    )

                r, _, _ = select_module.select([fd], [], [], max(0.1, remaining))

                if r:
                    try:
                        data = os.read(fd, 4096)
                        if not data:
                            break  # EOF
                        output_chunks.append(data)
                    except (BlockingIOError, OSError):
                        break
                else:
                    # Check if child exited
                    wpid, status = os.waitpid(pid, os.WNOHANG)
                    if wpid == pid:
                        # Read any remaining data
                        try:
                            while True:
                                data = os.read(fd, 4096)
                                if not data:
                                    break
                                output_chunks.append(data)
                        except (BlockingIOError, OSError):
                            pass
                        break
        except Exception:
            # Clean up on error
            try:
                os.killpg(os.getpgid(pid), 9)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
            raise

        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass

        duration = time.monotonic() - start
        output_text = b"".join(output_chunks).decode(enc, errors="replace")
        std_enc = enc

        # Truncate if needed
        truncated = False
        output_bytes = len(output_text.encode(std_enc, errors="replace"))
        if output_bytes > MAX_OUTPUT_BYTES:
            output_text = _truncate_at_bytes_enc(output_text, MAX_OUTPUT_BYTES, std_enc) + (
                f"\n... [truncated: output too large ({output_bytes} bytes)]"
            )
            truncated = True

        # Try to extract exit code from output (PTY doesn't give exit code directly)
        # Check for common shell exit code patterns
        exit_code = 0
        for line in output_text.split("\n"):
            line = line.strip()
            if line.startswith("exit_code:"):
                try:
                    exit_code = int(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass

        output_parts = [f"exit_code: {exit_code}"]
        if output_text:
            output_parts.append(f"output:\n{output_text}")
        if truncated:
            output_parts.append("[Note: output was truncated]")

        return ToolResult(
            success=exit_code == 0,
            output="\n".join(output_parts),
            artifacts={
                "exit_code": exit_code,
                "duration": round(duration, 3),
                "stdout_bytes": output_bytes,
                "truncated": truncated,
                "executor": "pty",
            },
        )


def create_pty_executor() -> Any:
    """Factory function: create PtyExecutor with fallback."""
    return PtyExecutor()