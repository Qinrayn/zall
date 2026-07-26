"""zall.core.open_ended — 开放式任务生成 + 永续自改进轮 (PARADIGM Step 3)。

可证伪经验机的"猜想"上游器官 (Context Agent 的开放式一面): 从经验库里已验证的
技能出发, 生成**新颖且可学** (novel-but-learnable) 的新任务, 驱动永不停止的探索
(借鉴 open-endedness / POET / Darwin Gödel Machine 的变异算子思想)。

闭环 (run_open_ended_round):
  已验证技能 → 生成邻近新任务 → RedBlueLoop 提议解 → SandboxVerifier 客观证伪 →
  熬过证伪的解作为**新技能**写回经验库 → 技能库变厚 → 下一轮从更丰富的技能再生成。

设计: 默认**模板变异**生成 (确定性、离线、模型无关); blue_fn 依赖注入 (可离线单测,
CLI 层接模型)。可选 llm_fn 提供更丰富的生成 (留待增强)。IPR-3: stdlib + core 模块。

不变量 (见 tests/test_open_ended_invariants.py):
  I   生成任务"可学": 派生自某个 parent 技能, 共享其关键词。
  II  生成任务"新颖": 文本不等于 parent (非回声); 生成集合内去重。
  III combine 策略需 >=2 seed; 单 seed 不产 combine。
  IV  空经验库 → 空生成 (诚实退让)。
  V   确定性: 相同 seed → 相同生成。
  VI  永续轮: 只有沙盒 verified 的解才写回经验库 (Popperian Gate)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from zall.core.experience_store import ExperienceStore, get_experience_store

# blue_fn(task_or_inspiration: str) -> solution_code: str
BlueFn = Callable[[str], str]

_HARDEN = "Harden and make robust the solution for: {t}. Cover edge cases, invalid input, and failure modes."
_GENERALIZE = "Generalize: {t}. Handle arbitrary sizes/types, not only the common case."
_VARY = "Re-solve under a constraint: {t}. Use only the Python standard library, no third-party deps."
_COMBINE = "Combine two capabilities into one solution: ({a}) AND ({b})."

# 策略 → 新颖度指示 (仅用于排序/展示, 非严格度量)
_STRATEGY_NOVELTY = {"combine": 0.9, "generalize": 0.7, "harden": 0.6, "vary": 0.5}


@dataclass(frozen=True)
class GeneratedTask:
    """一条生成的新任务 (open-ended 探索的候选)。"""

    task: str
    strategy: str      # harden | generalize | vary | combine
    parent: str        # 源技能任务
    novelty: float


class OpenEndedGenerator:
    """从已验证技能生成新颖且可学的新任务 (模板变异, 确定性)。"""

    def __init__(
        self,
        store: ExperienceStore | None = None,
        llm_fn: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        self._store = store
        self._llm_fn = llm_fn  # 可选: 模型驱动的更丰富生成 (默认不用)

    def _seed_tasks(self, seeds: list[str] | None) -> list[str]:
        if seeds is not None:
            return [s.strip() for s in seeds if s and s.strip()]
        store = self._store or get_experience_store()
        # 仅从已验证技能派生 (Popperian: 站在可靠经验上探索)
        return [r.task for r in store.skills() if r.task.strip()]

    def generate(self, k: int = 3, *, seeds: list[str] | None = None) -> list[GeneratedTask]:
        seed_tasks = self._seed_tasks(seeds)
        if not seed_tasks:
            return []  # IV: 诚实退让

        out: list[GeneratedTask] = []
        seen: set[str] = set()

        def _add(task: str, strategy: str, parent: str) -> None:
            key = task.strip().lower()
            # II: 去重 + 不回声 parent
            if not key or key in seen or key == parent.strip().lower():
                return
            seen.add(key)
            out.append(GeneratedTask(
                task=task, strategy=strategy, parent=parent,
                novelty=_STRATEGY_NOVELTY.get(strategy, 0.5),
            ))

        # 单技能变异 (harden / generalize / vary)
        for t in seed_tasks:
            _add(_GENERALIZE.format(t=t), "generalize", t)
            _add(_HARDEN.format(t=t), "harden", t)
            _add(_VARY.format(t=t), "vary", t)

        # III: 技能组合 (需 >=2 seed) — 相邻但新颖的能力叠加
        for i in range(len(seed_tasks)):
            for j in range(i + 1, len(seed_tasks)):
                a, b = seed_tasks[i], seed_tasks[j]
                # learnable: 两个技能应有一定关联, 但不必强制 (组合本身即探索)
                _add(_COMBINE.format(a=a, b=b), "combine", f"{a} + {b}")

        # 排序: 新颖度高优先, 确定性 (同新颖度按插入序)
        out.sort(key=lambda g: -g.novelty)
        return out[:max(0, k)]


@dataclass(frozen=True)
class OpenEndedReport:
    """一轮永续自改进的结构化报告。"""

    generated: int
    verified: int
    new_skills: tuple[str, ...] = field(default_factory=tuple)
    details: tuple[tuple[str, bool, str], ...] = field(default_factory=tuple)  # (task, verified, critique)

    def summary(self) -> str:
        lines = [f"open-ended: generated {self.generated} task(s), "
                 f"{self.verified} verified → {len(self.new_skills)} new skill(s)"]
        for task, ok, crit in self.details:
            mark = "verified" if ok else "refuted "
            lines.append(f"  [{mark}] {task[:60]} — {crit[:80]}")
        return "\n".join(lines)


def run_open_ended_round(
    blue_fn: BlueFn,
    *,
    store: ExperienceStore | None = None,
    k: int = 2,
    check: str | None = None,
    max_rounds: int = 1,
    proposals_per_round: int = 1,
    on_event: Callable[[str, dict], None] | None = None,
) -> OpenEndedReport:
    """永续自改进一轮: 生成新任务 → 红蓝提议+沙盒证伪 → verified 解写回经验库。

    Popperian Gate: 只有沙盒 verified 的解才作为**新技能**记录 (verified=True)。
    blue_fn 依赖注入 (可离线单测); check 为可选 ground-truth 断言脚本。
    on_event 可选观察者 (CLI live 渲染): 每个任务前发 ("task", {...}), 并传给 RedBlueLoop。
    """
    from zall.core.red_blue import RedBlueLoop
    from zall.core.sandbox_verifier import make_sandbox_red_fn

    st = store or get_experience_store()
    gen = OpenEndedGenerator(store=st)
    tasks = gen.generate(k)

    red_fn = make_sandbox_red_fn(check=check)
    new_skills: list[str] = []
    details: list[tuple[str, bool, str]] = []
    verified_count = 0

    for gt in tasks:
        if on_event is not None:
            try:
                on_event("task", {"task": gt.task, "strategy": gt.strategy, "parent": gt.parent})
            except Exception:
                pass
        loop = RedBlueLoop(
            blue_fn=blue_fn, red_fn=red_fn,
            max_rounds=max_rounds, proposals_per_round=proposals_per_round,
            on_event=on_event,
        )
        rep = loop.run(gt.task)
        best = rep.best
        ok = best is not None and not best.broken and best.score >= 1.0
        crit = best.critique if best is not None else "no solution survived"
        details.append((gt.task, ok, crit))
        if ok and best is not None:
            verified_count += 1
            rec = st.record(gt.task, best.content, verified=True, score=best.score)
            if rec is not None:
                new_skills.append(gt.task)

    return OpenEndedReport(
        generated=len(tasks),
        verified=verified_count,
        new_skills=tuple(new_skills),
        details=tuple(details),
    )
