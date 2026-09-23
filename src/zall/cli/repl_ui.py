"""zall.cli.repl_ui — REPL interactive loop (extracted from app.py).

Corresponds to:
  §9.2.1  Goal confirmation
  §4.3    Context cut
  §4.5    ConfirmGate
  §9.2.7  Skills (slash-command expansion)
  §9.2.11 MCP tools lifecycle

IPR constraints:
  IPR-3: only stdlib + rich + prompt_toolkit, no model SDK
"""

from __future__ import annotations

import sys
import time
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger
from zall._util.term import ensure_new_line as _ensure_new_line  # G10: 提示符行首保证
from zall.cli.commands import (
    _route_skill,
    _setup_completion,
    get_known_commands,
    handle_slash,
)
from zall.cli.config import _detect_provider, _onboarding
from zall.cli.file_complete import expand_at_references
from zall.cli.orchestrator import build_mcp_tools  # noqa: F401  (保留导出: 测试/插件 patch 面)
from zall.cli.orchestrator import make_usage_observer as _make_usage_observer
from zall.cli.prompt import make_prompt_fn
from zall.cli.render import _C, CliRenderer, _shared_console, clear_console_cache
from zall.cli.responder import CliUserResponder
from zall.core.builder import AgentBuilder
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import ModelCompactor
from zall.core.context import Context
from zall.core.loop import TRANSIENT_KEYWORDS, AgentLoop, is_transient_error
from zall.core.model import Message
from zall.mcp.tool import MCPTool
from zall.safety.rules_file import load_rules
from zall.skills import Skill, load_skills
from zall.tools.git_protect import GitProtect

_log = _get_zall_logger(__name__)

__all__ = [
    "REPL_MAX_STEPS",
    "TRANSIENT_KEYWORDS",
    "_make_usage_observer",
    "_print_banner",
    "_prompt",
    "build_repl_loop",
    "is_transient_error",
    "repl",
]

# REPL 对话态步数max: 100000 等价"无max"
REPL_MAX_STEPS = 100_000

# 瞬态(可重试)错误判定 — 真相源在 core.loop (run/REPL/TUI 三路径共用);
# TRANSIENT_KEYWORDS / is_transient_error 已从顶部 import 并通过 __all__ re-export。


def _prompt(state: dict[str, Any]) -> str:
    """Context-aware prompt: shows model name + plan mode."""
    model_name = state.get("model") or ""
    plan_mode = state.get("plan_mode", False)
    if model_name:
        base = f"({model_name})"
    else:
        base = "zall"
    if plan_mode:
        base += " [plan]"
    return f"{base} \u25b8 "


def _read_multiline_input(prompt: str, input_fn: Any) -> str | None:
    """Read input with multi-line support (fallback for when prompt_toolkit is unavailable).

    Supports:
    - Backslash continuation: line ending with ``\\`` shows continuation prompt (``... ``)
      and joins the next line(s). Multiple backslash continuations are supported.
    - Paste detection: input containing ``\\n`` (paste from clipboard) is accepted directly
      as-is, with embedded newlines preserved.
    - Single-line input: unchanged behavior (identity).

    This function is a safety net for the bare ``input()`` fallback path.
    When prompt_toolkit is available, ``make_prompt_fn`` already handles multi-line
    via Alt-Enter, backslash, double-enter, and unclosed-paren detection.
    """
    line = input_fn(prompt)
    if line is None:
        return None
    if "\n" in line:
        # Paste detection: multi-line paste accepted directly
        return line
    stripped = line.rstrip()
    if stripped.endswith("\\"):
        parts = [stripped[:-1]]
        while True:
            try:
                next_line = input_fn("... ")
            except (EOFError, KeyboardInterrupt):
                break
            if next_line is None:
                break
            next_stripped = next_line.rstrip()
            if next_stripped.endswith("\\"):
                parts.append(next_stripped[:-1])
            else:
                parts.append(next_stripped)
                break
        return "".join(parts)
    return line


