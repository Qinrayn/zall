"""zall.cli.commands.model — Model & config commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /model, /max-steps, /verbose, /plan

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import os
import platform
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
from zall._util.model_registry import _MODEL_PRESETS, _PROVIDER_REGISTRY
from zall.cli.environment import CwdMeta as _CwdMeta
from zall.cli.environment import build_system_prompt as _build_system_prompt
from zall.cli.render import _shared_console
from zall.core.context import Context as _Context
from zall.core.model import Message, ToolChoice
from zall.safety.config import load_config, CONFIG_DIR


# ── Dynamic model discovery ──


def _detect_configured_providers() -> dict[str, bool]:
    """Scan env vars and config to detect which providers have valid API keys.

    Returns a dict of provider → bool indicating if it's configured and ready.
    Only marks a provider as configured when its specific key/env is available.
    Fix: global api_key only marks the provider that matches the configured model,
    not both "openai" and "agnes".
    """
    from zall._util.model_registry import get_model_provider

    configured: dict[str, bool] = {}
    try:
        cfg = load_config()
        api_key = (cfg.get("api_key") or "").strip()
        model_name = (cfg.get("model") or "").strip()
    except Exception:
        cfg = {}
        api_key = ""
        model_name = ""

    # Determine which provider the global api_key actually belongs to
    global_key_provider: str | None = None
    if api_key and api_key != "your-api-key-here":
        # Infer from configured model name
        if model_name:
            global_key_provider = get_model_provider(model_name)
        else:
            # No model configured → default to agnes (the default)
            global_key_provider = "agnes"

    for provider, (_display, env_var, _base, _url, _prefixes, _adapter) in _PROVIDER_REGISTRY.items():
        # Check env var first (exact per-provider match)
        if env_var and os.environ.get(env_var, "").strip():
            configured[provider] = True
            continue
        # Check provider-specific config key
        prov_key = cfg.get(f"{provider}_api_key", "") if isinstance(cfg, dict) else ""
        if prov_key and prov_key != "your-api-key-here":
            configured[provider] = True
            continue
        # Check global api_key — only marks the inferred provider
        if provider == global_key_provider:
            configured[provider] = True
            continue
        # Ollama (local) is always "configured" — no key needed
        if provider == "ollama":
            configured[provider] = True
            continue
        configured[provider] = False
    return configured


def _build_dynamic_model_list(
    provider_ready: dict[str, bool],
    current_model: str,
    custom_providers: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, str, str, bool]]:
    """Build a dynamic model list marked with configured status.

    Each entry: (alias, full_name, note, provider, is_configured)
    Returns presets + custom providers with readiness info, sorted by provider then by name.
    """
    result: list[tuple[str, str, str, str, bool]] = []
    for alias, full_name, note, provider in _MODEL_PRESETS:
        is_configured = provider_ready.get(provider, False)
        result.append((alias, full_name, note, provider, is_configured))

    # Add custom providers from config.toml [[providers]]
    if custom_providers:
        for prov in custom_providers:
            if isinstance(prov, dict):
                name = prov.get("name", "")
                if name and not any(r[0] == name or r[1] == name for r in result):
                    api_base = prov.get("api_base", "")
                    note = api_base[:50] if api_base else "custom"
                    result.append((name, name, note, "openai", True))

    # Sort: configured providers first, then by provider group, then by alias
    _provider_order = {"agnes": 0, "openai": 1, "anthropic": 2, "gemini": 3, "deepseek": 4, "ollama": 5}
    result.sort(key=lambda x: (
        0 if x[4] or x[3] == "ollama" else 1,  # configured/local first
        _provider_order.get(x[3], 99),         # by provider group
        x[0],                                   # by alias
    ))
    return result


# extracted from _legacy.py lines 1407-1590
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
            _has_real_key = bool(api_key) and api_key != "your-api-key-here"
            key_status = "set" if _has_real_key else "MISSING"
            rows.append(("api_key", key_status, "green" if _has_real_key else "red"))
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
                import os as _os
                test_adapter = OpenAICompatAdapter(
                    model=model_name,
                    api_key=cfg.get("api_key") or _os.environ.get("ZALL_API_KEY", ""),
                    api_base=cfg.get("api_base") or _os.environ.get("ZALL_API_BASE", ""),
                    timeout=float(cfg.get("timeout", 120.0)),
                )
                try:
                    test_resp = test_adapter.complete(
                        messages=[Message(role="user", content="Say 'ok' in one word.")],
                        tools=[], tool_choice=ToolChoice.NONE,
                    )
                    if test_resp.content and "error" not in test_resp.content.lower():
                        rows.append(("model_api", f"OK ({test_resp.usage.get('total', 0)} tokens used)", "green"))
                    else:
                        rows.append(("model_api", f"ERROR: {test_resp.content[:80]}", "red"))
                finally:
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


def _show_model_usage(out: Any) -> None:
    """Show concise usage for /model."""
    out.write("  usage: /model <name>     switch model (e.g. /model gpt-4o-mini)\n")
    out.write("         /model -p <name>   switch + persist to ~/.zall/config.toml\n")
    out.write("         /model -g           show configuration guide\n")
    out.write("         /model             interactive picker\n")


def _show_model_guide(out: Any) -> None:
    """Show detailed guide on configuring models."""
    from zall.cli.render import _shared_console
    is_tty = hasattr(out, "isatty") and out.isatty()
    if is_tty:
        c = _shared_console(out)
        c.print()
        c.print("  [bold]Model Configuration Guide[/]")
        c.print()
        c.print("  [bold]1. Quick switch (in-memory)[/]")
        c.print("    /model <name>          — switch model for this session only")
        c.print("    /model -p <name>       — switch + save to config")
        c.print()
        c.print("  [bold]2. Configure a custom OpenAI-compatible API[/]")
        c.print("    Edit [dim]~/.zall/config.toml[/]:")
        c.print()
        c.print("      [model]")
        c.print('      name = "my-custom-model"')
        c.print('      api_base = "https://your-api-endpoint.com/v1"')
        c.print('      timeout = 300')
        c.print()
        c.print("    Or set environment variables:")
        c.print("      [dim]ZALL_MODEL[/]=my-custom-model")
        c.print("      [dim]ZALL_API_BASE[/]=https://your-api-endpoint.com/v1")
        c.print("      [dim]ZALL_API_KEY[/]=sk-...")
        c.print()
        c.print("  [bold]3. Add a new model alias[/]")
        c.print("    Add to [dim]~/.zall/config.toml[/]:")
        c.print()
        c.print("      [model]")
        c.print("      name = \"my-model\"")
        c.print("      [model.aliases]")
        c.print('      "my" = "my-model"')
        c.print('      "fast" = "my-model-2"')
        c.print()
        c.print("  [bold]4. Supported providers[/]")
        c.print("    [dim]·[/] OpenAI-compatible: any API with /v1/chat/completions endpoint")
        c.print("    [dim]·[/] Anthropic Claude: set ANTHROPIC_API_KEY env var")
        c.print("    [dim]·[/] Google Gemini: set GOOGLE_API_KEY env var")
        c.print("    [dim]·[/] Ollama: local, no key needed")
        c.print()
        c.print("  [dim]Examples:[/]")
        c.print("    /model gpt-4o-mini       → OpenAI cheap model")
        c.print("    /model flash              → alias -> agnes-2.0-flash")
        c.print("    /model -p deepseek-chat   → use DeepSeek, persist it")
        c.print("    /model -g                 → show this guide")
    else:
        out.write("Model Configuration Guide\n")
        out.write("=======================\n\n")
        out.write("1. Quick switch (in-memory):\n")
        out.write("   /model <name>        — switch model for this session only\n")
        out.write("   /model -p <name>     — switch + save to config\n\n")
        out.write("2. Configure custom API in ~/.zall/config.toml:\n")
        out.write("   [model]\n")
        out.write('   name = "my-custom-model"\n')
        out.write('   api_base = "https://your-api.com/v1"\n\n')
        out.write("3. Environment variables:\n")
        out.write("   ZALL_MODEL=my-model ZALL_API_BASE=... ZALL_API_KEY=...\n")


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
        if parts[0] in ("--guide", "-g", "--help", "-h"):
            # Show configuration guide
            _show_model_guide(out)
            return "handled"
        if not model_arg:
            _show_model_usage(out)
            return "handled"
        name = _resolve_model_alias(model_arg)
        state["model"] = name
        provider = _detect_provider(name)
        out.write(f"  model \u2192 {name}  [provider: {_PROVIDER_DISPLAY.get(provider, provider)}]\n")
        if persist:
            _persist_model_to_config(name)
            out.write("  \u2713 persisted to ~/.zall/config.toml\n")
        return "handled"

    cur = state.get("model") or _config_status().get("model") or "(unset)"
    cur_provider = _detect_provider(cur)
    _input_fn = state.get("_input_fn")
    is_tty = hasattr(out, "isatty") and out.isatty() and _input_fn is not None

    # ── Dynamic model discovery ──
    _provider_ready = _detect_configured_providers()
    _custom_providers = load_config().get("providers", [])
    _models = _build_dynamic_model_list(_provider_ready, cur, _custom_providers)
    _all_aliases = set(a for a, *_ in _MODEL_PRESETS)

    _PROVIDER_TAG = {"openai": "O", "anthropic": "C", "gemini": "G", "ollama": "L", "agnes": "A", "deepseek": "O"}
    _PROVIDER_LABEL = {"openai": "OpenAI-compatible", "anthropic": "Anthropic Claude", "gemini": "Google Gemini",
                       "ollama": "Ollama (local)", "agnes": "Agnes AI", "deepseek": "DeepSeek"}

    if not is_tty:
        # ── Plain text output ──
        out.write(f"  current model: {cur}\n")
        out.write(f"  provider: {_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}\n")
        out.write("  available:\n")
        _last_provider = None
        _idx = 0
        for alias, full_name, note, provider, is_configured in _models:
            if provider != _last_provider:
                label = _PROVIDER_LABEL.get(provider, provider)
                out.write(f"  {label}:\n")
                _last_provider = provider
            _idx += 1
            mark = "  ← current" if alias == cur else ""
            out.write(f"    {_idx:2d}. [{_PROVIDER_TAG.get(provider, '?')}] {alias:22s} {note}{mark}\n")
        if cur not in _all_aliases and cur != "(unset)":
            _idx += 1
            out.write(f"    {_idx:2d}. [{_PROVIDER_TAG.get(cur_provider, '?')}] {cur:22s} (current)\n")
        out.write("  usage: /model <name>  (eg. /model gpt-4o-mini, /model flash)\n")
        out.write("         /model -p <name>  (persist to config)\n")
        return "handled"

    # ── Rich TTY output ──
    from zall.cli.render import _shared_console
    c = _shared_console(out)
    c.print(f"  [bold]current model:[/] [cyan]{cur}[/]  [dim]·[/]  {_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}")
    c.print()

    # Group by provider
    _last_provider = None
    _idx = 0
    for alias, full_name, note, provider, is_configured in _models:
        if provider != _last_provider:
            label = _PROVIDER_LABEL.get(provider, provider)
            configured = _provider_ready.get(provider, False)
            if configured:
                c.print(f"  [dim]{label}[/]  [dim]· configured[/]")
            else:
                c.print(f"  [dim]{label}[/]")
            _last_provider = provider
        _idx += 1
        tag = _PROVIDER_TAG.get(provider, "?")
        # Current model gets bold/cyan styling
        if alias == cur:
            c.print(f"    {_idx:2d}. [bold cyan][{tag}][/] [bold cyan]{alias:22s}[/] [dim]{note}[/]  [cyan]← current[/]")
        else:
            cfg_tag = " [dim]· configured[/]" if is_configured else ""
            c.print(f"    {_idx:2d}. [dim][{tag}][/] {alias:22s} [dim]{note}[/]{cfg_tag}")

    # If current model is custom (not in presets), show it too
    if cur not in _all_aliases and cur != "(unset)":
        _idx += 1
        c.print("  [dim]Custom:[/]")
        c.print(f"    {_idx:2d}. [dim][{_PROVIDER_TAG.get(cur_provider, '?')}][/] [bold cyan]{cur:22s}[/] [cyan]← current[/]")

    c.print()
    fn = _input_fn
    if not fn:
        return "handled"
    try:
        sel = (fn("  select [N] / search keyword: ") or "").strip()
    except (EOFError, KeyboardInterrupt):
        c.print()
        return "handled"
    if not sel:
        return "handled"

    # Selection logic: number → preset; keyword → fuzzy match alias or search all
    if sel.isdigit():
        n = int(sel)
        # Build flat list for index lookup
        flat_models = [(a, f) for a, f, _, _, _ in _models]
        if cur not in _all_aliases and cur != "(unset)":
            flat_models.append((cur, cur))
        if 1 <= n <= len(flat_models):
            name = flat_models[n - 1][1]
        else:
            c.print(f"  [red]invalid selection {n}[/], model unchanged")
            return "handled"
    elif sel == "?":
        # Show detailed info about all models
        c.print()
        c.print("  [dim]You can type:[/]")
        c.print("    [dim]·[/] [bold]N[/] — select by number")
        c.print("    [dim]·[/] [bold]keyword[/] — fuzzy match (e.g. 'flash' matches all flash models)")
        c.print("    [dim]·[/] [bold]model name[/] — direct full name (e.g. 'gpt-4o-mini')")
        c.print("    [dim]·[/] /model [bold]-p[/] <name> — persist to config")
        return "handled"
    else:
        # Fuzzy match: search alias, full name, and note
        sel_lower = sel.lower().strip()
        scored: list[tuple[int, str]] = []

        # Scan all presets + custom current model
        for alias, full_name, note, provider, _ in _models:
            full_str = f"{alias} {full_name} {note} {provider}".lower()
            if sel_lower in full_str:
                # Prefer exact alias match over partial match
                score = 3 if sel_lower == alias.lower() else (2 if sel_lower in alias.lower() else (1 if sel_lower in full_name.lower() else 0))
                scored.append((score, full_name))
        if cur not in _all_aliases and cur != "(unset)":
            if sel_lower in cur.lower():
                scored.append((2, cur))

        if not scored:
            # No fuzzy match — try direct resolve_model_alias as fallback
            resolved = _resolve_model_alias(sel)
            # Validate the name has proper chars
            allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/@:")
            if all(c in allowed_chars for c in sel):
                name = resolved
            else:
                c.print(f"  [dim]no match for[/] '{sel}' [dim]— model unchanged[/] [dim](try '?' for help)[/]")
                return "handled"
        else:
            # Multiple fuzzy matches — pick highest score, or warn
            scored.sort(key=lambda x: (-x[0], x[1]))
            name = scored[0][1]
            if len(scored) > 1 and scored[0][0] == scored[1][0]:
                matches = [s[1] for s in scored[:5]]
                c.print(f"  [dim]multiple matches:[/] {', '.join(matches)}")
                c.print(f"  [dim]selected:[/] {name} [dim](use number to pick specific)[/]")

    state["model"] = name
    provider = _detect_provider(name)
    c.print(f"  model → [bold cyan]{name}[/]  [dim]·[/] {_PROVIDER_DISPLAY.get(provider, provider)}")
    return "handled"


@slash_command("/stats", description="show usage statistics (extensions)", category=_CATEGORY_MODEL)
def cmd_stats(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """Display extension-gathered statistics: tool call counts, errors, model info.

    Uses UsageExtension data when available, falls back to loop-level counters.
    E1: 从 extension_registry 读取 UsageExtension + AutoLearnExtension 数据。
    """
    if state is None:
        state = {}
    ext_reg = state.get("_ext_registry")

    # ── UsageExtension ──
    usage_stats: dict[str, Any] = {}
    if ext_reg is not None:
        usage_ext = ext_reg.get("usage_tracker")
        if usage_ext is not None and hasattr(usage_ext, "get_stats"):
            usage_stats = usage_ext.get_stats()

    # ── AutoLearnExtension ──
    learn_stats: dict[str, Any] = {}
    if ext_reg is not None:
        learn_ext = ext_reg.get("auto_learn")
        if learn_ext is not None and hasattr(learn_ext, "get_stats"):
            learn_stats = learn_ext.get_stats()

    # ── Loop-level counters (fallback) ──
    loop_stats = {}
    if loop is not None:
        loop_stats = {
            "steps": getattr(loop, "step_count", 0),
            "tool_calls": getattr(loop, "tool_call_count", 0),
            "model_calls": getattr(loop, "model_call_count", 0),
            "usage_summary": getattr(loop, "tool_usage_summary", {}),
        }

    # ── Render ──
    if hasattr(out, "isatty") and out.isatty():
        from zall.cli.render import _shared_console, _C
        c = _shared_console(out)
        # Model info
        model_name = usage_stats.get("model", "") or state.get("model", "?")
        goal_type = usage_stats.get("goal_type", "")
        c.print(f"  [{_C.ACCENT}]Usage Statistics[/]")
        c.print(f"  model: [bold]{model_name}[/]  goal: [dim]{goal_type}[/]")

        # Extension tool counts
        ext_tool_calls = usage_stats.get("tool_calls", {})
        if ext_tool_calls:
            c.print(f"  tools ({sum(ext_tool_calls.values())} total):")
            for tid, cnt in sorted(ext_tool_calls.items(), key=lambda x: -x[1]):
                errs = usage_stats.get("tool_errors", {}).get(tid, 0)
                err_tag = f" [red]({errs} err)[/]" if errs else ""
                c.print(f"    {tid}: {cnt}{err_tag}")

        # Loop-level counters
        if (loop_stats.get("model_calls") or 0) > 0:  # type: ignore[operator]
            c.print(f"  session: {loop_stats['steps']} steps, "
                    f"{loop_stats['model_calls']} model calls, "
                    f"{loop_stats['tool_calls']} tool calls")

        # Auto-learn patterns
        learn_tool_counts = learn_stats.get("tool_counts", {})
        if learn_tool_counts:
            c.print("  [gold1]Learned Patterns[/]")
            c.print(f"  sessions: [bold]{learn_stats.get('tool_chains', 0)}[/] "
                    f"errors: [bold]{learn_stats.get('error_patterns', 0)}[/]")
            frequent = {t: c for t, c in learn_tool_counts.items() if c >= 3}
            if frequent:
                c.print("  frequent tools (>=3 uses):")
                for t, cnt in sorted(frequent.items(), key=lambda x: -x[1]):
                    c.print(f"    {t}: {cnt}")
    else:
        # Plain text
        out.write("  Usage Statistics\n")
        out.write(f"  model: {usage_stats.get('model', '?')}\n")
        ext_tool_calls = usage_stats.get("tool_calls", {})
        for tid, cnt in sorted(ext_tool_calls.items(), key=lambda x: -x[1]):
            out.write(f"    {tid}: {cnt}\n")
        if (loop_stats.get("model_calls") or 0) > 0:  # type: ignore[operator]
            out.write(f"  steps: {loop_stats['steps']}, "
                      f"model calls: {loop_stats['model_calls']}\n")
    return "handled"

