"""SelfImprovementLoop 不变量 (借鉴 Hyra 递归自改进, verified-only 差异化)。

不变量 (each with counterexample, IPR-0):
  I   只有 verify 通过才 apply (低置信度/畸形候选 → 绝不 apply)。
  II  去重 + 收敛 (同候选不重复 apply; 无落地即 converged)。
  III dry_run 只验证不落地 (默认安全预览)。
  IV  单候选 apply 抛异常被隔离 (applied=False, 不中断其余)。
  V   构造校验 (max_rounds<1 / min_confidence 越界 → ValueError)。
  + default_verifier: 置信度门 / adjust_k 越界 / 不安全 skill 名 → 拒绝。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zall.core.self_improve import (
    SelfImprovementLoop,
    default_verifier,
)


def _cand(kind: str = "create_skill", target: str = "foo",
          value: object = "do x", confidence: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, target=target, value=value,
                           confidence=confidence, evidence="because")


# ──────────────────────────────────────────────────────────────────
# default_verifier
# ──────────────────────────────────────────────────────────────────
class TestDefaultVerifier:
    def test_confidence_gate(self) -> None:
        ok, reason = default_verifier(_cand(confidence=0.5), min_confidence=0.8)
        assert not ok and "confidence" in reason           # 反例: 低置信度不通过
        ok2, _ = default_verifier(_cand(confidence=0.9), min_confidence=0.8)
        assert ok2

    def test_adjust_k_range(self) -> None:
        assert not default_verifier(_cand(kind="adjust_k", target="bugfix", value=99))[0]  # 越界
        assert not default_verifier(_cand(kind="adjust_k", target="bugfix", value="x"))[0]  # 非 int
        assert default_verifier(_cand(kind="adjust_k", target="bugfix", value=3))[0]

    def test_unsafe_skill_name(self) -> None:
        assert not default_verifier(_cand(target="../etc/passwd"))[0]   # 反例: 路径穿越
        assert not default_verifier(_cand(target="a/b"))[0]
        assert not default_verifier(_cand(target="", value="x"))[0]
        assert default_verifier(_cand(target="my_skill", value="x"))[0]


# ──────────────────────────────────────────────────────────────────
# I: 只有 verify 通过才 apply
# ──────────────────────────────────────────────────────────────────
class TestApplyOnlyVerified:
    def test_low_confidence_not_applied(self) -> None:
        applied: list = []
        loop = SelfImprovementLoop(
            propose_fn=lambda: [_cand(confidence=0.3)],
            apply_fn=lambda c: applied.append(c) or {"applied": True},
            min_confidence=0.8, dry_run=False,
        )
        rep = loop.run()
        assert rep.applied == 0
        assert applied == []            # 反例: 低置信度绝不 apply
        assert rep.unverified == 1

    def test_verified_applied(self) -> None:
        applied: list = []
        loop = SelfImprovementLoop(
            propose_fn=lambda: [_cand(confidence=0.95)],
            apply_fn=lambda c: applied.append(c) or {"applied": True, "message": "ok"},
            min_confidence=0.8, dry_run=False,
        )
        rep = loop.run()
        assert rep.applied == 1 and len(applied) == 1


# ──────────────────────────────────────────────────────────────────
# III: dry-run 只验证不落地
# ──────────────────────────────────────────────────────────────────
class TestDryRun:
    def test_dry_run_applies_nothing(self) -> None:
        applied: list = []
        loop = SelfImprovementLoop(
            propose_fn=lambda: [_cand(confidence=0.95)],
            apply_fn=lambda c: applied.append(c) or {"applied": True},
            dry_run=True,
        )
        rep = loop.run()
        assert rep.applied == 0 and applied == []      # 反例: dry-run 不落地
        assert rep.outcomes[0].verified is True         # 但已验证


# ──────────────────────────────────────────────────────────────────
# II: 去重 + 收敛
# ──────────────────────────────────────────────────────────────────
class TestDedupeConverge:
    def test_same_candidate_applied_once(self) -> None:
        applied: list = []
        c = _cand(confidence=0.95)
        loop = SelfImprovementLoop(
            propose_fn=lambda: [c, c],       # 同候选出现两次
            apply_fn=lambda x: applied.append(x) or {"applied": True},
            min_confidence=0.8, dry_run=False, max_rounds=3,
        )
        rep = loop.run()
        assert len(applied) == 1          # 反例: 去重, 不重复 apply
        assert rep.converged is True

    def test_empty_proposals_converge(self) -> None:
        loop = SelfImprovementLoop(propose_fn=lambda: [],
                                   apply_fn=lambda c: {"applied": True})
        rep = loop.run()
        assert rep.proposed == 0 and rep.converged and rep.applied == 0


# ──────────────────────────────────────────────────────────────────
# IV: apply 异常隔离
# ──────────────────────────────────────────────────────────────────
class TestApplyIsolation:
    def test_apply_exception_isolated(self) -> None:
        def boom(c: object) -> dict:
            raise RuntimeError("disk full")
        loop = SelfImprovementLoop(
            propose_fn=lambda: [_cand(confidence=0.95)],
            apply_fn=boom, min_confidence=0.8, dry_run=False,
        )
        rep = loop.run()
        assert rep.applied == 0                          # 反例: 未真正落地
        assert rep.outcomes[0].verified and not rep.outcomes[0].applied
        assert "apply raised" in rep.outcomes[0].reason  # 隔离为记录, 不崩


# ──────────────────────────────────────────────────────────────────
# V: 构造校验
# ──────────────────────────────────────────────────────────────────
class TestConstruction:
    def test_invalid_max_rounds(self) -> None:
        with pytest.raises(ValueError):
            SelfImprovementLoop(propose_fn=lambda: [], apply_fn=lambda c: {}, max_rounds=0)

    def test_invalid_min_confidence(self) -> None:
        with pytest.raises(ValueError):
            SelfImprovementLoop(propose_fn=lambda: [], apply_fn=lambda c: {}, min_confidence=1.5)


# ──────────────────────────────────────────────────────────────────
# from_auto_learn 接线 (真实消费者)
# ──────────────────────────────────────────────────────────────────
class TestFromAutoLearn:
    def test_wires_to_extension(self) -> None:
        class FakeExt:
            def __init__(self) -> None:
                self.applied: list = []

            def get_suggestions(self) -> list:
                return [_cand(confidence=0.95)]

            def apply_suggestion(self, s: object) -> dict:
                self.applied.append(s)
                return {"applied": True, "message": "m"}

        ext = FakeExt()
        loop = SelfImprovementLoop.from_auto_learn(ext, min_confidence=0.8, dry_run=False)
        rep = loop.run()
        assert rep.applied == 1 and len(ext.applied) == 1

    def test_from_auto_learn_dry_run_default(self) -> None:
        class FakeExt:
            def __init__(self) -> None:
                self.applied: list = []

            def get_suggestions(self) -> list:
                return [_cand(confidence=0.95)]

            def apply_suggestion(self, s: object) -> dict:
                self.applied.append(s)
                return {"applied": True}

        ext = FakeExt()
        rep = SelfImprovementLoop.from_auto_learn(ext).run()  # dry_run 默认 True
        assert rep.applied == 0 and ext.applied == []          # 反例: 默认不落地


# ──────────────────────────────────────────────────────────────────
# 报告
# ──────────────────────────────────────────────────────────────────
class TestReport:
    def test_summary_counts(self) -> None:
        loop = SelfImprovementLoop(
            propose_fn=lambda: [_cand(confidence=0.95),
                                _cand(target="bar", confidence=0.3)],
            apply_fn=lambda c: {"applied": True, "message": "done"},
            min_confidence=0.8, dry_run=False,
        )
        rep = loop.run()
        s = rep.summary()
        assert "self-improve" in s and "applied" in s
        assert rep.applied == 1 and rep.unverified == 1