def _print_banner(out: Any, *, model: str | None, branch: str | None,
                  max_steps: int, verbose: bool, plan: bool = False) -> None:
    """REPL 启动屏 — Codex 式信息盒 (v0.6: 左对齐键值, 弃居中字距版)。

    布局对标 Codex CLI:
      ╭──────────────────────────────╮
      │ >_ zall (vX.Y.Z)             │
      │                              │
      │ model:     <model>  /model   │
      │ directory: ~/zall            │
      │ branch:    master            │
      ╰──────────────────────────────╯
        Tip: /help commands · ! shell · Ctrl-R history · Ctrl-D exit
    唯一一行启动提示; 键位已由常驻 footer 承担, 不再叠第二行。
    非 TTY (管道/CI) 降级为单行文本 — 输出契约不变。
    """
    try:
        import os as _os
        if _os.name == "nt":
            _os.system("cls")
        else:
            out.write("\033[2J\033[H")
            out.flush()
    except Exception:
        pass
    console = _shared_console(out)
    if model:
        display_model = model
    else:
        from zall.cli.config import _config_status
        display_model = _config_status().get("model") or "unset"
    from zall import __version__

    from zall.cli.render import _is_tty
    if not _is_tty(out):
        console.print(f"zall  v{__version__}  \u00b7  {display_model}")
        return

    from rich import box as _box
    from rich.align import Align as _Align
    from rich.console import Group as _Group
    from rich.panel import Panel as _Panel
    from rich.text import Text as _Text

    try:
        from zall.cli.render import is_ascii_glyphs
        ascii_mode = is_ascii_glyphs()
    except Exception:
        ascii_mode = False

    # 目录显示: home 折叠为 ~ (Codex 口径)
    try:
        cwd_str = str(__import__("pathlib").Path.cwd())
        home_str = str(__import__("pathlib").Path.home())
        if cwd_str == home_str:
            cwd_disp = "~"
        elif cwd_str.startswith(home_str + __import__("os").sep):
            cwd_disp = "~" + cwd_str[len(home_str):]
        else:
            cwd_disp = cwd_str
    except Exception:
        cwd_disp = "."

    def _kv(label: str, value: str, hint: str = "") -> _Text:
        t = _Text()
        t.append(f"{label:<11}", style=_C.DIM)
        t.append(value, style=_C.STATUS_BAR_TEXT)
        if hint:
            t.append(f"   {hint}", style=_C.DIM)
        return t

    header = _Text()
    header.append(">_", style=_C.DIM)
    header.append(" zall ", style=f"bold {_C.ACCENT}")
    header.append(f"(v{__version__})", style=_C.DIM)

    body: list[Any] = [header, _Text("")]
    body.append(_kv("model:", display_model, "/model to change"))
    body.append(_kv("directory:", cwd_disp))
    if branch:
        body.append(_kv("branch:", branch))
    if plan:
        body.append(_kv("mode:", "plan (read-only)"))
    if verbose:
        body.append(_kv("verbose:", "on"))

    panel = _Panel(
        _Group(*body),
        box=_box.ASCII if ascii_mode else _box.ROUNDED,
        border_style=_C.SUBTLE,
        padding=(0, 2),
        expand=False,
    )
    console.print()
    console.print(_Align(panel, align="center"))
    # Tip 行 (Codex "Tip:" 对标) — 与常驻 footer (/ commands · @ files · ? shortcuts)
    # 互补: 这里只留 footer 没有的逃生键位, 启动屏不堆第二行静态提示。
    console.print(
        f"  [{_C.SUBTLE}]Tip:[/] "
        f"[{_C.DIM}]/help commands \u00b7 ! shell \u00b7 Ctrl-R history \u00b7 Ctrl-D exit[/]"
    )
    console.print()


def _format_status_context(state: dict[str, Any]) -> str:
    """上下文剩余短句 ("62% left") — 供状态行/命令后回显 (Codex footer 口径)。"""
    ctx = int(state.get("ctx_tokens", 0) or 0)
    if not ctx:
        return ""
    model = str(state.get("model") or "")
    try:
        from zall._util.model_registry import get_window_size
        from zall.core.cache_stats import context_remaining_percent
        pct = context_remaining_percent(ctx, int(get_window_size(model) or 0))
    except Exception:
        pct = None
    return f"ctx {pct}% left" if pct is not None else f"ctx {ctx} tok"


def _format_status_cache(state: dict[str, Any]) -> str:
    """缓存命中短句 — CacheStats 优先, 回落 usage 累计 (无数据 → 空串)。"""
    stats = state.get("cache_stats")
    if stats is not None and getattr(stats, "has_cache_data", False):
        try:
            return stats.format_summary(with_write=False)
        except Exception:
            pass
    usage = state.get("usage") or {}
    cached = int(usage.get("cached", 0) or 0)
    prompt = int(usage.get("prompt", 0) or 0)
    if cached and prompt:
        return f"cache {round(cached * 100 / prompt)}%"
    return ""


