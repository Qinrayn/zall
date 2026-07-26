"""zall.core.red_blue — Experience Bank + 红蓝对抗共进化循环 (借鉴 Hyra, 吸取前沿)。

不是"盲目 verify"。真正让方案变好的是**对抗压力 + 共进化**, 借鉴三条前沿:
  1. 对抗 (Red-team / debate / self-play): Blue 提方案, Red **主动攻击**找失败模式,
     而非被动打勾。单次生成 → 对抗淘汰, 产出更鲁棒的解。
  2. 多样性 (Hyra Context Agent / quality-diversity): 从 Experience Bank 合成**多样**
     的"灵感"上下文 (含历史失败教训), 让 Blue 朝不同方向探索。
  3. 评估器共进化 (Hyra 双层循环 / POET 自博弈课程): Red 每轮基于 EB 里积累的失败
     **抬高标准**, eval 与 solution 共同进化。

zall 的角色定位 (诚实): 可复现/上链**不是**卖点本身, 而是让整个红蓝对抗过程
可被第三方复核与重放 —— 支撑, 而非头衔。真正的价值是对抗压力产出更好的解。

设计: 纯编排 + 依赖注入 (blue_fn / red_fn), 无模型依赖 (IPR-3)。模型驱动的
blue/red 在 CLI 层用 adapter 组装, 核心保持可离线单测。

不变量 (见 tests/test_red_blue_invariants.py):
  I   best() = 最高分且未被判 broken 的方案 (broken 绝不入选)。
  II  隔离: 单个 blue/red 异常 → 该方案记为 broken, 不中断其余 (IPR-0)。
  III 每轮记录 best 分 → round_best_scores 长度 == rounds (共进化趋势可观测)。
  IV  确定性: 相同注入函数 → 相同报告 (有序, 可复现)。
  V   构造校验 (max_rounds<1 / proposals_per_round<1 → ValueError)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# blue_fn(inspiration: str) -> content: str
BlueFn = Callable[[str], str]
# red_fn(content: str, bank: "ExperienceBank", round_idx: int) -> (score, critique, broken)
RedFn = Callable[[str, "ExperienceBank", int], "tuple[float, str, bool]"]
# on_event(phase, payload) -> None  观察者 (CLI live 渲染用); 只读, 不影响报告/确定性。
OnEvent = Callable[[str, dict], None]


@dataclass(frozen=True)
class Solution:
    """一份方案 + 其对抗评估结果 (Experience Bank 的记录单元)。"""

    sid: str
    round: int
    inspiration: str
    content: str
    score: float
    critique: str
    broken: bool = False


class ExperienceBank:
    """经验库 (EB): 存所有方案+评估; 兼任 Context Agent (合成多样灵感)。

    是红蓝双方的共享记忆 —— Blue 从中取"当前最优", Red 从中取"历史失败"以攻得更狠。
    """

    def __init__(self) -> None:
        self._solutions: list[Solution] = []

    def add(self, s: Solution) -> None:
        self._solutions.append(s)

    def all(self) -> list[Solution]:
        return list(self._solutions)

    def __len__(self) -> int:
        return len(self._solutions)

    def best(self) -> Solution | None:
        """最高分且未 broken 的方案 (不变量 I)。"""
        valid = [s for s in self._solutions if not s.broken]
        if not valid:
            return None
        # 有序: 分数相同取更早的 (确定性, 不变量 IV)
        return max(valid, key=lambda s: (s.score, -self._solutions.index(s)))

    def top(self, n: int = 3) -> list[Solution]:
        valid = [s for s in self._solutions if not s.broken]
        return sorted(valid, key=lambda s: -s.score)[:max(0, n)]

    def failures(self, n: int = 2) -> list[Solution]:
        """最差/破损方案 (供 Red 学习攻击点、Blue 规避)。"""
        fails = [s for s in self._solutions if s.broken or s.score <= 0.34]
        return fails[:max(0, n)]

    def inspirations(self, k: int, task: str) -> list[str]:
        """Context Agent: 从 EB 合成 k 份**多样**灵感上下文 (含失败教训, 促多向探索)。

        round 0 (EB 空): 全是任务本身 + 变体提示; 之后混入当前最优 + 某个失败。
        """
        best = self.best()
        fails = self.failures(2)
        out: list[str] = []
        for i in range(max(1, k)):
            parts = [f"TASK: {task}"]
            if best is not None:
                parts.append(f"CURRENT BEST (score {best.score:.2f}):\n{best.content}")
            # 一半灵感带失败教训 → 多样化 (别都往一个方向挤)
            if fails and i % 2 == 1:
                f = fails[i % len(fails)]
                parts.append(f"AVOID THIS FAILURE:\n{f.content}\ncritique: {f.critique}")
            parts.append(f"[variant {i + 1}] propose a distinct, improved solution.")
            out.append("\n\n".join(parts))
        return out


@dataclass(frozen=True)
class RedBlueReport:
    """一次红蓝对抗循环的结构化报告 (可复核/展示)。"""

    task: str
    rounds: int
    best: Solution | None
    round_best_scores: tuple[float, ...] = field(default_factory=tuple)
    solutions: tuple[Solution, ...] = field(default_factory=tuple)

    @property
    def improved(self) -> bool:
        """共进化是否体现进步: 末轮 best 分 >= 首轮 (非严格递减)。"""
        s = self.round_best_scores
        return bool(s) and s[-1] >= s[0]

    def summary(self) -> str:
        lines = [
            f"red-blue: task={self.task[:48]!r} · {self.rounds} round(s) · "
            f"{len(self.solutions)} proposals · "
            f"best={self.best.score:.2f}" if self.best else
            f"red-blue: task={self.task[:48]!r} · {self.rounds} round(s) · no valid solution",
        ]
        trend = " -> ".join(f"{x:.2f}" for x in self.round_best_scores)
        lines.append(f"  round-best trend: {trend}"
                     + ("  (improved)" if self.improved else "  (flat/regressed)"))
        if self.best is not None:
            lines.append(f"  winner critique: {self.best.critique[:120]}")
        return "\n".join(lines)


def heuristic_red_scorer(content: str, bank: ExperienceBank, round_idx: int) -> tuple[float, str, bool]:
    """离线默认 Red (无模型时): 结构启发式 + 逐轮抬高门槛 (共进化的最小演示)。

    真实场景应注入模型驱动的 Red (主动找 bug/边界/反例)。此处仅保证可离线运行。
    """
    text = (content or "").strip()
    if not text:
        return 0.0, "empty solution", True
    # 朴素质量信号: 长度 + 是否含结构 (换行/关键字)
    score = min(1.0, 0.3 + 0.1 * text.count("\n") + 0.2 * ("def " in text or "->" in text))
    # 共进化: 每轮门槛升高, 低于门槛判 broken (Red 变严)
    bar = 0.3 + 0.15 * round_idx
    broken = score < bar
    crit = f"score {score:.2f} vs bar {bar:.2f} (round {round_idx})" + (" — below bar" if broken else "")
    return score, crit, broken


class RedBlueLoop:
    """红蓝对抗共进化循环 (Blue 提议 → Red 攻击打分 → EB 记录 → 逐轮抬门槛)。

    依赖注入 blue_fn/red_fn (模型驱动或离线启发式), 核心可离线确定性单测。
    """

    def __init__(
        self,
        *,
        blue_fn: BlueFn,
        red_fn: RedFn | None = None,
        bank: ExperienceBank | None = None,
        max_rounds: int = 3,
        proposals_per_round: int = 2,
        on_event: OnEvent | None = None,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if proposals_per_round < 1:
            raise ValueError("proposals_per_round must be >= 1")
        self._blue = blue_fn
        self._red = red_fn or heuristic_red_scorer
        self._bank = bank or ExperienceBank()
        self._max_rounds = max_rounds
        self._ppr = proposals_per_round
        self._on_event = on_event

    @property
    def bank(self) -> ExperienceBank:
        return self._bank

    def _notify(self, phase: str, payload: dict) -> None:
        """观察者回调 (live UX); 回调异常绝不中断循环 (IPR-0 隔离)。"""
        if self._on_event is None:
            return
        try:
            self._on_event(phase, payload)
        except Exception:
            pass

    def run(self, task: str) -> RedBlueReport:
        round_best: list[float] = []
        counter = 0
        for r in range(self._max_rounds):
            round_start = len(self._bank)
            inspirations = self._bank.inspirations(self._ppr, task)
            for insp in inspirations:
                counter += 1
                sid = f"s{counter}"
                self._notify("propose", {"sid": sid, "round": r})
                # Blue 提议 (异常隔离 → broken)
                try:
                    content = self._blue(insp)
                except Exception as e:  # IPR-0: 单方案失败不中断
                    self._bank.add(Solution(sid, r, insp, "", 0.0, f"blue failed: {e}", broken=True))
                    self._notify("verdict", {"sid": sid, "round": r, "score": 0.0,
                                             "broken": True, "critique": f"blue failed: {e}",
                                             "content_len": 0})
                    continue
                # Red 对抗打分 (读 EB 抬门槛; 异常隔离 → broken)
                try:
                    score, critique, broken = self._red(content, self._bank, r)
                except Exception as e:
                    self._bank.add(Solution(sid, r, insp, content, 0.0, f"red failed: {e}", broken=True))
                    self._notify("verdict", {"sid": sid, "round": r, "score": 0.0,
                                             "broken": True, "critique": f"red failed: {e}",
                                             "content_len": len(content or "")})
                    continue
                self._bank.add(Solution(sid, r, insp, content, float(score), str(critique), bool(broken)))
                self._notify("verdict", {"sid": sid, "round": r, "score": float(score),
                                         "broken": bool(broken), "critique": str(critique),
                                         "content_len": len(content or "")})
            # per-round best (仅本轮方案; 对抗动态可升可降, 体现 Red/Blue 拉锢与共进化)
            this_round = [s for s in self._bank.all()[round_start:] if not s.broken]
            round_best.append(max((s.score for s in this_round), default=0.0))
        return RedBlueReport(
            task=task,
            rounds=self._max_rounds,
            best=self._bank.best(),
            round_best_scores=tuple(round_best),
            solutions=tuple(self._bank.all()),
        )
