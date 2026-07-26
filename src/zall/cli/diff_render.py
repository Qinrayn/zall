"""Rich diff 渲染三形态 (G1, kimi diff_render.py 对标).

三形态:
  - ``render_diff_panel``   完整 diff: 行号 + 整行背景色 + 词级内联高亮 (工具结果)
  - ``render_diff_preview`` 紧凑预览: 只显改动行, 上限 N 行 (审批面板)
  - ``render_diff_summary`` 超大文件降级: 只显行数变化摘要

数据来源两条路:
  - ``build_hunks(old, new)``        从原文直接构建 (SequenceMatcher, 最高保真)
  - ``parse_unified_hunks(text)``    从既有 unified diff 文本重建 (artifacts["diff"])

kimi 关键技巧移植:
  - SequenceMatcher.get_grouped_opcodes(n=3) 直接建 hunk, 免去 unified 文本往返
  - 词级内联高亮: 连续 -/+ 块按序配对, ratio()<0.5 (过于不同) 跳过
  - hunk 间以 ⋮ 分隔; 行号列宽按最大行号自适应
  - 颜色全部来自 theme.active() 的 diff_* 槽位 (G6 单一色源)

IPR constraints:
  IPR-0: tests/test_diff_render_invariants.py (含反例)
  IPR-3: 渲染层 (cli/), stdlib + rich only
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum, auto
from typing import Any

from rich.text import Text

# 词级内联高亮的相似度门槛: 低于此值的 -/+ 行对视为"整行重写", 不做词级配对
INLINE_DIFF_MIN_RATIO = 0.5

# 审批预览默认最多显示的改动行数 (kimi MAX_PREVIEW_CHANGED_LINES)
MAX_PREVIEW_CHANGED_LINES = 6

# 超过此行数的输入降级为 summary (防 SequenceMatcher O(n^2) 卡顿)
MAX_INLINE_DIFF_LINES = 5000


class DiffLineKind(Enum):
    CONTEXT = auto()
    ADD = auto()
    DELETE = auto()


@dataclass
class DiffLine:
    kind: DiffLineKind
    old_num: int  # 0 = 不适用 (ADD 行无旧行号)
    new_num: int  # 0 = 不适用 (DELETE 行无新行号)
    code: str
    content: Text | None = field(default=None, compare=False)
    is_inline_paired: bool = False


# ──────────────────────────────────────────────────────────────────────────
# Hunk 构建 — 两条来源路径
# ──────────────────────────────────────────────────────────────────────────


def build_hunks(
    old_text: str,
    new_text: str,
    old_start: int = 1,
    new_start: int = 1,
    n_context: int = 3,
) -> list[list[DiffLine]]:
    """从原文直接构建 hunk 列表 (kimi _build_diff_lines)。

    old_start/new_start 是 old_text/new_text 首行在各自文件中的行号 (1-based)。
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    hunks: list[list[DiffLine]] = []
    for group in matcher.get_grouped_opcodes(n=n_context):
        hunk: list[DiffLine] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    hunk.append(DiffLine(
                        DiffLineKind.CONTEXT,
                        old_start + i1 + k, new_start + j1 + k,
                        old_lines[i1 + k],
                    ))
                continue
            # delete/replace: 先旧行, insert/replace: 后新行 (同 unified 排列)
            if tag in ("delete", "replace"):
                for k in range(i2 - i1):
                    hunk.append(DiffLine(
                        DiffLineKind.DELETE,
                        old_start + i1 + k, 0, old_lines[i1 + k],
                    ))
            if tag in ("insert", "replace"):
                for k in range(j2 - j1):
                    hunk.append(DiffLine(
                        DiffLineKind.ADD,
                        0, new_start + j1 + k, new_lines[j1 + k],
                    ))
        if hunk:
            hunks.append(hunk)
    return hunks


_RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_unified_hunks(diff_text: str, line_offset: int = 0) -> tuple[list[list[DiffLine]], bool]:
    """从 unified diff 文本重建 hunk (供只有 artifacts["diff"] 的路径)。

    line_offset: 把 @@ 头的相对行号平移到真实文件行号
    (tools/_diff.py 只对 old/new 片段 diff, 行号从 1 起;
    edit_file artifacts["start_line"] - 1 即为偏移)。

    Returns:
        (hunks, truncated) — truncated=True 表示遇到截断标记行
        ("... (diff truncated" — 来自 tools/_diff.py 的 50 行截断)。
    """
    hunks: list[list[DiffLine]] = []
    cur: list[DiffLine] = []
    old_no = new_no = 0
    in_hunk = False
    truncated = False
    for line in diff_text.split("\n"):
        m = _RE_HUNK_HEADER.match(line)
        if m:
            if cur:
                hunks.append(cur)
                cur = []
            old_no = int(m.group(1))
            new_no = int(m.group(2))
            in_hunk = True
            continue
        if line.startswith("... (diff truncated"):
            truncated = True
            continue
        if not in_hunk or line.startswith(("---", "+++")):
            continue
        if line.startswith("+"):
            cur.append(DiffLine(DiffLineKind.ADD, 0, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            cur.append(DiffLine(DiffLineKind.DELETE, old_no, 0, line[1:]))
            old_no += 1
        elif line.startswith(" ") or line == "":
            # 上下文行; 尾部空串来自 split 的收尾, 只有在 hunk 中间才算内容
            if line == "" and not cur:
                continue
            cur.append(DiffLine(DiffLineKind.CONTEXT, old_no, new_no, line[1:]))
            old_no += 1
            new_no += 1
    if cur:
        # split 收尾可能带一个假上下文空行 (diff 文本以 \n 结尾), 剔除
        if cur and cur[-1].kind == DiffLineKind.CONTEXT and cur[-1].code == "":
            cur.pop()
        if cur:
            hunks.append(cur)
    if line_offset:
        for hunk in hunks:
            for dl in hunk:
                if dl.old_num:
                    dl.old_num += line_offset
                if dl.new_num:
                    dl.new_num += line_offset
    return hunks, truncated


def count_changes(hunks: list[list[DiffLine]]) -> tuple[int, int]:
    """统计 (added, removed) 行数。"""
    added = removed = 0
    for hunk in hunks:
        for dl in hunk:
            if dl.kind == DiffLineKind.ADD:
                added += 1
            elif dl.kind == DiffLineKind.DELETE:
                removed += 1
    return added, removed


def should_summarize(old_text: str, new_text: str) -> bool:
    """超大输入降级判定 (SequenceMatcher 二次方复杂度护栏)。"""
    return max(old_text.count("\n"), new_text.count("\n")) + 1 > MAX_INLINE_DIFF_LINES


# ──────────────────────────────────────────────────────────────────────────
# 词级内联高亮 (kimi _apply_inline_diff)
# ──────────────────────────────────────────────────────────────────────────


def _diff_colors() -> tuple[str, str, str, str]:
    """(add_bg, del_bg, add_hl, del_hl) — 主题驱动, 每次现取 (支持热切换)。"""
    from zall.cli import theme as theme_mod
    t = theme_mod.current()
    return (
        f"on {t.diff_add_bg}",
        f"on {t.diff_del_bg}",
        f"on {t.diff_add_hl}",
        f"on {t.diff_del_hl}",
    )


def _apply_inline_diff(del_lines: list[DiffLine], add_lines: list[DiffLine]) -> None:
    """把连续 -/+ 块按序配对, 相似行 (ratio>=0.5) 做字符级 opcodes 高亮。

    直接在 raw 字符串索引上 stylize (无语法高亮阶段无需 offset map;
    G7 接语法高亮时引入 kimi 的 tab offset map)。
    """
    _, _, add_hl, del_hl = _diff_colors()
    paired = min(len(del_lines), len(add_lines))
    for j in range(paired):
        old_code = del_lines[j].code
        new_code = add_lines[j].code
        old_text = Text(old_code)
        new_text = Text(new_code)
        del_lines[j].content = old_text
        add_lines[j].content = new_text
        sm = SequenceMatcher(None, old_code, new_code)
        if sm.ratio() < INLINE_DIFF_MIN_RATIO:
            continue
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("delete", "replace"):
                old_text.stylize(del_hl, i1, i2)
            if op in ("insert", "replace"):
                new_text.stylize(add_hl, j1, j2)
        del_lines[j].is_inline_paired = True
        add_lines[j].is_inline_paired = True


def _stylize_hunk(hunk: list[DiffLine]) -> None:
    """两遍: 先对连续 -/+ 块做词级配对, 再补齐余下行的 plain Text。"""
    i = 0
    while i < len(hunk):
        if hunk[i].kind == DiffLineKind.DELETE:
            del_start = i
            while i < len(hunk) and hunk[i].kind == DiffLineKind.DELETE:
                i += 1
            add_start = i
            while i < len(hunk) and hunk[i].kind == DiffLineKind.ADD:
                i += 1
            _apply_inline_diff(hunk[del_start:add_start], hunk[add_start:i])
        else:
            i += 1
    for dl in hunk:
        if dl.content is None:
            dl.content = Text(dl.code)


# ──────────────────────────────────────────────────────────────────────────
# 形态一: 完整 Panel (工具结果)
# ──────────────────────────────────────────────────────────────────────────


def _build_header(path: str, added: int, removed: int) -> Text:
    header = Text()
    if added:
        header.append(f"+{added} ", style="bold green")
    if removed:
        header.append(f"-{removed} ", style="bold red")
    header.append(path or "diff")
    return header


def render_diff_panel(path: str, hunks: list[list[DiffLine]], *, truncated: bool = False) -> Any:
    """完整 diff 面板: 行号列 + 标记列 + 内容列, 整行背景色, 词级高亮。"""
    from rich import box
    from rich.panel import Panel
    from rich.table import Table

    added, removed = count_changes(hunks)
    for hunk in hunks:
        _stylize_hunk(hunk)

    max_ln = 0
    for hunk in hunks:
        for dl in hunk:
            max_ln = max(max_ln, dl.old_num, dl.new_num)
    num_width = max(len(str(max_ln)), 2)

    table = Table(
        show_header=False, box=None, padding=(0, 0), show_edge=False, expand=True,
    )
    table.add_column(justify="right", width=num_width, no_wrap=True)
    table.add_column(width=3, no_wrap=True)
    table.add_column(ratio=1)

    add_bg, del_bg, _, _ = _diff_colors()
    for hunk_idx, hunk in enumerate(hunks):
        if hunk_idx > 0:
            table.add_row(Text("\u22ee", style="dim"), Text(""), Text(""))
        for dl in hunk:
            assert dl.content is not None
            if dl.kind == DiffLineKind.ADD:
                table.add_row(
                    Text(str(dl.new_num)), Text(" + ", style="green"),
                    dl.content, style=add_bg,
                )
            elif dl.kind == DiffLineKind.DELETE:
                table.add_row(
                    Text(str(dl.old_num)), Text(" - ", style="red"),
                    dl.content, style=del_bg,
                )
            else:
                table.add_row(
                    Text(str(dl.new_num), style="dim"), Text("   "), dl.content,
                )
    if truncated:
        table.add_row(
            Text(""), Text(""),
            Text("... diff truncated at source", style="dim italic"),
        )

    title = Text(" ")
    title.append_text(_build_header(path, added, removed))
    title.append(" ")
    return Panel(
        table, title=title, title_align="left",
        border_style="dim", box=box.SQUARE, padding=(0, 1),
    )


# ──────────────────────────────────────────────────────────────────────────
# 形态二: 紧凑预览 (审批面板 — 只显改动行)
# ──────────────────────────────────────────────────────────────────────────


def render_diff_preview(
    path: str,
    hunks: list[list[DiffLine]],
    max_lines: int = MAX_PREVIEW_CHANGED_LINES,
) -> tuple[list[Any], int]:
    """紧凑预览: 只显改动行 (无上下文), 上限 max_lines。

    Returns:
        (renderables, remaining) — remaining 为未展示的改动行数。
    """
    added, removed = count_changes(hunks)
    for hunk in hunks:
        _stylize_hunk(hunk)

    changed = [dl for hunk in hunks for dl in hunk if dl.kind != DiffLineKind.CONTEXT]
    shown = changed[:max_lines]
    remaining = len(changed) - len(shown)

    max_ln = max(
        (dl.old_num if dl.kind == DiffLineKind.DELETE else dl.new_num for dl in shown),
        default=0,
    )
    num_width = max(len(str(max_ln)), 2)

    result: list[Any] = [_build_header(path, added, removed)]
    for dl in shown:
        assert dl.content is not None
        line = Text()
        ln = dl.old_num if dl.kind == DiffLineKind.DELETE else dl.new_num
        line.append(str(ln).rjust(num_width), style="dim")
        is_add = dl.kind == DiffLineKind.ADD
        line.append(" + " if is_add else " - ", style="green" if is_add else "red")
        line.append_text(dl.content)
        result.append(line)
    if remaining > 0:
        result.append(Text(f"... {remaining} more changed lines", style="dim italic"))
    return result, remaining


# ──────────────────────────────────────────────────────────────────────────
# 形态三: 超大文件降级摘要
# ──────────────────────────────────────────────────────────────────────────


def render_diff_summary(path: str, old_line_count: int, new_line_count: int) -> Text:
    """超大文件降级: 只显行数变化。"""
    body = Text()
    body.append(path or "diff", style="bold")
    body.append("  ")
    body.append("file too large for inline diff ", style="dim italic")
    body.append(f"({old_line_count} \u2192 {new_line_count} lines)", style="dim")
    return body


# ──────────────────────────────────────────────────────────────────────────
# 便捷入口: 一步从 old/new 或 diff 文本渲染
# ──────────────────────────────────────────────────────────────────────────


def panel_from_texts(path: str, old_text: str, new_text: str,
                     old_start: int = 1, new_start: int = 1) -> Any:
    """old/new 原文 → 完整面板 (大文件自动降级 summary)。"""
    if should_summarize(old_text, new_text):
        return render_diff_summary(
            path, old_text.count("\n") + 1, new_text.count("\n") + 1,
        )
    hunks = build_hunks(old_text, new_text, old_start, new_start)
    return render_diff_panel(path, hunks)


def preview_from_texts(path: str, old_text: str, new_text: str,
                       old_start: int = 1, new_start: int = 1,
                       max_lines: int = MAX_PREVIEW_CHANGED_LINES) -> list[Any]:
    """old/new 原文 → 审批预览 renderable 列表 (大文件自动降级 summary)。"""
    if should_summarize(old_text, new_text):
        return [render_diff_summary(
            path, old_text.count("\n") + 1, new_text.count("\n") + 1,
        )]
    hunks = build_hunks(old_text, new_text, old_start, new_start)
    renderables, _ = render_diff_preview(path, hunks, max_lines=max_lines)
    return renderables


__all__ = [
    "DiffLine", "DiffLineKind",
    "build_hunks", "parse_unified_hunks", "count_changes", "should_summarize",
    "render_diff_panel", "render_diff_preview", "render_diff_summary",
    "panel_from_texts", "preview_from_texts",
    "INLINE_DIFF_MIN_RATIO", "MAX_PREVIEW_CHANGED_LINES", "MAX_INLINE_DIFF_LINES",
]
