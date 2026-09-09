"""G1 diff 渲染三形态不变量测试 (IPR-0, 含反例).

不变量:
  I-DIFF-1  行号保真: build_hunks 产出真实文件行号 (offset 正确平移)
  I-DIFF-2  词级配对: 相似 -/+ 行对做内联高亮; ratio<0.5 整行重写不配对 (反例)
  I-DIFF-3  双路等价: parse_unified_hunks(unified_diff(old,new)) 与
            build_hunks(old,new) 的 (kind, old_num, new_num, code) 序列一致
  I-DIFF-4  preview 上限: 只显改动行, 超出折叠且 remaining 计数正确
  I-DIFF-5  大文件降级: 超过 MAX_INLINE_DIFF_LINES 走 summary (反例: 小文件不降级)
  I-DIFF-6  审批预览: edit_file 审批从 args 现算 diff (此前审批盲区)
  I-DIFF-7  CJK / 空输入健壮性: 渲染不崩
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.text import Text

from zall.cli import diff_render as dr
from zall.cli.diff_render import DiffLineKind


def _render_to_str(renderable, width: int = 80) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=width)
    console.print(renderable)
    return buf.getvalue()


OLD = "def f(x):\n    return x + 1\n"
NEW = "def f(x, y):\n    return x + y\n"


# ── I-DIFF-1 行号保真 ──


def test_build_hunks_real_line_numbers():
    hunks = dr.build_hunks(OLD, NEW, old_start=10, new_start=10)
    assert len(hunks) == 1
    kinds = [(dl.kind, dl.old_num, dl.new_num) for dl in hunks[0]]
    assert kinds == [
        (DiffLineKind.DELETE, 10, 0),
        (DiffLineKind.DELETE, 11, 0),
        (DiffLineKind.ADD, 0, 10),
        (DiffLineKind.ADD, 0, 11),
    ]


def test_build_hunks_context_lines_numbered_both_sides():
    old = "a\nb\nc\nd\n"
    new = "a\nB\nc\nd\n"
    hunks = dr.build_hunks(old, new, old_start=5, new_start=5)
    ctx = [dl for dl in hunks[0] if dl.kind == DiffLineKind.CONTEXT]
    # 上下文行同时携带新旧行号且相等 (无增删偏移时)
    assert all(dl.old_num == dl.new_num and dl.old_num >= 5 for dl in ctx)


def test_line_offset_counterexample():
    """反例: 不传 offset 时 parse 行号从 1 计, 与真实行号不同。"""
    from zall.tools._diff import unified_diff
    d = unified_diff(OLD, NEW)
    no_offset, _ = dr.parse_unified_hunks(d)
    with_offset, _ = dr.parse_unified_hunks(d, line_offset=9)
    assert no_offset[0][0].old_num == 1
    assert with_offset[0][0].old_num == 10


# ── I-DIFF-2 词级配对 ──


def test_inline_pairing_similar_lines():
    hunks = dr.build_hunks(OLD, NEW)
    panel = dr.render_diff_panel("demo.py", hunks)
    assert panel is not None
    # 相似行 (只改了参数) 全部配对
    assert all(dl.is_inline_paired for dl in hunks[0])
    # 配对行的 Text 上有内联高亮 span
    styled = [dl for dl in hunks[0] if dl.content is not None and dl.content.spans]
    assert styled, "paired lines must carry inline highlight spans"


def test_inline_pairing_skips_dissimilar_counterexample():
    """反例: 完全不同的 -/+ 行 (ratio<0.5) 不做词级配对。"""
    old = "import os\n"
    new = "class Totally: pass\n"
    hunks = dr.build_hunks(old, new)
    dr.render_diff_panel("x.py", hunks)
    changed = [dl for dl in hunks[0] if dl.kind != DiffLineKind.CONTEXT]
    assert changed and not any(dl.is_inline_paired for dl in changed)


# ── I-DIFF-3 双路等价 ──


def test_parse_unified_equals_build():
    from zall.tools._diff import unified_diff
    old = "one\ntwo\nthree\nfour\nfive\n"
    new = "one\ntwo!\nthree\nfour\nFIVE\n"
    built = dr.build_hunks(old, new)
    parsed, truncated = dr.parse_unified_hunks(unified_diff(old, new))
    assert not truncated
    flat_b = [(d.kind, d.old_num, d.new_num, d.code) for h in built for d in h]
    flat_p = [(d.kind, d.old_num, d.new_num, d.code) for h in parsed for d in h]
    assert flat_b == flat_p


def test_parse_detects_truncation_marker():
    old = "\n".join(f"line {i}" for i in range(60)) + "\n"
    new = "\n".join(f"LINE {i}" for i in range(60)) + "\n"
    from zall.tools._diff import unified_diff
    d = unified_diff(old, new)  # 50 行截断
    assert "truncated" in d
    _, truncated = dr.parse_unified_hunks(d)
    assert truncated


# ── I-DIFF-4 preview 上限 ──


def test_preview_caps_changed_lines():
    old = "\n".join(f"a{i}" for i in range(10)) + "\n"
    new = "\n".join(f"b{i}" for i in range(10)) + "\n"
    hunks = dr.build_hunks(old, new)
    renderables, remaining = dr.render_diff_preview("f.txt", hunks, max_lines=6)
    # header + 6 行 + 折叠提示
    assert remaining == 20 - 6
    tail = renderables[-1]
    assert isinstance(tail, Text) and "more changed lines" in tail.plain


def test_preview_no_fold_when_under_cap_counterexample():
    """反例: 改动行数 <= 上限时无折叠提示。"""
    hunks = dr.build_hunks(OLD, NEW)
    renderables, remaining = dr.render_diff_preview("f.py", hunks, max_lines=6)
    assert remaining == 0
    assert not any(
        isinstance(r, Text) and "more changed lines" in r.plain for r in renderables
    )


# ── I-DIFF-5 大文件降级 ──


def test_should_summarize_thresholds():
    small = "x\n" * 100
    huge = "x\n" * (dr.MAX_INLINE_DIFF_LINES + 10)
    assert not dr.should_summarize(small, small)  # 反例: 小文件不降级
    assert dr.should_summarize(huge, small)
    assert dr.should_summarize(small, huge)


def test_panel_from_texts_degrades_to_summary():
    huge = "x\n" * (dr.MAX_INLINE_DIFF_LINES + 10)
    out = dr.panel_from_texts("big.txt", huge, huge + "y\n")
    text = _render_to_str(out)
    assert "too large" in text


# ── I-DIFF-6 审批预览 (审批盲区修复) ──


def _make_responder():
    from zall.cli.responder import CliUserResponder
    return CliUserResponder(is_tty=True, print_fn=lambda s: None)


def test_approval_preview_edit_file(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("keep\n" + OLD, encoding="utf-8")
    r = _make_responder()
    out = r._build_edit_preview("edit_file", {
        "path": str(f), "old_string": OLD, "new_string": NEW,
    })
    assert out is not None
    rendered = "".join(_render_to_str(x) for x in out)
    # 真实行号 (old 从第 2 行开始) + 改动内容可见
    assert "def f(x, y):" in rendered
    assert " 2 " in rendered or "2 -" in rendered.replace("  ", " ")


def test_approval_preview_batch_edit_caps_at_three(tmp_path):
    edits = []
    for i in range(5):
        f = tmp_path / f"f{i}.txt"
        f.write_text(f"old {i}\n", encoding="utf-8")
        edits.append({"path": str(f), "old_string": f"old {i}\n", "new_string": f"new {i}\n"})
    r = _make_responder()
    out = r._build_edit_preview("batch_edit", {"edits": edits})
    assert out is not None
    rendered = "".join(_render_to_str(x) for x in out)
    assert "2 more edits" in rendered


def test_approval_preview_not_applicable_counterexample():
    """反例: write_file (无 old_string) 返回 None → 走旧渲染。"""
    r = _make_responder()
    assert r._build_edit_preview("write_file", {"path": "x", "content": "hi"}) is None
    assert r._build_edit_preview("edit_file", {"path": "x"}) is None


# ── I-DIFF-7 健壮性 ──


def test_cjk_lines_render_without_crash():
    old = "打印(\"你好世界\")\n中文注释行\n"
    new = "打印(\"你好, 世界\")\n中文注释行\n"
    hunks = dr.build_hunks(old, new)
    text = _render_to_str(dr.render_diff_panel("中文.py", hunks), width=40)
    assert "你好" in text


def test_empty_and_identical_inputs():
    assert dr.build_hunks("", "") == []
    assert dr.build_hunks("same\n", "same\n") == []
    hunks, truncated = dr.parse_unified_hunks("")
    assert hunks == [] and not truncated


# ── 主题驱动 (G6 联动) ──


@pytest.fixture()
def _restore_theme():
    yield
    from zall.cli import theme
    theme.apply(theme.THEMES[theme.DEFAULT_THEME])


def test_diff_colors_follow_theme(_restore_theme):
    from zall.cli import theme
    theme.apply(theme.ATTIC)
    attic_colors = dr._diff_colors()
    assert all(c.startswith("on #") for c in attic_colors)
    # 反例孪生: 临时主题 (改动 diff 色槽) 必须产生不同色板 — 色直通主题槽位
    from dataclasses import replace
    other = replace(theme.ATTIC, diff_add_bg="#000000", diff_del_bg="#111111")
    theme.apply(other)
    assert dr._diff_colors() != attic_colors
