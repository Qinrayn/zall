"""zall.cli.commands.reload — /reload 热重载command (Item E).

对应 Pi 的 /reload 功能: 运行时重载 skills, MCP tools, rules, provider 配置,
无需重启 REPL 会话。
"""

from __future__ import annotations

from typing import Any

from zall.cli.commands._common import (
    _CATEGORY_TOOLS,
    _setup_completion,
    slash_command,
)


@slash_command(
    "/reload",
    aliases=("/rl",),
    description="reload skills, MCP tools, rules, and provider config",
    category=_CATEGORY_TOOLS,
)
def cmd_reload(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """热重载config: skills + MCP + rules + provider。

    用法: /reload
    不影响当前对话 (loop), 只更新下次模型调用时的配置。
    """
    if state is None:
        out.write("  /reload requires active REPL session\n")
        return "handled"

    errors: list[str] = []

    # 1. Reload rules
    try:
        from zall.safety.rules_file import load_rules
        new_rules = load_rules()
        if loop is not None and hasattr(loop, "_rules"):
            loop._rules = new_rules
        out.write("  \u2713 rules reloaded\n")
    except Exception as e:
        errors.append(f"rules: {e}")

    # 2. Reload skills
    try:
        from zall.skills import load_skills
        new_skills = load_skills()
        state["_skills"] = new_skills
        _setup_completion(new_skills)
        out.write("  \u2713 skills reloaded\n")
    except Exception as e:
        errors.append(f"skills: {e}")

    # 3. Reload MCP tools (close old connections, open new ones)
    try:
        from zall.cli.orchestrator import build_mcp_tools, build_tools, merge_tools, inject_subagent_context
        old_mcp = state.get("_mcp_tools", [])
        for t in old_mcp:
            try:
                t.close()
            except Exception:
                pass
        new_mcp = build_mcp_tools(out)
        state["_mcp_tools"] = new_mcp
        if loop is not None and hasattr(loop, "_tools"):
            native = build_tools()
            merged = merge_tools(tuple(native.tools), new_mcp)
            loop._tools = merged
            if hasattr(loop, "_tool_schemas"):
                loop._tool_schemas = list(merged.schemas)
            # 重新inject subagent context
            rules = getattr(loop, "_rules", None)
            model = getattr(loop, "_model", None)
            if model is not None and rules is not None:
                inject_subagent_context(merged, model, rules)
        out.write(f"  \u2713 MCP tools reloaded ({len(new_mcp)} tools)\n")
    except Exception as e:
        errors.append(f"MCP: {e}")

    # 4. Reload provider config (custom providers from TOML)
    try:
        from zall.cli.config import _clear_provider_registry_cache
        _clear_provider_registry_cache()
        out.write("  \u2713 provider config reloaded\n")
    except Exception as e:
        errors.append(f"provider: {e}")

    if errors:
        out.write(f"  \u26a0 partial errors: {'; '.join(errors)}\n")
    return "handled"