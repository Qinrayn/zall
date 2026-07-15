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

from zall.cli import config as _cli_config
from zall.cli.config import _PLACEHOLDER_API_KEY, _PROVIDER_DISPLAY, _config_status, _detect_provider, _onboarding, _persist_model_to_config, _resolve_model_alias
from zall.cli.environment import CwdMeta as _CwdMeta, build_system_prompt as _build_system_prompt_context, get_cached_cwd_meta as _get_cached_cwd_meta, read_agents_md as _read_agents_md  # noqa: F401 — re-export for test compat
from zall.cli.judge import SystemJudge, UndecidableJudge
from zall.cli.orchestrator import (
    build_mcp_tools as _build_mcp_tools,
    build_tools as _build_tools,
    confirm_goal as _confirm_goal,
    get_modified_files,
    inject_subagent_context as _inject_subagent_context,
    merge_tools as _merge_tools,
    refine_goal as _refine_goal,
)
from zall.cli.orchestrator import _make_goal  # orchestrator internal function
from zall.cli.render import CliRenderer, render_egress_summary, render_goal_card, _shared_console
from zall.cli.responder import CliUserResponder
from zall.cli.session import (
    _check_repl_autosave, _clear_repl_autosave, _get_cached_sessions,
    _list_sessions, _load_session_messages, _prune_sessions, _run_eval,
    _run_replay, _run_resume, _save_repl_state, _save_session,
    _search_sessions, _tag_session, _get_sessions_dir,
)
from zall.core.context import Context
from zall.core.goal import TerminationState
from zall.core.loop import AgentLoop, RunEgress
from zall.core.model import Message
from zall.core.refiner import GoalRefiner  # noqa: F401 — re-export for test compat
from zall.core.safety import SafeLevel
from zall.core.tool import ToolRegistry
from zall.safety.rules_file import load_rules
from zall.core.checkpoint import CheckpointManager
from zall.mcp.config import MCPServerSpec
from zall.mcp.tool import MCPTool
from zall.core.compactor import ModelCompactor
from zall.skills import Skill, find_skill, load_skills
from zall._util.model_registry import get_price as _get_model_price
from zall._util.win32 import ensure_utf8_stdio as _ensure_utf8_stdio, set_console_title as _set_console_title


REPL_MAX_STEPS = 100_000


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


# ── Backward compatibility: function signatures used by old tests and external code ──

from zall.cli.commands import (  # noqa: E402
    cmd_undo, cmd_checkpoint, cmd_revert, cmd_cost, cmd_git, cmd_commit,
    cmd_web, cmd_add, cmd_drop, cmd_fix, cmd_review, cmd_retry, cmd_search,
    cmd_diff, cmd_doctor, cmd_compact, cmd_init, cmd_model, cmd_plan,
    cmd_help, cmd_about, cmd_clear, cmd_max_steps, cmd_verbose, cmd_update,
    handle_slash, get_known_commands, get_command_meta,
    _suggest_command, _guess_common_command, _setup_completion,
    _route_skill, _print_skills, _cmd_init_simple,
    _INIT_RULES_TOML, _INIT_AGENTS_MD, _INIT_MCP_TOML, _INIT_SKILLS_TOML,
    _recalc_usage_from_timeline, _check_network_basic, _auto_step_loop,
)

# Legacy API aliases (preserving app_mod compatibility)
_KNOWN_COMMANDS = get_known_commands()
_handle_slash = handle_slash
from zall.cli.commands import _handle_bare_slash  # noqa: E402, F401

from zall.cli.repl_ui import (  # noqa: E402, F401
    repl,
    build_repl_loop as _build_repl_loop,
    _print_banner,
    _prompt,
    _make_usage_observer,
    REPL_MAX_STEPS,
)

if __name__ == "__main__":
    raise SystemExit(main())