"""zall.cli.commands._legacy — Backward-compatible re-export wrapper (v0.2.1).

v0.2.1 refactor: All command implementations moved to sub-modules:
  _common.py   — shared infrastructure (registry, routing, utilities)
  session.py   — /sessions, /resume, /eval, /replay, /cost, /compact, /undo, /retry
  model.py     — /model, /max-steps, /verbose, /plan
  files.py     — /add, /drop, /diff, /web, /search
  git.py       — /git, /commit
  system.py    — /help, /about, /version, /exit, /clear, /doctor, /init,
                 /checkpoint, /revert, /fix, /review

This file re-exports all public names for backward compatibility.
New code should import directly from the sub-modules (see __init__.py).

IPR constraints:
  IPR-3: only stdlib + rich + prompt_toolkit, no model SDK
"""

from __future__ import annotations

# Re-export from _common (shared infrastructure)
from zall.cli.commands._common import (  # noqa: F401
    SlashCommand,
    _COMMANDS,
    slash_command,
    get_known_commands,
    get_command_meta,
    handle_slash,
    _handle_bare_slash,
    _route_skill,
    _suggest_command,
    _guess_common_command,
    _setup_completion,
    _print_skills,
    _print_about,
    _print_help,
    _cmd_init_simple,
    _recalc_usage_from_timeline,
    _check_network_basic,
    _auto_step_loop,
    _estimate_tokens,
    _CATEGORY_CONTEXT,
    _CATEGORY_SESSION,
    _CATEGORY_MODEL,
    _CATEGORY_TOOLS,
    _CATEGORY_NAV,
    _INIT_RULES_TOML,
    _INIT_AGENTS_MD,
    _INIT_MCP_TOML,
    _INIT_SKILLS_TOML,
)

# Re-export from session
from zall.cli.commands.session import (  # noqa: F401
    cmd_sessions,
    cmd_resume,
    cmd_eval,
    cmd_replay,
    cmd_cost,
    cmd_compact,
    cmd_undo,
    cmd_retry,
)

# Re-export from model
from zall.cli.commands.model import (  # noqa: F401
    cmd_plan,
    cmd_max_steps,
    cmd_verbose,
    cmd_model,
    cmd_doctor,
)

# Re-export from files
from zall.cli.commands.files import (  # noqa: F401
    cmd_add,
    cmd_drop,
    cmd_diff,
    cmd_search,
    cmd_web,
)

# Re-export from git
from zall.cli.commands.git import (  # noqa: F401
    cmd_git,
    cmd_commit,
)

# Re-export from system
from zall.cli.commands.system import (  # noqa: F401
    cmd_help,
    cmd_about,
    cmd_version,
    cmd_exit,
    cmd_clear,
    cmd_checkpoint,
    cmd_revert,
    cmd_fix,
    cmd_review,
    cmd_init,
)