def _run_shell_passthrough(cmd: str, out: Any) -> None:
    """`!cmd` — 直接执行 shell 命令 (Codex "! for shell commands" 对标)。

    这是用户显式敲的命令 (不是模型提议), 所以不进确认门; 走 zall 自己的 bash
    执行器 (同一 shell 选择/超时/截断纪律), 输出只进 transcript —
    不进模型上下文 (与 Codex 语义一致: 想看就自己贴回去)。
    """
    if not cmd:
        out.write("  usage: !<shell command>   (runs directly, bypassing the agent)\n")
        out.flush()
        return
    try:
        from zall.tools.bash import BashTool
        result = BashTool().execute({"command": cmd, "timeout": 120})
    except Exception as e:  # 执行器异常不吞: 明确报出
        out.write(f"  \u2717 shell error: {e}\n")
        out.flush()
        return
    body = (result.output or "").rstrip("\n")
    out.write(f"  $ {cmd}\n")
    # BashTool 的 output 是给模型看的 (首行 exit_code + stdout:/stderr: 段头);
    # 直接执行时剥掉这些包装, 只留命令自己的输出 (stderr 段保留并标出)。
    lines = body.split("\n") if body else []
    if lines and lines[0].startswith("exit_code:"):
        lines = lines[1:]
    clean: list[str] = []
    in_stderr = False
    _err_prefix = "\u2502 "
    for ln in lines:
        if ln.strip() == "stdout:":
            in_stderr = False
            continue
        if ln.strip() == "stderr:":
            in_stderr = True
            continue
        clean.append(_err_prefix + ln if in_stderr else ln)
    body = "\n".join(clean).strip("\n")
    if body:
        out.write(body + "\n")
    code = (getattr(result, "artifacts", None) or {}).get("exit_code")
    if code is None:
        code = 0 if getattr(result, "success", False) else 1
    marker = "\u2713" if int(code) == 0 else "\u2717"
    out.write(f"  {marker} exit {int(code)}\n")
    out.flush()


def _format_turn_usage(state: dict[str, Any]) -> str:
    """会话累计用量行 (Codex FinalOutput 展示口径, verbose 时逐回合打印)。"""
    usage = state.get("usage") or {}
    if not usage:
        return ""
    from zall.core.cache_stats import format_tokens
    prompt = int(usage.get("prompt", 0) or 0)
    cached = int(usage.get("cached", 0) or 0)
    completion = int(usage.get("completion", 0) or 0)
    blended = max(0, prompt - cached) + completion
    line = f"session tokens: total {format_tokens(blended)} input {format_tokens(max(0, prompt - cached))}"
    if cached:
        line += f" (+ {format_tokens(cached)} cached)"
    line += f" output {format_tokens(completion)}"
    cache_note = _format_status_cache(state)
    if cache_note:
        line += f" \u00b7 {cache_note}"
    return line


def _echo_status(state: dict[str, Any]) -> None:
    """命令后回显状态行 (Argus _print_status_bar 纪律)。

    吸收轮: 回显前先把实时用量 (上下文剩余/缓存命中) 推给 renderer —
    用户每条命令后都能看到"现在什么状态"。
    renderer 缺席/非 TTY 时静默 — 管道与 CI 输出契约不受影响。
    """
    renderer = state.get("_renderer")
    if renderer is None or not hasattr(renderer, "render_status_bar"):
        return
    try:
        if hasattr(renderer, "update_status"):
            from zall.cli.environment import get_cached_cwd_meta
            loop = state.get("_loop")
            goal = ""
            try:
                if loop is not None and hasattr(getattr(loop, "goal", None), "statement"):
                    goal = loop.goal.statement.goal_type.value
            except Exception:
                goal = ""
            try:
                branch = get_cached_cwd_meta(state).git_branch or ""
            except Exception:
                branch = ""
            renderer.update_status(
                model=str(state.get("model") or ""),
                branch=branch,
                goal=goal,
                plan=bool(state.get("plan_mode", False)),
                context=_format_status_context(state),
                cache=_format_status_cache(state),
            )
        renderer.render_status_bar(force=True)
    except Exception:
        pass


