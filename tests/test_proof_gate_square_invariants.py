"""Proof Gate — exact perfect-square polynomial certifier (Diophantine-tuple tier).

Extends PARADIGM.md §5.1 to a second decidable-fragment prover: certify that a
polynomial P(t) is a perfect square *for all integer t* by exhibiting an integer
polynomial Q with P = Q^2 (exact). This is the certificate a Diophantine m-tuple
family needs — "a·b + n is a perfect square for all t" ⟺ "a·b + n = Q^2".

IPR-0: each invariant has a counterexample. Crucially, a polynomial that is NOT a
perfect square must be REFUTED with an explicit t (grounded, not by appeal to a
theorem) — the certifier falsifies false universal square-claims.
"""

from __future__ import annotations

from zall.core.proof_gate import (
    Poly,
    VerificationTier,
    poly_sqrt,
    verify_square_identity,
)


class TestPolySqrt:
    def test_perfect_squares(self) -> None:
        assert poly_sqrt(Poly([1, 2, 1])) == Poly([1, 1])       # (t+1)^2
        assert poly_sqrt(Poly([9, 12, 4])) == Poly([3, 2])      # (2t+3)^2
        assert poly_sqrt(Poly([4])) == Poly([2])                # constant 4
        assert poly_sqrt(Poly([1, 0, 2, 0, 1])) == Poly([1, 0, 1])  # (t^2+1)^2

    def test_non_squares_return_none(self) -> None:
        assert poly_sqrt(Poly([1, 1, 1])) is None   # t^2+t+1 (not a square poly)
        assert poly_sqrt(Poly([1, 1])) is None        # odd degree t+1
        assert poly_sqrt(Poly([1, 0, 2])) is None     # 2t^2+1, leading 2 not a square
        assert poly_sqrt(Poly([3])) is None           # constant 3

    def test_zero(self) -> None:
        assert poly_sqrt(Poly([])).is_zero()          # 0 = 0^2


class TestVerifySquareIdentity:
    def test_proven(self) -> None:
        c = verify_square_identity(Poly([1, 2, 1]))
        assert c.tier is VerificationTier.PROVEN

    def test_diophantine_pair_identity_proven(self) -> None:
        # The pair {t, t+2} is a Diophantine pair: t*(t+2)+1 = (t+1)^2.
        p = Poly([0, 1]) * Poly([2, 1]) + Poly([1])   # t*(t+2)+1 = [1,2,1]
        c = verify_square_identity(p)
        assert c.tier is VerificationTier.PROVEN

    def test_refuted_with_explicit_counterexample(self) -> None:
        # 反例: t^2+t+1 is not always a perfect square (t=1 -> 3). MUST be REFUTED.
        c = verify_square_identity(Poly([1, 1, 1]))
        assert c.tier is VerificationTier.REFUTED
        assert c.tier is not VerificationTier.PROVEN

    def test_constant_nonsquare_refuted(self) -> None:
        c = verify_square_identity(Poly([3]))
        assert c.tier is VerificationTier.REFUTED
