"""TUI 死锁守卫不变量 (2026-07-26 TUI pilot e2e 钓出的 100% 确定性死锁).

根因: `_tui_listener` (agent worker 线程) 在 `with self._buf_lock:` 块内调用
`self.call_from_thread(...)` — call_from_thread 阻塞等待主线程回调完成, 而回调
(`_flush_token_buffer`/`_flush_thinking_buffer`) 首行就要拿同一把 `_buf_lock`:
    worker: 持锁 → 等主线程回调    主线程: 执行回调 → 等锁    ⇒ 互死。
流式模型输出第一个 token 即触发, 整个 TUI 冻死 (faulthandler 栈转储实锤)。

I-TUI-LOCK: `src/zall/cli/tui/app.py` 中任何 `_buf_lock` 的 with 块体内
不得出现 `call_from_thread` 调用 (锁内只置标志, 跨线程调度必须在锁外)。

IPR-0: 用旧代码形态作反例自证检查器有效。
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "src" / "zall" / "cli" / "tui" / "app.py"


def _find_violations(source: str) -> list[int]:
    """返回违规行号: with <含 _buf_lock> 块体内出现 call_from_thread 调用。"""
    tree = ast.parse(source)
    violations: list[int] = []

    class _V(ast.NodeVisitor):
        def visit_With(self, node: ast.With) -> None:
            has_buf_lock = any(
                "_buf_lock" in ast.dump(item.context_expr) for item in node.items
            )
            if has_buf_lock:
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "call_from_thread"):
                        violations.append(sub.lineno)
            self.generic_visit(node)

    _V().visit(tree)
    return violations


def test_no_call_from_thread_inside_buf_lock() -> None:
    """I-TUI-LOCK: app.py 的 _buf_lock 块内禁止 call_from_thread (死锁)。"""
    source = APP_PY.read_text(encoding="utf-8")
    violations = _find_violations(source)
    assert not violations, (
        f"call_from_thread inside _buf_lock block at lines {violations} — "
        "this is the 2026-07-26 TUI deadlock pattern (worker holds lock waiting "
        "for main thread; main-thread flush callback waits for the lock)"
    )


def test_guard_detects_legacy_deadlock_pattern() -> None:
    """反例 (IPR-0): 旧代码形态必须被检查器检出, 证明守卫真的有效。"""
    legacy = (
        "class App:\n"
        "    def listener(self, token):\n"
        "        with self._buf_lock:\n"
        "            self._token_buffer += token\n"
        "            if not self._token_flush_pending:\n"
        "                self._token_flush_pending = True\n"
        "                self.call_from_thread(self._flush_token_buffer)\n"
    )
    violations = _find_violations(legacy)
    assert violations, "guard failed to detect the legacy deadlock pattern"


def test_guard_allows_lock_outside_dispatch() -> None:
    """修复后的形态 (锁内置标志 → 锁外调度) 必须通过守卫。"""
    fixed = (
        "class App:\n"
        "    def listener(self, token):\n"
        "        schedule = False\n"
        "        with self._buf_lock:\n"
        "            self._token_buffer += token\n"
        "            if not self._token_flush_pending:\n"
        "                self._token_flush_pending = True\n"
        "                schedule = True\n"
        "        if schedule:\n"
        "            self.call_from_thread(self._flush_token_buffer)\n"
    )
    assert not _find_violations(fixed)


def test_flush_callbacks_still_take_lock_first() -> None:
    """互补面: 主线程 flush 回调仍须拿锁读缓冲 (锁语义本身不能被删掉)。

    若有人"修复"死锁的方式是把 _flush_*_buffer 的锁删了, 会引入竞态 —
    这里守住另一半契约。
    """
    source = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("_flush_token_buffer", "_flush_thinking_buffer"):
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == name),
            None,
        )
        assert fn is not None, f"{name} missing from app.py"
        has_lock = any(
            isinstance(n, ast.With)
            and any("_buf_lock" in ast.dump(i.context_expr) for i in n.items)
            for n in ast.walk(fn)
        )
        assert has_lock, f"{name} must guard buffer access with _buf_lock"
