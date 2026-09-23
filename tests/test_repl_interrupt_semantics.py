"""v0.6.2 REPL 中断与异常语义 (Kimi 口径):

- 单次 Ctrl+C = 打断当前输入 (清行继续, 不退出)
- 2s 内双击 Ctrl+C → 退出, 码 130 (POSIX 惯例)
- 回合级未预期异常 → 落一行 internal error 回提示符, REPL 存活
"""
import io

from zall.cli import repl_ui


class _FakeTTY(io.StringIO):
    """StringIO 但 isatty()=True (mock 终端, 触发交互 branch)。"""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


def _seq_input(steps):
    """按序产出: str → 返回; BaseException 实例 → raise; 耗尽 → EOF。"""
    it = iter(steps)

    def _fn(_p: str = "> ") -> str:
        v = next(it)
        if isinstance(v, BaseException):  # KI/EOF 是 BaseException, 不是 Exception
            raise v
        return v

    return _fn


class TestInterruptSemantics:
    def test_single_interrupt_continues(self) -> None:
        """单次 Ctrl+C = 打断当前输入, 提示符照常回来 (Kimi/Codex 口径)。"""
        out = _FakeTTY()
        rc = repl_ui.repl(
            input_fn=_seq_input([KeyboardInterrupt(), "/exit"]),
            out=out, stream=False,
        )
        assert rc == 0
        assert "Ctrl-C again to exit" in out.getvalue()

    def test_double_interrupt_exits_130(self) -> None:
        """双击 Ctrl+C (2s 窗口内) → bye + 130 — 习惯性双击不被困在提示符。"""

        def _always_ki(_p: str = "> ") -> str:
            raise KeyboardInterrupt

        out = _FakeTTY()
        rc = repl_ui.repl(input_fn=_always_ki, out=out, stream=False)
        assert rc == 130
        assert "bye" in out.getvalue()

    def test_turn_exception_does_not_kill_repl(self, monkeypatch) -> None:
        """回合体未预期异常 → 一行 internal error, 会话存活可继续。"""
        def _boom(line: str):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(repl_ui, "expand_at_references", _boom)
        out = _FakeTTY()
        rc = repl_ui.repl(
            input_fn=_seq_input(["trigger turn", "/exit"]),
            out=out, stream=False,
        )
        assert rc == 0  # REPL 没死
        assert "internal error" in out.getvalue()
        assert "kaboom" in out.getvalue()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
