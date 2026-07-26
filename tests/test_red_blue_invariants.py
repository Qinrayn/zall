"""Red-Blue 对抗共进化循环 + Experience Bank 不变量 (借鉴 Hyra, 吸取前沿)。

不变量 (each with counterexample, IPR-0):
  I   best() = 最高分且未 broken (broken 高分方案绝不入选)。
  II  隔离: 单个 blue/red 异常 → 该方案 broken, 不中断其余。
  III round_best_scores 长度 == rounds (per-round best, 对抗动态可升可降)。
  IV  确定性: 相同注入函数 → 相同报告。
  V   构造校验 (max_rounds<1 / proposals_per_round<1 → ValueError)。
  + Context Agent inspirations: 数量=k; round0 无 best; 之后含 best。
  + heuristic_red_scorer: 空→broken; 门槛逐轮抬高。
"""

from __future__ import annotations

import pytest

from zall.core.red_blue import (
    ExperienceBank,
    RedBlueLoop,
    Solution,
    heuristic_red_scorer,
)


def _blue(insp: str) -> str:
    return "def solve():\n    return 42"


def _red_fixed(score: float, broken: bool = False):
    def r(content: str, bank: ExperienceBank, rnd: int) -> tuple[float, str, bool]:
        return score, f"fixed {score}", broken
    return r


# ──────────────────────────────────────────────────────────────────
# ExperienceBank
# ──────────────────────────────────────────────────────────────────
class TestExperienceBank:
    def test_best_ignores_broken(self) -> None:
        eb = ExperienceBank()
        eb.add(Solution("s1", 0, "i", "a", 0.9, "hi", broken=True))   # 高分但 broken
        eb.add(Solution("s2", 0, "i", "b", 0.5, "ok", broken=False))
        assert eb.best().sid == "s2"      # 反例: broken 的 0.9 不入选
        assert eb.best().score == 0.5

    def test_best_none_when_all_broken(self) -> None:
        eb = ExperienceBank()
        eb.add(Solution("s1", 0, "i", "a", 0.9, "x", broken=True))
        assert eb.best() is None

    def test_best_deterministic_on_ties(self) -> None:
        eb = ExperienceBank()
        eb.add(Solution("s1", 0, "i", "a", 0.7, "x"))
        eb.add(Solution("s2", 0, "i", "b", 0.7, "y"))
        assert eb.best().sid == "s1"      # 平分取更早 (确定性)

    def test_top_and_failures(self) -> None:
        eb = ExperienceBank()
        eb.add(Solution("s1", 0, "i", "a", 0.9, "x"))
        eb.add(Solution("s2", 0, "i", "b", 0.2, "y"))
        eb.add(Solution("s3", 0, "i", "c", 0.8, "z"))
        assert [s.sid for s in eb.top(2)] == ["s1", "s3"]
        assert any(s.sid == "s2" for s in eb.failures(2))

    def test_inspirations_count_and_content(self) -> None:
        eb = ExperienceBank()
        # round0: EB 空 → 无 best, 只含任务
        insp0 = eb.inspirations(3, "build X")
        assert len(insp0) == 3
        assert all("build X" in s for s in insp0)
        assert all("CURRENT BEST" not in s for s in insp0)  # 反例: 空库无 best
        # 有 best 后 → 灵感含当前最优
        eb.add(Solution("s1", 0, "i", "good sol", 0.9, "x"))
        insp1 = eb.inspirations(2, "build X")
        assert any("CURRENT BEST" in s for s in insp1)


