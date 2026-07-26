"""zall._util.term — 终端光标探测与提示符行首保证。

G10 (kimi utils/term.py 对标):
  bash 等工具输出无尾换行时, 下一个提示符会接在残留输出行尾。
  ensure_new_line() 在显示提示符前探测光标列, 不在行首才补 "\\n" —
  已在行首时零输出 (不产生多余空行)。

  探测方式:
    Windows: GetConsoleScreenBufferInfo (ctypes, 同步无竞态)
    Unix:    ESC[6n 光标位置查询 (cbreak + 非阻塞读, 200ms 超时兜底)

  修正 kimi 原版 off-by-one: kimi `_cursor_column_windows` 返回 1-indexed
  却判 `not in (None, 0)` — 行首 (列 1) 会误插空行。本版两平台统一
  1-indexed, 判据统一 `_needs_newline`。

IPR constraints:
  IPR-0: tests/test_term_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import time


def _needs_newline(column: int | None) -> bool:
    """1-indexed 列判据: 行首 (1) 或探测失败 (None) 都不补行。

    探测失败保守不写 — 宁可提示符接行尾, 不乱插空行破坏排版。
    """
    return column not in (None, 1)


def ensure_new_line() -> None:
    """确保下一个提示符从列 0 开始 (工具输出无尾换行时补 \\n)。"""
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return
    if sys.platform == "win32":
        pos = _cursor_position_windows()
    else:
        pos = _cursor_position_unix()
    column = pos[1] if pos else None
    if _needs_newline(column):
        sys.stdout.write("\n")
        sys.stdout.flush()


def _cursor_position_windows() -> tuple[int, int] | None:
    """Windows 光标位置 (row, column), 1-indexed; 失败返回 None 不抛。"""
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, ctypes.c_void_p(-1).value):
            return None

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        csbi = CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
            return None
        # Windows API 0-indexed → 统一 1-indexed
        return int(csbi.dwCursorPosition.Y) + 1, int(csbi.dwCursorPosition.X) + 1
    except Exception:
        return None


_CURSOR_POSITION_RE = re.compile(r"\x1b\[(\d+);(\d+)R")


def _cursor_position_unix() -> tuple[int, int] | None:
    """Unix 光标位置 (row, column), 1-indexed: ESC[6n 查询 + 200ms 超时。

    cbreak + 非阻塞读 — asyncio 取消或与 prompt_toolkit 自己的 stdin
    reader 竞争时不会卡死在不可中断的 os.read()。
    """
    if sys.platform == "win32":
        return None

    import select
    import termios
    import tty

    try:
        fd = sys.stdin.fileno()
        oldterm = termios.tcgetattr(fd)
    except Exception:
        return None

    was_blocking = True
    try:
        tty.setcbreak(fd)
        was_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        sys.stdout.write("\x1b[6n")
        sys.stdout.flush()

        response = ""
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            timeout = max(0.01, deadline - time.monotonic())
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 32)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            response += chunk.decode(encoding="utf-8", errors="ignore")
            match = _CURSOR_POSITION_RE.search(response)
            if match:
                return int(match.group(1)), int(match.group(2))
    finally:
        with contextlib.suppress(OSError):
            os.set_blocking(fd, was_blocking)
        with contextlib.suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, oldterm)

    return None
