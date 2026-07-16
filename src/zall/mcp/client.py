"""zall.mcp.client — 极简 MCP stdio JSON-RPC 客户端 (stdlib only).

Design (DESIGN.md §9.2.11 MCP 注册协议):
  - 零第三方依赖 (守 IPR-3): 仅用 stdlib 实现 MCP stdio 传输的 JSON-RPC 子集
    (initialize / tools/list / tools/call)。不依赖官方 mcp SDK。
  - MCP 是工具来源, 不豁免 Authority: 工具的 SafeLevel 判定在 §4.2.1
    context_judge, 本文件不管 (默认 greylist 由 context_judge 保证)。
  - 失败安全: connect / list / call 失败抛 MCPError; 调用方 (app._build_mcp_tools)
    捕获并跳过该 server, 不阻断核心 agent (IPR-0)。

传输细节:
  - MCP stdio 用换行分隔的 JSON-RPC 2.0 (每条消息一行 JSON)。
  - 单后台 reader 线程把带 id 的响应入队; 主线程按 id 取匹配响应。
  - server 主动发来的 notification (无 id) 直接忽略。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any

from zall.core.tool import ToolResult

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "zall", "version": "0.0.17"}
_TIMEOUT = 15.0


class MCPError(Exception):
    """MCP protocol层error (server return error / 非法response)。"""


class MCPConnectionError(MCPError):
    """MCP 连接层error (spawn 失败 / processexit / timeout)。"""


class MCPClient:
    """极简 MCP stdio 客户端。

    一个 MCPClient 对应一个已 spawn 的 MCP server 子进程。
    connect() 后连续调用 list_tools() / call_tool(); 用毕 close()。
    """

    __test__ = False

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = dict(env) if env else None
        self._proc: subprocess.Popen[str] | None = None
        self._id = 0
        self._lock = threading.Lock()
        # B3: 独立锁保护 stdin write (reader thread和主threadconcurrent写 stdin)
        self._stdin_lock = threading.Lock()
        # v0.1.1 fix: dict[rid, response] + Condition 支持concurrent访问 (替代 queue.Queue)
        # 旧 Queue + 丢弃非匹配 ID 的design会导致concurrent下response丢失
        self._pending: dict[int, dict[str, Any]] = {}
        self._cv = threading.Condition(self._lock)
        # B3: stop信号 — 通知 reader threadexit
        self._reader_stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._closed = False

    # ──────────────────────────────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────────────────────────────

    def connect(self) -> "MCPClient":
        """spawn server + initialize + return self; 失败抛 MCPConnectionError。"""
        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=self._env,
            )
        except (OSError, ValueError) as e:
            raise MCPConnectionError(f"spawn failed: {e}") from e

        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
                timeout=_TIMEOUT,
            )
        except Exception:
            self._terminate()
            raise

        # 通知 server 已就绪 (notification, 无 response, 不等)
        self._notify("notifications/initialized")
        return self

    def close(self) -> None:
        """关闭 server 子process; 幂等 (可被多个共享该 client 的 MCPTool 反复调用)。"""
        if self._closed:
            return
        self._closed = True
        self._terminate()

    def _terminate(self) -> None:
        if self._proc is None:
            return
        # B3: settingstop信号, 通知 reader threadexit
        self._reader_stop.set()
        try:
            self._proc.terminate()
        except OSError:
            pass
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        try:
            self._proc.wait(timeout=2.0)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
            except OSError:
                pass
        self._proc = None

    # ──────────────────────────────────────────────────────────────────────
    # protocolmethod
    # ──────────────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """return server 暴露的 tool 规格list: [{name, description, inputSchema}, ...]。"""
        result = self._request("tools/list", {}, timeout=_TIMEOUT)
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用一个 MCP tool, 把结果转成 zall ToolResult。"""
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=_TIMEOUT,
        )
        return _mcp_result_to_tool_result(result)

    # ──────────────────────────────────────────────────────────────────────
    # 传输
    # ──────────────────────────────────────────────────────────────────────

    def _send(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPConnectionError("not connected")
        # B3: stdin 锁保护, 防止 reader thread和主threadconcurrent写
        with self._stdin_lock:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise MCPConnectionError(f"write failed: {e}") from e

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(
        self, method: str, params: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            )
        # 等待response: 使用 Condition wait, 支持concurrent (v0.1.1 fix)
        deadline = time.monotonic() + timeout
        with self._cv:
            while time.monotonic() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise MCPConnectionError("server process exited")
                if rid in self._pending:
                    resp = self._pending.pop(rid)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPConnectionError(
                        f"timeout waiting for '{method}' response"
                    )
                self._cv.wait(timeout=min(remaining, 0.2))
            else:
                raise MCPConnectionError(
                    f"timeout waiting for '{method}' response"
                )
        if "error" in resp:
            err = resp["error"]
            raise MCPError(
                f"{err.get('message', 'unknown error')} (code {err.get('code')})"
            )
        result: dict[str, Any] = resp.get("result", {})
        return result

    def _reader_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                # B3: checkstop信号
                if self._reader_stop.is_set():
                    return
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 只收集"带 id 且非request"的response (server→client 的结果)
                if "id" in msg and "method" not in msg:
                    with self._cv:
                        self._pending[msg["id"]] = msg
                        self._cv.notify_all()
                # server 主动发来的request (id + method), 回应不支持
                elif "id" in msg and "method" in msg:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                    # B3: 使用 stdin 锁, 防止 reader thread和主threadconcurrent写
                    with self._stdin_lock:
                        try:
                            if self._proc.stdin is not None:
                                self._proc.stdin.write(json.dumps(err_resp) + "\n")
                                self._proc.stdin.flush()
                        except (OSError, ValueError):
                            pass
        except (OSError, ValueError):
            return


def _mcp_result_to_tool_result(result: dict[str, Any]) -> ToolResult:
    """把 MCP tools/call 的 result 转成 zall ToolResult。"""
    content = result.get("content", [])
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    output = "\n".join(parts).strip()
    is_error = bool(result.get("isError", False))
    if is_error:
        return ToolResult(
            success=False,
            output=output,
            error=output or "MCP tool returned error",
        )
    return ToolResult(
        success=True,
        output=output or "(no output)",
        artifacts={"mcp_tool_result": True},
    )
