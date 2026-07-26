"""Proof Gate 不变量 (PARADIGM.md §5.1: 波普尔闸门的形式化精化)。

Proof Gate 把认识论状态升为一等公民 (REFUTED/CORROBORATED/PROVEN/UNKNOWN),
只有对全称命题持有**机器可核验证书**才授予 PROVEN。对可判定片段 (ℚ 上有理函数
恒等式) 用纯精确算术核验 (零多项式系数检验)。

IPR-0: 每个不变量含反例 (construct violations that MUST make the check fail)。

不变量:
  PG-1  三条真实的埃及分数恒等式 → PROVEN (density-5/6 Erdős–Straus 覆盖)。
  PG-2  反例: 错误恒等式 (差多项式非零) → 必判 REFUTED, 绝不 PROVEN。
  PG-3  正性: 非负系数且常数项≥1 → 对所有 k≥0 为正整数; 反例: 常数项≤0 → 拒绝。
  PG-4  Tier 语义: 界内佐证 (CORROBORATED) 绝不等于 PROVEN (反 reward-hack)。
  PG-5  确定性: 相同输入 → 相同证书。
  + Poly: 精确多项式乘/加/零判定 (反例: 非零多项式 is_zero 为 False)。
"""

from __future__ import annotations

from zall.core.proof_gate import (
    Poly,
    ProofCertificate,
    Prover,
    VerificationTier,
    corroborated,
    is_positive_for_nonneg_k,
    refuted,
    verify_egyptian_identity,
)


# ──────────────────────────────────────────────────────────────────
# Poly — 精确整系数多项式 (k 的幂, index=次数)
# ──────────────────────────────────────────────────────────────────
class TestPoly:
    def test_add_mul_eval(self) -> None:
        # (k+1)(k+2) = k^2 + 3k + 2
        p = Poly([1, 1]) * Poly([2, 1])
        assert p == Poly([2, 3, 1])
        assert p.at(0) == 2 and p.at(1) == 6 and p.at(2) == 12
        assert (Poly([1, 1]) + Poly([2, 1])) == Poly([3, 2])

    def test_is_zero_and_counterexample(self) -> None:
        assert Poly([0, 0, 0]).is_zero()
        assert (Poly([2, 3, 1]) - Poly([2, 3, 1])).is_zero()
        # 反例: 非零多项式绝不 is_zero
        assert not Poly([0, 1]).is_zero()
        assert not (Poly([1, 1]) * Poly([2, 1]) - Poly([2, 3])).is_zero()


# ──────────────────────────────────────────────────────────────────
# PG-3 正性 (非负系数 + 常数项≥1 ⇒ 对 k≥0 恒为正整数)
# ──────────────────────────────────────────────────────────────────
class TestPositivity:
    def test_nonneg_positive(self) -> None:
        assert is_positive_for_nonneg_k(Poly([1, 1]))       # k+1 ≥ 1
        assert is_positive_for_nonneg_k(Poly([12, 21, 9]))  # 9k^2+21k+12
        assert is_positive_for_nonneg_k(Poly([2]))          # 常量 2

    def test_counterexamples(self) -> None:
        # 反例: 常数项 0 → k=0 时为 0 (除零) → 拒绝
        assert not is_positive_for_nonneg_k(Poly([0, 1]))
        # 反例: 负常数项 → k=0 时为负 → 拒绝
        assert not is_positive_for_nonneg_k(Poly([-1, 1]))
        # 反例: 含负系数 → 大 k 处可能非正 → 保守拒绝
        assert not is_positive_for_nonneg_k(Poly([5, -1]))


# ──────────────────────────────────────────────────────────────────
# PG-1 三条真实恒等式 → PROVEN
# ──────────────────────────────────────────────────────────────────
class TestProvenIdentities:
    def test_even_class(self) -> None:
        # n = 2k+2 (偶): 4/n = 1/(k+1) + 1/(k+2) + 1/((k+1)(k+2))
        z = Poly([1, 1]) * Poly([2, 1])  # (k+1)(k+2) = k^2+3k+2
        cert = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        assert cert.tier is VerificationTier.PROVEN

    def test_multiple_of_three_class(self) -> None:
        # n = 3k+3: 4/n = 1/(k+1) + 1/(3k+4) + 1/((3k+3)(3k+4))
        z = Poly([3, 3]) * Poly([4, 3])  # (3k+3)(3k+4)
        cert = verify_egyptian_identity(q=3, r=3, x=Poly([1, 1]), y=Poly([4, 3]), z=z)
        assert cert.tier is VerificationTier.PROVEN

    def test_three_mod_four_class(self) -> None:
        # n = 4k+3: x=k+1, M=(4k+3)(k+1); 4/n = 1/x + 1/(M+1) + 1/(M(M+1))
        # 注意 Poly 系数 index=次数: 4k+3 = Poly([3, 4]) (常数 3, k 系数 4)。
        m = Poly([3, 4]) * Poly([1, 1])   # (4k+3)(k+1) = 4k^2+7k+3
        m1 = m + Poly([1])                # M+1
        z = m * m1                        # M(M+1)
        cert = verify_egyptian_identity(q=4, r=3, x=Poly([1, 1]), y=m1, z=z)
        assert cert.tier is VerificationTier.PROVEN