def build_repl_loop(
    first_input: str,
    state: dict[str, Any],
    yes: bool,
    json_mode: bool,
    stream: bool,
    out: Any,
    *,
    max_steps: int | None = None,
    verbose: bool = False,
    seed_messages: list[Message] | None = None,
    plan_mode: bool = False,
    mcp_tools: tuple[MCPTool, ...] = (),
    ext_registry: Any = None,
    strict: bool = False,
    responder: Any = None,
) -> AgentLoop | None:
    """Construct a REPL AgentLoop (delegates to orchestrator)."""
    from zall.cli import config as _cli_config
    from zall.cli.environment import CwdMeta, build_system_prompt
    from zall.cli.orchestrator import (
        build_perception_engine,
        build_tools,
        inject_ask_user_interaction,
        inject_subagent_context,
        merge_tools,
        refine_goal,
        _sessions_dir,
    )

    try:
        model_name = state.get("model")
        # v0.3.0 (B2): 复用session级 adapter (httpx 连接池), 不每对话重建。
        # /model 切换后 model_name 变 → 关闭旧的重建新的; /clear 不动 adapter。
        # test patch _build_adapter return_value=fake 总返同一instance, 本逻辑等价。
        cached = state.get("_adapter")
        if cached is not None and getattr(cached, "model_name", None) == model_name:
            adapter = cached
        else:
            if cached is not None and hasattr(cached, "close"):
                try:
                    cached.close()
                except Exception:
                    pass
            provider = _detect_provider(model_name)
            from zall.safety.config import load_config as _load_cfg
            _cfg = _load_cfg()
            _timeout = _cfg.get("timeout", 120.0)
            adapter = _cli_config._build_adapter(provider, model=model_name, timeout=_timeout)
            state["_adapter"] = adapter
    except ValueError as e:
        out.write(f"  \u2717 config error: {e}\n")
        return None

    tools = merge_tools(build_tools().tools, list(mcp_tools))
    rules = load_rules()
    # P2 fix: 会话级通知 scope — 并行子代理完成通知只进本会话的 loop
    import uuid as _uuid
    run_scope = f"repl_{_uuid.uuid4().hex[:12]}"
    inject_subagent_context(tools, adapter, rules, scope=run_scope)
    goal = refine_goal(first_input, judge_mode="none")
    context = Context(user_raw=first_input, cwd_meta=CwdMeta())
    renderer = CliRenderer(json_mode=json_mode, stream=out or sys.stderr,
                           verbose=verbose, disable_spinner=stream)
    state["_renderer"] = renderer
    observer = _make_usage_observer(renderer, state)
    is_interactive = sys.stdin.isatty()
    _out_stream = out or sys.stderr
    def _print_fn(s: str) -> None:
        _out_stream.write(s + "\n")
        _out_stream.flush()
    def _greylist_choose(choices: list[tuple[str, str, str]]) -> str:
        # 确认门主选择走数字选择器 (默认 reject=2); 编辑子提示仍走 ask_fn 文本。
        # type-ahead 防护: 提问前冲刷模型运行时提前敲的滞留按键
        # (与 TUI 选择菜单宽限期同源; 仅真 TTY 生效, 注入 input_fn 的测试不受影响)。
        from zall.cli.responder import flush_stdin_typeahead
        from zall.cli.select import select_prompt
        if state.get("_input_fn") is None:
            flush_stdin_typeahead()
        return select_prompt(
            _out_stream, "confirm tool call", choices,
            input_fn=state.get("_input_fn") or input, default_index=1,
        )
    responder = responder or CliUserResponder(
        yes=yes, is_tty=is_interactive, plan_mode=plan_mode,
        print_fn=_print_fn,
        ask_fn=state.get("_input_fn"),  # v0.3.0 (B3): gate 确认走同一输入栈 (prompt_toolkit), 避免裸 input() 撞裂显示; 测试无 _input_fn 时回落 input
        choose_fn=_greylist_choose,
    )
    git_protect = GitProtect()
    try:
        checkpoint_mgr = CheckpointManager()
    except (OSError, PermissionError, ValueError):
        checkpoint_mgr = None
    # kimi AskUserQuestion 对标: 为 ask_user 注入交互 (TUI 桥 / REPL 选择器;
    # 非交互保持未注入 → 工具自动 dismiss)
    inject_ask_user_interaction(tools, responder, state, out or sys.stderr)
    loop = (
        AgentBuilder()
        .with_model(adapter)
        .with_tools(tools)
        .with_rules(rules)
        .with_goal(goal)
        .with_context(context)
        .with_responder(responder)
        .with_observer(observer)
        .with_max_steps(max_steps if max_steps and max_steps > 0 else REPL_MAX_STEPS)
        .with_stream(stream)
        .with_git_protect(git_protect)
        .with_checkpoint(checkpoint_mgr)
        .with_plan_mode(plan_mode)
        .with_strict(strict)
        .with_compactor(ModelCompactor())
        .with_extensions(ext_registry)
        .with_perception(build_perception_engine())
        .with_timeline_spill_dir(_sessions_dir())   # M-fix: 长会话内存有界
        .build()
    )
    if seed_messages is None:
        # TUI 路径不显式传 seed; 从 state 取 CLI --continue/-r 恢复的消息
        seed_messages = state.pop("resume_messages", None)
    if seed_messages:
        loop.set_messages(list(seed_messages))
    else:
        loop.set_messages([
            Message(role="system", content=build_system_prompt(
                context, mcp_tools=mcp_tools, plan_mode=plan_mode,
                skills=state.get("_skills"))),
            Message.user(first_input),
        ])
    # kimi background→notification→inject 闭环: 主 loop 消费进程级通知中心
    # (并行子代理完成即推送入上下文; 子代理 loop 不设 → 不误吞)
    try:
        from zall.core.notifications import get_notification_center
        loop.set_notification_center(get_notification_center(), scope=run_scope)
    except Exception:
        pass
    return loop


