"""test用的极简 MCP server (stdio JSON-RPC).

只实现 zall.mcp.client 依赖的minimalsub集:
  - initialize → returns serverInfo
  - tools/list → returns两个 tool (echo / add)
  - tools/call → 回显参数 / 求和

用法: `python -m tests._mock_mcp_server` (被 MCPClient 作forsub进程 spawn).
每次读到一行 JSON-RPC 请求, 写回一行 JSON-RPC 响应 (带匹配 id).
"""

import json
import sys


def _respond(req, result):
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": result}


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")

        if method == "initialize":
            sys.stdout.write(
                json.dumps(
                    _respond(
                        msg,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "mock", "version": "1.0"},
                        },
                    )
                )
                + "\n"
            )
            sys.stdout.flush()

        elif method == "tools/list":
            sys.stdout.write(
                json.dumps(
                    _respond(
                        msg,
                        {
                            "tools": [
                                {
                                    "name": "echo",
                                    "description": "回显传入的 text",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {"text": {"type": "string"}},
                                        "required": ["text"],
                                    },
                                },
                                {
                                    "name": "add",
                                    "description": "两个数相加",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "a": {"type": "number"},
                                            "b": {"type": "number"},
                                        },
                                        "required": ["a", "b"],
                                    },
                                },
                            ]
                        },
                    )
                )
                + "\n"
            )
            sys.stdout.flush()

        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                text = args.get("text", "")
                result = {"content": [{"type": "text", "text": f"echo:{text}"}]}
            elif name == "add":
                result = {
                    "content": [
                        {"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}
                    ]
                }
            else:
                result = {"content": [{"type": "text", "text": "?"}]}
            sys.stdout.write(json.dumps(_respond(msg, result)) + "\n")
            sys.stdout.flush()
        # notifications/initialized 等无response


if __name__ == "__main__":
    main()
