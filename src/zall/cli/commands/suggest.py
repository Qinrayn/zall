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

from rich.markup import escape

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


@slash_command(
    "/evolve",
    aliases=("/improve",),
    description="self-improve loop: verify → apply only verified gains (learn from Hyra)",
    category=_CATEGORY_CONTEXT,
)
def cmd_evolve(arg: str, out: Any, loop: Any | None = None,
               state: dict[str, Any] | None = None) -> str:
    """持续自改进循环 (借鉴腾讯 Hyra 递归自改进, 但只落地可验证增益)。

    与 /suggest (逐条人工 apply) 不同: /evolve 自动对所有候选做
    验证 (置信度门 + 结构/安全检查), 只落地通过验证的, 全程有据。

    用法:
      /evolve              预览 (dry-run): 验证候选, 显示会应用/拒绝, 不落地
      /evolve apply        应用所有通过验证的候选
      /evolve [apply] -c 0.9   置信度阈值 (默认 0.8)
    """
    if state is None:
        state = {}
    ext_reg = state.get("_ext_registry")
    learn_ext = ext_reg.get("auto_learn") if (ext_reg is not None and hasattr(ext_reg, "get")) else None
    if learn_ext is None:
        _write(out, "  auto-learn extension not loaded (install with /init)")
        return "handled"

    parts = arg.strip().split()
    do_apply = bool(parts) and parts[0].lower() in ("apply", "yes", "-y")
    min_conf = 0.8
    if "-c" in parts:
        try:
            min_conf = max(0.0, min(1.0, float(parts[parts.index("-c") + 1])))
        except (ValueError, IndexError):
            _write(out, "  usage: /evolve [apply] [-c <0..1>]")
            return "handled"

    try:
        from zall.core.self_improve import SelfImprovementLoop
        si = SelfImprovementLoop.from_auto_learn(
            learn_ext, min_confidence=min_conf, dry_run=not do_apply,
        )
        report = si.run()
    except Exception as e:
        _write(out, f"  \u2717 self-improve failed: {e}")
        return "handled"

    from rich.markup import escape
    for line in report.summary().split("\n"):
        _write(out, "  " + escape(line))   # escape: reason 可能含 [ , 不该被当作 markup
    if not do_apply:
        _write(out, "  [dim](dry-run — 用 /evolve apply 落地已验证的改进)[/]")
    return "handled"


# ── /lab: 可证伪经验机的落地入口 (PARADIGM Steps 1-3 串成的用户命令) ──

_LAB_BLUE_SYSTEM = (
    "You are a meticulous Python engineer. Given a TASK, output exactly ONE "
    "self-contained Python solution inside a single ```python code block and nothing else.\n"
    "Rules:\n"
    "- The script MUST run stand-alone with `python solution.py` and exit 0 on success.\n"
    "- Include a small self-test at the bottom using `assert` that checks the core "
    "behavior AND at least one edge case, then print('ok').\n"
    "- Standard library only. No prose or explanation outside the code block."
)


def _lab_get_adapter(loop: Any, state: dict[str, Any]) -> Any:
    """解析可用 adapter: loop.model_adapter -> state['_adapter'] -> 按 model 现建 (无则 None)。"""
    ad = getattr(loop, "model_adapter", None) if loop is not None else None
    if ad is not None:
        return ad
    ad = state.get("_adapter")
    if ad is not None:
        return ad
    try:
        from zall.cli import config as _cli_config
        model_name = state.get("model")
        provider = _cli_config._detect_provider(model_name)
        from zall.safety.config import load_config as _load_cfg
        timeout = _load_cfg().get("timeout", 120.0)
        ad = _cli_config._build_adapter(provider, model=model_name, timeout=timeout)
        state["_adapter"] = ad
        return ad
    except Exception:
        return None


def _make_lab_blue_fn(adapter: Any):
    """把 adapter 包成 RedBlueLoop 的 blue_fn: 灵感/任务 -> 解法代码 (单次模型调用)。"""
    from zall.core.model import Message, ToolChoice

    def blue_fn(inspiration: str) -> str:
        msgs = [
            Message(role="system", content=_LAB_BLUE_SYSTEM),
            Message.user(inspiration),
        ]
        resp = adapter.complete(msgs, [], ToolChoice.NONE)
        return resp.content or ""

    return blue_fn


def _lab_render_event(out: Any):
    """返回 on_event 回调: 把红蓝对抗每一步 live 打到 out (● 风格, 极简)。"""
    def on_event(phase: str, payload: dict[str, Any]) -> None:
        if phase == "task":
            _write(out, f"\n  conjecture  {escape(str(payload.get('task', ''))[:72])}")
            _write(out, f"    [dim]via {escape(str(payload.get('strategy', '?')))} of "
                        f"{escape(str(payload.get('parent', ''))[:40])}[/]")
        elif phase == "propose":
            _write(out, "    \u251c propose   [dim]model thinking\u2026[/]")
        elif phase == "verdict":
            ok = not payload.get("broken", True)
            mark = "\u2713 verified" if ok else "\u2717 refuted"
            n = int(payload.get("content_len", 0) or 0)
            crit = escape(str(payload.get("critique", ""))[:64])
            _write(out, f"    \u2514 refute    sandbox \u00b7 {n} chars \u00b7 {mark}  [dim]{crit}[/]")
    return on_event


