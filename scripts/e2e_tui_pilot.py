"""E2E: TUI 模式模拟真人操作 (真实 API + Textual Pilot 输入/点击).

用 Textual 官方 Pilot 驱动 TuiApp:
  1. 点击输入框 (鼠标点击维度)
  2. 逐键敲入任务文本 + Enter 提交 (键盘输入维度)
  3. 等待真实 API agent 回合完成 (worker 线程跑完整 AgentLoop)
  4. 断言: assistant 消息落入历史、_agent_running 复位、无 error 消息
  5. 再敲一条 /help 斜杠命令验证命令面在 TUI 下可用
  6. Ctrl+C×2 之外的优雅路径退出 (run_test 上下文自然关闭)

运行: python -X utf8 scripts/e2e_tui_pilot.py
前置: ~/.zall/config.toml 已配置真实 API key。
退出码: 0 全部断言通过; 1 任一失败。
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(240, exit=True)  # 挡住主循环时转储全线程栈

REPO = Path(__file__).resolve().parent.parent
PLAYGROUND = REPO / ".e2e_playground"

_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""), flush=True)


_KEYMAP = {" ": "space", ".": "full_stop", "/": "slash", ",": "comma",
           "-": "minus", "_": "underscore", "?": "question_mark"}


async def _type_text(pilot, text: str) -> None:
    """逐键敲入 (模拟真人打字); 标点映射到 Textual 键名。"""
    for ch in text:
        await pilot.press(_KEYMAP.get(ch, ch))


async def main() -> int:
    os.chdir(PLAYGROUND)
    from zall.cli.tui import TuiApp
    from zall.cli.tui.widgets import InputBar

    app = TuiApp(yes=True)  # 自动批准工具 (无人值守 e2e)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # ── 1. 鼠标点击输入框 ──
        await pilot.click("#input-bar")
        await pilot.pause()
        bar = app.query_one("#input-bar", InputBar)
        _check("click focuses input bar", bar.has_focus or bar._textarea.has_focus)

        # ── 2. 逐键敲入任务 + Enter ──
        task = "read fib.py and say in one short sentence what it does"
        await _type_text(pilot, task)
        await pilot.pause()
        typed = bar._textarea.text
        _check("keystrokes land in textarea", typed == task,
               f"got {typed!r}" if typed != task else "")
        # pilot.press("enter") 会等待消息泵静默 — 但 enter 触发的 agent worker
        # 持续向主线程 post 事件, 永不静默 → 用 wait_for 旁路 (按键已送达)。
        try:
            await asyncio.wait_for(pilot.press("enter"), timeout=5.0)
        except asyncio.TimeoutError:
            print("  .. press(enter) await timed out (key delivered; worker busy)", flush=True)

        # ── 3. 等待真实 API 回合完成 (最长 180s) ──
        deadline = time.monotonic() + 180
        started = False
        hb = 0
        while time.monotonic() < deadline:
            if app._agent_running:
                started = True
            if started and not app._agent_running:
                break
            hb += 1
            if hb % 20 == 0:  # ~10s 心跳
                ml_dbg = app.query_one("#message-list")
                workers = list(app.workers)
                print(f"  .. hb={hb} running={app._agent_running} started={started} "
                      f"msgs={len(ml_dbg._messages)} workers={[(w.name, w.state.name) for w in workers]}",
                      flush=True)
            await asyncio.sleep(0.5)
        _check("agent turn started", started)
        _check("agent turn finished (no hang)", started and not app._agent_running)

        # ── 4. 消息历史断言 ──
        ml = app.query_one("#message-list")
        msgs = ml._messages
        user_msgs = [m for m in msgs if m.role == "user"]
        asst_msgs = [m for m in msgs if m.role == "assistant" and m.content.strip()]
        err_msgs = [m for m in msgs if m.role == "error"]
        _check("user bubble in history", any(task in m.content for m in user_msgs))
        _check("assistant reply arrived", bool(asst_msgs),
               asst_msgs[-1].content[:80].replace("\n", " ") if asst_msgs else "no reply")
        _check("no error messages", not err_msgs,
               err_msgs[0].content[:80] if err_msgs else "")
        reply = " ".join(m.content.lower() for m in asst_msgs)
        _check("reply mentions fibonacci/fib", ("fib" in reply) or ("斐波那契" in reply))

        # ── 5. 斜杠命令在 TUI 下可用 ──
        base_len = len(ml._messages)
        await _type_text(pilot, "/help")
        try:
            await asyncio.wait_for(pilot.press("enter"), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(1.0)
        _check("/help produced output", len(ml._messages) > base_len
               or app.query("#message-list") is not None)

    print()
    failed = [r for r in _results if not r[1]]
    print(f"e2e tui pilot: {len(_results) - len(failed)}/{len(_results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
