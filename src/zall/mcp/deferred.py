"""zall.mcp.deferred — MCP 延迟后台加载 (kimi KimiToolset 三段式状态机对标, 原创).

kimi soul/toolset.py 的 defer→start→wait 状态机逐行研读后, 按 zall 的
同步+线程架构重写 (kimi 用 asyncio.Task, zall 用 daemon thread):

  idle ──start()──▶ loading ──连接完成──▶ ready
                       │
                    wait() 收敛 (首个回合构建前调用, 保证工具可用)

价值: MCP server 连接 (子进程启动 + 握手) 常需数百 ms~数秒, 此前在启动路径
同步阻塞 — REPL/TUI 首屏被拖慢。现在启动即回, 连接在后台进行, 首个回合
构建时才收敛等待 (通常此刻早已就绪, 等待为零)。

失败安全 (IPR-0): 后台加载抛异常 → 记录 + 空工具集降级, 不影响主流程。

IPR constraints:
  IPR-0: tests/test_mcp_deferred_invariants.py (含反例)
  IPR-3: stdlib only (加载实现委托 cli.orchestrator.build_mcp_tools)
"""

from __future__ import annotations

import io
import threading
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger

_log = _get_zall_logger(__name__)


class DeferredMCPLoader:
    """MCP 工具的三段式后台加载器 (idle → loading → ready)。"""

    __test__ = False

    def __init__(self, load_fn: Any = None) -> None:
        # load_fn 可注入 (测试用); 默认 build_mcp_tools
        self._load_fn = load_fn
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._tools: list[Any] = []
        self._log_buffer = io.StringIO()
        self._error: str = ""

    # ── 状态 ──

    @property
    def state(self) -> str:
        """idle | loading | ready"""
        with self._lock:
            if self._thread is None:
                return "idle"
        return "ready" if self._done.is_set() else "loading"

    def status(self) -> dict[str, Any]:
        """只读状态快照 (kimi mcp_status_snapshot 对标, 供 /doctor 等显示)。"""
        st = self.state
        return {
            "state": st,
            "tool_count": len(self._tools) if st == "ready" else 0,
            "log": self._log_buffer.getvalue(),
            "error": self._error,
        }

    # ── 生命周期 ──

    def start(self, *, servers: Any = None) -> bool:
        """幂等启动后台加载; 已启动则返回 False。"""
        with self._lock:
            if self._thread is not None:
                return False
            self._thread = threading.Thread(
                target=self._run, kwargs={"servers": servers},
                name="zall-mcp-loader", daemon=True,
            )
            self._thread.start()
            return True

    def _run(self, servers: Any = None) -> None:
        try:
            if self._load_fn is not None:
                self._tools = list(self._load_fn())
            else:
                from zall.cli.orchestrator import build_mcp_tools
                self._tools = list(build_mcp_tools(self._log_buffer, servers=servers))
        except Exception as e:
            # IPR-0: 后台失败降级为空工具集, 不影响主流程
            self._error = f"{type(e).__name__}: {e}"
            self._tools = []
            _log.warning("deferred MCP loading failed: %s", self._error)
        finally:
            self._done.set()

    def wait(self, timeout: float | None = 30.0) -> list[Any]:
        """收敛等待并返回工具列表 (未启动 → 空; 超时 → 当前已得, 可能为空)。

        首个回合构建前调用 — 通常后台早已完成, 等待为零。
        """
        with self._lock:
            started = self._thread is not None
        if not started:
            return []
        if not self._done.wait(timeout=timeout):
            _log.warning("deferred MCP loading still pending after %ss", timeout)
            return []
        return list(self._tools)

    def close_all(self, timeout: float = 5.0) -> None:
        """退出前收敛 + 关闭已加载的 server 连接 (幂等, REPL finally 调用)。

        线程可能在 wait 超时后仍存活 — 继续等待其收尾, 一旦返回立即关闭
        新生的工具, 否则子进程/套接字随线程悬挂泄漏。
        """
        with self._lock:
            thread = self._thread
            tools = list(self._tools)
            self._tools = []
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                _log.warning(
                    "deferred MCP loader thread still alive after %ss "
                    "(server connections may leak on process exit)", timeout,
                )
                return
        for t in tools:
            close = getattr(t, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    _log.warning("MCP tool close failed (non-fatal)", exc_info=True)


__all__ = ["DeferredMCPLoader"]
