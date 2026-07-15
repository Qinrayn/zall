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
from typing import Any

from zall.cli.commands import (
    _route_skill,
    _setup_completion,
    get_known_commands,
    handle_slash,
)
from zall.cli.config import _onboarding, _detect_provider
from zall.cli.orchestrator import make_usage_observer as _make_usage_observer
from zall.cli.orchestrator import build_mcp_tools
from zall.cli.prompt import make_prompt_fn
from zall.cli.render import CliRenderer, _shared_console, clear_console_cache
from zall.cli.responder import CliUserResponder
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import ModelCompactor
from zall.core.context import Context
from zall.core.goal import GoalTriple, GoalType, RefinedGoal
from zall.core.loop import AgentLoop
from zall.core.model import Message
from zall.core.refiner import GoalRefiner
from zall.core.tool import ToolRegistry
from zall.mcp.tool import MCPTool
from zall.safety.rules_file import load_rules
from zall.skills import Skill, load_skills
from zall.tools.git_protect import GitProtect

__all__ = [
    "repl",
    "build_repl_loop",
    "_print_banner",
    "_prompt",
    "_make_usage_observer",
    "REPL_MAX_STEPS",
]

# REPL 对话态步数max: 100000 等价"无max"
REPL_MAX_STEPS = 100_000


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


