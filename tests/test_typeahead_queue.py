"""typeahead — 干活期间打字排队 (2026-09-19 实测反馈: 中途干活不能发信息)。

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

from typing import Any

from zall.cli.typeahead import TypeaheadCollector, _feed_char


def _new_state() -> dict[str, Any]:
    return {"buf": "", "queue": []}


class TestFeedChar:
    def test_printable_accumulates_in_buffer(self) -> None:
        st = _new_state()
        for ch in "hi 12":
            _feed_char(st, ch, lambda: None)
        assert st["buf"] == "hi 12"
        assert st["queue"] == []

    def test_enter_commits_nonempty_line(self) -> None:
        st = _new_state()
        for ch in "hello":
            _feed_char(st, ch, lambda: None)
        _feed_char(st, "\r", lambda: None)
        assert st["queue"] == ["hello"]
        assert st["buf"] == ""

    def test_enter_on_empty_line_queues_nothing(self) -> None:
        """Counterexample: 空行 Enter 不得产生空串任务。"""
        st = _new_state()
        for ch in "  ":
            _feed_char(st, ch, lambda: None)
        _feed_char(st, "\n", lambda: None)
        assert st["queue"] == []
        assert st["buf"] == ""

    def test_backspace_removes_last_char(self) -> None:
        st = _new_state()
        for ch in "abc":
            _feed_char(st, ch, lambda: None)
        _feed_char(st, "\x08", lambda: None)
        assert st["buf"] == "ab"
        _feed_char(st, "\x7f", lambda: None)
        assert st["buf"] == "a"

    def test_ctrl_c_invokes_interrupt(self) -> None:
        st = _new_state()
        st["buf"] = "partial"
        hits: list[int] = []
        _feed_char(st, "\x03", lambda: hits.append(1))
        assert hits == [1]
        # 打到一半的 buffer 不被误提交
        assert st["queue"] == []

    def test_windows_function_key_prefix_swallows_next_code(self) -> None:
        """Counterexample: 方向键 (0xe0 + 码) 不得往 buffer 里塞垃圾。"""
        st = _new_state()
        for ch in "ab":
            _feed_char(st, ch, lambda: None)
        _feed_char(st, "\xe0", lambda: None)   # 前缀码
        _feed_char(st, "M", lambda: None)      # 功能键主码 — 应被吞
        assert st["buf"] == "ab"

    def test_esc_sequence_marker_swallows_next_code(self) -> None:
        st = _new_state()
        for ch in "ok":
            _feed_char(st, ch, lambda: None)
        _feed_char(st, "\x1b", lambda: None)
        _feed_char(st, "A", lambda: None)
        assert st["buf"] == "ok"

    def test_ctrl_d_is_not_text(self) -> None:
        st = _new_state()
        _feed_char(st, "\x04", lambda: None)
        assert st["buf"] == ""
        assert st["queue"] == []


class TestCollectorLifecycle:
    def test_non_tty_start_is_noop_and_drain_empty(self) -> None:
        """Counterexample: 非 TTY (测试管道) 下 start() 不起线程、drain 为空。"""
        c = TypeaheadCollector()
        c.start()
        c.stop()
        assert c.drain() == []
        assert c.buffer == ""
        assert c.queued_count == 0
        assert c.interrupted is False

    def test_drain_moves_and_clears_queue(self) -> None:
        c = TypeaheadCollector()
        with c._lock:
            c._state["queue"] = ["a", "b"]
        first = c.drain()
        assert first == ["a", "b"]
        assert c.drain() == []  # 第二次 drain 不重发


class TestConsumeEcho:
    """实测反馈: 打字必须当场可见 (光标处回显), 不能只挂在 spinner 上。"""

    def _collector_with_fake_stdout(self, monkeypatch: Any) -> list[str]:
        import zall.cli.typeahead as ta_mod

        written: list[str] = []

        class _FakeOut:
            def write(self, s: str) -> None:
                written.append(s)

            def flush(self) -> None:
                pass

        monkeypatch.setattr(ta_mod.sys, "__stdout__", _FakeOut())
        return written

    def test_printable_char_echoes_at_cursor(self, monkeypatch: Any) -> None:
        written = self._collector_with_fake_stdout(monkeypatch)
        c = TypeaheadCollector()
        with c._lock:
            c._consume("h")
            c._consume("i")
        assert written == ["h", "i"]
        assert c.buffer == "hi"

    def test_backspace_erases_previous_char(self, monkeypatch: Any) -> None:
        written = self._collector_with_fake_stdout(monkeypatch)
        c = TypeaheadCollector()
        with c._lock:
            c._consume("a")
            c._consume("\x08")
        assert "".join(written) == "a\b \b"  # 退格三连: 移动-盖空白-移回
        assert c.buffer == ""

    def test_enter_commit_writes_newline(self, monkeypatch: Any) -> None:
        written = self._collector_with_fake_stdout(monkeypatch)
        c = TypeaheadCollector()
        with c._lock:
            c._consume("x")
            c._consume("\r")
        assert "\n" in written
        assert c.queued_count == 1

    def test_function_key_codes_echo_nothing(self, monkeypatch: Any) -> None:
        """Counterexample: 功能键/ESC 序列不得往终端漏字符。"""
        written = self._collector_with_fake_stdout(monkeypatch)
        c = TypeaheadCollector()
        with c._lock:
            c._consume("\xe0")
            c._consume("M")
            c._consume("\x1b")
            c._consume("A")
            c._consume("\x03")
        assert written == []
