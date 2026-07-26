"""G11 shorten/shorten_middle cell-width 截断不变量测试 (IPR-0, 含反例).

不变量:
  I-SHORT-1  shorten 词边界优先; 纯 CJK 无空格硬切不塌缩成只剩省略号 (反例)
  I-SHORT-2  cell-width 感知: CJK 全角计 2 格, 等长 ASCII 对照能容纳更多字符
  I-SHORT-3  shorten 结果 display_width <= width; 短文本原样返回 (反例)
  I-SHORT-4  shorten_middle 头尾都保留信息; 换行被移除; CJK 不超宽
  I-SHORT-5  display_width: ASCII=len, CJK=2*len, 混合可加
  I-SHORT-6  truncate 不做空白归一化 (保留缩进, 与 shorten 对照反例)
"""

from __future__ import annotations

from zall._util.string import display_width, shorten, shorten_middle, truncate

# ── I-SHORT-5 display_width ──


def test_display_width_ascii_equals_len():
    assert display_width("hello world") == len("hello world")


def test_display_width_cjk_doubles():
    assert display_width("中文测试") == 8


def test_display_width_mixed_additive():
    assert display_width("ab中文cd") == 4 + 4


def test_display_width_empty():
    assert display_width("") == 0


# ── I-SHORT-1 词边界 ──


def test_shorten_prefers_word_boundary():
    out = shorten("hello world foobar baz", width=16)
    # 切点回退到空格处, 不会出现半个单词
    assert out.endswith("…")
    body = out[:-1]
    assert body == body.rstrip()
    assert body in ("hello world", "hello world foobar"[: len(body)])
    assert " foob" not in out or out[:-1].split()[-1] in ("world", "foobar")


def test_shorten_cjk_no_space_hard_cut_counterexample():
    """反例: 纯 CJK 无空格 — 硬切而非塌缩成只剩 '…'。"""
    out = shorten("中文中文中文中文中文", width=8)
    assert out != "…"
    assert len(out) > 1
    assert out.endswith("…")
    assert display_width(out) <= 8


# ── I-SHORT-2 cell-width 感知 ──


def test_shorten_cjk_counts_double_width():
    cjk = shorten("中" * 20, width=10)
    ascii_ = shorten("a" * 20, width=10)
    # 同 width 下, ASCII 保留的字符数应多于 CJK
    assert len(ascii_) > len(cjk)
    assert display_width(cjk) <= 10
    assert display_width(ascii_) <= 10


# ── I-SHORT-3 不超宽 ──


def test_shorten_never_exceeds_width():
    for text in ("x" * 100, "中" * 100, "word " * 40, "a中b文c" * 30):
        for width in (1, 2, 5, 8, 20):
            assert display_width(shorten(text, width=width)) <= width


def test_shorten_short_text_unchanged_counterexample():
    """反例: 不超宽的文本原样返回 (仅空白归一化)。"""
    assert shorten("hello", width=20) == "hello"
    assert shorten("a  b\n c", width=20) == "a b c"


def test_shorten_whitespace_normalised():
    assert shorten("  foo\t\tbar  ", width=80) == "foo bar"


# ── I-SHORT-4 shorten_middle ──


def test_shorten_middle_keeps_head_and_tail():
    path = "C:/Users/somebody/projects/zall/src/zall/core/executor.py"
    out = shorten_middle(path, 30)
    assert "…" in out
    assert out.startswith("C:/U")
    assert out.endswith(".py")
    assert display_width(out) <= 30


def test_shorten_middle_removes_newlines():
    out = shorten_middle("line1\r\nline2\nline3", 100)
    assert "\n" not in out and "\r" not in out


def test_shorten_middle_cjk_within_width():
    out = shorten_middle("中" * 40, 21)
    assert display_width(out) <= 21
    assert out[0] == "中" and out[-1] == "中"


def test_shorten_middle_short_text_unchanged_counterexample():
    """反例: 短文本原样返回。"""
    assert shorten_middle("abc.py", 30) == "abc.py"


# ── I-SHORT-6 truncate 保留缩进 ──


def test_truncate_preserves_indentation():
    line = "    def foo():  # " + "x" * 200
    out = truncate(line, 100)
    assert out.startswith("    def foo():")
    assert display_width(out) <= 100
    assert out.endswith("…")


def test_truncate_vs_shorten_whitespace_counterexample():
    """反例对照: shorten 归一化空白, truncate 不动。"""
    text = "  a   b"
    assert truncate(text, 80) == "  a   b"
    assert shorten(text, width=80) == "a b"


def test_truncate_cjk_within_width():
    out = truncate("中" * 60, 15)
    assert display_width(out) <= 15
