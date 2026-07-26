"""G5 大段粘贴折叠不变量测试 (IPR-0, 含反例).

不变量:
  I-PASTE-1  折叠阈值: >=LINE_THRESHOLD 行或 >=CHAR_THRESHOLD 字符才折叠
             (反例: 14 行短文本不折叠, 原样归一化返回)
  I-PASTE-2  往返保真: maybe_fold → expand 恢复归一化原文;
             多占位符各自展开; 未知 id 原样保留 (反例)
  I-PASTE-3  入口清洗: CRLF/CR → LF; 孤立 surrogate → U+FFFD (不崩)
  I-PASTE-4  占位符格式: 单行无 "+N lines"; 多行有; id 单调递增
  I-PASTE-5  TUI 集成: ChatTextArea.expanded_text 展开占位符
"""

from __future__ import annotations

import pytest

from zall.cli import paste_fold as pf
from zall.cli.paste_fold import PasteFolder


BIG = "\n".join(f"line {i}" for i in range(30))          # 30 行 → 折叠
SMALL = "\n".join(f"line {i}" for i in range(5))         # 5 行短 → 不折叠


# ── I-PASTE-1 折叠阈值 ──


def test_fold_threshold_lines():
    assert pf.should_fold(BIG)
    assert pf.should_fold("x" * pf.CHAR_THRESHOLD)


def test_no_fold_below_threshold_counterexample():
    """反例: 阈值之下不折叠, maybe_fold 返回归一化原文。"""
    assert not pf.should_fold(SMALL)
    assert not pf.should_fold("x" * (pf.CHAR_THRESHOLD - 1))
    folder = PasteFolder()
    assert folder.maybe_fold(SMALL) == SMALL


# ── I-PASTE-2 往返保真 ──


def test_roundtrip_fold_expand():
    folder = PasteFolder()
    token = folder.maybe_fold(BIG)
    assert token.startswith("[Pasted text #1")
    command = f"看看这段日志: {token} 有什么问题?"
    expanded = folder.expand(command)
    assert BIG in expanded
    assert "[Pasted text" not in expanded


def test_multiple_placeholders_expand_independently():
    folder = PasteFolder()
    t1 = folder.fold("AAA\n" * 20)
    t2 = folder.fold("BBB\n" * 20)
    out = folder.expand(f"{t1} 与 {t2}")
    assert "AAA" in out and "BBB" in out
    assert out.index("AAA") < out.index("BBB")


def test_unknown_id_kept_verbatim_counterexample():
    """反例: 跨会话历史召回的未知 id 原样保留, 不崩。"""
    folder = PasteFolder()
    cmd = "prefix [Pasted text #42 +99 lines] suffix"
    assert folder.expand(cmd) == cmd


# ── I-PASTE-3 入口清洗 ──


def test_crlf_normalized():
    folder = PasteFolder()
    out = folder.maybe_fold("a\r\nb\rc")
    assert out == "a\nb\nc"


def test_lone_surrogate_sanitized():
    dirty = "ok\ud800bad"
    clean = pf.sanitize_surrogates(dirty)
    assert "\ud800" not in clean
    clean.encode("utf-8")  # 必须可编码 (进 json/历史文件不崩)


def test_cjk_preserved():
    folder = PasteFolder()
    text = "中文行\n" * 20
    token = folder.maybe_fold(text)
    assert "中文行" in folder.expand(token)


# ── I-PASTE-4 占位符格式 ──


def test_placeholder_format_and_monotonic_ids():
    folder = PasteFolder()
    single = folder.fold("one line only")
    multi = folder.fold("l1\nl2\nl3")
    assert single == "[Pasted text #1]"
    assert multi == "[Pasted text #2 +3 lines]"


def test_count_lines():
    assert pf.count_lines("") == 1
    assert pf.count_lines("a") == 1
    assert pf.count_lines("a\nb") == 2


# ── I-PASTE-5 TUI 集成 ──


def test_chat_textarea_expanded_text():
    pytest.importorskip("textual")
    from zall.cli.tui.widgets import ChatTextArea

    ta = ChatTextArea(text="")
    token = ta._paste_folder.maybe_fold(BIG)
    ta.load_text(f"分析 {token}")
    assert "[Pasted text #1" in ta.text
    assert BIG in ta.expanded_text
    assert "[Pasted text" not in ta.expanded_text
