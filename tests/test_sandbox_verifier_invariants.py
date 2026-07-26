"""SandboxVerifier 不变量 (PARADIGM Step 2: grounded refutation, 真实沙盒执行)。

这些测试**真的在沙盒里跑 Python** (非 mock) — 证明 Red 的证伪 = 客观执行结果, 非自评。

不变量 (each with counterexample):
  I   干净运行的解法 → verified (score 1.0, not broken)。
  II  报错/语法错的解法 → refuted (broken) — 反例: 坏代码绝不被判 verified。
  III check 断言: 正确解法通过 → verified; 错误解法 → refuted (反例)。
  IV  空解法 → broken。
  V   接入 RedBlueLoop: blue 出好代码→best verified; blue 出坏代码→best None。
  + extract_code: 去 ``` 围栏; 无围栏原样。
"""

from __future__ import annotations

from zall.core.red_blue import RedBlueLoop
from zall.core.sandbox_verifier import (
    SandboxVerifier,
    extract_code,
    make_sandbox_red_fn,
)


class TestExtractCode:
    def test_strips_fence(self) -> None:
        assert extract_code("```python\nprint(1)\n```") == "print(1)"
        assert extract_code("no fence here x=1") == "no fence here x=1"
        assert extract_code("") == ""


class TestVerifyRun:
    def test_clean_code_verified(self) -> None:
        r = SandboxVerifier().verify("print('hello world')")
        assert r.score == 1.0 and r.broken is False       # I

    def test_broken_code_refuted(self) -> None:
        # 语法错 → 反例: 绝不 verified
        r = SandboxVerifier().verify("print( 'unterminated")
        assert r.broken is True and r.score == 0.0        # II

    def test_runtime_error_refuted(self) -> None:
        r = SandboxVerifier().verify("raise ValueError('boom')")
        assert r.broken is True

    def test_empty_broken(self) -> None:
        r = SandboxVerifier().verify("")
        assert r.broken is True                            # IV


class TestVerifyCheck:
    CHECK = "from solution import add\nassert add(2, 3) == 5\nprint('ok')"

    def test_correct_solution_passes_check(self) -> None:
        r = SandboxVerifier().verify("def add(a, b):\n    return a + b", check=self.CHECK)
        assert r.score == 1.0 and not r.broken             # III

    def test_wrong_solution_fails_check(self) -> None:
        # 反例: 实现错误 → 断言失败 → refuted (客观证伪, 非自评)
        r = SandboxVerifier().verify("def add(a, b):\n    return a - b", check=self.CHECK)
        assert r.broken is True and r.score == 0.0         # III 反例


class TestRedBlueIntegration:
    def test_good_solution_survives(self) -> None:
        loop = RedBlueLoop(
            blue_fn=lambda insp: "print('ok')",
            red_fn=make_sandbox_red_fn(),
            max_rounds=1, proposals_per_round=1,
        )
        rep = loop.run("print ok")
        assert rep.best is not None and rep.best.score == 1.0   # V

    def test_broken_solution_refuted(self) -> None:
        # blue 出语法错代码 → 沙盒证伪 → 无 verified 幸存者
        loop = RedBlueLoop(
            blue_fn=lambda insp: "def (:",
            red_fn=make_sandbox_red_fn(),
            max_rounds=1, proposals_per_round=1,
        )
        rep = loop.run("bad")
        assert rep.best is None                                  # V 反例
