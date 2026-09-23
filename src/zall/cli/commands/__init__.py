"""zall.cli.commands — All slash command handlers (package, v0.2.1).

Command implementations split into sub-modules by responsibility:
  _common.py   — shared infrastructure (registry, routing, utilities)
  session.py   — /sessions, /resume, /eval, /replay, /compact, /undo, /retry
  model.py     — /model, /max-steps, /verbose, /plan
  files.py     — /add, /drop, /diff
  git.py       — /git, /commit
  system.py    — /help, /about, /version, /exit, /clear, /doctor, /init,
                 /checkpoint, /revert, /fix, /review
"""

from __future__ import annotations

# 显式导出清单: app.py / repl_ui.py 从本包 import 的所有符号
__all__ = [
    # _common
    "SlashCommand", "_COMMANDS", "slash_command",
    "get_known_commands", "get_command_meta", "handle_slash",
    "get_palette_commands", "fuzzy_rank",
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
    "cmd_compact", "cmd_undo", "cmd_retry",
    "cmd_remember", "cmd_forget",
    # model
    "cmd_plan", "cmd_max_steps", "cmd_verbose", "cmd_model", "cmd_doctor",
    # files
    "cmd_add", "cmd_drop", "cmd_diff",
    # git
    "cmd_git", "cmd_commit",
    # system
    "cmd_help", "cmd_about", "cmd_version", "cmd_exit", "cmd_clear",
    "cmd_checkpoint", "cmd_revert", "cmd_fix", "cmd_review",
    "cmd_init", "cmd_update", "cmd_advanced",
    "cmd_forget_permissions",
    # reload
    "cmd_reload",
    # v040
    "cmd_lsp", "cmd_sandbox", "cmd_codegraph", "cmd_chatstate", "cmd_plugin",
    # v0.4.10 self-evolution
    "cmd_suggest", "cmd_learn",
    # PARADIGM: verifier-grounded self-improvement
    "cmd_lab",
    # v0.6.0 UX
    "cmd_strict", "cmd_fast",
    # v1.1 Science Kit (E3)
    "cmd_science",
    # v1.1 Verifiability (E6)
    "cmd_verify",
    # F1: 配置一等化 (/config)
    "cmd_config",
    # F2: 跨会话记忆 (/memory)
    "cmd_memory",
]

# Import from _common (shared infrastructure)
from zall.cli.commands._common import (
    _CATEGORY_ADVANCED,
    _CATEGORY_CONTEXT,
    _CATEGORY_MODEL,
    _CATEGORY_NAV,
    _CATEGORY_SESSION,
    _CATEGORY_TOOLS,
    _CATEGORY_VIEW,
    _COMMANDS,
    _INIT_AGENTS_MD,
    _INIT_MCP_TOML,
    _INIT_RULES_TOML,
    _INIT_SKILLS_TOML,
    SlashCommand,
    _auto_step_loop,
    _check_dependency_version,
    _check_disk_space,
    _check_git_health,
    _check_mcp_health,
    _check_network_basic,
    _check_network_http,
    _check_path_tools,
    _check_trust_anchor,
    _cmd_init_simple,
    _estimate_tokens,
    _generate_agents_md,
    _guess_common_command,
    _handle_bare_slash,
    _print_about,
    _print_advanced_help,
    _print_help,
    _print_skills,
    _recalc_usage_from_timeline,
    _route_skill,
    _setup_completion,
    _suggest_command,
    cmd_expand,
    cmd_fold,
    fuzzy_rank,
    get_command_meta,
    get_known_commands,
    get_palette_commands,
    handle_slash,
    slash_command,
)

# F1: 配置一等化 (/config - api_key/api_base/window_size/采样参数设置)
from zall.cli.commands.config import (
    cmd_config,
)

# kimi 对标: /btw 侧问 (不污染主上下文的快速问答)
from zall.cli.commands.btw import cmd_btw as cmd_btw

# Import from eval (Phase 1: core/eval timeline-based evaluation)
from zall.cli.commands.eval import cmd_eval as cmd_eval

# Import from files
from zall.cli.commands.files import (
    cmd_add,
    cmd_diff,
    cmd_drop,
)

# Import from git
from zall.cli.commands.git import (
    cmd_commit,
    cmd_git,
)

# Import from model
from zall.cli.commands.model import (
    cmd_doctor,
    cmd_max_steps,
    cmd_model,
    cmd_plan,
    cmd_verbose,
)

# Import from reload
from zall.cli.commands.reload import (
    cmd_reload,
)

# v1.1: Science Kit (E3, §12.3)
from zall.cli.commands.science import (
    cmd_science,
)

# Import from session
from zall.cli.commands.session import (
    cmd_compact,
    cmd_forget,
    cmd_remember,
    cmd_replay,
    cmd_resume,
    cmd_retry,
    cmd_sessions,
    cmd_undo,
)

# v0.4.10: 自进化命令
from zall.cli.commands.suggest import (
    cmd_lab,
    cmd_learn,
    cmd_suggest,
)

# Import from system
from zall.cli.commands.system import (
    cmd_about,
    cmd_advanced,
    cmd_checkpoint,
    cmd_clear,
    cmd_exit,
    cmd_fix,
    cmd_forget_permissions,
    cmd_help,
    cmd_init,
    cmd_memory,
    cmd_revert,
    cmd_review,
    cmd_update,
    cmd_verify,
    cmd_version,
)

# v0.4.0: 新系统命令
from zall.cli.commands.v040 import (
    cmd_chatstate,
    cmd_codegraph,
    cmd_lsp,
    cmd_plugin,
    cmd_sandbox,
)
