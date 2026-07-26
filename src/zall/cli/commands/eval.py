"""zall.cli.commands.eval — /eval command using core/eval (Phase 1).

Phase 1 评估体系落地: /eval 命令读取 timeline.jsonl, 计算
goal_achievement_rate + timeline_integrity_rate。

用法:
  /eval              — 评估最近一次 session
  /eval <session_id> — 评估指定 session
  /eval --all        — 评估所有 sessions

IPR constraints:
  IPR-0: invariant tests at tests/test_eval_invariants.py
         test_eval_command_produces_metrics_from_timeline (反例: 无 timeline → eval 输出空)
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zall.cli.commands._common import (
    _CATEGORY_SESSION,
    slash_command,
)
from zall.core.eval import (
    CoreEvalReport,
    evaluate_from_timeline,
)


def _get_sessions_dir() -> Path:
    """获取 sessions 目录。"""
    from zall.cli.session import _get_sessions_dir as _inner
    return _inner()


def _list_sessions_text(base_dir: Path) -> list[tuple[str, Path]]:
    """列出所有 session (返回 (run_id, dir_path) 列表)。"""
    sessions: list[tuple[str, Path]] = []
    if not base_dir.exists():
        return sessions
    for d in sorted(base_dir.iterdir()):
        if d.is_dir():
            meta = d / "meta.json"
            if meta.exists():
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    sessions.append((data.get("run_id", d.name), d))
                except (json.JSONDecodeError, OSError):
                    sessions.append((d.name, d))
            else:
                sessions.append((d.name, d))
    return sessions


@slash_command("/eval", description="evaluate session from timeline (Phase 1)", category=_CATEGORY_SESSION)
def cmd_eval(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """/eval: 评估 session(s) 从 timeline。

    参数:
      (空)        — 评估最近一次 session
      <session_id> — 评估指定 session (匹配 run_id 前缀)
      --all       — 评估所有 sessions
    """
    parts = arg.split()
    subcmd = parts[0] if parts else ""

    if subcmd == "--all":
        return _eval_all(out)

    if subcmd and not subcmd.startswith("-"):
        return _eval_one(out, subcmd)

    # 默认: 评估最近一次 session
    return _eval_latest(out)


def _eval_latest(out: Any) -> str:
    """评估最近一次 session。"""
    base_dir = _get_sessions_dir()
    sessions = _list_sessions_text(base_dir)
    if not sessions:
        out.write("  (no sessions to evaluate)\n")
        return ""

    _, session_dir = sessions[-1]
    report = evaluate_from_timeline(session_dir)
    if report is None:
        out.write(f"  (no timeline for session {session_dir.name})\n")
        return ""

    _render_report(out, report)
    return ""


def _eval_one(out: Any, session_id: str) -> str:
    """评估指定 session。"""
    base_dir = _get_sessions_dir()
    sessions = _list_sessions_text(base_dir)
    matched = [(sid, d) for sid, d in sessions if session_id in sid]
    if not matched:
        out.write(f"  (no session matching '{session_id}')\n")
        return ""

    # 取第一个匹配
    _, session_dir = matched[0]
    report = evaluate_from_timeline(session_dir)
    if report is None:
        out.write(f"  (no timeline for session {session_dir.name})\n")
        return ""

    _render_report(out, report)
    return ""


def _eval_all(out: Any) -> str:
    """评估所有 sessions。"""
    base_dir = _get_sessions_dir()
    sessions = _list_sessions_text(base_dir)
    if not sessions:
        out.write("  (no sessions to evaluate)\n")
        return ""

    reports: list[CoreEvalReport] = []
    skipped = 0
    for _, session_dir in sessions:
        report = evaluate_from_timeline(session_dir)
        if report is not None:
            reports.append(report)
        else:
            skipped += 1

    if not reports:
        out.write("  (no timelines available for any session)\n")
        return ""

    # 汇总
    total = len(reports)
    healthy = sum(1 for r in reports if r.health == "healthy")
    warning = sum(1 for r in reports if r.health == "warning")
    critical = sum(1 for r in reports if r.health == "critical")

    out.write(f"  eval - {total} session(s) with timeline")
    if skipped:
        out.write(f" ({skipped} skipped, no timeline)")
    out.write(f"\n  healthy={healthy} warning={warning} critical={critical}\n")
    out.write("\n")

    # 逐个显示
    for i, report in enumerate(reports):
        out.write(f"  [{i+1}/{total}] Run {report.run_id}: {report.health.upper()}\n")
        for m in report.metrics:
            out.write(f"    {m.name:25s} {m.value:6.0%}  |  {m.anti_name:25s} {m.anti_value:6.0%}\n")
            for n in m.notes:
                out.write(f"      {n}\n")

    return ""


def _render_report(out: Any, report: CoreEvalReport) -> None:
    """渲染评估报告 — 支持 rich 表格 (TTY) 和纯文本回退。"""
    # 判断是否支持 rich 表格
    is_tty = hasattr(out, "isatty") and out.isatty()
    try:
        from rich.table import Table

        from zall.cli.render import _shared_console
        has_rich = True
    except ImportError:
        has_rich = False

    if is_tty and has_rich:
        # Rich 表格渲染
        c = _shared_console(out)
        c.print()
        c.print(f"  [bold]eval[/] [dim]·[/] run [dim]{report.run_id[:16]}[/] [dim]·[/] health: ", end="")
        health_colors = {"healthy": "green", "warning": "yellow", "critical": "red"}
        c.print(f"[bold {health_colors.get(report.health, 'dim')}]{report.health}[/]")

        for m in report.metrics:
            table = Table(show_header=True, header_style="dim",
                          border_style="dim", padding=(0, 1), box=None)
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", justify="right")
            table.add_column("Anti-Metric", style="cyan", no_wrap=True)
            table.add_column("Anti-Value", justify="right")
            # 颜色编码
            v_color = "green" if m.value >= 0.8 else ("yellow" if m.value >= 0.5 else "red")
            a_color = "green" if m.anti_value >= 0.8 else ("yellow" if m.anti_value >= 0.5 else "red")
            table.add_row(
                m.name, f"[{v_color}]{m.value:6.0%}[/]",
                m.anti_name, f"[{a_color}]{m.anti_value:6.0%}[/]",
            )
            c.print(table)
            for n in m.notes:
                note_color = "red" if "WARNING" in n or "CRITICAL" in n else "dim"
                c.print(f"  [{note_color}]{n}[/]")
    else:
        # 纯文本回退
        out.write(f"\n  eval - run {report.run_id} - health: ")
        if is_tty:
            codes = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "dim": "\033[2m"}
            reset = "\033[0m"
            color = codes.get(report.health, "")
            out.write(f"{color}{report.health}{reset}\n")
        else:
            out.write(f"{report.health}\n")

        for m in report.metrics:
            out.write(f"  {m.name:25s} {m.value:6.0%}  |  {m.anti_name:25s} {m.anti_value:6.0%}\n")
            for n in m.notes:
                out.write(f"    {n}\n")
    out.write("  " + "-" * 50 + "\n")
    out.flush()


__all__ = ["cmd_eval"]