def repl(
    *,
    model: str | None = None,
    yes: bool = False,
    judge_mode: str = "none",
    json_mode: bool = False,
    stream: bool = True,
    verbose: bool = False,
    input_fn: Any = None,
    out: Any = None,
    strict: bool = False,
    resume_session: str | None = None,
) -> int:
    """REPL: 单一对话态 (持续 AgentLoop + step() + 共享context)。"""
    from zall.cli.environment import get_cached_cwd_meta
    from zall.cli.orchestrator import confirm_goal

    out = out or sys.stderr

    # §9.2.11: REPL session内 MCP server 只连接一次。
    # kimi 对标 (延迟后台加载): 启动零阻塞, 连接在后台进行,
    # 首个回合构建前才收敛 (通常届时早已就绪, 等待为零)。
    from zall.mcp.deferred import DeferredMCPLoader
    _mcp_loader = DeferredMCPLoader()
    _mcp_loader.start()
    mcp_tools: list[MCPTool] = []  # 首个回合构建前由 loader.wait() 填充
    skills: list[Skill] = load_skills()

    # Extension registry (Pi-style self-evolving agent)
    from zall.core.extension import ExtensionRegistry
    ext_registry = ExtensionRegistry()
    try:
        from zall.extensions.auto_learn import create_auto_learn
        from zall.extensions.usage_tracker import create_usage_tracker
        ext_registry.register(create_auto_learn())
        ext_registry.register(create_usage_tracker())
        # v0.4.10 (B1): connect auto-learn config overrides to config layer
        from zall.cli.config_layers import set_extension_suggestions
        _learn_ext = ext_registry.get("auto_learn")
        if _learn_ext is not None and hasattr(_learn_ext, "get_config_overrides"):
            try:
                # E2.3: Auto-apply pending suggestions so apply_suggestion's
                # side effects (file writes, overrides persistence) really happen.
                if hasattr(_learn_ext, "get_suggestions") and hasattr(_learn_ext, "apply_suggestion"):
                    for _s in _learn_ext.get_suggestions():
                        _learn_ext.apply_suggestion(_s)
                _overrides = _learn_ext.get_config_overrides()
                if _overrides:
                    set_extension_suggestions(_overrides)
            except Exception:
                pass
    except Exception as _ext_err:
        _log.warning("built-in extensions skipped: %s", _ext_err)  # Built-in extensions are optional

    # v2.x: 先建 state (供 prompt bottom_toolbar 读 model/context 显示)。
    # model 未传 --model 时从 config 解析真实名 (与 TUI 一致, 否则 toolbar 显 zall)。
    from zall.cli.config import _config_status as _cfg_status
    state: dict[str, Any] = {
        "model": model or (_cfg_status().get("model") or ""),
        "max_steps": REPL_MAX_STEPS,
        "verbose": verbose, "usage": {"prompt": 0, "completion": 0},
        "_mcp_tools": mcp_tools,    # Item E: 供 /reload 访问
        "_skills": skills,           # Item E: 供 /reload 访问
        "_ext_registry": ext_registry,
    }

    if input_fn is None:
        input_fn = make_prompt_fn(
            commands=list(get_known_commands()),
            skills=[s.name for s in skills],
            state=state,  # bottom_toolbar 读实时 model/context
        )
    else:
        input_fn = input_fn or input
    state["_input_fn"] = input_fn

    # CLI --continue/-r: 启动期恢复会话 (设 state["resume_messages"], 供下方 loop 构建 seed)
    if resume_session:
        from zall.cli.session import _run_resume
        _run_resume(out, resume_session, state)

    _onboarding(out, input_fn)
    _setup_completion(skills)

    from zall.cli.session import _check_repl_autosave, _clear_repl_autosave, _save_repl_state
    _check_repl_autosave(out, state)

    _print_banner(out, model=state.get("model") or model,
                  branch=get_cached_cwd_meta(state).git_branch,
                  max_steps=state["max_steps"],
                  verbose=state["verbose"], plan=state.get("plan_mode", False))
    out.flush()

    # v2: background update check (non-blocking)
    try:
        from zall.cli.update import get_update_hint, start_background_check
        start_background_check()
        # lazy 3 秒后check结果 (给后台thread时间完成)
        import threading as _th
        def _show_update_hint() -> None:
            _th.Event().wait(3.0)
            hint = get_update_hint()
            if hint:
                out.write(f"  \u2192 {hint}\n")
                out.flush()
        _t = _th.Thread(target=_show_update_hint, daemon=True)
        _t.start()
    except Exception as _update_err:
        _log.warning("background update check failed: %s", _update_err)  # 更新检查失败不阻断

    loop: AgentLoop | None = None
    # 干活期间打字排队 (Codex queued-message 口径): 采集线程接管回合期间的
    # 键盘输入 — Enter 提交进队列, 回合结束后逐条作为后续消息发出; 此前
    # 打进去的字会被 flush_stdin_typeahead() 整个丢弃 ("中途干活不能发信息")。
    from zall.cli.typeahead import TypeaheadCollector as _TypeaheadCollector
    _ta: _TypeaheadCollector = _TypeaheadCollector()
    _queued_turns: list[str] = []
    _last_interrupt_at: float = 0.0  # Ctrl+C 双击退出判定 (2s 窗口)

    try:
        while True:
            try:
                _ensure_new_line()  # G10: 工具输出无尾换行时补行, 提示符不接行尾
                # 恢复提示期间被提前敲入的首条任务 (session._check_repl_autosave 转交)
                pending = state.pop("_pending_first_input", None)
                if pending is not None:
                    line = pending
                    out.write(f"{_prompt(state)}{line}\n")
                    out.flush()
                elif _queued_turns:
                    # 上一回合结束 — 提交干活期间排队的消息 (不回提示符)
                    line = _queued_turns.pop(0)
                    remaining = len(_queued_turns)
                    out.write(f"{_prompt(state)}{line}\n")
                    if remaining:
                        out.write(f"  · {remaining} more queued\n")
                    out.flush()
                else:
                    prompt = _prompt(state)
                    line = _read_multiline_input(prompt, input_fn)
            except EOFError:
                out.write("\n  bye\n")
                return 0
            except KeyboardInterrupt:
                # Kimi/Codex 口径: 单次 Ctrl+C 打断的是当前输入 (清行继续);
                # 2s 内两次 → 退出 (POSIX 惯例码 130) — 否则习惯性双击永远
                # 困在提示符, 只能 Ctrl+D。
                now = time.monotonic()
                if now - _last_interrupt_at < 2.0:
                    out.write("  bye\n")
                    return 130
                _last_interrupt_at = now
                out.write("\n  \u00b7 Ctrl-C again to exit\n")
                continue
            if line is None:
                return 0
            line = line.strip()
            if not line:
                # v0.6.0: 空行时更新状态栏
                renderer = state.get("_renderer")
                if renderer is not None and hasattr(renderer, "render_status_bar"):
                    renderer.render_status_bar()
                continue
            # input长度limit: 防止意外粘贴巨量文本导致 OOM
            if len(line) > 100_000:
                out.write(f"  \u26a0 input too long ({len(line):,} chars), truncated to 100,000\n")
                line = line[:100_000]

            # ── 回合级兜底 (Kimi 口径): REPL 不因未预期异常崩会话 ──
            # @file 展开 / loop 构建 / step 循环 / 渲染收尾任何一处抛错, 只落一行
            # 错误回提示符, 会话上下文原样保留 (换模型重试/继续)。
            # Ctrl+C 不经此路: 它是 BaseException, except Exception 接不住,
            # 仍由 step 循环的中断语义与外层 console_main 分层处理。
            try:
                if line.startswith("/"):
                    if line.strip() == "/":
                        from zall.cli.commands import _handle_bare_slash
                        _handle_bare_slash(out)
                        continue
                    skind, spayload = _route_skill(line, skills, out)
                    if skind == "task":
                        line = spayload
                    elif skind == "handled":
                        continue
                    else:
                        try:
                            action = handle_slash(line, state, out, loop)
                        except Exception as e:
                            out.write(f"  \u2717 command error: {e}\n")
                            out.flush()
                            continue
                        if action == "exit":
                            return 0
                        if action == "clear":
                            loop = None
                        # Argus _print_status_bar 纪律: 命令后回显当前状态行
                        _echo_status(state)
                        continue

                # Codex 交互对标: 裸 "?" = 快捷键卡片 (TUI 内 ? 浮层的控制台同源)
                if line == "?":
                    from zall.cli.commands.system import _print_shortcuts
                    _print_shortcuts(out)
                    _echo_status(state)
                    continue
                # Codex "! for shell commands": 用户显式命令直接执行, 不经过模型
                if line.startswith("!") and len(line) > 1:
                    _run_shell_passthrough(line[1:].strip(), out)
                    continue
                if line == "!":
                    # 实测反馈: 裸 "!" 落到模型变成一个 goal, 还可能撞上 API 错误刷屏。
                    # 这里给用法提示 (与快捷键卡片口径一致)。
                    out.write("  ! <command> runs a shell command directly, without the model\n")
                    out.write("  e.g. !git status · !ls · !python -V\n")
                    out.flush()
                    continue

                # v2.x: @file 引用展开 — 把 @path 解析到的真实文件内容注入消息 (Claude Code 式)。
                # 只展开真实文件; 非文件 @token 原样保留。slash 命令已在上方返回, 不受影响。
                line, _injected = expand_at_references(line)
                if _injected:
                    out.write(f"  \u00b7 injected {len(_injected)} file(s): {', '.join(_injected[:5])}\n")
                    out.flush()

                if loop is None:
                    # MCP 后台加载收敛 (首个回合构建前保证工具可用)
                    if not mcp_tools:
                        mcp_tools = _mcp_loader.wait()
                        state["_mcp_tools"] = mcp_tools
                        _mcp_log = _mcp_loader.status().get("log", "")
                        if _mcp_log.strip():
                            out.write(_mcp_log)
                    loop = build_repl_loop(
                        line, state, yes, json_mode, stream, out,
                        max_steps=state.get("max_steps", REPL_MAX_STEPS),
                        verbose=state.get("verbose", False),
                        seed_messages=state.pop("resume_messages", None),
                        plan_mode=state.get("plan_mode", False),
                        mcp_tools=tuple(mcp_tools),
                        ext_registry=ext_registry,
                        strict=state.get("strict", strict),
                    )
                    if loop is None:
                        continue
                    state["_loop"] = loop
                    # v0.6.0: 更新状态栏
                    renderer = state.get("_renderer")
                    if renderer is not None and hasattr(renderer, "update_status"):
                        from zall.cli.environment import get_cached_cwd_meta
                        _meta = get_cached_cwd_meta(state)
                        renderer.update_status(
                            model=state.get("model", "") or str(getattr(loop.model_adapter, "model_name", "")),
                            branch=_meta.git_branch or "",
                            goal=loop.goal.statement.goal_type.value if hasattr(loop.goal, "statement") else "",
                            plan=state.get("plan_mode", False),
                        )
                    confirmed, final_goal = confirm_goal(out, loop.goal, judge_mode="none", yes=yes, strict=strict, input_fn=input_fn)
                    if not confirmed:
                        out.write("  goal not confirmed; type a new task to retry.\n")
                        out.flush()
                        loop = None
                        state.pop("_loop", None)
                        continue
                    # 如果用户修改了 goal, 更新 loop 的 goal
                    if final_goal is not None and final_goal is not loop.goal:
                        # 通过 builder 属性更新 loop 的 goal
                        if hasattr(loop, "_goal"):
                            loop._goal = final_goal
                else:
                    loop.add_user_message(line)

                # 回合开始: 采集键盘输入 (Enter 排队), spinner 状态行实时回显
                from zall.cli.render import set_typeahead_source as _set_ta_src
                _ta.start()
                _set_ta_src(lambda: (_ta.buffer, _ta.queued_count))
                while True:
                    try:
                        pre_step_msg_count = len(loop.messages)
                        result = loop.step()
                    except KeyboardInterrupt:
                        # stop spinner (防止残留output)
                        renderer = state.get("_renderer")
                        if renderer is not None and hasattr(renderer, "_stop_spinner"):
                            renderer._stop_spinner()
                        # 流式输出保留: flush buffer + 插入 [Interrupted] 标记
                        # 不像旧行为完全丢弃已显示的内容, 而是保留用户已看到的 token
                        if renderer is not None and hasattr(renderer, "interrupt_stream"):
                            renderer.interrupt_stream()
                        # E4: rollback messages to pre-step state (discard model partial output)
                        pre_step_msgs = loop.messages[:pre_step_msg_count]
                        loop.set_messages(pre_step_msgs)
                        # Record user_interrupt event in timeline
                        try:
                            import time as _time

                            from zall.core.verifiability import EventType as _EventType
                            loop.recorder.append(
                                event_id=f"user_interrupt_{loop.step_count}",
                                ts=int(_time.time() * 1000),
                                event_type=_EventType.USER_INTERRUPT,
                                payload={"step": loop.step_count, "partial_output_preserved": True},
                            )
                        except Exception:
                            pass
                        out.write("\n  [interrupted]\n")
                        out.flush()
                        break
                    if result.is_terminal:
                        if result.egress and result.egress.error:
                            err = result.egress.error
                            if is_transient_error(err):
                                # 噪声收敛 (2026-09-19 实测反馈): 错误全文已由 ✗ error
                                # 行打印, 这里不再重复 ⚠ 全文 — 只留一行紧凑重试提示
                                # auto-retry up to 3 times with backoff
                                import time as _time

                                from zall._util.backoff import backoff_delay
                                retried = False
                                interrupted = False
                                for attempt in range(1, 4):
                                    delay = round(backoff_delay(attempt), 1)  # G13: 指数+抖动
                                    out.write(f"  · retry {attempt}/3 in {delay}s · ctrl-c to stop\n")
                                    out.flush()
                                    _time.sleep(delay)
                                    try:
                                        # stop spinner before retry
                                        renderer = state.get("_renderer")
                                        if renderer is not None and hasattr(renderer, "_stop_spinner"):
                                            renderer._stop_spinner()
                                        result = loop.retry_step()  # v0.4.9 (A2): no step_count drift
                                    except KeyboardInterrupt:
                                        out.write("  · interrupted\n")
                                        out.flush()
                                        interrupted = True
                                        break
                                    if result.is_terminal and result.egress and result.egress.error:
                                        if not is_transient_error(result.egress.error):
                                            break  # non-transient → fall through to error handler
                                        # still transient, continue retry loop
                                    else:
                                        retried = True
                                        break  # step succeeded
                                if retried:
                                    continue  # go back to step loop
                                if interrupted:
                                    # 用户已主动中断 — 不再补"API 仍不可用" (实测反馈: 明明
                                    # 是我停的, 还刷 API 错误信息)
                                    loop = None
                                    state.pop("_loop", None)
                                    break
                                # all retries exhausted
                                out.write("  · API still unavailable after 3 retries. Try /model to switch models.\n")
                                out.flush()
                                break
                            if "max_steps" in err or "MAX_STEPS" in err:
                                out.write("  \u00b7 context limit reached, starting fresh conversation\n")
                            else:
                                out.write(f"  \u2717 {err[:100]}\n")
                                out.write("  session ended (terminal)\n")
                        else:
                            out.write("  session ended (terminal)\n")
                        loop = None
                        state.pop("_loop", None)
                        break
                    if result.kind == "awaiting_input":
                        _save_repl_state(loop, state)
                        # 在 prompt 前显示折叠工具提示 (便于用户发现 /expand)
                        renderer = state.get("_renderer")
                        if renderer is not None and hasattr(renderer, "folded_count"):
                            fc = renderer.folded_count
                            if fc > 0:
                                out.write(f"  [{fc} tool(s) folded · type /expand <N> to show, /expand all to show all]\n")
                        # v1.2: 上下文 footer 提示 (借鉴 Claude Code)
                        if renderer is not None and hasattr(renderer, "render_contextual_hint"):
                            renderer.render_contextual_hint("idle")
                        # verbose: 会话累计用量行 (Codex FinalOutput 口径)
                        if state.get("verbose"):
                            _usage_line = _format_turn_usage(state)
                            if _usage_line:
                                out.write(f"  {_usage_line}\n")
                        out.write("\n")
                        break
                # 回合结束: 停采集, 排队消息转交外层循环逐条提交 (不回提示符)
                _ta.stop()
                _set_ta_src(None)
                _queued_turns.extend(_ta.drain())
                if _ta.buffer:
                    out.write(f"  \u00b7 typed ahead \"{_ta.buffer[:40]}\" (not sent — Enter would have queued it)\n")
                out.flush()
            except Exception as e:
                # 回合中断时采集线程必须停 — 否则提示符处键盘被 typeahead 吞掉
                try:
                    _ta.stop()
                    _set_ta_src(None)
                except Exception:
                    pass
                out.write(f"  \u2717 internal error: {e}\n")
                if state.get("verbose"):
                    import traceback as _tb
                    _tb.print_exc()
                else:
                    out.write("  (session kept — retry, /model to switch, or rerun with --verbose for traceback)\n")
                out.flush()
                continue
    finally:
        # v0.4.9 (A3): 退出时停止持久 spinner 线程
        _renderer = state.get("_renderer")
        if _renderer is not None and hasattr(_renderer, "shutdown_spinner"):
            _renderer.shutdown_spinner()
        # 首个回合从未触发时, 后台可能已连上 server — 收敛后统一关闭 (防泄漏);
        # close_all 等待仍在收尾的加载线程并关闭其最终产出的连接
        _mcp_loader.close_all(timeout=5)
        for t in mcp_tools:
            t.close()
        # v0.3.0 (B2): 关闭session级 adapter (httpx 连接池释放); fake adapter 无 close skip
        _adapter = state.get("_adapter")
        if _adapter is not None and hasattr(_adapter, "close"):
            try:
                _adapter.close()
            except Exception as _close_err:
                _log.warning("REPL adapter close failed (non-fatal): %s", _close_err)
        state.pop("_adapter", None)
        clear_console_cache()  # v0.3.0 (A2): 释放累积的 Console 缓存
        _clear_repl_autosave()