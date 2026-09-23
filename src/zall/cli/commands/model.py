"""zall.cli.commands.model — Model & config commands.

Extracted from _legacy.py (v0.2.1 refactor).
Commands: /model, /max-steps, /verbose, /plan

IPR constraints:
  IPR-3: only stdlib + rich, no model SDK
"""

from __future__ import annotations

import os
import platform
import re
import threading
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
from zall.cli.select import Choice, choice_menu, secret_prompt
from zall.core.context import Context as _Context
from zall.core.model import Message, ToolChoice
from zall.safety.config import CONFIG_DIR, load_config

# ── Dynamic model discovery ──


def _is_placeholder_key(key: str) -> bool:
    """占位/示例 key (不是真凭据) 判定 — 别让残留的假 key 把 provider 算成"已配置"。

    实测: config 里留过 anthropic = "sk-secret-456" 这类测试值, 列表因此混进
    Anthropic。按分隔符切段后整段命中标记词才算 (真 key 是无分隔随机串, 撞不上;
    "sk-secret-456" → ["sk","secret","456"] 命中 "secret")。
    """
    k = (key or "").strip().lower()
    if not k or k == "your-api-key-here":
        return True
    markers = {"secret", "placeholder", "changeme", "dummy", "fake", "example",
               "testkey", "yourkey", "xxx", "xxxx", "sk-xxx", "none", "null"}
    segments = {s for s in re.split(r"[^a-z0-9]+", k) if s}
    return bool(segments & markers) or k in markers


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
    if api_key and not _is_placeholder_key(api_key):
        # Infer from configured model name
        if model_name:
            global_key_provider = get_model_provider(model_name, registry=merged_registry)
        else:
            # No model configured -> default to agnes (the default)
            global_key_provider = "agnes"

    for provider, (_display, env_var, _base, _url, _prefixes, _adapter) in merged_registry.items():
        # Check env var first (exact per-provider match)
        _env_key = os.environ.get(env_var, "").strip() if env_var else ""
        if _env_key and not _is_placeholder_key(_env_key):
            configured[provider] = True
            continue
        # Check provider-specific config key ([keys].<provider>, 旧字段 fallback)
        prov_key = ""
        if isinstance(cfg, dict):
            _pkeys = cfg.get("provider_keys") or {}
            if isinstance(_pkeys, dict):
                prov_key = str(_pkeys.get(provider) or "").strip()
            prov_key = prov_key or str(cfg.get(f"{provider}_api_key", "") or "").strip()
        if prov_key and not _is_placeholder_key(prov_key):
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


_ROW_WARN_NOT_LIVE = "not in upstream /models"


def _probe_configured_providers(providers: list[str]) -> dict[str, list[str]]:
    """并行探测多个 provider 的上游 /models (3s 超时, 失败静默)。

    返回 {provider: [model_id, ...]}; 没返回的 provider 即探测失败/不支持
    (Anthropic 原生端点、网关未实现 /models 等) — 调用方回落预设列表。

    G7: 探测顺带收集 context_length 注入 model_registry._LIVE_WINDOWS —
    /models 元数据是窗口大小的**事实来源** (如 sensenova 网关 deepseek-v4-flash
    实为 1M, 内置表只写过 128k 旧值), 使 footer/水位/压缩用真实窗口。
    """
    from concurrent.futures import ThreadPoolExecutor

    from zall._util.model_registry import set_live_windows
    from zall.cli.model_switch import probe_models, provider_endpoint

    lock = threading.Lock()
    windows: dict[str, int] = {}

    def _one(p: str) -> tuple[str, list[str] | None]:
        try:
            ep = provider_endpoint(p)
            if not ep.api_base:
                return p, None
            _w: dict[str, int] = {}
            ids = probe_models(ep.api_base, ep.api_key, timeout=3.0,
                               windows_out=_w)
            with lock:
                windows.update(_w)
            return p, ids
        except Exception:
            return p, None

    found: dict[str, list[str]] = {}
    try:
        with ThreadPoolExecutor(max_workers=min(6, len(providers))) as pool:
            for prov, ids in pool.map(_one, providers):
                if ids:
                    found[prov] = list(ids)
    except Exception:
        return {}
    # G7: 本次探测结果整体替换 live 表 (upstream 元数据变更后不残留旧值)
    try:
        set_live_windows(windows)
    except Exception:
        pass
    return found


