"""zall.mcp.config — MCP server configload (.zall/mcp.toml).

DESIGN §9.2.11: MCP server 注册来源。极简 TOML 格式 (与 rules.toml 同源优先级):

    [[servers]]
    name = "filesystem"
    command = "mcp-server-filesystem"
    args = ["/abs/path"]
    env = { "KEY" = "value" }   # 可选

优先级: 项目级 .zall/mcp.toml > 用户级 ~/.zall/mcp.toml (同名后者覆盖)。
无配置 → 返回 [] (zall 无 MCP 工具, 核心 agent 不受影响, IPR-0 失败安全)。
解析失败 → 返回 [] (失败安全, 不阻断)。

IPR constraints:
  IPR-3: 仅 stdlib (手写极简 [[servers]] 解析, 不引 toml 库, 与 rules_file 同源哲学)
  IPR-0: 文件缺失 / 编码错误 / 解析错误都不得让 agent 启动崩溃
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MCPServerSpec:
    """一个 MCP server 的声明。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


def load_mcp_config(
    user_path: str | None = None,
    project_path: str | None = None,
) -> list[MCPServerSpec]:
    """load MCP server 声明 (项目级覆盖用户级同名)。"""
    project = (
        _load_one(Path(project_path) / ".zall" / "mcp.toml")
        if project_path
        else _load_one(Path.cwd() / ".zall" / "mcp.toml")
    )
    user = (
        _load_one(Path(user_path))
        if user_path
        else _load_one(Path.home() / ".zall" / "mcp.toml")
    )
    merged: dict[str, MCPServerSpec] = {}
    for spec in user:
        merged[spec.name] = spec
    for spec in project:
        merged[spec.name] = spec  # 项目级优先
    return list(merged.values())


def _load_one(path: Path) -> list[MCPServerSpec]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        return _parse_servers(text)
    except Exception:
        # 失败security: parseexception → skip该file, 不阻断 agent 启动 (IPR-0)
        return []


def _parse_servers(text: str) -> list[MCPServerSpec]:
    """parse极简 [[servers]] TOML (只支持 name/command/args[]/env{})。

    支持单行与多行两种写法:
        args = ["/root", "--read-only"]          # 单行数组
        args = [                                 # 多行数组
          "/root"
        ]
        env = { "TOKEN" = "abc" }                # 单行内联表
    """
    servers: list[MCPServerSpec] = []
    current: dict[str, Any] | None = None
    in_args = False
    in_env = False

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line == "[[servers]]":
            if current:
                servers.append(_spec_from(current))
            current = {}
            in_args = False
            in_env = False
            continue
        if current is None:
            continue

        # 单行 args 数组: args = ["a", "b"]
        m = re.match(r'args\s*=\s*\[(.*)\]\s*$', line)
        if m:
            current.setdefault("args", []).extend(_split_inline_array(m.group(1)))
            continue
        # 单行 env 内联表: env = { "K" = "V", "K2" = "V2" }
        m2 = re.match(r'env\s*=\s*\{(.*)\}\s*$', line)
        if m2:
            current.setdefault("env", {}).update(_parse_inline_env(m2.group(1)))
            continue

        if line == "args = [":
            current["args"] = []
            in_args = True
            continue
        if line == "]" and in_args:
            in_args = False
            continue
        if line == "env = {":
            current["env"] = {}
            in_env = True
            continue
        if line == "}" and in_env:
            in_env = False
            continue

        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if in_args:
                current.setdefault("args", []).append(_unquote(val))
            elif in_env:
                # env 项形如 KEY = "VALUE" (v0.1.1 fix: 使用 key 而非 val)
                ekey = key.strip()  # 使用外层解析的 key (等号左侧)
                eval_ = val.strip()  # val 是等号右侧的带引号值
                current.setdefault("env", {})[_unquote(ekey)] = _unquote(eval_)
            else:
                current[key] = _unquote(val)

    if current:
        servers.append(_spec_from(current))
    return servers


from zall._util.toml import unquote_value as _unquote


def _split_inline_array(s: str) -> list[str]:
    items: list[str] = []
    for part in s.split(","):
        part = part.strip()
        if part:
            items.append(_unquote(part))
    return items


def _parse_inline_env(s: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for part in s.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        env[_unquote(k.strip())] = _unquote(v.strip())
    return env


def _spec_from(d: dict[str, Any]) -> MCPServerSpec:
    name = d.get("name", "")
    command = d.get("command", "")
    if not name or not command:
        raise ValueError("server missing name/command")
    return MCPServerSpec(
        name=name,
        command=command,
        args=tuple(d.get("args", [])),
        env=dict(d.get("env", {})),
    )