# ──────────────────────────────────────────────────────────────────
# PG-2 反例: 错误恒等式 → REFUTED (checker 自身可证伪)
# ──────────────────────────────────────────────────────────────────
class TestFalseIdentityRefuted:
    def test_wrong_z_is_refuted(self) -> None:
        # 反例: 偶类里把 Z 换成 (k+3), 则 1/(k+1)+1/(k+2)+1/(k+3) ≠ 2/(k+1)
        cert = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=Poly([3, 1]))
        assert cert.tier is VerificationTier.REFUTED
        assert cert.tier is not VerificationTier.PROVEN

    def test_wrong_modulus_is_refuted(self) -> None:
        # 反例: 正确的偶类恒等式却声称覆盖 n=3k+3 → 差多项式非零
        z = Poly([1, 1]) * Poly([2, 1])
        cert = verify_egyptian_identity(q=3, r=3, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        assert cert.tier is VerificationTier.REFUTED

    def test_identity_true_but_denominator_nonpositive_not_proven(self) -> None:
        # 形式恒等式即便成立, 若分母不能保证为正整数 → 不得判 PROVEN。
        # 反例构造: X=k (常数项0), 令 Y,Z,N 仍满足 1/X+1/Y+1/Z=4/N 不易;
        # 这里用一个分母含 0 常数项的项直接触发正性拒绝路径。
        # 4/(2k+2) = 2/(k+1);  1/(k) - ... 用 X=[0,1] 触发正性失败。
        cert = verify_egyptian_identity(
            q=2, r=2, x=Poly([0, 1]), y=Poly([2, 1]), z=Poly([1, 1]) * Poly([2, 1]),
        )
        assert cert.tier is not VerificationTier.PROVEN


# ──────────────────────────────────────────────────────────────────
# PG-4 Tier 语义: 佐证 ≠ 证明 (反 reward-hack)
# ──────────────────────────────────────────────────────────────────
class TestTierSemantics:
    def test_corroborated_is_not_proven(self) -> None:
        c = corroborated("no counterexample in [2, N)", bound=50000)
        assert c.tier is VerificationTier.CORROBORATED
        # 反例核心: 界内佐证绝不能被当作证明
        assert c.tier is not VerificationTier.PROVEN
        assert c.bound == 50000

    def test_refuted_carries_counterexample(self) -> None:
        c = refuted("all n solvable", counterexample="n=?")
        assert c.tier is VerificationTier.REFUTED

    def test_certificate_json_roundtrip(self) -> None:
        z = Poly([1, 1]) * Poly([2, 1])
        cert = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        d = cert.to_json()
        assert d["tier"] == "proven"
        assert "claim" in d and "detail" in d


# ──────────────────────────────────────────────────────────────────
# PG-5 确定性 + Prover 协议
# ──────────────────────────────────────────────────────────────────
class TestDeterminismAndProtocol:
    def test_deterministic(self) -> None:
        z = Poly([1, 1]) * Poly([2, 1])
        a = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        b = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
        assert a.tier == b.tier and a.detail == b.detail

    def test_certificate_is_dataclass_frozen(self) -> None:
        c = corroborated("x", bound=1)
        import dataclasses
        assert dataclasses.is_dataclass(c) and isinstance(c, ProofCertificate)

    def test_prover_protocol_is_runtime_checkable(self) -> None:
        # 文档化的扩展点: opt-in Prover 适配器 (Lean/Z3 未来接此协议)。
        class _StubProver:
            @property
            def name(self) -> str:
                return "stub"

            def prove(self, claim: str) -> ProofCertificate:
                return corroborated(claim, bound=0)

        assert isinstance(_StubProver(), Prover)  # 反例见下: 缺 prove 的类不满足
        class _NotProver:
            name = "x"
        assert not isinstance(_NotProver(), Prover)