def _build_display_rows(
    models: list[tuple[str, str, str, str, bool]],
    avail: dict[str, list[str]],
    cur: str,
    configured_any: bool,
) -> tuple[list[tuple[str, str, str, str, bool]], list[str]]:
    """把全量预设裁剪成"这台机器能用的模型"。

    用户口径: 只配了商汤, 不该被一长串别家预设刷屏; 列表要列可用模型。
      - 已配置且探到上游 /models → 只列 live id (note 沿用同名预设的说明),
        配置里上游已撤的 id 不再出现;
      - 已配置但探不到 (Anthropic/Ollama 类端点) → 照旧列预设;
      - 未配置的 provider → 只要有一个已配置就折叠成一行提示 (全新安装一个
        都没配时保持全量预设, 否则列表空得没法选);
      - 当前模型不在 live 列表里 (自定义名/上游已撤) 补一行并标警示 —
        "现在用的是什么"永远可见。
    返回 (rows, 被折叠的 provider)。
    """
    notes: dict[tuple[str, str], str] = {}
    for alias, full_name, note, provider, _ in models:
        notes.setdefault((provider, full_name), note)
        notes.setdefault((provider, alias), note)

    rows: list[tuple[str, str, str, str, bool]] = []
    hidden: list[str] = []
    emitted: set[str] = set()
    for alias, full_name, note, provider, is_configured in models:
        ids = avail.get(provider)
        if ids is not None:
            if provider in emitted:
                continue
            emitted.add(provider)
            has_cur = False
            for mid in ids:
                rows.append((mid, mid, notes.get((provider, mid), ""), provider, True))
                has_cur = has_cur or mid == cur
            if not has_cur and (alias == cur or full_name == cur):
                rows.append((alias, full_name, _ROW_WARN_NOT_LIVE, provider, True))
            continue
        if not is_configured and configured_any:
            if provider not in emitted:
                emitted.add(provider)
                hidden.append(provider)
            continue
        rows.append((alias, full_name, note, provider, is_configured))
    return rows, hidden


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
        c.print("  [bold]4. Models & providers[/]")
        c.print("    [dim]·[/] any OpenAI-compatible API: /provider → 「+ 添加新网关」菜单向导")
        c.print("      (menu asks key once, autodetects models, saves everything)")
        c.print("    [dim]·[/] built-in shortcut catalog (14 gateways): /provider zhipu/qwen/kimi/...")
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
    *, as_model_cmd: bool = False, provider: str | None = None,
) -> None:
    """切换 model (走统一热切换通路); 目标 adapter 建不起来时只记 model 名。

    统一走 apply_switch 的好处: 正在运行的 loop + 持有旧 adapter 引用的子代理
    工具一并换新 (无需 /clear); 缺 key 等不可恢复错误时降级为纯 state 写入,
    下一轮对话重建 adapter 时生效, 不中断用户。
    """
    from zall.cli.model_switch import apply_switch

    # provider 显式给出时用于上游 live id (裸模型名推断不出归属); 预设走原推断。
    res = apply_switch(state, loop, model=model, provider=provider or None,
                       persist=persist)
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

    Kimi CLI 交互口径: 交互式全是菜单向导 (↑↓ 选择 · 隐藏贴 key · 自动探测
    模型), 不用手写任何 key=/base=/model=; 交互接入自动持久化, 下次启动还在。

    用法:
      /provider                       交互菜单: 列出所有提供商 + 配置状态 + 当前,
                                      选项底部有「＋ 添加新网关」向导入口
      /provider <name>                切换到该提供商 (自动选其默认模型, 立即生效)
      /provider <name> <model>        切换提供商 + 指定模型
      /provider <name> -p             切换并持久化 (下次启动仍用它)
      /provider <name> key=sk-...     切换并保存该 provider 的 key (config [keys] 段)
      /provider <name> base=<url> key=<key> [model=<id>]
                                     高级用法 (脚本/非交互): 直接给出网关三要素;
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
        """菜单选中后切换; 目标 provider 没有 key 时隐藏式询问一次。

        交互 = 用户明确在选: 一律持久化 (对齐 Kimi /model 写回 config),
        下次启动仍在; 缺 key 回车跳过则只切不存 key。
        """
        if not ready.get(prov, False) and prov != "ollama" and _input_fn is not None:
            disp = get_provider_display(prov)
            try:
                k = secret_prompt(f"  API key for {prov} ({disp}) — paste, Enter to skip: ",
                                  input_fn=_input_fn, is_tty=is_tty)
            except (EOFError, KeyboardInterrupt):
                return "handled"
            if k:
                return _do_switch(prov, persist=True, key=k)
        return _do_switch(prov, persist=True)

    # ── 智能网关 (2026-09-19 反馈: "只能选列表里的提供商"; v0.7 → Kimi 向导) ──
    # 交互路径只做三件事: 选平台 → 贴 key → 选模型, 全程菜单 + 隐藏输入;
    # base 自动补全, key 必问, 模型经 /models 探测 — "三件套"压缩成
    # "一个菜单 + 一把 key", 交互接入自动持久化 (重启还在)。
    is_tty = hasattr(out, "isatty") and out.isatty()
    _input_fn = state.get("_input_fn")

    def _gateway_ask_key(name: str, display: str, base: str) -> str | None:
        """网关接入必问 key (隐藏输入, Kimi 口径); 回车取消整个切换。"""
        inline_key = str(parsed["key"] or "")
        if inline_key:
            return inline_key
        if _input_fn is None:
            out.write(f"  \u2717 {name} needs a key \u2014 non-interactive: "
                      f"/provider {name} key=<key> base={base}\n")
            return None
        try:
            k = secret_prompt(f"  Enter API key for {name} ({display} \u2192 {base}) "
                              f"\u2014 paste, Enter to cancel: ",
                              input_fn=_input_fn, is_tty=is_tty)
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            return None
        if not k:
            out.write("  \u00b7 cancelled\n")
            return None
        return k

    def _gateway_pick_model(base: str, key: str) -> str:
        """探测网关模型列表 (Kimi: "Verifying API key..." 流程) 并菜单让用户挑。

        探测失败不阻断 (落 "" → 当前模型 + 提示手设); 交互路径用 ↑↓ 菜单
        (Kimi "Select a model"), 非交互/无输入栈降级输出列表 + 提示。
        """
        out.write("  Verifying API key\u2026\n")
        windows: dict[str, int] = {}
        ids = probe_models(base, key, windows_out=windows)
        if windows:
            try:
                from zall._util.model_registry import set_live_windows
                set_live_windows(windows)
            except Exception:
                pass
        if not ids:
            out.write(f"  \u26a0 couldn't list models at {base} "
                      f"(key rejected or no /models endpoint)\n")
            out.write("    \u00b7 switch proceeds \u2014 set the model with /model <id>\n")
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
        pick = choice_menu(
            out, f"Select a model \u2014 \u00b7 {len(ids)} at {base} (Enter=pick, Ctrl+C=cancel):",
            [(mid, mid, "") for mid in ids],
            default_index=0,
        )
        if pick is None:
            return ""
        return pick

    def _gateway_switch(gw: tuple[str, str, str], model: str, persist: bool) -> str:
        """智能网关接入: key → 验证/选模型 → 切换 (交互路径默认持久化)。"""
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

    def _gateway_wizard() -> str:
        """「＋ 添加新网关」向导 (Kimi setup 口径): 平台 → URL → key → 模型。

        全部交互输入, 结束时 apply_switch(persist=True) 一步落盘
        ([[providers]] + [keys] + default model), 重启仍在。
        """
        # 1) 平台: 内置目录 + 自定义 URL 一项
        platform_choices: list[Choice] = [
            (name, f"{display}  \u00b7  {base}", "")
            for name, (base, display, _a) in sorted(_KNOWN_GATEWAYS.items())
        ]
        platform_choices.append(("__url__", "\uff0b custom URL (any OpenAI-compatible API)", ""))
        name = choice_menu(
            out,
            "Select a platform \u2014 Enter=pick, Ctrl+C=cancel:",
            platform_choices,
        )
        if name is None:
            return "handled"
        if name == "__url__":
            # 自定义 URL: 提示给出示例, 允许裸 host (自动补 https:// + /v1)
            if _input_fn is None:
                out.write("  \u2717 custom gateway needs TTY \u2014 non-interactive: "
                          "/provider <name> base=<url> key=<key>\n")
                return "handled"
            try:
                raw = (_input_fn("  Base URL (e.g. https://api.example.com/v1) "
                                 "\u2014 Enter to cancel: ") or "").strip()
            except (EOFError, KeyboardInterrupt):
                out.write("\n")
                return "handled"
            if not raw:
                out.write("  \u00b7 cancelled\n")
                return "handled"
            gw = resolve_gateway(raw)
            if gw is None:
                out.write(f"  \u2717 cannot parse URL '{raw}' \u2014 need "
                          "https://host[/path]\n")
                return "handled"
            name, base, display = gw
        else:
            base, display, _aliases = _KNOWN_GATEWAYS[name]
        # 2) key (隐藏输入) → 验证 + 模型菜单 → 切换 (persist=True 落盘)
        return _gateway_switch((name, base, display), model_arg, persist=True)

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

    # ── 列出 (TTY 下可交互选择: 菜单 ↑↓ / 数字 / 名字直输) ──
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
        c.print(f"    [dim]{len(providers) + 1:2d}[/]  [success]\uff0b 添加新网关[/]"
                "  [dim]\u00b7 any OpenAI-compatible API[/]")
        c.print()
        c.print("  [dim]switch:[/] number \u00b7 name \u00b7 gateway name \u00b7 URL \u2014 e.g. "
                "[accent]3[/], [accent]deepseek[/], [accent]zhipu[/], [accent]glm[/], "
                "[accent]https://api.x.ai/v1[/]")
        c.print("  [dim]add any OpenAI-compatible API:[/] 选 [success]+\u4e00[/]"
                " (或直接贴 URL) \u2014 菜单问 key、自动探测模型、自动记住")
        c.print()
        if _input_fn:
            # Kimi 交互口径: ↑↓ 菜单主路径; 也可以输数字 / 名字 / URL 直切
            menu_choices: list[Choice] = [
                (key, f"{key}  \u00b7  {display}  \u00b7  "
                      + ("\u2713 ready" if ready.get(key, False) else "\u00b7 needs key"),
                 "")
                for key, display, _env, _url in providers
            ]
            menu_choices.append(("__url__", "\uff0b 添加新网关 \u00b7 any OpenAI-compatible API", ""))
            sel = choice_menu(
                out,
                "Select a provider \u2014 Enter=pick, Ctrl+C=cancel:",
                menu_choices,
            )
            if sel is None:
                return "handled"
            if sel == "__url__":
                return _gateway_wizard()
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
        out.write("  switch: /provider <name-or-number> [model] [-p]\n")
        out.write(f"  known gateways ({len(_KNOWN_GATEWAYS)}): {' '.join(sorted(_KNOWN_GATEWAYS))}"
                  "  - gateway name is enough (base auto)\n")
        out.write("  add any OpenAI-compatible API: run /provider in the REPL — the menu\n"
                  "  asks the key once, auto-detects models, saves everything.\n")
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

    # ── 上游可用性探测 (列表只认上游 /models) ──
    # 配置里"支持"≠ 上游"还在" — sensenova 下线 deepseek-chat、agnes-2.5
    # 曾 503 都是配置先行、上游已撤。对每个"已配置"的 provider 并行探一次
    # /models (3s 超时, 失败静默): 探到的 live id 直接作为该组可选模型列出,
    # 列表呈现"你能用的"。未配置的 provider 不探 (没 key, 探了也是 401)。
    _probe_targets = [p for p, ready in _provider_ready.items() if ready]
    _avail_by_provider = (_probe_configured_providers(_probe_targets)
                          if _probe_targets else {})
    # Ollama 是本地服务: "配好了"= 本机真在跑 (探到 /models); 没跑就不占列表
    if _provider_ready.get("ollama") and not _avail_by_provider.get("ollama"):
        _provider_ready["ollama"] = False

    _models = _build_dynamic_model_list(_provider_ready, cur, _custom_providers)
    _all_aliases = set(a for a, *_ in _MODEL_PRESETS)

    # de-hardcode: tag/label 派生自 model_registry 单一真相源 (不再在此重复硬编码)
    # A4: 用合并表 (含自定义 provider) 构建标签/显示名, 自定义 provider 也获正确标记。
    from zall.cli.config import _get_provider_registry as _get_merged_registry
    _merged_registry = _get_merged_registry()
    _PROVIDER_TAG = {p: get_provider_tag(p) for p in _merged_registry}
    _PROVIDER_LABEL = {p: get_provider_display(p) for p in _merged_registry}

    # 显示行 = 裁剪后的可用模型 (见 _build_display_rows); 未配置的 provider 折叠
    _rows, _hidden_providers = _build_display_rows(
        _models, _avail_by_provider, cur, configured_any=any(_provider_ready.values()))
    # live id → provider: 选中裸模型 id 时显式带上归属 provider
    _live_provider = {mid: p for p, ids in _avail_by_provider.items() for mid in ids}
    _row_names = {r[1] for r in _rows}

    if not is_tty:
        # ── Plain text output ──
        out.write(f"  current model: {cur}\n")
        out.write(f"  provider: {_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}\n")
        out.write("  available:\n")
        _last_provider = None
        _idx = 0
        for alias, full_name, note, provider, is_configured in _rows:
            if provider != _last_provider:
                label = _PROVIDER_LABEL.get(provider, provider)
                _ids = _avail_by_provider.get(provider)
                out.write(f"  {label}{f' ({len(_ids)} live upstream)' if _ids else ''}:\n")
                _last_provider = provider
            _idx += 1
            mark = "  ← current" if alias == cur else ""
            out.write(f"    {_idx:2d}. [{_PROVIDER_TAG.get(provider, '?')}] {alias:22s} {note}{mark}\n")
        if _hidden_providers:
            _names = ", ".join(_PROVIDER_LABEL.get(p, p) for p in _hidden_providers)
            out.write(f"  · not configured: {_names}  (/provider to connect)\n")
        if cur not in _all_aliases and cur != "(unset)" and cur not in _row_names:
            _idx += 1
            out.write(f"    {_idx:2d}. [{_PROVIDER_TAG.get(cur_provider, '?')}] {cur:22s} (current)\n")
        out.write("  usage: /model <name>  (eg. /model gpt-4o-mini, /model flash)\n")
        out.write("         /model -p <name>  (persist to config)\n")
        return "handled"

