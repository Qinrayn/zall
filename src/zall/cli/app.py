"""zall CLI entry point — zall. Thin orchestration layer.

Kept from original app.py after v0.2.0 refactor:
  - run() one-shot execution pipeline
  - main() CLI entry + argparse
  - _build_adapter, goal helpers, system prompt, tool registration

Phase 2 cleanup: duplicated code replaced with imports from orchestrator.py / environment.py.
Command handlers moved to commands.py.
REPL moved to repl_ui.py.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from zall._util.win32 import ensure_utf8_stdio as _ensure_utf8_stdio
from zall._util.win32 import set_console_title as _set_console_title

# B-启动提速: 重型核心依赖仅作类型注解 (from __future__ import annotations 下不求值),
# 运行时用到处再懒加载 — 让 --version/--help/--init 不拖整个 core 链 (~0.4s)。
if TYPE_CHECKING:
    from zall.core.context import Context
    from zall.core.loop_events import RunEgress
    from zall.mcp.tool import MCPTool


# ── System prompt (delegated to environment.py) ──


def _build_system_prompt(context: Context, mcp_tools: tuple[MCPTool, ...] = ()) -> str:
    """Build the system prompt. Delegates to environment.py."""
    from zall.cli.environment import build_system_prompt as _build_system_prompt_context
    return _build_system_prompt_context(context, mcp_tools=mcp_tools)


# ── Tool registration (delegated to orchestrator.py) — _build_tools, _merge_tools, _build_mcp_tools, _inject_subagent_context imported above ──

def run(
    user_task: str,
    *,
    model: str | None = None,
    yes: bool = False,
    judge_mode: str = "none",
    json_mode: bool = False,
    max_steps: int | None = None,
    stream: bool = True,
    verbose: bool = False,
    out: Any = None,
    strict: bool = False,
) -> RunEgress:
    """Wire up AgentLoop and execute (thin wiring layer, delegates to orchestrator.py)."""
    from zall.cli.orchestrator import run as _orchestrator_run
    return _orchestrator_run(
        user_task,
        model=model,
        yes=yes,
        judge_mode=judge_mode,
        json_mode=json_mode,
        max_steps=max_steps,
        stream=stream,
        verbose=verbose,
        out=out,
        strict=strict,
    )


# ── argparse + main ──


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zall",
        description="zall - model-agnostic, falsifiable, reproducible coding agent",
        epilog=(
            "run modes:\n"
            "  zall              interactive console (default; type while the model runs)\n"
            "  zall '<task>'     one-shot: run the task once and exit (scriptable, has exit code)\n"
            "  zall --tui        optional inline Textual UI (auto-falls back to the console)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("task", nargs="*",
                   help="task to run once and exit (non-interactive); omit to start the interactive UI")
    # v1.4: 所有选项提供短标志 (学 kimi/opencode: 不必每次输 --)
    p.add_argument("--model", "-m", default=None, help="model name (overrides config)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="auto-accept greylist actions (NEVER overrides blacklist)")
    p.add_argument("--judge", choices=["none", "system"], default="none",
                   help="judge mode: none=undecidable(default), system=run pytest")
    p.add_argument("--json", "-j", action="store_true",
                   help="output events as NDJSON")
    p.add_argument("--no-stream", action="store_true",
                   help="disable token streaming")
    p.add_argument("--max-steps", "-n", type=int, default=None, help="max steps")
    p.add_argument("--init", action="store_true",
                   help="initialize zall project config (.zall/) in current directory")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="show full tool output (default: compact summary)")
    p.add_argument("--strict", "-S", action="store_true",
                   help="strict mode: enable full confirm/downgrade gates")
    p.add_argument("--version", "-V", action="store_true",
                   help="show version and exit")
    # 交互界面 (console 优先, v2.2):
    #   默认     → 同步 REPL 控制台 (Argus 式主界面)
    #   --tui    → 可选 inline TUI (Textual; 终端不支持自动回退)
    #   --no-tui → 兼容别名 (与默认等价; 旧脚本不报错)
    p.add_argument("--tui", dest="tui_mode", action="store_true", default=False,
                   help="opt-in inline Textual UI (default is the console REPL)")
    p.add_argument("--no-tui", dest="tui_mode", action="store_false",
                   default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    # 会话续接 (kimi -C/-r parity)
    p.add_argument("--continue", "-C", dest="continue_", action="store_true",
                   help="continue the most recent session in this directory")
    p.add_argument("--resume", "-r", dest="resume", nargs="?", const="", default=None,
                   help="resume a session by ID; without ID opens an interactive picker")
    return p


def _resolve_resume_target(args: argparse.Namespace) -> str | None:
    """解析 --continue/--resume 的目标会话 id (无 → None)。-r 无 id → 启动期选择器。"""
    from zall.cli.session import _get_cached_sessions
    if getattr(args, "continue_", False):
        entries = _get_cached_sessions()
        return entries[0][0].name if entries else None
    resume = getattr(args, "resume", None)
    if resume is None:
        return None
    if resume:  # 显式 id
        return resume
    entries = _get_cached_sessions()  # -r 无 id → 交互式选择器
    if not entries:
        print("  (no sessions to resume)", file=sys.stderr)
        return None
    from zall.cli.select import select_prompt
    choices = [
        (d.name, d.name[:8],
         f"{data.get('final_state', '?')} \u00b7 {str(data.get('saved_at', ''))[:16]}")
        for d, data in entries[:9]
    ]
    return select_prompt(sys.stderr, "resume session", choices) or None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (pyproject [project.scripts] zall = zall.cli:main)."""
    _ensure_utf8_stdio()
    _set_console_title()  # Set console title
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from zall import __version__
        print(f"zall {__version__}")
        return 0

    task = " ".join(args.task).strip() if args.task else ""

    if args.init or task == "init":
        from zall.cli.commands import cmd_init
        cmd_init(task, sys.stderr, None, {})
        return 0

    if not task:
        # 交互界面 (v2.2 console 优先):
        #   默认    → 同步 REPL 控制台 (Argus 式主界面)
        #   --tui   → 可选 inline TUI; 终端不支持 (rc==2 / textual 缺失) → 回退 REPL
        resume_session = _resolve_resume_target(args)  # --continue / -r
        if getattr(args, "tui_mode", False):  # 仅显式 --tui 进 Textual
            from zall.cli.config import _onboarding
            _onboarding(sys.stderr, input)  # 进任何交互界面前先配置 API key (idempotent)
            rc = 2
            try:
                from zall.cli.tui import run_tui
                rc = run_tui(
                    model=args.model, yes=args.yes,
                    verbose=args.verbose, strict=args.strict,
                    inline=True, resume_session=resume_session,
                )
            except ImportError:
                rc = 2  # textual 未安装 → 回退同步 REPL
            if rc != 2:
                return rc
            # rc == 2: 终端不支持 TUI (dumb/CI/textual 缺失) → 回退同步 REPL
        from zall.cli.repl_ui import repl
        return repl(
            model=args.model, yes=args.yes, judge_mode=args.judge,
            json_mode=args.json, stream=not args.no_stream, verbose=args.verbose,
            strict=args.strict, resume_session=resume_session,
        )

    egress = run(
        task, model=args.model, yes=args.yes, judge_mode=args.judge,
        json_mode=args.json, max_steps=args.max_steps,
        stream=not args.no_stream, verbose=args.verbose,
        strict=args.strict,
    )
    from zall.core.goal import TerminationState
    if egress.final_state == TerminationState.MET:
        return 0
    if egress.final_state == TerminationState.NOT_MET:
        return 1
    # UNDECIDABLE: 区分两种语义 (Bug fix 2026-07-26) —
    #   未开 judge (--judge none, 默认): UNDECIDABLE 是必然终态, 任务本身
    #   正常跑完 → 0 (否则 `zall -y "..." && next` 永远断链, 脚本化不可用);
    #   开了 judge 但裁决不了 / 执行出错 → 2 (诚实的不确定, PR-0)。
    if args.judge == "none" and not egress.error:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())