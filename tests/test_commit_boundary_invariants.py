"""流式提交边界 (commit_boundary, kimi _find_committed_boundary 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-COMMIT-1: 未超阈 → 0 (不提交, 反例); 超阈 → 切点在段落边界且 ≥ 阈值一半。
  I-COMMIT-2: 代码围栏安全 — 切出前缀 ``` 数必为偶 (不撕裂代码块);
              整段都在未闭合代码块内 → 0 (宁可不切)。
  I-COMMIT-3: 集成 — 超长流式时前缀固化进 MessageList, 活跃区只剩尾部;
              内容无一字丢失 (前缀+尾部 == 原文语义)。
"""

from __future__ import annotations

import pytest

from zall.cli.tui.commit_boundary import (
    DEFAULT_COMMIT_THRESHOLD,
    find_committed_boundary,
)


# ── I-COMMIT-1 ──


def test_below_threshold_no_commit() -> None:
    text = "para one\n\npara two"
    assert find_committed_boundary(text, threshold=1000) == 0   # 反例: 未超阈


def test_cut_at_paragraph_boundary() -> None:
    para = "x" * 400
    text = ("\n\n".join([para] * 6)) + "\n\ntail"
    b = find_committed_boundary(text, threshold=1500)
    assert b > 0
    assert text[b:b + 2] == "\n\n"          # 切点恰在段落边界
    assert b >= 1500 // 2                   # 不碎片化
    assert b <= 1500                        # 不超过阈值窗口


def test_no_paragraph_boundary_means_no_commit() -> None:
    """反例: 无段落边界的长文本 (单段) → 不切, 宁可整块留活跃区。"""
    text = "y" * 20_000
    assert find_committed_boundary(text, threshold=6000) == 0


def test_default_threshold_used() -> None:
    text = ("z" * 100 + "\n\n") * 100        # ~10200 chars
    assert find_committed_boundary(text) > 0
    assert find_committed_boundary(text[:DEFAULT_COMMIT_THRESHOLD]) == 0


# ── I-COMMIT-2: 代码围栏安全 ──


def test_never_splits_open_code_fence() -> None:
    head = "intro\n\n```python\n" + ("code line\n\n" * 400)   # 围栏未闭合
    b = find_committed_boundary(head, threshold=2000)
    assert b == 0                            # 整个前缀区都在代码块内 → 不切


def test_cuts_before_open_fence() -> None:
    """围栏前有安全段落 → 切在围栏之前 (前缀围栏数为偶)。"""
    safe = ("plain para\n\n" * 300)          # ~3600 chars 安全区
    text = safe + "```python\n" + ("code\n\n" * 500)
    b = find_committed_boundary(text, threshold=4000)
    assert b > 0
    assert text[:b].count("```") % 2 == 0    # 前缀围栏成对 (此处为 0 个)
    assert "```" not in text[:b]


# ── I-COMMIT-3: TUI 集成 (mock live/msg_list, 离线) ──


def test_app_commits_prefix_and_keeps_tail() -> None:
    pytest.importorskip("textual")
    from types import SimpleNamespace

    from zall.cli.tui import TuiApp

    app = TuiApp()
    committed: list = []

    class _Live:
        message = None
        def set_message(self, m): self.message = m
        def refresh(self): pass
        is_active = True

    app._cached_live = _Live()
    app._cached_msg_list = SimpleNamespace(add_message=committed.append)

    para = "word " * 100                     # ~500 chars/段
    full = ""
    for _ in range(30):                      # 累计 ~15K chars, 必触发提交
        chunk = para + "\n\n"
        full += chunk
        with app._buf_lock:
            app._token_buffer = chunk
        app._flush_token_buffer()

    assert committed, "long stream must commit prefix into history"
    committed_text = "".join(m.content for m in committed)
    tail = app._streaming_content
    # 内容无损: 固化前缀 + 活跃尾部 == 原文 (允许边界处换行被规整)
    assert (committed_text + "\n\n" + tail).replace("\n", "") == full.replace("\n", "")
    # 活跃尾部受控 (不随总长无界增长)
    assert len(tail) <= 6000 + len(para) + 2


def test_app_short_stream_commits_nothing() -> None:
    """反例孪生: 短回复绝不提前固化 (单气泡完整性)。"""
    pytest.importorskip("textual")
    from types import SimpleNamespace

    from zall.cli.tui import TuiApp

    app = TuiApp()
    committed: list = []

    class _Live:
        message = None
        def set_message(self, m): self.message = m
        def refresh(self): pass
        is_active = True

    app._cached_live = _Live()
    app._cached_msg_list = SimpleNamespace(add_message=committed.append)
    with app._buf_lock:
        app._token_buffer = "short reply\n\nwith two paragraphs"
    app._flush_token_buffer()
    assert committed == []
    assert app._streaming_content == "short reply\n\nwith two paragraphs"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