# ── Rich TTY output: ↑↓ 菜单主路径 (Kimi 交互口径) ──
    # 列表裁剪与上游探测已由 _build_display_rows 完成; 这里把"这台机器能
    # 用的模型"直接作为菜单项: ↑↓/jk 移动 · 数字直选 · 打字即过滤 · Enter
    # 确认; 过滤无匹配时 Enter 把输入原文交回 (自由文本 = 直接输名字切换,
    # 另做字符校验, 防止把 state.model 污染成垃圾名)。
    from zall.cli.render import _shared_console
    from zall.cli.select import choice_menu as _pick

    c = _shared_console(out)
    c.print(f"  [bold]current model:[/] [cyan]{cur}[/]  [dim]·[/]  "
            f"{_PROVIDER_DISPLAY.get(cur_provider, cur_provider)}")
    c.print()

    _menu_choices: list[Choice] = []
    _cur_idx = 0
    for i, (alias, full_name, note, provider, _is_cfg) in enumerate(_rows):
        _parts = [_PROVIDER_LABEL.get(provider, provider)]
        if note and note != _ROW_WARN_NOT_LIVE:
            _parts.append(note)
        if full_name == cur or alias == cur:
            _parts.append("\u2190 current")
            _cur_idx = i
        if note == _ROW_WARN_NOT_LIVE:
            _parts.append(f"\u26a0 {note}")
        _menu_choices.append((full_name, alias, " \u00b7 ".join(dict.fromkeys(_parts))))
    # 当前模型不在显示行 (自定义名/上游刚撤) → 补一行, "现在用的是什么"可见
    if cur != "(unset)" and cur not in _row_names and cur not in _all_aliases:
        _menu_choices.append((cur, cur, "custom \u00b7 \u2190 current"))
        _cur_idx = len(_menu_choices) - 1

    _sel = _pick(
        out,
        f"Select a model \u2014 {len(_menu_choices)} available \u00b7 "
        "\u2191\u2193 move \u00b7 type to filter \u00b7 Enter=pick, Ctrl+C=cancel",
        _menu_choices,
        default_index=_cur_idx,
        free_text=True,
        # ptk 渲染失败时降级单行选择 — 读取走 REPL 注入的输入 (可测)
        input_fn=_input_fn,
    )
    if not _sel:
        return "handled"  # 取消 — 什么都不改

    if _sel in {v for v, _l, _d in _menu_choices}:
        name = _sel
    else:
        # 自由文本 (过滤无匹配 Enter 回传): 直接按名字切换, 校验防止污染
        if _sel.startswith("/"):
            c.print(f"  [yellow]'{_sel}'[/] [dim]是命令, 不是模型名 — "
                    "先 Ctrl+C/回车退出选择, 再到提示符运行[/dim]")
            return "handled"
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/@:")
        if not all(ch in allowed_chars for ch in _sel):
            c.print(f"  [dim]no match for[/] '{_sel}' [dim]— model unchanged[/dim]"
                    " [dim](在菜单里输入名字过滤, 或 /model <名字>)[/dim]")
            return "handled"
        name = _resolve_model_alias(_sel)

    _switch_model_and_report(state, loop, name, False, out,
                             provider=_live_provider.get(name))
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

