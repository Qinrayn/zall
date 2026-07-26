"""zall.cli.tui.tui_responder — confirm gate bridged into the Textual TUI.

Fixes the hang where the TUI used CliUserResponder, whose ask() calls input()
on stdin that Textual owns → the worker thread blocked forever ("等待中").

Reuses all of CliUserResponder's decision logic (greylist/blacklist,
always-allow persistence, y/n/a/e/s parsing, blacklist override) but:
  - routes the permission panel to the TUI message area (main thread),
  - blocks the worker thread on a threading.Event until the user answers
    in the input box, then returns their reply string.

Threading model:
  worker thread (loop.step) → responder.ask() → _tui_ask() posts to main
  thread (begin_confirm) then Event.wait(); main thread collects the user's
  input and calls submit_reply() → Event.set() → worker resumes.
"""

from __future__ import annotations

import threading
from typing import Any

from zall.cli.responder import CliUserResponder
from zall.core.action import Action


class TuiUserResponder(CliUserResponder):
    """CliUserResponder whose prompt + panel go through the Textual app."""

    __test__ = False

    def __init__(self, app: Any, *, yes: bool = False, plan_mode: bool = False) -> None:
        self._app = app
        self._event = threading.Event()
        self._reply = ""
        # is_tty=True: 走交互路径 (非 TTY 会 auto-reject, 那样等于禁用确认)。
        super().__init__(
            yes=yes,
            is_tty=True,
            plan_mode=plan_mode,
            ask_fn=self._tui_ask,
            print_fn=self._tui_print,
            choose_fn=self._tui_choose,
        )

    # 父类的 _print (选项提示等) → 静默 (确认 UI 自带说明, 避免污染)
    def _tui_print(self, s: str) -> None:
        return None

    def _render_permission_panel(self, action: Action, level: str) -> None:
        """覆盖父类的 stderr 面板 → 投递到 TUI 消息区 (主线程)。"""
        try:
            self._app.call_from_thread(
                self._app.show_confirm_request, action.tool_id, dict(action.args), level
            )
        except Exception:
            pass

    def _tui_ask(self, prompt: str) -> str:
        """阻塞 worker 线程, 等待用户在 TUI 输入框回答。"""
        self._event.clear()
        self._reply = ""
        try:
            self._app.call_from_thread(self._app.begin_confirm, prompt)
        except Exception:
            return ""  # 无法提问 → 空 → 父类判为 reject (安全默认)
        self._event.wait()
        return self._reply

    def _tui_choose(self, choices: list[tuple[str, str, str]]) -> str:
        """阻塞 worker 线程, 在主线程弹出可选择菜单 (方向/数字键), 返回选中 value。

        用于 greylist 主选择 (y/n/a/e); 编辑子提示仍走 _tui_ask 文本输入框。
        无法弹菜单 → 'n' (reject, 安全默认)。
        """
        self._event.clear()
        self._reply = ""
        try:
            self._app.call_from_thread(self._app.begin_confirm_select, list(choices))
        except Exception:
            return "n"
        self._event.wait()
        return self._reply or "n"

    def submit_reply(self, text: str) -> None:
        """主线程: 用户在确认模式下提交回答 (唤醒被阻塞的 worker)。"""
        self._reply = text or ""
        self._event.set()

    def cancel(self) -> None:
        """主线程: 中断/退出时取消等待 (返回空 → reject)。"""
        self._reply = ""
        self._event.set()
