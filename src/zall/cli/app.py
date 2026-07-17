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
from typing import Any

from zall.cli.environment import build_system_prompt as _build_system_prompt_context
from zall.core.context import Context
from zall.core.goal import TerminationState
from zall.core.loop_events import RunEgress
from zall.mcp.tool import MCPTool
from zall._util.win32 import ensure_utf8_stdio as _ensure_utf8_stdio, set_console_title as _set_console_title


# ── System prompt (delegated to environment.py) ──


def _build_system_prompt(context: Context, mcp_tools: tuple[MCPTool, ...] = ()) -> str:
    """Build the system prompt. Delegates to environment.py."""
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
    )


# ── argparse + main ──


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zall",
        description="zall — model-agnostic, falsifiable, reproducible coding agent",
    )
    p.add_argument("task", nargs="*", help="the task to perform (empty → enter REPL)")
    p.add_argument("--model", default=None, help="model name (overrides config)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="auto-accept greylist actions (NEVER overrides blacklist)")
    p.add_argument("--judge", choices=["none", "system"], default="none",
                   help="judge mode: none=undecidable(default), system=run pytest")
    p.add_argument("--json", action="store_true",
                   help="output events as NDJSON")
    p.add_argument("--no-stream", action="store_true",
                   help="disable token streaming")
    p.add_argument("--max-steps", type=int, default=None, help="max steps")
    p.add_argument("--init", action="store_true",
                   help="initialize zall project config (.zall/) in current directory")
    p.add_argument("--verbose", action="store_true",
                   help="show full tool output (default: compact summary)")
    p.add_argument("--version", "-V", action="store_true",
                   help="show version and exit")
    return p


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
        from zall.cli.repl_ui import repl
        return repl(
            model=args.model, yes=args.yes, judge_mode=args.judge,
            json_mode=args.json, stream=not args.no_stream, verbose=args.verbose,
        )

    egress = run(
        task, model=args.model, yes=args.yes, judge_mode=args.judge,
        json_mode=args.json, max_steps=args.max_steps,
        stream=not args.no_stream, verbose=args.verbose,
    )
    if egress.final_state == TerminationState.MET:
        return 0
    if egress.final_state == TerminationState.NOT_MET:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())