def _lab_show_skills(out: Any, store: Any) -> str:
    skills = store.skills()
    if not skills:
        _write(out, "  no verified skills yet - run /lab <task> to learn one")
        return "handled"
    _write(out, f"  \u25cf learned skills ({len(skills)})")
    for r in skills[-12:]:
        _write(out, f"    \u2713 {escape(r.task[:64])}  [dim](score {r.score:.2f})[/]")
    return "handled"


def _lab_show_stats(out: Any, store: Any) -> str:
    st = store.stats()
    _write(out, "  \u25cf experience store")
    _write(out, f"    total: {st['total']}  \u00b7  verified skills: {st['verified']}"
                f"  \u00b7  unverified: {st['unverified']}")
    return "handled"


def _lab_run_task(out: Any, store: Any, blue_fn: Any, task: str) -> str:
    from zall.core.red_blue import RedBlueLoop
    from zall.core.sandbox_verifier import make_sandbox_red_fn
    _write(out, f"\n  conjecture  {escape(task[:72])}")
    rb = RedBlueLoop(
        blue_fn=blue_fn, red_fn=make_sandbox_red_fn(),
        max_rounds=1, proposals_per_round=1,
        on_event=_lab_render_event(out),
    )
    try:
        rep = rb.run(task)
    except Exception as e:
        _write(out, f"    \u2717 lab failed: {escape(str(e))}")
        return "handled"
    best = rep.best
    if best is not None and not best.broken and best.score >= 1.0:
        rec = store.record(task, best.content, verified=True, score=best.score)
        note = "skill distilled" if rec is not None else "already known"
        _write(out, f"\n  \u2713 verified \u2192 {note} \u00b7 skills: {len(store.skills())} total")
    else:
        _write(out, "\n  \u2717 refuted - no verified solution (nothing distilled)")
        _write(out, "  [dim]the sandbox rejected every proposal; try rephrasing the task[/]")
    return "handled"


def _lab_run_open_ended(out: Any, store: Any, blue_fn: Any) -> str:
    from zall.core.open_ended import run_open_ended_round
    if not store.skills():
        _write(out, "\n  [dim]no verified skills yet - bootstrap one first, e.g.:[/]")
        _write(out, "  [dim]  /lab write a function that returns the nth fibonacci number[/]")
        return "handled"
    try:
        rep = run_open_ended_round(
            blue_fn, store=store, k=2, max_rounds=1, proposals_per_round=1,
            on_event=_lab_render_event(out),
        )
    except Exception as e:
        _write(out, f"    \u2717 lab failed: {escape(str(e))}")
        return "handled"
    _write(out, f"\n  \u2713 {rep.verified}/{rep.generated} verified \u2192 "
                f"{len(rep.new_skills)} new skill(s) \u00b7 skills: {len(store.skills())} total")
    return "handled"


@slash_command(
    "/lab",
    aliases=("/selfplay",),
    description="verifier-grounded self-improvement: conjecture -> sandbox-refute -> distill skill",
    category=_CATEGORY_CONTEXT,
)
def cmd_lab(arg: str, out: Any, loop: Any | None = None,
            state: dict[str, Any] | None = None) -> str:
    """可证伪经验机的落地入口 (PARADIGM): 模型提解 -> 沙盒客观证伪 -> 熬过者蒸馏成技能。

    与 /evolve (auto-learn 工具建议) 不同: /lab 用**真实模型**提解、在**隔离沙盒**里
    执行证伪 (grounded reward), 只有熬过证伪的解才作为技能写入经验库 (跨会话复利)。

    用法:
      /lab <task>     一个任务: 模型提解 -> 沙盒执行证伪 -> verified 则记为技能
      /lab            开放式一轮: 从已验证技能派生新任务再各自提解+证伪 (需已有技能)
      /lab skills     列出已学到的技能 (verified 经验)
      /lab stats      经验库统计
    """
    if state is None:
        state = {}
    from zall.core.experience_store import get_experience_store
    store = state.get("_experience_store") or get_experience_store()

    sub = arg.strip()
    low = sub.lower()
    if low in ("skills", "skill"):
        return _lab_show_skills(out, store)
    if low in ("stats", "stat"):
        return _lab_show_stats(out, store)

    adapter = _lab_get_adapter(loop, state)
    if adapter is None:
        _write(out, "  \u2717 no model available - set an API key (or run a turn first)")
        return "handled"
    blue_fn = _make_lab_blue_fn(adapter)
    _write(out, "  \u25cf lab \u00b7 verifier-grounded self-improvement")
    if sub:
        return _lab_run_task(out, store, blue_fn, sub)
    return _lab_run_open_ended(out, store, blue_fn)


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