"""Experience learning gate: VerificationTier-aware distillation (PARADIGM §5.1 wiring).

The Popperian Gate (ExperienceStore) now consumes proof_gate's VerificationTier.
Hard wall (machine-enforced): CORROBORATED (bounded corroboration) is NEVER
distilled into proven_skills nor rendered as PROVEN in the recall context.

IPR-0: each invariant has a counterexample.
"""

from __future__ import annotations

from zall.core.experience_store import (
    TIER_CORROBORATED,
    TIER_PROVEN,
    TIER_REFUTED,
    ExperienceStore,
)
from zall.core.proof_gate import Poly, corroborated, refuted, verify_egyptian_identity


def _store(tmp_path) -> ExperienceStore:
    return ExperienceStore(path=tmp_path / "exp.jsonl")


class TestTierWall:
    def test_proven_vs_corroborated_wall(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("prove 4/n even class", "identity certificate", tier=TIER_PROVEN)
        s.record("search es residual", "no counterexample below 20000",
                 tier=TIER_CORROBORATED, bound=20000)
        proven = {r.task for r in s.proven_skills()}
        assert "prove 4/n even class" in proven
        # 反例核心: 界内佐证绝不进 proven_skills (那道墙)
        assert "search es residual" not in proven

    def test_corroborated_recalled_but_labeled_not_proven(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("es residual search", "no counterexample below 20000",
                 tier=TIER_CORROBORATED, bound=20000)
        ctx = s.build_recall_context("es residual search bound")
        assert "CORROBORATED" in ctx and "20000" in ctx
        # 反例: 佐证行绝不被渲染成 PROVEN
        assert "[PROVEN]" not in ctx

    def test_proven_labeled_proven(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("prove even egyptian identity", "difference polynomial is zero",
                 tier=TIER_PROVEN)
        ctx = s.build_recall_context("prove even egyptian identity")
        assert "[PROVEN]" in ctx

    def test_refuted_never_recalled(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("bogus primes claim", "counterexample residual value", tier=TIER_REFUTED)
        # 反例: 被证伪的经验绝不作为技能召回/注入
        assert s.recall("bogus primes claim residual") == []
        assert s.build_recall_context("bogus primes claim residual") == ""
        assert all(r.task != "bogus primes claim" for r in s.proven_skills())


class TestCertificateBridge:
    def test_bridge_maps_proven(self, tmp_path) -> None:
        s = _store(tmp_path)
        z = Poly([1, 1]) * Poly([2, 1])
        cert = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        s.record_certificate(cert, task="4/n even class solvable")
        assert any(r.task == "4/n even class solvable" for r in s.proven_skills())

    def test_bridge_maps_corroborated_not_proven(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record_certificate(corroborated("no counterexample residual", bound=50000),
                             task="es residual family")
        # 反例: 佐证证书桥接后不得进 proven_skills
        assert not any(r.task == "es residual family" for r in s.proven_skills())
        hits = s.recall("es residual family", verified_only=True)
        assert any(r.task == "es residual family" for r in hits)  # 仍可召回

    def test_bridge_maps_refuted(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record_certificate(refuted("all n satisfy property", counterexample="value"),
                             task="false universal claim")
        # 反例: refuted 证书桥接后绝不召回
        assert s.recall("false universal claim") == []


class TestBackwardCompat:
    def test_verified_bool_still_works(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("parse json config", "use json.loads carefully", verified=True)
        s.record("parse yaml config", "broken approach", verified=False)
        hits = s.recall("parse json config file")
        assert any("json.loads" in r.outcome for r in hits)
        # 反例: verified=False 记录不作为技能召回
        assert all(r.verified for r in hits)
        assert not any("broken approach" in r.outcome for r in hits)
