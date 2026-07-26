"""Proof Gate — covering-system certifier (third decidable-fragment prover).

A covering system is a finite set of congruences {a_i (mod m_i)} such that every
integer satisfies at least one. "Every integer is covered" is a UNIVERSAL claim
that is *decidable*: it holds iff every residue mod L = lcm(m_i) is covered. So
it belongs to the Proof Gate: PROVEN if all residues are covered, REFUTED with
an explicit uncovered residue otherwise (grounded counterexample).

Covering systems are the classical tool behind Romanov-/Erdős-type results
(e.g. a positive density of integers not of the form 2^k + prime), so this
certifier opens a third demonstration area beyond Egyptian fractions and
Diophantine tuples.

IPR-0: each invariant has a counterexample — a non-covering set MUST be REFUTED
with the smallest uncovered residue.
"""

from __future__ import annotations

import pytest

from zall.core.proof_gate import VerificationTier, verify_covering_system


# The famous Erdős (1950) covering system, moduli {2,3,4,8,12,24}.
ERDOS_1950 = [(0, 2), (0, 3), (1, 4), (3, 8), (7, 12), (23, 24)]


class TestCoveringProven:
    def test_erdos_1950_is_a_covering(self) -> None:
        c = verify_covering_system(ERDOS_1950)
        assert c.tier is VerificationTier.PROVEN
        assert c.data["lcm"] == 24

    def test_trivial_mod1_covers(self) -> None:
        assert verify_covering_system([(0, 1)]).tier is VerificationTier.PROVEN

    def test_evens_and_odds_cover(self) -> None:
        assert verify_covering_system([(0, 2), (1, 2)]).tier is VerificationTier.PROVEN


class TestCoveringRefuted:
    def test_only_evens_refuted_with_witness(self) -> None:
        # 反例: {0 mod 2} misses every odd number → REFUTED, smallest witness 1.
        c = verify_covering_system([(0, 2)])
        assert c.tier is VerificationTier.REFUTED
        assert c.data["uncovered_residue"] == 1

    def test_gap_refuted(self) -> None:
        # 反例: {0 mod 2, 0 mod 4} covers evens only → odd residue 1 uncovered.
        c = verify_covering_system([(0, 2), (0, 4)])
        assert c.tier is VerificationTier.REFUTED
        assert c.data["uncovered_residue"] % 2 == 1

    def test_mod3_gap_refuted(self) -> None:
        # 反例: {0 mod 3, 1 mod 3} misses residue 2 (mod 3).
        c = verify_covering_system([(0, 3), (1, 3)])
        assert c.tier is VerificationTier.REFUTED
        assert c.data["uncovered_residue"] == 2

    def test_empty_refuted(self) -> None:
        c = verify_covering_system([])
        assert c.tier is VerificationTier.REFUTED


class TestCoveringValidationAndDeterminism:
    def test_bad_modulus_raises(self) -> None:
        with pytest.raises(ValueError):
            verify_covering_system([(0, 0)])          # modulus must be >= 1
        with pytest.raises(ValueError):
            verify_covering_system([(0, -3)])

    def test_deterministic(self) -> None:
        a = verify_covering_system(ERDOS_1950)
        b = verify_covering_system(ERDOS_1950)
        assert a.tier == b.tier and a.detail == b.detail

    def test_residue_normalized(self) -> None:
        # a is taken mod m: (25, 24) == (1, 24); still not a covering alone.
        c = verify_covering_system([(25, 24)])
        assert c.tier is VerificationTier.REFUTED
