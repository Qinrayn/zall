"""zall.cli.typeahead — 干活期间打字排队 (Codex queued-message 口径)。

回合进行中 REPL 输入循环阻塞, 此前打进去的字会被 flush_stdin_typeahead()
整个丢弃 — 用户体感"中途干活不能发信息"。采集线程补上这条通路:

  - 回合开始 start(), 回合结束 stop(); 仅 TTY 生效
  - 可打印字符进 live buffer (spinner 状态行实时回显, render 侧钩子)
  - Enter 把 buffer 提交进队列; 退格删除; 方向键等功能键忽略
  - Ctrl-C (\x03, POSIX raw / ConPTY 路径) → _thread.interrupt_main(),
    与真实控制台 SIGINT 同效
  - 回合结束后 REPL drain() 取队列, 逐条作为后续用户消息提交

线程安全: buffer/queue 读写全部走锁; 线程里绝不碰 rich/console。
Windows 用 msvcrt (console 输入缓冲), POSIX 用 termios cbreak + select —
ISIG 保持开启, 真实 Ctrl-C 仍走 SIGINT, 不与此线程抢。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable


def _feed_char(state: dict[str, Any], ch: str, on_interrupt: Callable[[], None]) -> None:
    """把一个输入字符并入采集状态 (纯函数, 便于离线单测)。

    state keys: buf (str, in-progress line), queue (list[str]), saw_prefix (bool —
    Windows 功能键两码序列的第一码)。special keys: \r/\n 提交, \x08/\x7f 退格,
    \x03 中断, \x1b 序列吞到字母为止。
    """
    if state.pop("saw_prefix", False):
        return  # 功能键第二码 — 直接丢弃
    if ch in ("\x00", "\xe0"):
        state["saw_prefix"] = True
        return
    if ch == "\x03":
        on_interrupt()
        return
    if ch in ("\r", "\n"):
        line = state["buf"].strip()
        state["buf"] = ""
        if line:
            state["queue"].append(line)
        return
    if ch in ("\x08", "\x7f"):
        state["buf"] = state["buf"][:-1]
        return
    if ch == "\x1b":
        state["saw_prefix"] = True  # ESC 序列: 下一码 (箭头等) 吞掉
        return
    if ch == "\x04":  # Ctrl-D: 不在此解释, 交给主线程输入栈
        return
    if ch.isprintable():
        state["buf"] += ch


class TypeaheadCollector:
    """回合期间的键盘采集器。非 TTY 下 start() 是 no-op。

    实测反馈 (2026-09-19): 只把 buffer 挂在 spinner 状态行上不够 — 推理流式
    输出期间 spinner 是停的, 用户打字完全看不见。采集线程现在逐字符直接回显
    在光标处 (像普通终端回显一样): 流式输出停下时字符就落在输出下方, spinner
    重绘时状态行尾部的 `▌ buf` 接管显示; Enter 提交后换行。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"buf": "", "queue": []}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interrupted = False
        self._is_tty = False

    # ── 生命周期 ──

    def start(self) -> None:
        try:
            self._is_tty = sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:
            self._is_tty = False
        if not self._is_tty or self._thread is not None:
            return
        self._stop.clear()
        self._interrupted = False
        with self._lock:
            self._state = {"buf": "", "queue": []}
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="zall-typeahead")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ── 读取 (spinner 回显 / REPL 提交) ──

    @property
    def buffer(self) -> str:
        """正在打的行 (spinner 状态行回显用)。"""
        with self._lock:
            return str(self._state.get("buf", ""))

    @property
    def queued_count(self) -> int:
        with self._lock:
            return len(self._state.get("queue", []))

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    def drain(self) -> list[str]:
        """取走已提交的队列 (回合结束后 REPL 逐条作为用户消息发出)。"""
        with self._lock:
            q = self._state.get("queue", [])
            self._state["queue"] = []
            return list(q)

    # ── 回显 (打字可见性) ──

    def _echo(self, text: str) -> None:
        """直接把字符写到真实终端光标处。锁内调用; 失败静默 (回显不能杀回合)。"""
        try:
            out = sys.__stdout__
            if out is not None:
                out.write(text)
                out.flush()
        except Exception:
            pass

    # ── 后台读循环 ──

    def _on_interrupt(self) -> None:
        self._interrupted = True
        try:
            import _thread
            _thread.interrupt_main()
        except Exception:
            pass

    def _consume(self, ch: str) -> None:
        """采集 + 回显一个字符 (锁内)。"""
        before_len = len(self._state.get("buf", ""))
        _feed_char(self._state, ch, self._on_interrupt)
        after = self._state.get("buf", "")
        if ch in ("\x08", "\x7f"):
            if before_len > 0:
                self._echo("\b \b")  # 擦掉上一个字符
        elif ch in ("\r", "\n"):
            if self._state.get("queue"):
                self._echo("\n")  # 提交 → 换行, 后续输出落在新行
        elif ch == "\x03" or ch in ("\x00", "\xe0"):
            pass  # 无回显
        elif len(after) > before_len:
            self._echo(ch)  # 普通字符 → 原样回显
        # ESC 序列/被吞的功能键码: 不回显

    def _loop(self) -> None:
        if sys.platform == "win32":
            self._loop_win32()
        else:
            self._loop_posix()

    def _loop_win32(self) -> None:
        try:
            import msvcrt
        except ImportError:
            return
        while not self._stop.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    with self._lock:
                        self._consume(ch)
                else:
                    time.sleep(0.03)
            except Exception:
                break  # console 关闭等 — 采集静默退出, 不影响回合

    def _loop_posix(self) -> None:
        import select
        import termios
        import tty

        try:
            fd = sys.stdin.fileno()
        except Exception:
            return
        try:
            old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        except Exception:
            return
        try:
            # cbreak: 关 ICANON/ECHO (自己回显), 保持 ISIG (真实 Ctrl-C 仍走 SIGINT)
            tty.setcbreak(fd)  # type: ignore[attr-defined]
        except Exception:
            return
        try:
            while not self._stop.is_set():
                r, _w, _x = select.select([fd], [], [], 0.05)
                if not r:
                    continue
                try:
                    ch = sys.stdin.read(1)
                except Exception:
                    break
                if not ch:
                    break
                with self._lock:
                    self._consume(ch)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]
            except Exception:
                pass
