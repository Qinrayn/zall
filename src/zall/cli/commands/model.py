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

from zall._util.model_registry import (
    _MODEL_PRESETS,
    _PROVIDER_REGISTRY,
    get_provider_display,
    get_provider_tag,
    list_providers,
)
from zall.cli.commands._common import (
    _CATEGORY_MODEL,
    _check_dependency_version,
    _check_disk_space,
    _check_git_health,
    _check_mcp_health,
    _check_network_basic,
    _check_network_http,
    _check_path_tools,
    _check_trust_anchor,
    slash_command,
)
from zall.cli.config import (
    _PROVIDER_DISPLAY,
    _config_status,
    _detect_provider,
    _resolve_model_alias,
)
from zall.cli.environment import CwdMeta as _CwdMeta
from zall.cli.environment import build_system_prompt as _build_system_prompt
from zall.cli.render import _shared_console
from zall.core.context import Context as _Context
from zall.core.model import Message, ToolChoice
from zall.safety.config import CONFIG_DIR, load_config

# ── Dynamic model discovery ──


def _detect_configured_providers() -> dict[str, bool]:
    """Scan env vars and config to detect which providers have valid API keys.

    Returns a dict of provider → bool indicating if it's configured and ready.
    Only marks a provider as configured when its specific key/env is available.
    Fix: global api_key only marks the provider that matches the configured model,
    not both "openai" and "agnes".
    """
    from zall._util.model_registry import get_model_provider
    from zall.cli.config import _get_provider_registry

    configured: dict[str, bool] = {}
    try:
        cfg = load_config()
        api_key = (cfg.get("api_key") or "").strip()
        model_name = (cfg.get("model") or "").strip()
    except Exception:
        cfg = {}
        api_key = ""
        model_name = ""

    # A4 fix: 用合并表 (含自定义 provider), 而非仅内置 _PROVIDER_REGISTRY。
    merged_registry = _get_provider_registry()

    # Determine which provider the global api_key actually belongs to
    global_key_provider: str | None = None
    if api_key and api_key != "your-api-key-here":
        # Infer from configured model name
        if model_name:
            global_key_provider = get_model_provider(model_name, registry=merged_registry)
        else:
            # No model configured -> default to agnes (the default)
            global_key_provider = "agnes"

    for provider, (_display, env_var, _base, _url, _prefixes, _adapter) in merged_registry.items():
        # Check env var first (exact per-provider match)
        if env_var and os.environ.get(env_var, "").strip():
            configured[provider] = True
            continue
        # Check provider-specific config key ([keys].<provider>, 旧字段 fallback)
        prov_key = ""
        if isinstance(cfg, dict):
            _pkeys = cfg.get("provider_keys") or {}
            if isinstance(_pkeys, dict):
                prov_key = str(_pkeys.get(provider) or "").strip()
            prov_key = prov_key or str(cfg.get(f"{provider}_api_key", "") or "").strip()
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
                    # A4 fix: 用自定义 provider 真实名, 而非硬编码 "openai"。
                    result.append((name, name, note, name, True))

    # Sort: configured providers first, then by provider group, then by alias
    _provider_order = {"agnes": 0, "openai": 1, "anthropic": 2, "gemini": 3, "deepseek": 4, "ollama": 5}
    result.sort(key=lambda x: (
        0 if x[4] or x[3] == "ollama" else 1,  # configured/local first
        _provider_order.get(x[3], 50),         # A4: 未知 provider 排在内置之后 (50), 而非最后 (99)
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


def _apply_strict_mode(state: dict[str, Any], loop: Any | None, enabled: bool, out: Any) -> None:
    """设置严格模式 — /strict /fast /mode 共享的单一真相源 (合并去重)。"""
    state["strict"] = enabled
    # 如果当前 loop 支持, 同步更新
    if loop is not None and hasattr(loop, "_strict"):
        loop._strict = enabled
    if enabled:
        out.write("  strict mode \u2192 on (full confirm gates, safe but slower)\n")
    else:
        out.write("  strict mode \u2192 off (auto-confirm, faster)\n")


@slash_command("/mode", description="show/switch interaction mode (strict|fast)", category=_CATEGORY_MODEL)
def cmd_mode(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """统一的模式开关 — 合并 /strict + /fast (二者保留为快捷方式)。

    用法:
      /mode              显示当前模式 (strict / plan)
      /mode strict       启用严格模式 (每步确认, 安全); 别名: safe
      /mode fast         关闭严格模式 (自动确认, 更快); 别名: auto, normal
    """
    if state is None:
        state = {}
    sub = arg.strip().lower()
    if not sub:
        strict_on = bool(state.get("strict", False))
        plan_on = bool(state.get("plan_mode", False))
        out.write(f"  mode: strict={'on' if strict_on else 'off'}, "
                  f"plan={'on' if plan_on else 'off'}\n")
        out.write("  usage: /mode strict | /mode fast    (plan mode: /plan)\n")
        return "handled"
    if sub in ("strict", "safe"):
        _apply_strict_mode(state, loop, True, out)
    elif sub in ("fast", "auto", "normal"):
        _apply_strict_mode(state, loop, False, out)
    else:
        out.write(f"  unknown mode '{sub}' (use: strict | fast)\n")
    return "handled"


@slash_command("/strict", description="shortcut for /mode strict", category=_CATEGORY_MODEL)
def cmd_strict(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """启用严格模式 (等价 /mode strict)。"""
    if state is None:
        state = {}
    _apply_strict_mode(state, loop, True, out)
    return "handled"


@slash_command("/fast", description="shortcut for /mode fast", category=_CATEGORY_MODEL)
def cmd_fast(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """禁用严格模式 (等价 /mode fast)。"""
    if state is None:
        state = {}
    _apply_strict_mode(state, loop, False, out)
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
                import os as _os

                from zall.adapters.openai_compat import OpenAICompatAdapter
                test_adapter = OpenAICompatAdapter(
                    model=model_name,
                    api_key=cfg.get("api_key") or _os.environ.get("ZALL_API_KEY", ""),
                    api_base=cfg.get("api_base") or _os.environ.get("ZALL_API_BASE", ""),
                    timeout=float(cfg.get("timeout", 120.0)),
                )
                try:
                    import time as _time
                    _t0 = _time.time()
                    test_resp = test_adapter.complete(
                        messages=[Message(role="user", content="Say 'ok' in one word.")],
                        tools=[], tool_choice=ToolChoice.NONE,
                    )
                    _lat = _time.time() - _t0
                    if test_resp.content and "error" not in test_resp.content.lower():
                        # 报告延迟: 让用户看到端点慢不慢 (慢端点是响应慢的主因)
                        _lat_color = "green" if _lat < 3 else "warn" if _lat < 8 else "red"
                        rows.append(("model_api", f"OK ({_lat:.1f}s, {test_resp.usage.get('total', 0)} tokens)", _lat_color))
                        if _lat >= 8:
                            rows.append(("model_speed", f"SLOW endpoint (~{_lat:.0f}s/call) — try /provider or a faster model", "warn"))
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


def _print_switch_result(
    res: Any, out: Any, *, show_endpoint: bool = False, as_model_cmd: bool = False,
) -> None:
    """渲染切换结果 (命令共用; 一眼看清"现在用谁、key 从哪来")。

    Argus `_announce_selected` 对标: 切 provider 成功 = "选中仪式" —
    居中 Selected 面板头 + KV 信息表 (model/endpoint/key 来源), 切完即验。
    """
    if not res.ok:
        out.write(f"  \u2717 switch failed: {res.error}\n")
        return
    tag = get_provider_tag(res.provider)
    live = "  [takes effect now]" if res.live else ""
    if as_model_cmd:
        out.write(f"  model \u2192 {res.model}  [provider: {res.display}]{live}\n")
    else:
        out.write(f"  provider \u2192 {res.display} [{tag}]  [model: {res.model}]{live}\n")
    ep = res.endpoint
    if ep is not None:
        # 平时一行带过; 端点/key 有变化 (切换 provider、命令行给了 key/base) 才展开
        show = show_endpoint or ep.base_source.startswith("inline") or ep.key_source.startswith("inline")
        if show and hasattr(out, "isatty") and out.isatty():
            from zall.cli.render import kv_table
            pairs = [("model", res.model)]
            if ep.api_base:
                pairs.append(("endpoint", f"{ep.api_base}  ({ep.base_source})"))
            pairs.append(("key", ep.key_source if ep.key_source != "none" else "NOT SET"))
            kv_table(out, f"selected: {res.display}", pairs,
                     highlight=("model", "endpoint", "key"))
        elif show:
            if ep.api_base:
                out.write(f"    endpoint: {ep.api_base}  ({ep.base_source})\n")
            out.write(f"    key: {ep.key_source if ep.key_source != 'none' else 'NOT SET'}\n")
    for note in res.notes:
        out.write(f"  \u26a0 {note}\n")
    if res.persisted:
        out.write("  \u2713 persisted to ~/.zall/config.toml\n")


def _switch_model_and_report(
    state: dict[str, Any], loop: Any | None, model: str, persist: bool, out: Any,
    *, as_model_cmd: bool = False,
) -> None:
    """切换 model (走统一热切换通路); 目标 adapter 建不起来时只记 model 名。

    统一走 apply_switch 的好处: 正在运行的 loop + 持有旧 adapter 引用的子代理
    工具一并换新 (无需 /clear); 缺 key 等不可恢复错误时降级为纯 state 写入,
    下一轮对话重建 adapter 时生效, 不中断用户。
    """
    from zall.cli.model_switch import apply_switch

    res = apply_switch(state, loop, model=model, persist=persist)
    if res.ok:
        _print_switch_result(res, out, as_model_cmd=as_model_cmd)
        return
    # 降级: 只记 model 名, 等 adapter 可重建时再起效
    state["model"] = _resolve_model_alias(model)
    out.write(f"  ✗ switch failed: {res.error}\n")
    out.write(f"  (model recorded as {state['model']}; "
              f"takes effect on the next new conversation)\n")


def _parse_switch_args(parts: list[str]) -> dict[str, Any]:
    """解析 /provider、/model 的公共参数 (位置参 + key=/base=/model= + -p)。"""
    parsed: dict[str, Any] = {
        "persist": False, "positional": [], "key": "", "base": "", "model": "",
    }
    for p in parts:
        if p in ("-p", "--persist"):
            parsed["persist"] = True
        elif p.startswith("key=") or p.startswith("--key="):
            parsed["key"] = p.split("=", 1)[1].strip()
        elif p.startswith("base=") or p.startswith("--base="):
            parsed["base"] = p.split("=", 1)[1].strip()
        elif p.startswith("model=") or p.startswith("--model="):
            parsed["model"] = p.split("=", 1)[1].strip()
        else:
            parsed["positional"].append(p)
    return parsed


@slash_command("/provider", aliases=("/prov", "/p"),
               description="show/switch model provider (takes effect immediately)",
               category=_CATEGORY_MODEL)
def cmd_provider(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """列出或切换模型提供商。**切换立即生效, 无需 /clear**。

    任意 OpenAI 兼容端点三件套即可接入, 不必预注册:
      /provider mygw base=https://x.com/v1 key=sk-... model=my-model

    用法:
      /provider                       列出所有提供商 (TTY 下可输数字选择) + 配置状态 + 当前
      /provider <name>                切换到该提供商 (自动选其默认模型, 当前会话立即生效)
      /provider <name> <model>        切换提供商 + 指定模型
      /provider <name> -p             切换并持久化 (下次启动仍用它)
      /provider <name> key=sk-...     切换并保存该 provider 的 key (config [keys] 段)
      /provider <name> base=<url> key=<key> [model=<id>]
                                      三件套直接接入 (base URL + API key + model id);
                                      配 -p 时连 provider 一起记住, 之后 /provider <name> 一键切回
    """
    if state is None:
        state = {}
    from zall.cli.model_switch import (
        _KNOWN_GATEWAYS,
        apply_switch,
        probe_models,
        resolve_gateway,
    )

    parsed = _parse_switch_args(arg.split() if arg else [])
    pos = parsed["positional"]
    target = pos[0] if pos else ""
    model_arg = " ".join(pos[1:]).strip() if len(pos) > 1 else ""
    ready = _detect_configured_providers()
    # 合并表 (内置 + [[providers]] 自定义) — 实测反馈: 只列内置 6 家时用户
    # 配好的自定义 provider 看不见, 也不知道"这是啥, 咋换"。
    from zall.cli.config import _get_provider_registry as _merged_reg
    merged = _merged_reg()
    providers = list_providers(merged)  # (key, display, env_var, key_url)

    cur_model = state.get("model") or _config_status().get("model") or ""
    cur_provider = state.get("provider") or (_detect_provider(cur_model) if cur_model else "")

    def _do_switch(prov: str, model: str = "", persist: bool = False,
                   key: str = "", base: str = "") -> str:
        res = apply_switch(
            state, loop, provider=prov, model=model or None, persist=persist,
            inline_key=key or None, inline_base=base or None, persist_key=bool(key),
        )
        _print_switch_result(res, out, show_endpoint=True)
        return "handled"

    def _interactive_switch(prov: str) -> str:
        """选择后切换; 目标 provider 没有 key 时内联询问一次 (回车跳过)。"""
        if not ready.get(prov, False) and prov != "ollama" and _input_fn is not None:
            disp = get_provider_display(prov)
            try:
                k = (_input_fn(f"  API key for {prov} ({disp}) — paste, Enter to skip: ") or "").strip()
            except (EOFError, KeyboardInterrupt):
                c.print()
                return "handled"
            if k:
                return _do_switch(prov, key=k)
        return _do_switch(prov)

    # ── 智能网关 (2026-09-19 反馈: "只能选列表里的提供商") ──
    # 目录名/别名/任意 URL 直接接: base 自动补全, key 询问一次, 模型列表
    # 自动探测并让用户挑 — "三件套"压缩成"一个名字 + 一把 key"。
    is_tty = hasattr(out, "isatty") and out.isatty()
    _input_fn = state.get("_input_fn")

    def _gateway_ask_key(name: str, display: str, base: str) -> str | None:
        """网关接入必问 key; 回车取消整个切换。"""
        inline_key = str(parsed["key"] or "")
        if inline_key:
            return inline_key
        if _input_fn is None:
            out.write(f"  \u2717 {name} needs a key \u2014 non-interactive: "
                      f"/provider {name} key=<key> base={base}\n")
            return None
        try:
            k = (_input_fn(f"  API key for {name} ({display} \u2192 {base}) "
                           f"\u2014 paste, Enter to cancel: ") or "").strip()
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            return None
        if not k:
            out.write("  \u00b7 cancelled\n")
            return None
        return k

    def _gateway_pick_model(base: str, key: str) -> str:
        """探测网关模型列表并让用户挑; 失败不阻断 (落当前模型 + 提示手设)。"""
        ids = probe_models(base, key)
        if not ids:
            out.write(f"  \u26a0 couldn't list models at {base} "
                      f"(key rejected or no /models endpoint)\n")
            out.write("    switch proceeds \u2014 set the model with /model <id>\n")
            return ""
        if cur_model and cur_model in ids:
            out.write(f"  \u00b7 gateway has {len(ids)} models; keeping {cur_model}\n")
            return ""
        if _input_fn is None or not is_tty:
            preview = ", ".join(ids[:8])
            out.write(f"  \u00b7 {len(ids)} models: {preview}"
                      + (" \u2026" if len(ids) > 8 else "") + "\n")
            out.write("    set with /model <id>\n")
            return ""
        shown = ids[:15]
        for i, mid in enumerate(shown, 1):
            out.write(f"    {i:2d}. {mid}\n")
        if len(ids) > len(shown):
            out.write(f"    \u2026 (+{len(ids) - len(shown)} more \u2014 type the id)\n")
        try:
            pick = (_input_fn(f"  model [1-{len(shown)}] / id "
                              f"(Enter = {shown[0]}): ") or "").strip()
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            return ""
        if not pick:
            return shown[0]
        if pick.isdigit() and 1 <= int(pick) <= len(shown):
            return shown[int(pick) - 1]
        return pick

    def _gateway_switch(gw: tuple[str, str, str], model: str, persist: bool) -> str:
        name, base, display = gw
        key = _gateway_ask_key(name, display, base)
        if key is None:
            return "handled"
        if not model:
            model = _gateway_pick_model(base, key)
        res = apply_switch(
            state, loop, provider=name, model=model or None, persist=persist,
            inline_key=key or None, inline_base=base, persist_key=bool(key),
        )
        _print_switch_result(res, out, show_endpoint=True)
        return "handled"

    if target:
        persist = bool(parsed["persist"])
        if parsed["key"]:
            persist = True  # key 只给了这一次机会, 顺带落盘才叫"便捷"
        model_arg = parsed["model"] or model_arg
        # Argus `use N` 对标: /provider 4 直接切列表第 4 家 — 记住序号就能盲切
        if target.isdigit():
            n = int(target)
            if 1 <= n <= len(providers):
                return _do_switch(providers[n - 1][0], model_arg, persist,
                                  parsed["key"], parsed["base"])
            out.write(f"  invalid selection {n} (1-{len(providers)}) — run /provider to list\n")
            return "handled"
        # 不在注册表 → 试智能网关 (目录名/别名/URL); 都不是才走三件套报错
        if target not in merged and target.lower() not in merged:
            gw = resolve_gateway(target)
            if gw is not None:
                return _gateway_switch(gw, model_arg, persist)
        return _do_switch(target, model_arg, persist, parsed["key"], parsed["base"])

    # ── 列出 (TTY 下可交互数字选择) ──
    cur_disp = get_provider_display(cur_provider) if cur_provider else "(unset)"
    if is_tty:
        c = _shared_console(out)
        c.print(f"  [bold]current provider:[/] [accent]{cur_disp}[/]"
                + (f"  [dim]model: {cur_model}[/]" if cur_model else ""))
        c.print()
        for i, (key, display, _env, _url) in enumerate(providers, 1):
            host = str((merged.get(key) or ("", "", "", "", [], ""))[2] or _url or "")
            host_disp = host.replace("https://", "").replace("http://", "").rstrip("/")
            custom = key not in _PROVIDER_REGISTRY
            note = f"{host_disp}  [dim](custom)[/]" if custom else host_disp
            if ready.get(key, False):
                cfg = "[success]\u2713 ready[/]"
            else:
                cfg = "[dim]\u00b7 needs key[/]"
            if key == cur_provider:
                name = f"[accent bold]\u25cf {key}[/]"
                marker = "  [accent]current[/]"
            else:
                name = f"[accent]{key}[/]"
                marker = ""
            c.print(f"    [dim]{i:2d}[/]  [dim][{get_provider_tag(key)}][/] {name}"
                    f"  {cfg}  [dim]{note}[/]{marker}")
        c.print()
        c.print("  [dim]switch:[/] number \u00b7 name \u00b7 gateway name \u00b7 URL \u2014 e.g. "
                "[accent]3[/], [accent]deepseek[/], [accent]zhipu[/], [accent]glm[/], "
                "[accent]https://api.x.ai/v1[/]")
        c.print(f"  [dim]known gateways ({len(_KNOWN_GATEWAYS)}, name + key = done, "
                "base auto):[/] [accent]" + " ".join(sorted(_KNOWN_GATEWAYS)) + "[/]")
        c.print("  [dim]add any gateway:[/] [accent]/provider <name> base=<url> key=<key> "
                "[model=<id>][/]  [dim]\u00b7 -p persists[/]")
        c.print()
        if _input_fn:
            try:
                sel = (_input_fn("  select [N] / provider name (empty to stay): ") or "").strip()
            except (EOFError, KeyboardInterrupt):
                c.print()
                return "handled"
            if not sel:
                return "handled"
            if sel.isdigit():
                n = int(sel)
                if 1 <= n <= len(providers):
                    return _interactive_switch(providers[n - 1][0])
                out.write(f"  invalid selection {n}\n")
                return "handled"
            return _interactive_switch(sel)
        c.print("  [dim]usage: /provider <name>  (e.g. /provider anthropic)  \u00b7 takes effect immediately[/]")
    else:
        out.write(f"  current provider: {cur_disp}\n")
        for i, (key, display, _env, _url) in enumerate(providers, 1):
            cfg = "configured" if ready.get(key, False) else "needs key"
            marker = "  <- current" if key == cur_provider else ""
            host = str((merged.get(key) or ("", "", "", "", [], ""))[2] or _url or "")
            host_disp = host.replace("https://", "").replace("http://", "").rstrip("/")
            custom_tag = " (custom)" if key not in _PROVIDER_REGISTRY else ""
            out.write(f"    {i:2d}. [{get_provider_tag(key)}] {key:12s} {display}"
                      f"  ({cfg})  {host_disp}{custom_tag}{marker}\n")
        out.write("  switch: /provider <name-or-number> [model] [-p] [key=...] [base=...]\n")
        out.write(f"  known gateways ({len(_KNOWN_GATEWAYS)}): {' '.join(sorted(_KNOWN_GATEWAYS))}"
                  "  — name + key is enough (base auto)\n")
        out.write("  add any gateway (three fields): /provider mygw base=https://x.com/v1 key=sk-... model=my-model\n")
    return "handled"


@slash_command("/thinking", description="show/toggle the model's thinking process", category=_CATEGORY_MODEL)
def cmd_thinking(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """开关/查看模型思考过程展示 (like Claude Code)。

    用法:
      /thinking            显示当前状态
      /thinking on         开启 (Anthropic Claude 3.7/4 请求扩展思考, 默认预算 2048)
      /thinking off        关闭
      /thinking <N>        开启并设 thinking 预算为 N token (>=1024)

    说明: DeepSeek-R1 / o1·o3 / Gemini 思考模型无需开关, reasoning 自动展示;
    此开关主要影响 Anthropic Claude。切换下次对话生效 (重建 adapter)。
    """
    import os as _os
    if state is None:
        state = {}
    a = (arg or "").strip().lower()

    def _rebuild_adapter() -> None:
        _ad = state.pop("_adapter", None)
        if _ad is not None and hasattr(_ad, "close"):
            try:
                _ad.close()
            except Exception:
                pass

    if not a:
        budget = _os.environ.get("ZALL_THINKING_BUDGET", "").strip()
        on = bool(budget and budget != "0") or (
            _os.environ.get("ZALL_THINKING", "").strip().lower() in ("1", "true", "yes", "on"))
        if on:
            out.write(f"  thinking: on (budget {budget or '2048'} tokens)\n")
        else:
            out.write("  thinking: off\n")
        out.write("  usage: /thinking on | off | <budget>\n")
        out.write("  note: DeepSeek-R1 / o1 / Gemini thinking models always show reasoning.\n")
        return "handled"

    if a in ("off", "0", "no", "false"):
        _os.environ["ZALL_THINKING"] = "0"
        _os.environ.pop("ZALL_THINKING_BUDGET", None)
        _rebuild_adapter()
        out.write("  thinking \u2192 off\n")
        return "handled"

    if a in ("on", "yes", "true"):
        _os.environ["ZALL_THINKING"] = "1"
        _os.environ.pop("ZALL_THINKING_BUDGET", None)
        _rebuild_adapter()
        out.write("  thinking \u2192 on (budget 2048 tokens; Anthropic Claude 3.7/4)\n")
        return "handled"

    if a.isdigit():
        n = int(a)
        if n < 1024:
            out.write("  thinking budget must be >= 1024 tokens\n")
            return "handled"
        _os.environ["ZALL_THINKING_BUDGET"] = str(n)
        _os.environ["ZALL_THINKING"] = "1"
        _rebuild_adapter()
        out.write(f"  thinking \u2192 on (budget {n} tokens)\n")
        return "handled"

    out.write(f"  unknown option '{arg}'. usage: /thinking on | off | <budget>\n")
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
        # guide check on effective first token (after -p strip), so `/model -p -g` works too
        _first = model_arg.split()[0] if model_arg else ""
        if _first in ("--guide", "-g", "--help", "-h"):
            # Show configuration guide
            _show_model_guide(out)
            return "handled"
        if not model_arg:
            _show_model_usage(out)
            return "handled"
        _switch_model_and_report(state, loop, model_arg, persist, out, as_model_cmd=True)
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

    # de-hardcode: tag/label 派生自 model_registry 单一真相源 (不再在此重复硬编码)
    # A4: 用合并表 (含自定义 provider) 构建标签/显示名, 自定义 provider 也获正确标记。
    from zall.cli.config import _get_provider_registry as _get_merged_registry
    _merged_registry = _get_merged_registry()
    _PROVIDER_TAG = {p: get_provider_tag(p) for p in _merged_registry}
    _PROVIDER_LABEL = {p: get_provider_display(p) for p in _merged_registry}

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

    _switch_model_and_report(state, loop, name, False, out)
    return "handled"


@slash_command("/stats", aliases=("/usage",), description="show usage statistics (extensions)", category=_CATEGORY_MODEL)
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
        from zall.cli.render import _C, _shared_console
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

