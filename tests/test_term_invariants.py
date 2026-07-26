"""G10 ensure_new_line 提示符行首保证不变量测试 (IPR-0, 含反例).

不变量:
  I-TERM-1  _needs_newline 判据 (1-indexed): 列 >1 → 补行;
            行首 (1) / 探测失败 (None) → 不补 (kimi off-by-one 回归守卫)
  I-TERM-2  非 TTY 零输出 (管道/重定向下绝不写)
  I-TERM-3  TTY: 列 >1 恰写一个 \\n; 行首/探测失败零输出 (反例)
  I-TERM-4  探测函数失败安全: 错误平台返回 None 不抛
"""

from __future__ import annotations

import sys

import zall._util.term as term
from zall._util.term import _needs_newline, ensure_new_line

# ── I-TERM-1 判据 ──


def test_needs_newline_mid_line():
    assert _needs_newline(2) is True
    assert _needs_newline(40) is True


def test_needs_newline_at_line_start_counterexample():
    """反例守卫 (kimi off-by-one): 行首列 1 绝不补行。"""
    assert _needs_newline(1) is False


def test_needs_newline_probe_failure_counterexample():
    """反例: 探测失败保守不写 — 宁接行尾不乱插空行。"""
    assert _needs_newline(None) is False


# ── I-TERM-2/3 ensure_new_line 行为 ──


class _FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, s: str) -> int:
        self.written.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def _run(monkeypatch, *, tty: bool, pos):
    out = _FakeStream(tty)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", _FakeStream(tty))
    monkeypatch.setattr(term, "_cursor_position_windows", lambda: pos)
    monkeypatch.setattr(term, "_cursor_position_unix", lambda: pos)
    ensure_new_line()
    return out


def test_non_tty_writes_nothing_counterexample(monkeypatch):
    """反例: 非 TTY (管道) 即使列 >1 也零输出。"""
    out = _run(monkeypatch, tty=False, pos=(3, 20))
    assert out.written == []


def test_tty_mid_line_writes_exactly_one_newline(monkeypatch):
    out = _run(monkeypatch, tty=True, pos=(3, 20))
    assert out.written == ["\n"]


def test_tty_line_start_writes_nothing_counterexample(monkeypatch):
    """反例: 已在行首 → 不产生多余空行。"""
    out = _run(monkeypatch, tty=True, pos=(3, 1))
    assert out.written == []


def test_tty_probe_failure_writes_nothing_counterexample(monkeypatch):
    """反例: 探测失败 → 保守零输出。"""
    out = _run(monkeypatch, tty=True, pos=None)
    assert out.written == []


# ── I-TERM-4 探测函数失败安全 ──


def test_wrong_platform_probe_returns_none():
    if sys.platform == "win32":
        assert term._cursor_position_unix() is None
    else:
        assert term._cursor_position_windows() is None


def test_native_probe_never_raises():
    """pytest 捕获流下句柄/termios 通常无效 — 只要求不抛。"""
    if sys.platform == "win32":
        term._cursor_position_windows()
    else:
        term._cursor_position_unix()
