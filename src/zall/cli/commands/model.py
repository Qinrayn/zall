"""zall.cli.commands.model — Model & config commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /model, /max-steps, /verbose, /plan

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from zall.cli.commands._common import (
    _CATEGORY_MODEL,
    slash_command,
    _check_network_basic,
    _check_network_http,
    _check_git_health,
    _check_mcp_health,
    _check_dependency_version,
    _check_path_tools,
    _check_trust_anchor,
    _check_disk_space,
)
from zall.cli.config import (
    _config_status, _detect_provider, _persist_model_to_config,
    _resolve_model_alias, _PROVIDER_DISPLAY,
)
from zall._util.model_registry import _MODEL_PRESETS
from zall.cli.environment import CwdMeta as _CwdMeta
from zall.cli.environment import build_system_prompt as _build_system_prompt
from zall.cli.render import _shared_console
from zall.core.context import Context as _Context
from zall.core.model import Message, ToolChoice
from zall.safety.config import load_config, CONFIG_DIR

# Extracted from _legacy.py lines 1407-1590
@slash_command("/plan", description="toggle plan mode (read-only)", category=_CATEGORY_MODEL)
def cmd_plan(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if state is None:
        state = {}
    state["plan_mode"] = not state.get("plan_mode", False)
    if loop and hasattr(loop, "set_plan_mode"):
        loop.set_plan_mode(state["plan_mode"])
    # O6: 切换 plan mode 时更新 system prompt (inject/remove plan 指令)
    if loop and hasattr(loop, "messages") and hasattr(loop, "set_messages"):
        msgs = list(loop.messages)
        for i, m in enumerate(msgs):
            if m.role == "system":
                new_sys = _build_system_prompt(
                    loop._context if hasattr(loop, "_context") else _Context(
                        user_raw="", cwd_meta=_CwdMeta()),
                    plan_mode=state["plan_mode"],
                )
                new_m = m.__class__(role="system", content=new_sys)
                msgs[i] = new_m
                loop.set_messages(msgs)
                break
    if state["plan_mode"]:
        out.write("  plan mode \u2192 on (analysis-first; writes blocked; reads allowed)\n")
    else:
        out.write("  plan mode \u2192 off (normal authority posture)\n")
    return "handled"


@slash_command("/max-steps", description="show/set step limit", category=_CATEGORY_MODEL)
def cmd_max_steps(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if state is None:
        state = {}
    if arg:
        try:
            n = int(arg)
            if n <= 0:
                out.write("  max-steps must be positive\n")
            else:
                state["max_steps"] = n
                out.write(f"  max-steps \u2192 {n} (applies to next new conversation)\n")
        except ValueError:
            out.write("  usage: /max-steps [N]\n")
    else:
        cur = state.get("max_steps", 100_000)
        out.write(f"  current max-steps: {cur}\n")
    return "handled"


@slash_command("/verbose", description="toggle verbose output", category=_CATEGORY_MODEL)
def cmd_verbose(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if state is None:
        state = {}
    state["verbose"] = not state.get("verbose", False)
    renderer = state.get("_renderer")
    if renderer is not None and hasattr(renderer, "set_verbose"):
        renderer.set_verbose(state["verbose"])
    out.write(f"  verbose \u2192 {'on' if state['verbose'] else 'off'}"
              f" (applies to next new conversation)\n")
    return "handled"


@slash_command("/doctor", description="diagnose config / dependencies / network / project", category=_CATEGORY_MODEL)
def cmd_doctor(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """全面诊断: config / network / dependency / Git / MCP / 项目健康。

    Usage:
        /doctor           — 全面诊断
        /doctor network   — 仅网络检查
        /doctor deps      — 仅依赖检查
        /doctor git       — 仅 Git 检查
    """
    import importlib as _importlib
    from pathlib import Path

    # 支持子commandfilter
    arg_lower = arg.strip().lower()
    filter_mode = arg_lower if arg_lower in ("network", "deps", "git", "config", "project") else ""

    rows: list[tuple[str, str, str]] = []
    try:
        cfg = load_config()
    except Exception as cfg_err:
        cfg = {"api_key": "", "model": "", "api_base": ""}
        cfg_err_str = str(cfg_err)
    else:
        cfg_err_str = ""

    # ── System ──
    if not filter_mode or filter_mode == "config":
        rows.append(("platform", f"{platform.system()} {platform.release()}", "dim"))
        rows.append(("python", f"{platform.python_version()}", "dim"))

    # ── Config ──
    if not filter_mode or filter_mode == "config":
        if cfg_err_str:
            rows.append(("config", f"ERROR: {cfg_err_str}", "red"))
        else:
            api_key = cfg.get("api_key")
            key_status = "set" if api_key and api_key != "your-api-key-here" else "MISSING"
            rows.append(("api_key", key_status, "green" if api_key else "red"))
            model = cfg.get("model") or "MISSING"
            rows.append(("model", model, "green" if cfg.get("model") else "red"))
            provider = _detect_provider(model)
            pname = _PROVIDER_DISPLAY.get(provider, provider)
            env_key = ""
            if provider == "anthropic":
                env_key = os.environ.get("ANTHROPIC_API_KEY", "")
            elif provider == "gemini":
                env_key = os.environ.get("GOOGLE_API_KEY", "")
            elif provider == "ollama":
                env_key = os.environ.get("OLLAMA_HOST", "")
            provider_detail = pname
            if provider == "ollama":
                provider_detail += f" (host={env_key or 'http://localhost:11434'})"
            else:
                provider_detail += f" (env={'set' if env_key else 'N/A'})"
            rows.append(("provider", provider_detail, "green"))
            api_base = cfg.get("api_base") or "MISSING"
            rows.append(("api_base", api_base, "green" if cfg.get("api_base") else "red"))
            config_file = CONFIG_DIR / "config.toml"
            if config_file.exists():
                rows.append(("config_path", str(config_file), "dim"))
            sd = Path.home() / ".zall" / "sessions"
            rows.append(("sessions_dir", f"{sd} (exists)" if sd.exists() else f"{sd} (none yet)", "green" if sd.exists() else "dim"))
            project_zall = Path.cwd() / ".zall"
            if project_zall.exists():
                project_items = [p.name for p in project_zall.iterdir() if p.is_file()]
                rows.append(("project_dir", f"{len(project_items)} files", "dim"))

# ── Network ──
    if not filter_mode or filter_mode == "network":
        api_base = cfg.get("api_base", "") if not cfg_err_str else ""
        dns_check = _check_network_basic(api_base)
        rows.append(("dns", dns_check[1], "green" if dns_check[0] == "ok" else "red"))
        tcp_check = _check_network_http(api_base)
        rows.append(("connect", tcp_check[1], "green" if tcp_check[0] == "ok" else "red"))

    # ── Model API validate ──
    if not filter_mode or filter_mode == "network":
        model_name = cfg.get("model", "") if not cfg_err_str else ""
        if model_name and dns_check[0] == "ok" and tcp_check[0] == "ok":
            try:
                from zall.adapters.openai_compat import OpenAICompatAdapter
                test_adapter = OpenAICompatAdapter(model=model_name)
                test_resp = test_adapter.complete(
                    messages=[Message(role="user", content="Say 'ok' in one word.")],
                    tools=[], tool_choice=ToolChoice.NONE,
                )
                if test_resp.content and "error" not in test_resp.content.lower():
                    rows.append(("model_api", f"OK ({test_resp.usage.get('total', 0)} tokens used)", "green"))
                else:
                    rows.append(("model_api", f"ERROR: {test_resp.content[:80]}", "red"))
                test_adapter.close()
            except Exception as api_err:
                rows.append(("model_api", f"FAILED: {api_err}", "red"))

    # ── Dependencies ──
    if not filter_mode or filter_mode == "deps":
        for dep in ("pydantic", "cryptography", "httpx", "rich"):
            ver = _check_dependency_version(dep)
            ok = ver != "MISSING"
            label = f"{dep}=={ver}" if ver and ver != "ok" else ("ok" if ok else "MISSING")
            rows.append((f"dep:{dep}", label, "green" if ok else "red"))
        path_check = _check_path_tools()
        rows.append(("path_tools", path_check[1], "green" if path_check[0] == "ok" else "warn" if path_check[0] == "warn" else "red"))

    # ── Project ──
    if not filter_mode or filter_mode == "project":
        git_check = _check_git_health()
        rows.append(("git", git_check[1], "green" if git_check[0] == "ok" else "warn" if git_check[0] == "warn" else "red"))
        mcp_check = _check_mcp_health()
        rows.append(("mcp", mcp_check[1], "green" if mcp_check[0] == "ok" else "warn" if mcp_check[0] == "warn" else "red"))
        anchor_check = _check_trust_anchor()
        rows.append(("trust", anchor_check[1], "green" if anchor_check[0] == "ok" else "warn"))
        # check AGENTS.md
        agents_md = Path.cwd() / ".zall" / "AGENTS.md"
        if agents_md.exists():
            try:
                md_size = agents_md.stat().st_size
                has_content = md_size > 150  # 大于模板大小表示有实际内容
                rows.append(("agents.md", f"{md_size}B {'(has content)' if has_content else '(template only)'}", "green" if has_content else "warn"))
            except Exception:
                rows.append(("agents.md", "exists (unreadable)", "warn"))
        disk_check = _check_disk_space()
        rows.append(("disk", disk_check[1], "green" if disk_check[0] == "ok" else "warn"))

    # ── Context watermark ──
    if not filter_mode and loop is not None and hasattr(loop, "compactor") and loop.compactor is not None:
        try:
            wm = getattr(loop.compactor, "watermark_monitor", None)
            if wm is not None:
                report = wm.get_watermark_report(loop.messages, loop.model_adapter.model_name)
                wm_status = report.get("status", "normal")
                wm_str = f"{report.get('watermark', 0)*100:.0f}% ({report.get('estimated_tokens', 0)}/{report.get('window_size', 0)})"
                wm_color = "green" if wm_status == "normal" else ("yellow" if wm_status == "warning" else "red")
                rows.append(("watermark", f"{wm_str} ({wm_status})", wm_color))
        except Exception:
            pass

    # ── 渲染结果 ──
    if not rows:
        out.write(f"  no diagnostics for filter '{arg_lower}' (try: network, deps, git, config, project)\n")
        return "handled"

    if hasattr(out, "isatty") and out.isatty():
        from rich.table import Table
        c = _shared_console(out)
        t = Table(title="zall doctor", show_header=True, header_style="cyan",
                  border_style="dim", padding=(0, 1), expand=False)
        t.add_column("check", style="dim")
        t.add_column("status")
        for k, v, color in rows:
            t.add_row(k, f"[{color}]{v}[/]")
        c.print(t)
        # 如果有红色项, 显示建议
        red_items = [(k, v) for k, v, color in rows if color == "red"]
        if red_items:
            c.print()
            c.print("[bold red]Issues found:[/]")
            for k, v in red_items:
                if "api_key" in k:
                    c.print(f"  • [bold]{k}[/]: Set ZALL_API_KEY or run with --init")
                elif "MISSING" in v or "MISSING" in str(v):
                    c.print(f"  • [bold]{k}[/]: pip install {k.split(':')[-1]}")
                elif "network" in k or "connect" in k or "dns" in k:
                    c.print(f"  • [bold]{k}[/]: Check your network, api_base, and VPN settings. Run /doctor network")
                elif "git" in k:
                    c.print(f"  • [bold]{k}[/]: Ensure git is installed and you're in a git repository")
                else:
                    c.print(f"  • [bold]{k}[/]: {v}")
    else:
        for k, v, _ in rows:
            out.write(f"  {k:16s} {v}\n")
    return "handled"


@slash_command("/model", description="show/switch model", category=_CATEGORY_MODEL)
def cmd_model(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    if state is None:
        state = {}
    if arg:
        parts = arg.split()
        persist = False
        model_arg = arg
        if parts[0] in ("--persist", "-p"):
            persist = True
            model_arg = " ".join(parts[1:]).strip()
        if not model_arg:
            out.write("  usage: /model <name> [-p|--persist]\n")
            return "handled"
        name = _resolve_model_alias(model_arg)
        state["model"] = name
        provider = _detect_provider(name)
        out.write(f"  model \u2192 {name}  [provider: {_PROVIDER_DISPLAY.get(provider, provider)}]\n")
        if persist:
            _persist_model_to_config(name)
            out.write(f"  \u2713 persisted to ~/.zall/config.toml\n")
        return "handled"

    cur = state.get("model") or _config_status().get("model") or "(unset)"
    cur_provider = _detect_provider(cur)
    input_fn = state.get("_input_fn")
    if not hasattr(out, "isatty") or not out.isatty() or input_fn is None:
        out.write(f"  current model: {cur}\n")
        out.write(f"  provider: {_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}\n")
        out.write("  usage: /model <name>  (eg. /model gpt-4o-mini, /model flash)\n")
        out.write("         /model -p <name>  (persist to config)\n")
        return "handled"

    out.write(f"  current model: {cur}\n")
    out.write(f"  provider: {_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}\n")
    out.write("  available:\n")
    _PROVIDER_TAG = {"openai": "[O]", "anthropic": "[A]", "gemini": "[G]", "ollama": "[L]"}
    for i, (alias, _full, note, provider) in enumerate(_MODEL_PRESETS, 1):
        mark = "  \u2190 current" if alias == cur else ""
        tag = _PROVIDER_TAG.get(provider, "[?]")
        out.write(f"    {i}. {tag} {alias:20s} {note}{mark}\n")
    try:
        sel = (input_fn("  select [N] or type a model name: ") or "").strip()
    except (EOFError, KeyboardInterrupt):
        out.write("\n")
        return "handled"
    if not sel:
        return "handled"
    if sel.isdigit() and 1 <= int(sel) <= len(_MODEL_PRESETS):
        name = _MODEL_PRESETS[int(sel) - 1][1]
    else:
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/@:")
        if all(c in allowed_chars for c in sel):
            name = _resolve_model_alias(sel)
        else:
            out.write("  (model name contains invalid characters, model unchanged)\n")
            return "handled"
    state["model"] = name
    provider = _detect_provider(name)
    out.write(f"  model \u2192 {name}  [provider: {_PROVIDER_DISPLAY.get(provider, provider)}]\n")
    return "handled"

