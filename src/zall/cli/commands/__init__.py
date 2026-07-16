"""zall.cli.commands — All slash command handlers (package, v0.2.1).

Command implementations split into sub-modules by responsibility:
  _common.py   — shared infrastructure (registry, routing, utilities)
  session.py   — /sessions, /resume, /eval, /replay, /cost, /compact, /undo, /retry
  model.py     — /model, /max-steps, /verbose, /plan
  files.py     — /add, /drop, /diff, /web, /search
  git.py       — /git, /commit
  system.py    — /help, /about, /version, /exit, /clear, /doctor, /init,
                 /checkpoint, /revert, /fix, /review
  _legacy.py   — backward-compatible re-export wrapper (for direct imports)
"""

from __future__ import annotations

# 显式导出清单: app.py / repl_ui.py 从本包 import 的所有符号
__all__ = [
    # _common
    "SlashCommand", "_COMMANDS", "slash_command",
    "get_known_commands", "get_command_meta", "handle_slash",
    "_handle_bare_slash", "_route_skill",
    "_suggest_command", "_guess_common_command", "_setup_completion",
    "_print_skills", "_print_about", "_print_help",
    "_cmd_init_simple", "_recalc_usage_from_timeline",
    "_check_network_basic", "_check_network_http", "_check_git_health",
    "_check_mcp_health", "_check_dependency_version", "_check_path_tools",
    "_check_trust_anchor", "_check_disk_space",
    "_auto_step_loop", "_estimate_tokens",
    "_CATEGORY_CONTEXT", "_CATEGORY_SESSION", "_CATEGORY_MODEL",
    "_CATEGORY_TOOLS", "_CATEGORY_NAV", "_CATEGORY_VIEW",
    "_INIT_RULES_TOML", "_INIT_AGENTS_MD", "_INIT_MCP_TOML", "_INIT_SKILLS_TOML",
    "_generate_agents_md", "cmd_expand", "cmd_fold",
    # session
    "cmd_sessions", "cmd_resume", "cmd_eval", "cmd_replay",
    "cmd_cost", "cmd_compact", "cmd_undo", "cmd_retry",
    "cmd_remember", "cmd_forget",
    # model
    "cmd_plan", "cmd_max_steps", "cmd_verbose", "cmd_model", "cmd_doctor",
    # files
    "cmd_add", "cmd_drop", "cmd_diff", "cmd_search", "cmd_web",
    # git
    "cmd_git", "cmd_commit",
    # system
    "cmd_help", "cmd_about", "cmd_version", "cmd_exit", "cmd_clear",
    "cmd_checkpoint", "cmd_revert", "cmd_fix", "cmd_review",
    "cmd_init", "cmd_update",
    # reload
    "cmd_reload",
]

# Import from _common (shared infrastructure)
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
    _check_network_http,
    _check_git_health,
    _check_mcp_health,
    _check_dependency_version,
    _check_path_tools,
    _check_trust_anchor,
    _check_disk_space,
    _auto_step_loop,
    _estimate_tokens,
    _CATEGORY_CONTEXT,
    _CATEGORY_SESSION,
    _CATEGORY_MODEL,
    _CATEGORY_TOOLS,
    _CATEGORY_NAV,
    _CATEGORY_VIEW,
    _INIT_RULES_TOML,
    _INIT_AGENTS_MD,
    _INIT_MCP_TOML,
    _INIT_SKILLS_TOML,
    _generate_agents_md,
    cmd_expand,
    cmd_fold,
)

# Import from session
from zall.cli.commands.session import (  # noqa: F401
    cmd_sessions,
    cmd_resume,
    cmd_eval,
    cmd_replay,
    cmd_cost,
    cmd_compact,
    cmd_undo,
    cmd_retry,
    cmd_remember,
    cmd_forget,
)

# Import from model
from zall.cli.commands.model import (  # noqa: F401
    cmd_plan,
    cmd_max_steps,
    cmd_verbose,
    cmd_model,
    cmd_doctor,
)

# Import from files
from zall.cli.commands.files import (  # noqa: F401
    cmd_add,
    cmd_drop,
    cmd_diff,
    cmd_search,
    cmd_web,
)

# Import from git
from zall.cli.commands.git import (  # noqa: F401
    cmd_git,
    cmd_commit,
)

# Import from system
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
    cmd_update,
)

# Import from reload
from zall.cli.commands.reload import (  # noqa: F401
    cmd_reload,
)

# v0.4.0: 新系统命令
from zall.cli.commands.v040 import (  # noqa: F401
    cmd_lsp,
    cmd_sandbox,
    cmd_codegraph,
    cmd_chatstate,
    cmd_plugin,
)