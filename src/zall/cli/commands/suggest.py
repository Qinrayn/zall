"""zall.cli.commands.suggest — Self-evolution commands.

Commands:
  /suggest  — List pending SelfSuggestions (auto-learned improvements)
  /learn    — Show learned statistics and manage ignore list

IPR-3: stdlib + rich only, no model SDK.
"""

from __future__ import annotations

import json
import os
from typing import Any

from zall.cli.commands._common import (
    _CATEGORY_CONTEXT,
    slash_command,
)
from zall.cli.render import _shared_console
from zall.core.lifecycle import SelfSuggestion

# ── Ignore list persistence ──
_IGNORE_FILE = "ignored_suggestions.json"


def _get_ignored_path() -> str:
    from zall.safety.config import CONFIG_DIR
    learned_dir = os.path.join(str(CONFIG_DIR), "learned")
    os.makedirs(learned_dir, exist_ok=True)
    return os.path.join(learned_dir, _IGNORE_FILE)


def _load_ignored() -> set[str]:
    path = _get_ignored_path()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_ignored(ignored: set[str]) -> None:
    path = _get_ignored_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(ignored), f, ensure_ascii=False)
    except OSError:
        pass


def _suggestion_key(s: SelfSuggestion) -> str:
    """Unique key for a suggestion (used for dedup and ignore)."""
    return f"{s.kind}:{s.target}:{s.value}"


# ──────────────────────────────────────────────────────────────────────────
# /suggest
# ──────────────────────────────────────────────────────────────────────────


@slash_command(
    "/suggest",
    aliases=("/sug",),
    description="show self-evolution suggestions from auto-learn",
    category=_CATEGORY_CONTEXT,
)
def cmd_suggest(arg: str, out: Any, loop: Any | None = None,
                state: dict[str, Any] | None = None) -> str:
    """List pending SelfSuggestions with apply/ignore/detail.

    Usage:
      /suggest          — list all pending suggestions
      /suggest apply N  — apply suggestion #N
      /suggest ignore N — ignore suggestion #N (won't show again)
      /suggest detail N — show full details of suggestion #N
    """
    if state is None:
        state = {}

    ext_reg = state.get("_ext_registry")
    if ext_reg is None:
        _write(out, "  no extension registry available")
        return "handled"

    learn_ext = ext_reg.get("auto_learn") if hasattr(ext_reg, "get") else None
    if learn_ext is None:
        _write(out, "  auto-learn extension not loaded (install with /init)")
        return "handled"

    try:
        suggestions = learn_ext.get_suggestions()
    except Exception:
        _write(out, "  \u2717 failed to read suggestions")
        return "handled"

    if not suggestions:
        _write(out, "  no pending suggestions. Run more tasks to generate patterns.")
        return "handled"

    # Filter out ignored suggestions
    ignored = _load_ignored()
    suggestions = [s for s in suggestions if _suggestion_key(s) not in ignored]

    if not suggestions:
        _write(out, "  all suggestions have been ignored. Use /learn clear to reset.")
        return "handled"

    parts = arg.strip().split(maxsplit=2)
    action = parts[0].lower() if parts else "list"

    if action == "apply" and len(parts) >= 2:
        try:
            idx = int(parts[1]) - 1
            if 0 <= idx < len(suggestions):
                s = suggestions[idx]
                result = learn_ext.apply_suggestion(s)
                if result.get("applied"):
                    _write(out, f"  \u2713 {result['message']}")
                else:
                    _write(out, f"  \u2717 {result.get('message', 'apply failed')}")
            else:
                _write(out, f"  suggestion #{parts[1]} not found (1-{len(suggestions)})")
        except ValueError:
            _write(out, "  usage: /suggest apply <N>")
            return "handled"

    if action == "ignore" and len(parts) >= 2:
        try:
            idx = int(parts[1]) - 1
            if 0 <= idx < len(suggestions):
                s = suggestions[idx]
                ignored.add(_suggestion_key(s))
                _save_ignored(ignored)
                _write(out, f"  \u2713 ignored suggestion #{parts[1]}: {s.kind} {s.target}")
            else:
                _write(out, f"  suggestion #{parts[1]} not found (1-{len(suggestions)})")
        except ValueError:
            _write(out, "  usage: /suggest ignore <N>")
        return "handled"

    if action == "detail" and len(parts) >= 2:
        try:
            idx = int(parts[1]) - 1
            if 0 <= idx < len(suggestions):
                s = suggestions[idx]
                _print_suggestion_detail(out, idx + 1, s)
            else:
                _write(out, f"  suggestion #{parts[1]} not found (1-{len(suggestions)})")
        except ValueError:
            _write(out, "  usage: /suggest detail <N>")
        return "handled"

    # Default: list suggestions
    _write(out, f"  \u2139 {len(suggestions)} suggestion(s) pending:")
    for i, s in enumerate(suggestions, 1):
        _print_suggestion_summary(out, i, s)
    _write(out, "  use /suggest apply <N> | ignore <N> | detail <N>")
    return "handled"


