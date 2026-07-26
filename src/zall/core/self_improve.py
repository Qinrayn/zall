"""zall.core.self_improve — 持续自改进循环 (借鉴腾讯 Hyra 的递归自我改进)。

Hyra (Hunyuan Research Agent) 用"生产者-消费者"异步流水线做递归自我改进:
探索 → 提出更优方案 → 产出改进。但其过程对外不透明。

zall 的版本坚持 IPR-0 精神做出**差异化**: 每个候选改进必须先通过独立 `verify`
才允许落地 (apply); 未通过的被记录 + 理由拒绝, 全程可第三方复核。即
**verified-only self-improvement** —— 只保留"可验证的增益"。

设计:
  纯编排, 依赖注入 (propose/verify/apply 三个函数), 无模型依赖 (IPR-3):
    - propose_fn() -> list[candidate]      产出候选 (通常 AutoLearnExtension.get_suggestions)
    - verify_fn(candidate) -> (ok, reason) 独立验证 (差异化: 只留可验证增益)
    - apply_fn(candidate) -> dict          落地   (通常 AutoLearnExtension.apply_suggestion)
  candidate 为鸭子类型: 具备 .kind / .target / .value / .confidence / .evidence
  (即 core.lifecycle.SelfSuggestion), 但本模块不 import 它以保持解耦。

不变量 (对应 tests/test_self_improve_invariants.py):
  I  只有 verify 通过的候选才 apply (未通过绝不 apply)。
  II 每轮去重 (kind,target,value); 无新候选/无落地即收敛 (converged), 最多 max_rounds。
  III dry_run=True → 只验证不落地 (安全预览, 默认)。
  IV 单候选 apply 抛异常被隔离为 applied=False, 不中断其余 (IPR-0)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 类型别名
Candidate = Any  # 鸭子类型: .kind/.target/.value/.confidence/.evidence
ProposeFn = Callable[[], "list[Candidate]"]
VerifyFn = Callable[[Candidate], "tuple[bool, str]"]
ApplyFn = Callable[[Candidate], "dict[str, Any]"]


def default_verifier(candidate: Candidate, min_confidence: float = 0.8) -> tuple[bool, str]:
    """默认验证器: 置信度门 + 结构/安全检查 (可被调用方替换为跑测试等更强验证)。

    这是"可验证增益"的最小实现 —— 拒绝低置信度 / 畸形 / 不安全的候选。
    Counterexample: confidence 低于阈值、adjust_k 越界、含路径分隔符的 skill 名 → 拒绝。
    """
    conf = 0.0
    try:
        conf = float(getattr(candidate, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, "confidence not a number"
    if conf < min_confidence:
        return False, f"confidence {conf:.2f} < {min_confidence:.2f}"

    kind = str(getattr(candidate, "kind", "") or "")
    target = str(getattr(candidate, "target", "") or "")
    value = getattr(candidate, "value", None)

    if kind == "adjust_k":
        try:
            k = int(value)
        except (TypeError, ValueError):
            return False, f"adjust_k value not int: {value!r}"
        if not (1 <= k <= 10):
            return False, f"adjust_k out of range [1,10]: {k}"
    elif kind == "create_skill":
        if (not target or "/" in target or "\\" in target
                or ".." in target or target != target.strip()):
            return False, f"unsafe skill name: {target!r}"
        if not value:
            return False, "empty skill body"
    elif kind in ("adjust_judge", "register_goaltype"):
        if not target:
            return False, f"empty target for {kind}"
    return True, "ok"


@dataclass(frozen=True)
class ImprovementOutcome:
    """单个候选的处置结果 (可复核: 记录是否验证/落地及理由)。"""

    kind: str
    target: str
    verified: bool
    applied: bool
    reason: str
    apply_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class ImprovementReport:
    """一次自改进循环的结构化报告 (全程有据, 供审计/展示)。"""

    rounds: int
    proposed: int
    applied: int
    converged: bool
    outcomes: tuple[ImprovementOutcome, ...] = field(default_factory=tuple)

    @property
    def rejected(self) -> int:
        return sum(1 for o in self.outcomes if o.verified and not o.applied)

    @property
    def unverified(self) -> int:
        return sum(1 for o in self.outcomes if not o.verified)

    def summary(self) -> str:
        head = (f"self-improve: {self.rounds} round(s) · {self.proposed} proposed · "
                f"{self.applied} applied · {self.unverified} unverified · "
                f"{self.rejected} rejected"
                + (" · converged" if self.converged else ""))
        lines = [head]
        for o in self.outcomes:
            mark = "applied " if o.applied else ("verified" if o.verified else "rejected")
            lines.append(f"  [{mark}] {o.kind}:{o.target} — {o.reason}")
        return "\n".join(lines)


class SelfImprovementLoop:
    """verified-only 持续自改进循环 (借鉴 Hyra, 但只落地可验证增益)。

    每轮: propose → (去重) → verify → 仅对通过者 apply。无新候选/无落地即收敛。
    dry_run=True (默认) 时只验证不落地 —— 安全预览。
    """

    def __init__(
        self,
        *,
        propose_fn: ProposeFn,
        apply_fn: ApplyFn,
        verify_fn: VerifyFn | None = None,
        min_confidence: float = 0.8,
        max_rounds: int = 3,
        dry_run: bool = True,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError("min_confidence must be in [0.0, 1.0]")
        self._propose = propose_fn
        self._apply = apply_fn
        self._verify = verify_fn or (lambda c: default_verifier(c, min_confidence))
        self._min_confidence = min_confidence
        self._max_rounds = max_rounds
        self._dry_run = dry_run

    @classmethod
    def from_auto_learn(
        cls,
        ext: Any,
        *,
        verify_fn: VerifyFn | None = None,
        min_confidence: float = 0.8,
        max_rounds: int = 3,
        dry_run: bool = True,
    ) -> SelfImprovementLoop:
        """接线到既有 AutoLearnExtension (真实消费者): get_suggestions / apply_suggestion。"""
        return cls(
            propose_fn=ext.get_suggestions,
            apply_fn=ext.apply_suggestion,
            verify_fn=verify_fn,
            min_confidence=min_confidence,
            max_rounds=max_rounds,
            dry_run=dry_run,
        )

    def run(self) -> ImprovementReport:
        outcomes: list[ImprovementOutcome] = []
        seen: set[tuple[str, str, str]] = set()
        proposed_total = 0
        applied_total = 0
        rounds = 0
        converged = False

        for _ in range(self._max_rounds):
            rounds += 1
            candidates = list(self._propose() or [])

            # II: 去重 (kind,target,value) — 跨轮不重复处理同一候选
            fresh: list[Candidate] = []
            for c in candidates:
                key = (
                    str(getattr(c, "kind", "")),
                    str(getattr(c, "target", "")),
                    repr(getattr(c, "value", None)),
                )
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(c)

            if not fresh:
                converged = True
                break

            proposed_total += len(fresh)
            round_applied = 0

            for c in fresh:
                kind = str(getattr(c, "kind", "?"))
                target = str(getattr(c, "target", "?"))
                try:
                    ok, reason = self._verify(c)
                except Exception as e:  # 验证器异常 → 视为未通过 (不落地)
                    outcomes.append(ImprovementOutcome(kind, target, False, False, f"verify raised: {e}"))
                    continue

                if not ok:
                    # I: 未通过验证 → 绝不 apply
                    outcomes.append(ImprovementOutcome(kind, target, False, False, reason))
                    continue

                if self._dry_run:
                    # III: 预览 — 已验证但不落地
                    outcomes.append(ImprovementOutcome(kind, target, True, False, "verified (dry-run)"))
                    continue

                try:
                    res = self._apply(c)
                except Exception as e:  # IV: 隔离单候选 apply 异常
                    outcomes.append(ImprovementOutcome(kind, target, True, False, f"apply raised: {e}"))
                    continue

                applied_ok = bool(res.get("applied")) if isinstance(res, dict) else bool(res)
                msg = res.get("message", "") if isinstance(res, dict) else ""
                outcomes.append(ImprovementOutcome(
                    kind, target, True, applied_ok,
                    msg or ("applied" if applied_ok else "apply returned falsy"),
                    res if isinstance(res, dict) else None,
                ))
                if applied_ok:
                    round_applied += 1
                    applied_total += 1

            # II: 本轮无任何落地 (全被拒/dry-run) → 无状态变化 → 收敛
            if round_applied == 0:
                converged = True
                break

        return ImprovementReport(
            rounds=rounds,
            proposed=proposed_total,
            applied=applied_total,
            converged=converged,
            outcomes=tuple(outcomes),
        )
