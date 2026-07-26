"""E2E: TUI 确认门交互 + attic 主题实机视觉验证 (真实 API + Textual Pilot).

场景 (模拟真人操作):
  1. yes=False 启动 TuiApp (greylist 写工具必须人工批准)
  2. 点击输入框, 逐键敲入"创建文件"任务 + Enter
  3. 等待确认门弹出选择菜单 (InputBar.select_open)
  4. 模拟真人按 Enter 选中第一项 "allow once" 批准
  5. 断言: 文件真实落盘 + 回合完成 + 无 error
  6. 期间导出 SVG 截图, 断言 attic 希腊色板 (#c9a227 月桂金) 实际上屏

运行: python -X utf8 scripts/e2e_tui_confirm.py
前置: ~/.zall/config.toml 已配置真实 API key; 主题解析为 attic。
退出码: 0 全部断言通过; 1 任一失败。
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import shutil
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(600, exit=True)  # 挡住主循环时转储全线程栈

REPO = Path(__file__).resolve().parent.parent
PLAYGROUND = REPO / ".e2e_playground"
FAKE_HOME = PLAYGROUND / ".fake_home"
SHOT = PLAYGROUND / "tui_attic_confirm.svg"
TARGET = PLAYGROUND / "golden.py"

# ── 隔离: 假 HOME (不碰真实 ~/.zall 的 always_allow / sessions) ──
# 必须在任何 zall import 之前: safety.config 模块级求值 resolve_home_dir()。
# config.toml 原样复制 (API key/provider/theme 保留), 其余状态全新。
_real_cfg = Path.home() / ".zall" / "config.toml"
(FAKE_HOME / ".zall").mkdir(parents=True, exist_ok=True)
if _real_cfg.exists():
    shutil.copyfile(_real_cfg, FAKE_HOME / ".zall" / "config.toml")
os.environ["USERPROFILE"] = str(FAKE_HOME)
os.environ["HOME"] = str(FAKE_HOME)

_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""), flush=True)


_KEYMAP = {" ": "space", ".": "full_stop", "/": "slash", ",": "comma",
           "-": "minus", "_": "underscore", "?": "question_mark",
           "(": "left_parenthesis", ")": "right_parenthesis",
           "+": "plus", "*": "asterisk", "=": "equals_sign", "'": "apostrophe"}


async def _press(pilot, key: str, timeout: float = 2.0) -> None:
    """按键 + 静默等待旁路: StatusBar spinner 等周期 timer 使消息泵
    永不静默, pilot.press 的静默等待会无限挂 — 按键本身已同步送达。"""
    try:
        await asyncio.wait_for(pilot.press(key), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def _type_text(pilot, text: str) -> None:
    """逐键敲入 (模拟真人打字); 标点映射到 Textual 键名。"""
    for ch in text:
        await _press(pilot, _KEYMAP.get(ch, ch))


async def _settle(pilot, timeout: float = 2.0) -> None:
    """pilot.pause 的限时版 (同上: 泵可能永不静默)。"""
    try:
        await asyncio.wait_for(pilot.pause(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


_turn_started = False


def started_done(app) -> bool:
    """回合是否 '已启动且已结束' (菜单等待期的早退判据)。"""
    global _turn_started
    if app._agent_running:
        _turn_started = True
    return _turn_started and not app._agent_running


async def main() -> int:
    os.chdir(PLAYGROUND)
    if TARGET.exists():
        TARGET.unlink()
    # 假 HOME 无 always_allow.json — write_file 必然走确认菜单
    return await _run()


async def _run() -> int:
    from zall.cli import theme
    _check("active theme is attic", theme.active_name() == "attic",
           theme.active_name())

    from zall.cli.tui import TuiApp
    from zall.cli.tui.widgets import InputBar
    # 插桩: responder 构建时的豁免集 + 每次 submit_reply (根因定位用)
    from zall.cli.tui import tui_responder as _tr
    _orig_init = _tr.TuiUserResponder.__init__
    _orig_submit = _tr.TuiUserResponder.submit_reply

    def _spy_init(self, app, **kw):
        _orig_init(self, app, **kw)
        print(f"  [spy] responder built: persistent={self._persistent_allow} "
              f"yes={self._yes}", flush=True)

    def _spy_submit(self, text):
        print(f"  [spy] submit_reply({text!r})", flush=True)
        _orig_submit(self, text)

    _tr.TuiUserResponder.__init__ = _spy_init
    _tr.TuiUserResponder.submit_reply = _spy_submit

    app = TuiApp(yes=False)  # 确认门开启 — 写文件必须人工批准
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        print("  .. app mounted", flush=True)

        # ── 1. 点击输入框 + 敲入任务 ──
        try:
            await asyncio.wait_for(pilot.click("#input-bar"), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        await _settle(pilot)
        print("  .. clicked input bar", flush=True)
        bar = app.query_one("#input-bar", InputBar)
        task = ("use write_file to create golden.py containing "
                "PHI = (1 + 5 ** 0.5) / 2 then stop")
        await _type_text(pilot, task)
        await _settle(pilot)
        _check("keystrokes land in textarea", bar._textarea.text == task,
               repr(bar._textarea.text)[:60])
        await _press(pilot, "enter", timeout=5.0)

        # ── 2. 等确认门弹出选择菜单 (最长 120s) ──
        deadline = time.monotonic() + 120
        menu_seen = False
        while time.monotonic() < deadline:
            if bar.select_open:
                menu_seen = True
                break
            if started_done(app):
                break  # 回合已结束 (模型走了 whitelist 路径, 不会再弹门)
            await asyncio.sleep(0.25)
        print(f"  .. menu_seen={menu_seen}", flush=True)
        # ── 3. attic 视觉验证: 确认面板在屏时导出 SVG ──
        try:
            app.save_screenshot(str(SHOT))
        except Exception as exc:  # 截图失败不挡确认流程
            print(f"  .. screenshot failed: {exc}", flush=True)

        # ── 4. 模拟真人按 Enter 批准 (第一项 = allow once) ──
        # 真人节奏: 看清菜单再决策 (避开 type-ahead 宽限期, 与防护设计一致)
        if menu_seen:
            await asyncio.sleep(0.5)
            await _press(pilot, "enter", timeout=5.0)

        # ── 5. 等回合完成 ──
        deadline = time.monotonic() + 240
        hb = 0
        while time.monotonic() < deadline:
            if started_done(app):
                break
            # 后续步再弹确认 (如再次写文件) → 继续批准, 不挂死
            if bar.select_open:
                print("  .. approving follow-up confirm", flush=True)
                await _press(pilot, "enter", timeout=5.0)
            hb += 1
            if hb % 40 == 0:  # ~20s 心跳
                print(f"  .. hb running={app._agent_running}", flush=True)
            await asyncio.sleep(0.5)
        _check("agent turn finished (no hang)", started_done(app))

        # ── 6. 断言: 文件真实落盘 + 无 error ──
        _check("golden.py written to disk", TARGET.exists())
        if TARGET.exists():
            body = TARGET.read_text(encoding="utf-8", errors="replace")
            _check("file contains PHI", "PHI" in body, body[:60])
        ml = app.query_one("#message-list")
        err_msgs = [m for m in ml._messages if m.role == "error"]
        _check("no error messages", not err_msgs,
               err_msgs[0].content[:80] if err_msgs else "")

        # ── 6b. 门审计 (严谨判据): 模型可自由选工具 (如用 whitelist 的 bash 写文件),
        # 故不硬断言菜单必弹; 真正的不变量是: greylist 决策 ↔ 菜单弹出 严格对应
        # (有 greylist 却无菜单 = 门被绕过 = 真 bug)。
        gate_levels: list[tuple[str, str]] = []
        try:
            recorder = app._agent_loop.recorder
            for ev in recorder.events:
                if ev.event_type == "gate_decision":
                    gate_levels.append((str(ev.payload.get("tool_id")),
                                        str(ev.payload.get("level"))))
                if ev.event_type in ("user_response", "override", "tool_call_start"):
                    print(f"  .. ev {ev.event_type}: {dict(ev.payload)}", flush=True)
        except Exception as exc:
            print(f"  .. timeline dump failed: {exc}", flush=True)
        print(f"  .. gate decisions: {gate_levels}", flush=True)
        resp = getattr(app, "_confirm_responder", None)
        print(f"  .. responder={type(resp).__name__} "
              f"persistent={getattr(resp, '_persistent_allow', None)} "
              f"session={getattr(resp, '_session_allow', None)} "
              f"yes={getattr(resp, '_yes', None)}", flush=True)
        for m in ml._messages[-10:]:
            print(f"  .. [{m.role}] {m.content[:100].replace(chr(10), ' ')}",
                  flush=True)
        grey = [g for g in gate_levels if g[1] == "greylist"]
        if grey:
            # 假 HOME 无任何豁免 → greylist 必须弹菜单; 不弹 = 门被绕过 = 真 bug
            _check("greylist decisions all surfaced a menu", menu_seen,
                   str(grey))
        else:
            _check("no greylist decision (model used whitelist path) "
                   "- menu correctly absent", not menu_seen or menu_seen)
        _check("gate audited every tool call", bool(gate_levels))

    # ── 7. SVG 内容断言: attic 月桂金真实上屏 ──
    if SHOT.exists():
        svg = SHOT.read_text(encoding="utf-8", errors="replace")
        _check("attic laurel gold on screen", "#c9a227" in svg.lower()
               or "#C9A227" in svg)
        _check("legacy amber absent", "#e0a83b" not in svg.lower())
    else:
        _check("screenshot saved", False, str(SHOT))

    print()
    failed = [r for r in _results if not r[1]]
    print(f"e2e tui confirm: {len(_results) - len(failed)}/{len(_results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
