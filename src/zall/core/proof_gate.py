"""zall.core.proof_gate — Proof Tier: 波普尔闸门的形式化精化 (PARADIGM.md §5.1)。

反驳与确认在逻辑上不对称:
  - 全称命题 ∀n P(n) 的**反驳**只需一个反例 → sandbox_verifier 真实执行即可。
  - 其**确认**永远无法由有限次执行得到 (休谟/波普尔归纳问题): 沙盒只能在界内
    **佐证 (corroborate)**, 不能**证明 (prove)**。

本模块把认识论状态升为一等公民 `VerificationTier`, 并提供一个 **Proof Gate**:
只有对全称命题持有**机器可核验证书**时才授予 PROVEN。对**可判定片段** (ℚ 上有理
函数恒等式), 证书用纯精确整数算术核验 —— 把 1/X+1/Y+1/Z = 4/(qk+r) 交叉相乘为
多项式恒等式, 检验差多项式**逐系数为零** (零多项式 ⟺ 恒等式成立), 再检验分母对
所有 k≥0 为正整数。这是一台无需 Lean/Coq 的、离线确定性的全称命题证明器。

Lean 的定位 (诚实): 可判定片段不需要 Lean; Lean 仅在命题离开可判定片段
(需归纳/引理/情形分析) 时才有价值, 且只应作 opt-in 的 `Prover` 适配器 —— 沿用
本模块的 `Prover` 协议, 与 `TrustAnchor`/`red_fn` 注入模式一致。

IPR constraints:
  IPR-0: invariant tests at tests/test_proof_gate_invariants.py, includes counterexample
  IPR-1: this file corresponds to docs/PARADIGM.md §5.1
  IPR-3: stdlib only (dataclasses/enum/typing), no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# ──────────────────────────────────────────────────────────────────────────
# §5.1 VerificationTier — 认识论状态 (一等公民)
# ──────────────────────────────────────────────────────────────────────────


class VerificationTier(str, Enum):
    """一个命题相对证据的认识论状态 (PARADIGM.md §5.1)。

    严格偏序 (强 → 弱): REFUTED / PROVEN 是终局确定; CORROBORATED 是有界佐证
    (**未证明**); UNKNOWN 是未定。关键红线: CORROBORATED 绝不等价于 PROVEN
    —— 这道墙防止自改进循环把"界内佐证"当"证明"蒸馏成技能 (反 reward-hack)。
    """

    REFUTED = "refuted"            # 已给出反例 (可判定, 沙盒执行)
    CORROBORATED = "corroborated"  # 界内佐证, 未证明 (须携带界 N)
    PROVEN = "proven"              # 对全称命题持有机器可核验证书
    UNKNOWN = "unknown"            # 以上皆非


# ──────────────────────────────────────────────────────────────────────────
# 精确整系数多项式 (可判定片段的核验基元)
# ──────────────────────────────────────────────────────────────────────────


class Poly:
    """k 的整系数多项式 (index = 次数), 精确算术, 不可变。

    规范形: 去掉高次的零系数; 零多项式的 coeffs 为空元组 ()。
    两个多项式相等 ⟺ 规范形系数元组相等 ⟺ 作为函数在所有 k 上相等
    (系数比较是恒等式判定, 无需采样)。
    """

    __slots__ = ("coeffs",)

    def __init__(self, coeffs: Iterable[int]) -> None:
        c = [int(x) for x in coeffs]
        while c and c[-1] == 0:
            c.pop()
        object.__setattr__(self, "coeffs", tuple(c))

    # ── 相等 / 哈希 / 展示 ──
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Poly) and self.coeffs == other.coeffs

    def __hash__(self) -> int:
        return hash(self.coeffs)

    def __repr__(self) -> str:
        return f"Poly({list(self.coeffs)})"

    # ── 查询 ──
    def is_zero(self) -> bool:
        """零多项式判定 (规范形为空)。这是恒等式核验的核心。"""
        return len(self.coeffs) == 0

    def degree(self) -> int:
        return len(self.coeffs) - 1  # 零多项式返回 -1

    def constant(self) -> int:
        return self.coeffs[0] if self.coeffs else 0

    def nonneg(self) -> bool:
        """所有系数非负 (正性充分条件之一)。"""
        return all(c >= 0 for c in self.coeffs)

    def at(self, k: int) -> int:
        """Horner 法精确求值 (整数进整数出)。命名为 at 而非 eval，避免与内置 eval 混淆。"""
        acc = 0
        for c in reversed(self.coeffs):
            acc = acc * k + c
        return acc

    # ── 运算 (精确, 返回新实例) ──
    def __add__(self, other: Poly) -> Poly:
        a, b = self.coeffs, other.coeffs
        n = max(len(a), len(b))
        return Poly([
            (a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0)
            for i in range(n)
        ])

    def __sub__(self, other: Poly) -> Poly:
        a, b = self.coeffs, other.coeffs
        n = max(len(a), len(b))
        return Poly([
            (a[i] if i < len(a) else 0) - (b[i] if i < len(b) else 0)
            for i in range(n)
        ])

    def __mul__(self, other: Poly) -> Poly:
        a, b = self.coeffs, other.coeffs
        if not a or not b:
            return Poly([])  # 零多项式
        out = [0] * (len(a) + len(b) - 1)
        for i, ai in enumerate(a):
            if ai == 0:
                continue
            for j, bj in enumerate(b):
                out[i + j] += ai * bj
        return Poly(out)


def _as_poly(p: Poly | Iterable[int]) -> Poly:
    return p if isinstance(p, Poly) else Poly(p)


def is_positive_for_nonneg_k(p: Poly) -> bool:
    """充分判据: 非负系数且常数项≥1 ⇒ 对所有整数 k≥0, p(k) 是 ≥1 的正整数。

    证明: 每项 a_i·k^i ≥ 0 (k≥0, a_i≥0), 且 a_0 ≥ 1 ⇒ p(k) ≥ 1。整系数整 k ⇒ 整数。
    这是**充分非必要**条件 (保守): 拒绝含负系数者, 宁可判 UNKNOWN 也不误判 PROVEN。
    反例 (须拒绝): 常数项 0 (k=0 除零)、负常数项、任意负系数。
    """
    return p.nonneg() and p.constant() >= 1


# ──────────────────────────────────────────────────────────────────────────
# §5.1 ProofCertificate — 机器可核验证书
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProofCertificate:
    """一条命题的认识论裁定 + 可复核依据 (PARADIGM.md §5.1)。

    `tier` 是裁定; `detail` 是人类可读理由; `data` 是可机器复核的原始依据
    (如多项式系数、差多项式); `bound` 仅 CORROBORATED 时有意义 (佐证的界)。
    """

    kind: str
    claim: str
    tier: VerificationTier
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    bound: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "claim": self.claim,
            "tier": self.tier.value,
            "detail": self.detail,
            "data": self.data,
            "bound": self.bound,
        }


# ── 非 PROVEN tier 的构造器 (供沙盒/搜索/反例侧使用) ──


def corroborated(claim: str, bound: int, detail: str = "") -> ProofCertificate:
    """界内佐证: 有界搜索未见反例。**不是证明** —— tier=CORROBORATED, 携带界。"""
    return ProofCertificate(
        kind="empirical_search",
        claim=claim,
        tier=VerificationTier.CORROBORATED,
        detail=detail or f"no counterexample within bound {bound}; NOT a proof (Hume/Popper).",
        bound=bound,
    )


def refuted(claim: str, counterexample: str, detail: str = "") -> ProofCertificate:
    """已给出反例 → 全称命题被证伪。"""
    return ProofCertificate(
        kind="counterexample",
        claim=claim,
        tier=VerificationTier.REFUTED,
        detail=detail or f"counterexample exhibited: {counterexample}",
        data={"counterexample": counterexample},
    )


def unknown(claim: str, detail: str = "") -> ProofCertificate:
    return ProofCertificate(
        kind="undetermined", claim=claim, tier=VerificationTier.UNKNOWN, detail=detail,
    )


# ──────────────────────────────────────────────────────────────────────────
# §5.1 Proof Gate — 可判定片段的全称命题证明器 (无需 Lean)
# ──────────────────────────────────────────────────────────────────────────


def verify_egyptian_identity(
    q: int,
    r: int,
    x: Poly | Iterable[int],
    y: Poly | Iterable[int],
    z: Poly | Iterable[int],
    *,
    claim: str = "",
) -> ProofCertificate:
    """证明 4/(q·k + r) = 1/X(k) + 1/Y(k) + 1/Z(k) 对所有整数 k≥0 成立。

    这是对一整条算术级数 {n = q·k + r : k≥0} 的**全称证明** (无穷多 n 一次证完),
    不是逐实例搜索。方法 (可判定片段, 纯精确算术):
      1. 交叉相乘: 恒等式 ⟺ (Y·Z + X·Z + X·Y)·N − 4·X·Y·Z ≡ 0, 其中 N = q·k+r。
      2. 差多项式逐系数为零 ⟺ 两个有理函数相等 (无需采样, 系数比较即证明)。
      3. 正性: X,Y,Z,N 对所有 k≥0 是正整数 (is_positive_for_nonneg_k 充分判据),
         且 N 常数项 ≥ 2 (保证 n≥2)。

    裁定:
      PROVEN     恒等式成立且正性成立 → 整条级数被证明。
      REFUTED    恒等式不成立 (差多项式非零) → 所声称的证书是**假的** (checker 自证伪)。
      UNKNOWN    恒等式成立但正性/整性无法保证 → 不足以判 PROVEN (谦卑, 不误判)。

    Raises:
      ValueError: q < 1 (非法算术级数步长)。
    """
    if q < 1:
        raise ValueError(f"q (modulus step) must be >= 1, got {q}")
    X, Y, Z = _as_poly(x), _as_poly(y), _as_poly(z)
    N = Poly([r, q])  # r + q·k

    # 1+2: 差多项式 (Y·Z + X·Z + X·Y)·N − 4·X·Y·Z
    diff = (Y * Z + X * Z + X * Y) * N - Poly([4]) * X * Y * Z
    identity_ok = diff.is_zero()

    auto_claim = (
        claim
        or f"forall k>=0: 4/({q}k+{r}) = 1/{list(X.coeffs)} + 1/{list(Y.coeffs)} "
           f"+ 1/{list(Z.coeffs)}  (Egyptian-fraction identity)"
    )
    data: dict[str, Any] = {
        "q": q, "r": r,
        "x": list(X.coeffs), "y": list(Y.coeffs), "z": list(Z.coeffs),
        "difference_poly": list(diff.coeffs),
        "identity_holds": identity_ok,
    }

    if not identity_ok:
        return ProofCertificate(
            kind="rational_identity",
            claim=auto_claim,
            tier=VerificationTier.REFUTED,
            detail=(
                "identity is FALSE: cross-multiplied difference numerator "
                f"{list(diff.coeffs)} is not the zero polynomial."
            ),
            data=data,
        )

    positivity_ok = (
        is_positive_for_nonneg_k(X)
        and is_positive_for_nonneg_k(Y)
        and is_positive_for_nonneg_k(Z)
        and N.nonneg() and N.constant() >= 2
    )
    data["positivity_holds"] = positivity_ok
    if not positivity_ok:
        return ProofCertificate(
            kind="rational_identity",
            claim=auto_claim,
            tier=VerificationTier.UNKNOWN,
            detail=(
                "identity holds formally, but denominators are not proven to be "
                "positive integers for all k>=0 (need nonneg coeffs, constant>=1; "
                "N constant>=2). Cannot grant PROVEN."
            ),
            data=data,
        )

    return ProofCertificate(
        kind="rational_identity",
        claim=auto_claim,
        tier=VerificationTier.PROVEN,
        detail=(
            "PROVEN for all k>=0: difference polynomial is identically zero "
            "(exact coefficient check) and X,Y,Z,N are positive integers for all "
            "k>=0. This proves 4/n = 1/x+1/y+1/z for the entire arithmetic "
            f"progression n = {q}k+{r}."
        ),
        data=data,
    )


# ──────────────────────────────────────────────────────────────────────────
# §5.1 Perfect-square polynomial certifier (Diophantine-tuple tier)
# ──────────────────────────────────────────────────────────────────────────


def _isqrt_exact(n: int) -> int | None:
    """Exact integer square root: return r with r*r==n (r>=0), else None."""
    if n < 0:
        return None
    r = math.isqrt(n)
    return r if r * r == n else None


def poly_sqrt(p: Poly) -> Poly | None:
    """Exact integer polynomial square root: return Q (leading coeff > 0) with
    Q*Q == p, or None if p is not the square of an integer polynomial.

    Pure integer arithmetic (no floats): determine Q top-down from p's leading
    half, then verify Q*Q == p exactly. If p is a perfect-square polynomial then
    p(t) is a perfect square for every integer t (Q(t) is an integer) — the
    certificate a Diophantine m-tuple family needs.
    """
    if p.is_zero():
        return Poly([])  # 0 = 0^2
    coeffs = list(p.coeffs)
    deg = len(coeffs) - 1
    if deg % 2 != 0:
        return None  # odd degree can't be a square
    s = _isqrt_exact(coeffs[-1])
    if s is None:
        return None  # leading coeff not a perfect square
    m = deg // 2
    q = [0] * (m + 1)
    q[m] = s
    # Solve for q[m-k] from the coefficient of x^(deg-k), k = 1..m.
    for k in range(1, m + 1):
        known = 0
        for i in range(m - k + 1, m):      # both indices already determined
            known += q[i] * q[deg - k - i]
        num = coeffs[deg - k] - known
        den = 2 * q[m]
        if den == 0 or num % den != 0:
            return None  # no integer square root
        q[m - k] = num // den
    Q = Poly(q)
    return Q if Q * Q == p else None  # verify lower half too


def verify_square_identity(
    p: Poly | Iterable[int], *, search_bound: int = 64, claim: str = "",
) -> ProofCertificate:
    """Certify (or refute) that P(t) is a perfect square for all integer t>=0.

    PROVEN     P = Q^2 for an integer polynomial Q (poly_sqrt succeeds).
    REFUTED    an explicit small t with P(t) not a perfect square is exhibited
               (grounded counterexample — not an appeal to a theorem).
    UNKNOWN    not a square polynomial, yet no small counterexample found.
    """
    P = _as_poly(p)
    auto = claim or f"forall integer t>=0: P(t) is a perfect square, P={list(P.coeffs)}"
    Q = poly_sqrt(P)
    if Q is not None:
        return ProofCertificate(
            kind="square_identity",
            claim=auto,
            tier=VerificationTier.PROVEN,
            detail=(f"P = Q^2 with Q={list(Q.coeffs)} (exact); hence P(t) is a "
                    f"perfect square for every integer t."),
            data={"p": list(P.coeffs), "q": list(Q.coeffs)},
        )
    for t in range(max(1, search_bound)):
        v = P.at(t)
        if _isqrt_exact(v) is None:
            return refuted(
                auto,
                counterexample=f"t={t}, P(t)={v}",
                detail=(f"P is not a perfect-square polynomial; P({t})={v} is not "
                        f"a perfect square."),
            )
    return unknown(
        auto,
        detail=("P is not a perfect-square polynomial, but no counterexample found "
                f"within t<{search_bound}; cannot certify (not PROVEN)."),
    )


# ──────────────────────────────────────────────────────────────────────────
# §5.1 Covering-system certifier (third decidable-fragment prover)
# ──────────────────────────────────────────────────────────────────────────


def verify_covering_system(
    congruences: list[tuple[int, int]], *, claim: str = "",
) -> ProofCertificate:
    """Certify whether {a_i (mod m_i)} is a covering system (every integer is
    covered by at least one congruence).

    "Every integer is covered" is a decidable universal claim: it holds iff every
    residue mod L = lcm(m_i) is covered. Hence:
      PROVEN   every residue mod L is covered.
      REFUTED  an explicit residue r (mod L) covered by no congruence is exhibited
               (grounded counterexample: the integer r is uncovered).

    Covering systems underpin Romanov-/Erdős-type non-representability results,
    so this is a third demonstration area for the Proof Gate.

    Raises:
      ValueError: any modulus < 1.
    """
    norm: list[tuple[int, int]] = []
    for a, m in congruences:
        if m < 1:
            raise ValueError(f"modulus must be >= 1, got {m}")
        norm.append((a % m, m))
    lcm = 1
    for _, m in norm:
        lcm = math.lcm(lcm, m)
    auto = claim or f"forall integer n: n is covered by one of {norm}"
    for r in range(lcm):
        if not any(r % m == a for (a, m) in norm):
            return ProofCertificate(
                kind="covering_system",
                claim=auto,
                tier=VerificationTier.REFUTED,
                detail=(f"residue {r} (mod {lcm}) is covered by no congruence; "
                        f"the integer {r} is uncovered, so this is not a covering."),
                data={"congruences": norm, "lcm": lcm, "uncovered_residue": r},
            )
    return ProofCertificate(
        kind="covering_system",
        claim=auto,
        tier=VerificationTier.PROVEN,
        detail=(f"every residue mod {lcm} is covered; hence every integer satisfies "
                f"at least one congruence (a covering system)."),
        data={"congruences": norm, "lcm": lcm},
    )


# ──────────────────────────────────────────────────────────────────────────
# §5.1 Prover 协议 — opt-in 证明器扩展点 (Lean/Z3 未来接此)
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Prover(Protocol):
    """外部证明器的最小注入协议 (与 TrustAnchor/red_fn 同构)。

    纯 Python 的可判定片段证明器 (verify_egyptian_identity 的封装) 满足它;
    未来的 Lean/Z3 适配器也接此协议 —— 核心永不硬依赖外部工具链 (守 IPR-3),
    仅在命题离开可判定片段时按需 opt-in。
    """

    @property
    def name(self) -> str: ...

    def prove(self, claim: str) -> ProofCertificate: ...
