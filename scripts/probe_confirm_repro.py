"""确定性复现: TUI 确认门在 scripted adapter 下是否弹选择菜单.

ScriptedAdapter 强制 write_file 调用 (greylist) — 无真实 API, 秒级复现。
观测: bar.select_open / responder 类型 / 消息区内容 / 文件是否落盘。
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(120, exit=True)

REPO = Path(__file__).resolve().parent.parent
PLAYGROUND = REPO / ".e2e_playground"
SCRIPT = PLAYGROUND / "probe_script.json"
TARGET = PLAYGROUND / "probe_out.txt"


async def main() -> int:
    os.chdir(PLAYGROUND)
    if TARGET.exists():
        TARGET.unlink()
    SCRIPT.write_text(json.dumps({
        "responses": [
            {"tool_calls": [{"id": "t1", "tool_id": "write_file",
                             "args": {"path": str(TARGET),
                                      "content": "probe"}}]},
            {"content": "done", "stop_reason": "stop"},
        ]
    }), encoding="utf-8")
    os.environ["ZALL_SCRIPT"] = str(SCRIPT)

    # 摘除 write_file 豁免
    allow_path = Path.home() / ".zall" / "always_allow.json"
    backup = allow_path.read_text(encoding="utf-8") if allow_path.exists() else None
    if backup:
        data = json.loads(backup)
        data["tool_ids"] = [t for t in data.get("tool_ids", []) if t != "write_file"]
        allow_path.write_text(json.dumps(data), encoding="utf-8")

    try:
        from zall.cli.tui import TuiApp
        from zall.cli.tui.widgets import InputBar
        # 插桩: 观察 responder 构建时的豁免集与每次 submit_reply
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

        app = TuiApp(yes=False)
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                await asyncio.wait_for(pilot.pause(), timeout=2)
            except asyncio.TimeoutError:
                pass
            bar = app.query_one("#input-bar", InputBar)
            bar._textarea.text = "do the scripted thing"
            # 直接触发提交路径 (绕过逐键, 聚焦确认门本身)
            app.post_message(InputBar.Submitted("do the scripted thing"))
            deadline = time.monotonic() + 30
            seen = False
            while time.monotonic() < deadline:
                if bar.select_open:
                    seen = True
                    break
                await asyncio.sleep(0.1)
            print("select_open seen:", seen, flush=True)
            print("responder:", type(getattr(app, '_confirm_responder', None)).__name__,
                  flush=True)
            resp = getattr(app, "_confirm_responder", None)
            if resp is not None:
                print("persistent_allow:", getattr(resp, "_persistent_allow", None),
                      "session_allow:", getattr(resp, "_session_allow", None),
                      "yes:", getattr(resp, "_yes", None), flush=True)
            if seen:  # 模拟真人: 看清菜单再批准 (type-ahead 宽限期之外)
                await asyncio.sleep(0.5)
                try:
                    await asyncio.wait_for(pilot.press("enter"), timeout=2)
                except asyncio.TimeoutError:
                    pass
            for _ in range(60):
                if not app._agent_running:
                    break
                await asyncio.sleep(0.5)
            ml = app.query_one("#message-list")
            for m in ml._messages[-6:]:
                print(f"  [{m.role}] {m.content[:90].replace(chr(10),' ')}",
                      flush=True)
            print("file written:", TARGET.exists(), flush=True)
            return 0 if seen else 1
    finally:
        if backup:
            allow_path.write_text(backup, encoding="utf-8")
        os.environ.pop("ZALL_SCRIPT", None)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
