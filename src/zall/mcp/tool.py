"""zall.mcp.tool — MCPTool: 把 MCP server 暴露的一个 tool 包装成 zall Tool。

DESIGN §9.2.11: MCP 工具走 Authority 三层名单, 默认 greylist (deny-by-default)。
本类只负责"翻译":
  - 把 MCP 的 inputSchema 转成 OpenAI function schema (模型可调用)
  - 把 tools/call 的 result 转成 ToolResult (经 MCPClient)
  - 用命名空间化的 tool_id (mcp__<server>__<tool>) 防与 native 工具撞名

Authority 判定 (whitelist/greylist/blacklist) 在 §4.2.1 context_judge,
本类不介入 —— 默认 greylist 由 context_judge 无匹配默认 greylist 保证。

IPR constraints:
  IPR-0: 失败安全由 MCPClient 保证 (call_tool 抛 MCPError 由上层 gate 捕获)
  IPR-3: 仅 stdlib + core.tool
  IPR-4: tool primitive, 非主 Loop
"""

from __future__ import annotations

import functools
import hashlib
import re
from typing import Any

from zall.core.tool import ToolResult
from zall.mcp.client import MCPClient

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize(name: str) -> str:
    """把任意 MCP 名称洗成 OpenAI function name security字符 (^[A-Za-z0-9_-]+$)。

    全非 ASCII 名称 (如中文) 会被全替换为 `_`，此时用 hex 摘要兜底。
    """
    sanitized = _NAME_RE.sub("_", name)
    # B4: 全非 ASCII 等导致纯下划线 → 用 hex digestfallback
    stripped = sanitized.replace("_", "")
    if not stripped:
        sanitized = "mcp_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return sanitized


def _make_tool_id(server: str, name: str) -> str:
    """命名空间化 tool_id, 防撞名; 长度max 64 (OpenAI function name 约束)。"""
    server_part = _sanitize(server)
    name_part = _sanitize(name)
    prefix = "mcp__"
    gap = "__"
    tid = f"{prefix}{server_part}{gap}{name_part}"
    if len(tid) > 64:
        # B6 fix: 双向truncate — server 和 name 按比例缩减, 确保总长 <= 64
        available = 64 - len(prefix) - len(gap)  # 留给 server + name 的可用长度
        if available <= 0:
            # 极端情况: 前缀本身都超 64 (几乎不可能, 但防御)
            return prefix + hashlib.sha256(f"{server}:{name}".encode()).hexdigest()[:16]
        total = len(server_part) + len(name_part)
        # server 至少preserve 8 字符, name 至少preserve 1 字符 (可读性底线)
        s_max = max(8, int(available * len(server_part) / total))
        n_max = max(1, available - s_max)
        server_part = server_part[:s_max]
        name_part = name_part[:n_max]
        tid = f"{prefix}{server_part}{gap}{name_part}"
        # 防御: 仍超则用 hash fallback
        if len(tid) > 64:
            tid = prefix + hashlib.sha256(f"{server}:{name}".encode()).hexdigest()[:16]
    return tid


class MCPTool:
    """一个 MCP server 暴露的一个tool, implementation zall Tool protocol。

    tool_id 命名空间化 (mcp__<server>__<tool>) 满足 ToolRegistry 的
    tool_id 唯一不变量 (同一 server 内 tool 名天然唯一)。
    """

    __test__ = False

    def __init__(
        self, server_name: str, spec: dict[str, Any], client: MCPClient
    ) -> None:
        self._server = server_name
        self._mcp_name = spec.get("name", "")
        self._spec = spec
        self._client = client
        self._tool_id = _make_tool_id(server_name, self._mcp_name)

    @property
    def server_name(self) -> str:
        return self._server

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @functools.cached_property
    def schema(self) -> dict[str, Any]:
        """转成 OpenAI function-calling schema (MCP inputSchema 已是 JSON Schema)。"""
        params = self._spec.get("inputSchema") or {"type": "object", "properties": {}}
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        desc = (
            self._spec.get("description")
            or f"MCP tool '{self._mcp_name}' from server '{self._server}'"
        )
        return {
            "type": "function",
            "function": {
                "name": self._tool_id,
                "description": desc,
                "parameters": params,
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """execute: 用原始 MCP tool 名调 server (tool_id 是命名空间化的, 不匹配)。"""
        return self._client.call_tool(self._mcp_name, args)

    def close(self) -> None:
        """关闭底层 MCP server 连接 (幂等, 委托给共享的 MCPClient)。"""
        try:
            self._client.close()
        except Exception:
            pass
