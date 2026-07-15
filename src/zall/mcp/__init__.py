"""zall.mcp — MCP (Model Context Protocol) 集成 (DESIGN.md §9.2.11).

三个子模块:
  - config: 加载 .zall/mcp.toml 的 server 声明
  - client: 极简 stdlib MCP stdio JSON-RPC 客户端 (initialize/list/call)
  - tool:   把 MCP server 暴露的 tool 包装成 zall Tool (走 Authority 三层名单)

核心立场 (§9.2.11):
  MCP 是工具来源, 不豁免 Authority。新 MCP tool 默认 greylist
  (deny-by-default), 由 §4.2.1 context_judge 无匹配默认 greylist 保证。
  本包不处理 Authority 判定, 只负责"翻译" MCP 协议 ↔ zall Tool 接口。

IPR constraints:
  IPR-0: 失败安全 — MCP server 不可用必须跳过, 不阻断核心 agent
  IPR-3: 仅 stdlib (client 不引第三方 MCP SDK)
  IPR-4: 本包是 tool layer primitive, 不是主 Loop
"""

from zall.mcp.config import MCPServerSpec, load_mcp_config
from zall.mcp.tool import MCPTool

__all__ = ["MCPServerSpec", "load_mcp_config", "MCPTool"]