# ──────────────────────────────────────────────────────────────────
# RedBlueLoop
# ──────────────────────────────────────────────────────────────────
class TestRedBlueLoop:
    def test_basic_run_selects_best(self) -> None:
        loop = RedBlueLoop(blue_fn=_blue, red_fn=_red_fixed(0.7),
                           max_rounds=2, proposals_per_round=2)
        rep = loop.run("task")
        assert rep.best is not None and rep.best.score == 0.7
        assert len(rep.round_best_scores) == 2          # 不变量 III
        assert len(rep.solutions) == 4                   # 2 rounds × 2

    def test_blue_exception_isolated(self) -> None:
        calls = {"n": 0}

        def flaky_blue(insp: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("blue boom")
            return "def ok(): pass"
        loop = RedBlueLoop(blue_fn=flaky_blue, red_fn=_red_fixed(0.6),
                           max_rounds=1, proposals_per_round=2)
        rep = loop.run("t")
        broken = [s for s in rep.solutions if s.broken]
        assert len(broken) == 1 and "blue failed" in broken[0].critique  # 隔离
        assert rep.best is not None                       # 另一个方案存活

    def test_red_exception_isolated(self) -> None:
        def boom_red(content: str, bank: ExperienceBank, rnd: int):
            raise RuntimeError("red boom")
        loop = RedBlueLoop(blue_fn=_blue, red_fn=boom_red,
                           max_rounds=1, proposals_per_round=1)
        rep = loop.run("t")
        assert rep.best is None
        assert rep.solutions[0].broken and "red failed" in rep.solutions[0].critique

    def test_coevolution_trend_can_rise(self) -> None:
        # Red 逐轮宽松 → per-round best 上升 (Blue 追上)
        def red_easing(content: str, bank: ExperienceBank, rnd: int):
            return min(1.0, 0.4 + 0.2 * rnd), f"r{rnd}", False
        rep = RedBlueLoop(blue_fn=_blue, red_fn=red_easing,
                          max_rounds=3, proposals_per_round=1).run("t")
        assert rep.round_best_scores == (0.4, pytest.approx(0.6), pytest.approx(0.8))
        assert rep.improved is True

    def test_coevolution_trend_can_regress(self) -> None:
        # 反例: Red 逐轮变严 → per-round best 下降 (Red 压过 Blue)
        def red_hardening(content: str, bank: ExperienceBank, rnd: int):
            score = max(0.0, 0.9 - 0.3 * rnd)
            return score, f"r{rnd}", False
        rep = RedBlueLoop(blue_fn=_blue, red_fn=red_hardening,
                          max_rounds=3, proposals_per_round=1).run("t")
        assert rep.round_best_scores[0] > rep.round_best_scores[-1]  # 下降
        assert rep.improved is False                                  # 反例: 未进步

    def test_determinism(self) -> None:
        def make() -> RedBlueLoop:
            return RedBlueLoop(blue_fn=_blue, red_fn=_red_fixed(0.55),
                               max_rounds=2, proposals_per_round=2)
        r1 = make().run("same task")
        r2 = make().run("same task")
        assert r1.round_best_scores == r2.round_best_scores
        assert [s.sid for s in r1.solutions] == [s.sid for s in r2.solutions]
        assert r1.best.score == r2.best.score

    def test_construction_validation(self) -> None:
        with pytest.raises(ValueError):
            RedBlueLoop(blue_fn=_blue, max_rounds=0)
        with pytest.raises(ValueError):
            RedBlueLoop(blue_fn=_blue, proposals_per_round=0)


# ──────────────────────────────────────────────────────────────────
# heuristic_red_scorer (离线默认 Red)
# ──────────────────────────────────────────────────────────────────
class TestHeuristicRed:
    def test_empty_is_broken(self) -> None:
        score, crit, broken = heuristic_red_scorer("", ExperienceBank(), 0)
        assert broken and score == 0.0            # 反例: 空方案判 broken

    def test_bar_rises_each_round(self) -> None:
        content = "def f():\n    return 1"
        _, _, broken_r0 = heuristic_red_scorer(content, ExperienceBank(), 0)
        _, _, broken_r5 = heuristic_red_scorer(content, ExperienceBank(), 5)
        # 同样内容, 后面轮次门槛更高 → 更易被判 broken (共进化: Red 变严)
        assert broken_r0 is False
        assert broken_r5 is True