def _print_banner(out: Any, *, model: str | None, branch: str | None,
                  max_steps: int, verbose: bool, plan: bool = False) -> None:
    """REPL banner — Obsidian theme: architectural minimal header."""
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
    # Architectural header
    width = min(50, max(30, len(display_model) + 20))
    console.print(f"  [bold gold1]\u256d{'\u2500' * width}\u256e[/]")
    console.print(f"  [bold gold1]\u2502[/]  [bold]zall[/]  "
                  f"[dim gold1]\u00b7[/]  [dim]{display_model}[/]")
    meta_parts = []
    if branch:
        meta_parts.append(f"[dim]{branch}[/]")
    if plan:
        meta_parts.append("[turquoise4]plan[/]")
    if verbose:
        meta_parts.append("[dim]verbose[/]")
    if meta_parts:
        console.print(f"  [bold gold1]\u2502[/]  [dim]{'  '.join(meta_parts)}[/]")
    console.print(f"  [bold gold1]\u2570{'\u2500' * width}\u256f[/]")


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
) -> AgentLoop | None:
    """construct REPL 对话态的 AgentLoop (委托给 orchestrator)。"""
    from zall.cli import config as _cli_config
    from zall.cli.orchestrator import build_tools, merge_tools, inject_subagent_context, refine_goal, confirm_goal, build_mcp_tools
    from zall.cli.environment import build_system_prompt, CwdMeta

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
            adapter = _cli_config._build_adapter(provider, model=model_name)
            state["_adapter"] = adapter
    except ValueError as e:
        out.write(f"  \u2717 config error: {e}\n")
        return None

    tools = merge_tools(build_tools().tools, list(mcp_tools))
    rules = load_rules()
    inject_subagent_context(tools, adapter, rules)
    goal = refine_goal(first_input, judge_mode="none")
    context = Context(user_raw=first_input, cwd_meta=CwdMeta())
    renderer = CliRenderer(json_mode=json_mode, stream=out or sys.stderr,
                           verbose=verbose, disable_spinner=stream)
    state["_renderer"] = renderer
    observer = _make_usage_observer(renderer, state)
    is_interactive = sys.stdin.isatty()
    _out_stream = out or sys.stderr
    _print_fn = lambda s: (_out_stream.write(s + "\n"), _out_stream.flush())[-1] or None
    responder = CliUserResponder(
        yes=yes, is_tty=is_interactive, plan_mode=plan_mode,
        print_fn=_print_fn,
        ask_fn=state.get("_input_fn"),  # v0.3.0 (B3): gate 确认走同一输入栈 (prompt_toolkit), 避免裸 input() 撕裂显示; 测试无 _input_fn 时回落 input
    )
    git_protect = GitProtect()
    try:
        checkpoint_mgr = CheckpointManager()
    except (OSError, PermissionError, ValueError):
        checkpoint_mgr = None
    loop = AgentLoop(
        model=adapter, tools=tools, rules=rules, goal=goal, context=context,
        user_responder=responder, judge=None, observer=observer,
        max_steps=max_steps if max_steps and max_steps > 0 else REPL_MAX_STEPS,
        stream=stream, git_protect=git_protect, checkpoint_mgr=checkpoint_mgr,
        plan_mode=plan_mode, compactor=ModelCompactor(),
    )
    if seed_messages:
        loop.set_messages(list(seed_messages))
    else:
        loop.set_messages([
            Message(role="system", content=build_system_prompt(
                context, mcp_tools=mcp_tools, plan_mode=plan_mode)),
            Message.user(first_input),
        ])
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
) -> int:
    """REPL: 单一对话态 (持续 AgentLoop + step() + 共享context)。"""
    from zall.cli.environment import CwdMeta, get_cached_cwd_meta
    from zall.cli.orchestrator import confirm_goal

    out = out or sys.stderr

    # §9.2.11: REPL session内 MCP server 只连接一次
    mcp_tools: list[MCPTool] = build_mcp_tools(out)
    skills: list[Skill] = load_skills()

    if input_fn is None:
        input_fn = make_prompt_fn(
            commands=list(get_known_commands()),
            skills=[s.name for s in skills],
        )
    else:
        input_fn = input_fn or input

    _onboarding(out, input_fn)
    _setup_completion(skills)
    state: dict[str, Any] = {
        "model": model, "max_steps": REPL_MAX_STEPS,
        "verbose": verbose, "usage": {"prompt": 0, "completion": 0},
        "_input_fn": input_fn,
        "_mcp_tools": mcp_tools,    # Item E: 供 /reload 访问
        "_skills": skills,           # Item E: 供 /reload 访问
    }

    from zall.cli.session import _check_repl_autosave, _save_repl_state, _clear_repl_autosave
    _check_repl_autosave(out, state)

    _print_banner(out, model=state.get("model") or model,
                  branch=get_cached_cwd_meta(state).git_branch,
                  max_steps=state["max_steps"],
                  verbose=state["verbose"], plan=state.get("plan_mode", False))
    out.write("  /help for commands \u00b7 Ctrl-D to exit \u00b7 /plan = read-only mode\n")
    out.flush()

    # v2: background update check (non-blocking)
    try:
        from zall.cli.update import start_background_check, get_update_hint
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
    except Exception:
        pass  # 更新检查失败不阻断

    loop: AgentLoop | None = None

    try:
        while True:
            try:
                prompt = _prompt(state)
                line = input_fn(prompt)
            except EOFError:
                out.write("\n  bye\n")
                return 0
            except KeyboardInterrupt:
                out.write("\n")
                continue
            if line is None:
                return 0
            line = line.strip()
            if not line:
                continue
            # input长度limit: 防止意外粘贴巨量文本导致 OOM
            if len(line) > 100_000:
                out.write(f"  \u26a0 input too long ({len(line):,} chars), truncated to 100,000\n")
                line = line[:100_000]

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
                    continue

            if loop is None:
                loop = build_repl_loop(
                    line, state, yes, json_mode, stream, out,
                    max_steps=state.get("max_steps", REPL_MAX_STEPS),
                    verbose=state.get("verbose", False),
                    seed_messages=state.pop("resume_messages", None),
                    plan_mode=state.get("plan_mode", False),
                    mcp_tools=tuple(mcp_tools),
                )
                if loop is None:
                    continue
                state["_loop"] = loop
                if not confirm_goal(out, loop.goal, judge_mode="none", yes=yes, input_fn=input_fn):
                    out.write("  goal not confirmed; type a new task to retry.\n")
                    out.flush()
                    loop = None
                    state.pop("_loop", None)
                    continue
            else:
                loop.add_user_message(line)

            while True:
                try:
                    result = loop.step()
                except KeyboardInterrupt:
                    # stop spinner (防止残留output)
                    renderer = state.get("_renderer")
                    if renderer is not None and hasattr(renderer, "_stop_spinner"):
                        renderer._stop_spinner()
                    out.write("\n  \u00b7 interrupted (context preserved, continue typing)\n")
                    out.flush()
                    break
                if result.is_terminal:
                    if result.egress and result.egress.error:
                        err = result.egress.error
                        transient_keywords = (
                            "429", "rate limit", "rate_limit", "timeout",
                            "connection", "503", "502", "500",
                            "temporary", "try again", "retry",
                            "service unavailable", "bad gateway", "too many requests",
                        )
                        err_lower = err.lower()
                        if any(kw in err_lower for kw in transient_keywords):
                            out.write(f"  \u26a0 {err[:100]}\n")
                            out.write("  \u00b7 transient error, context preserved \u2014 try again\n")
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
                    out.write("\n")
                    break
            out.flush()
    finally:
        for t in mcp_tools:
            t.close()
        # v0.3.0 (B2): 关闭session级 adapter (httpx 连接池释放); fake adapter 无 close skip
        _adapter = state.get("_adapter")
        if _adapter is not None and hasattr(_adapter, "close"):
            try:
                _adapter.close()
            except Exception:
                pass
        state.pop("_adapter", None)
        clear_console_cache()  # v0.3.0 (A2): 释放累积的 Console 缓存
        _clear_repl_autosave()