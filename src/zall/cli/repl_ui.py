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
    """REPL banner — Obsidian 框式 header (v1.4: 恢复框式设计 + 呼吸空行)。"""
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
    # v1.4: 框式 banner (用户偏好) + 上下呼吸空行
    width = min(50, max(30, len(display_model) + 20))
    _dash_line = "\u2500" * width
    console.print()
    console.print(f"  [bold {_C.ACCENT}]\u256d{_dash_line}\u256e[/]")
    console.print(f"  [bold {_C.ACCENT}]\u2502[/]  [bold]zall[/]  "
                  f"[dim {_C.ACCENT}]\u00b7[/]  [dim]{display_model}[/]")
    meta_parts = []
    if branch:
        meta_parts.append(f"[{_C.INFO}]{branch}[/]")
    if plan:
        meta_parts.append(f"[{_C.THINKING}]plan[/]")
    if verbose:
        meta_parts.append(f"[{_C.DIM}]verbose[/]")
    if meta_parts:
        sep = f"  [{_C.SUBTLE}]\u00b7[/]  "
        console.print(f"  [bold {_C.ACCENT}]\u2502[/]  {sep.join(meta_parts)}")
    console.print(f"  [bold {_C.ACCENT}]\u2570{_dash_line}\u256f[/]")
    console.print()


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
    out.write("  /help commands \u00b7 Ctrl-D exit \u00b7 Ctrl-R search history \u00b7 /plan read-only\n")
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

    try:
        while True:
            try:
                _ensure_new_line()  # G10: 工具输出无尾换行时补行, 提示符不接行尾
                prompt = _prompt(state)
                line = _read_multiline_input(prompt, input_fn)
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
                # v0.6.0: 空行时更新状态栏
                renderer = state.get("_renderer")
                if renderer is not None and hasattr(renderer, "render_status_bar"):
                    renderer.render_status_bar()
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
                            out.write(f"  \u26a0 {err[:100]}\n")
                            # auto-retry up to 3 times with backoff
                            import time as _time

                            from zall._util.backoff import backoff_delay
                            retried = False
                            for attempt in range(1, 4):
                                delay = round(backoff_delay(attempt), 1)  # G13: 指数+抖动
                                out.write(f"  · retry {attempt}/3 in {delay}s...\n")
                                out.flush()
                                _time.sleep(delay)
                                try:
                                    # stop spinner before retry
                                    renderer = state.get("_renderer")
                                    if renderer is not None and hasattr(renderer, "_stop_spinner"):
                                        renderer._stop_spinner()
                                    result = loop.retry_step()  # v0.4.9 (A2): no step_count drift
                                except KeyboardInterrupt:
                                    out.write("  \u00b7 interrupted\n")
                                    out.flush()
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
                            # all retries exhausted
                            out.write("  \u00b7 API still unavailable after 3 retries. Try /model to switch models.\n")
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
                    out.write("\n")
                    break
            out.flush()
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