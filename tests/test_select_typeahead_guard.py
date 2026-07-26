"""确认门选择菜单 type-ahead 防护不变量 (I-SELECT-GRACE).

背景 (kimi 审批面板思想 + e2e 实测风险): 模型运行时用户提前敲的
Enter/数字键滞留在消息泵里, 确认菜单打开瞬间被重放 — 一个滞留回车
即可误批写盘操作 (甚至滞留 'a' 误触 always-allow)。

防护: InputBar.open_select 记录打开时刻; ChatTextArea 在 select_mode
下用 is_typeahead_decision() 吞掉宽限期 (SELECT_GRACE_S) 内的决策键。

不变量 (each with counterexample):
  A  宽限期内的决策键 (Enter/1-9) 被判定为 type-ahead (吞掉)。
  B  宽限期过后同样的键不被吞 (反例孪生: 真人决策必须可达)。
  C  导航 (up/down) 与 escape 任何时刻都不吞 (误 Esc 是安全方向)。
  D  opened_at 未记录 (0) 时不吞 (不破坏非菜单路径)。
  E  open_select 真实写入 select_opened_at (接线不变量)。
"""

from __future__ import annotations

import time

import pytest
pytest.importorskip("textual")

from zall.cli.tui.widgets import (
    SELECT_GRACE_S,
    InputBar,
    is_typeahead_decision,
)

_CHOICES = [
    ("y", "allow once", "run this tool call"),
    ("n", "reject", "skip this tool call"),
]


# ── A: 宽限期内决策键被吞 ──


def test_decision_keys_swallowed_within_grace() -> None:
    t0 = 100.0
    for key in ("enter", "1", "9"):
        assert is_typeahead_decision(key, t0, t0 + 0.01), key
        assert is_typeahead_decision(key, t0, t0 + SELECT_GRACE_S - 0.01), key


# ── B: 宽限期后不吞 (反例孪生: 真人决策必须可达) ──


def test_decision_keys_pass_after_grace() -> None:
    t0 = 100.0
    for key in ("enter", "1", "9"):
        # 浮点减法误差: 用明确超出宽限期的边距 (不在正好边界上断言)
        assert not is_typeahead_decision(key, t0, t0 + SELECT_GRACE_S + 0.01), key
        assert not is_typeahead_decision(key, t0, t0 + 10.0), key


# ── C: 导航与 Esc 任何时刻不吞 ──


def test_navigation_and_escape_never_swallowed() -> None:
    t0 = 100.0
    for key in ("up", "down", "escape"):
        assert not is_typeahead_decision(key, t0, t0 + 0.001), key
        assert not is_typeahead_decision(key, t0, t0 + 1.0), key


# ── D: opened_at 未记录时不吞 ──


def test_unrecorded_opened_at_never_swallows() -> None:
    assert not is_typeahead_decision("enter", 0.0, 123.0)
    assert not is_typeahead_decision("1", 0.0, 123.0)


# ── E: open_select 接线 — 真实写入打开时刻 ──


def test_open_select_records_opened_at() -> None:
    bar = InputBar()
    assert bar._textarea.select_opened_at == 0.0  # 初始未记录
    before = time.monotonic()
    bar.open_select("confirm tool call", _CHOICES)
    after = time.monotonic()
    assert before <= bar._textarea.select_opened_at <= after
    assert bar._textarea.select_mode is True
    # 反例孪生: 若 open_select 忘记记录时刻, 防护整体失效 → 上断言失败


def test_grace_constant_is_humane() -> None:
    """宽限期须 >0 (有防护) 且 <1s (真人不感知延迟); 反例: 0 或 2s 都失败。"""
    assert 0.0 < SELECT_GRACE_S < 1.0


# ── F: REPL 路径同源防护 (flush_stdin_typeahead) ──


def test_flush_stdin_noop_on_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 TTY (测试管道/CI) 静默跳过, 不碰 msvcrt/termios (反例: 碰则崩)。"""
    import sys as _sys
    from zall.cli.responder import flush_stdin_typeahead
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: False, raising=False)
    flush_stdin_typeahead()  # 不抛即通过


def test_flush_stdin_swallows_pending_keys_on_tty(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """TTY 下排空滞留键 (Windows: kbhit/getwch 循环); 异常降级不崩。"""
    import sys as _sys
    from zall.cli import responder as _resp
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True, raising=False)
    if _resp.os.name == "nt":
        import msvcrt
        pending = ["y", "\r"]  # 模拟滞留的 "y↵"
        monkeypatch.setattr(msvcrt, "kbhit", lambda: bool(pending))
        monkeypatch.setattr(msvcrt, "getwch", lambda: pending.pop(0))
        _resp.flush_stdin_typeahead()
        assert pending == []  # 全部被排空
    else:
        _resp.flush_stdin_typeahead()  # POSIX: tcflush 真调不崩即通过


def test_greylist_ask_flushes_only_for_bare_input(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """接线不变量: 裸 input 才冲刷; 注入 ask_fn (测试/prompt_toolkit) 不碰 OS 缓冲。"""
    from zall.cli import responder as _resp
    from zall.core.action import Action
    from zall.core.safety import Judgement, SafeLevel
    calls: list[str] = []
    monkeypatch.setattr(_resp, "flush_stdin_typeahead",
                        lambda: calls.append("flushed"))
    r = _resp.CliUserResponder(
        is_tty=True, ask_fn=lambda _p: "n", print_fn=lambda _s: None)
    action = Action(tool_id="write_file", args={"path": "x"})
    j = Judgement(level=SafeLevel.GREYLIST, matched_rule_ids=("r",))
    resp = r.ask(action, j)
    assert resp.response_type.value == "reject"
    # 反例孪生: 注入了 ask_fn → 绝不能碰 OS 缓冲 (否则测试/真输入栈被干扰)
    assert calls == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