# ──────────────────────────────────────────────────────────────────────────
# /learn
# ──────────────────────────────────────────────────────────────────────────


@slash_command(
    "/learn",
    aliases=(),
    description="show auto-learn statistics",
    category=_CATEGORY_CONTEXT,
)
def cmd_learn(arg: str, out: Any, loop: Any | None = None,
              state: dict[str, Any] | None = None) -> str:
    """Show learned statistics and manage ignore list.

    Usage:
      /learn          — show all learned statistics
      /learn clear    — clear the ignored suggestions list
      /learn suggest  — same as /suggest (alias for convenience)
    """
    if state is None:
        state = {}

    parts = arg.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else "stats"

    if action == "clear":
        _save_ignored(set())
        _write(out, "  \u2713 ignored suggestions list cleared")
        return "handled"

    if action == "suggest":
        return cmd_suggest("", out, loop, state)

    ext_reg = state.get("_ext_registry")
    if ext_reg is None:
        _write(out, "  no extension registry available")
        return "handled"

    learn_ext = ext_reg.get("auto_learn") if hasattr(ext_reg, "get") else None
    if learn_ext is None:
        _write(out, "  auto-learn extension not loaded")
        return "handled"

    try:
        stats = learn_ext.get_stats()
    except Exception:
        _write(out, "  \u2717 failed to read stats")
        return "handled"

    if not stats:
        _write(out, "  no statistics available yet")
        return "handled"

    # Render stats
    tool_counts = stats.get("tool_counts", {})
    tool_errors = stats.get("tool_errors", {})
    tool_chains = stats.get("tool_chains", 0)
    total_suggestions = stats.get("total_suggestions", 0)
    sessions_tracked = stats.get("sessions_tracked", 0)

    _write(out, "  \u2139 Learned Statistics")
    _write(out, f"    sessions tracked: {sessions_tracked}")
    _write(out, f"    tool chains: {tool_chains}")
    _write(out, f"    total suggestions generated: {total_suggestions}")

    if tool_counts:
        _write(out, "    tool usage:")
        for tid, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:10]:
            err_count = tool_errors.get(tid, 0)
            err_mark = f" \u2717{err_count}" if err_count else ""
            _write(out, f"      {tid}: {count}{err_mark}")

    ignored = _load_ignored()
    if ignored:
        _write(out, f"    ignored suggestions: {len(ignored)}")

    ignored_count = len(_load_ignored())
    _write(out, f"  use /learn clear to reset ignored suggestions ({ignored_count} ignored)")
    return "handled"


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

_SUGGESTION_ICONS = {
    "adjust_k": "\u2699",
    "create_skill": "\u2728",
    "register_goaltype": "\ud83d\udce6",
    "add_rule": "\ud83d\udee1",
    "adjust_judge": "\u2696",
}


def _print_suggestion_summary(out: Any, idx: int, s: SelfSuggestion) -> None:
    icon = _SUGGESTION_ICONS.get(s.kind, "\u2753")
    conf = f"{s.confidence:.0%}" if s.confidence >= 0.01 else ""
    _write(out, f"  [{idx}] {icon} {s.kind} {s.target} {conf}")


def _print_suggestion_detail(out: Any, idx: int, s: SelfSuggestion) -> None:
    _write(out, f"  [{idx}] {s.kind}")
    _write(out, f"    target:    {s.target}")
    _write(out, f"    value:     {s.value}")
    _write(out, f"    confidence: {s.confidence:.0%}")
    _write(out, f"    evidence:  {s.evidence}")


def _write(out: Any, msg: str) -> None:
    """Write a message to the output stream."""
    if hasattr(out, "isatty") and out.isatty():
        try:
            c = _shared_console(out)
            c.print(msg)
        except Exception:
            out.write(msg + "\n")
            out.flush()
    else:
        out.write(msg + "\n")
        out.flush()