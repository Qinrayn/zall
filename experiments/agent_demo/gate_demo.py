"""Agent learning gate demo: the epistemic wall prevents reward-hacking.

A CONCRETE demonstration that the VerificationTier gate matters in a REAL
agent scenario (code generation + learning), not just pure mathematics.

Scenario:
  1. Blue proposes a naive is_prime(n) implementation.
  2. SandboxVerifier runs it against bounded test cases [2..20] → PASSES → CORROBORATED.
  3. But the function FAILS on edge cases (n=1, n=0, n=-1) — the bounded test
     did NOT prove universality.
  4. WITHOUT the epistemic gate: the function enters the skill pool as if "proven"
     → misleads future reuse → degradation (reward-hacking surface).
  5. WITH the epistemic gate (VerificationTier):
     a) ExperienceStore records it as CORROBORATED(bound=20), NOT PROVEN.
     b) proven_skills() EXCLUDES it (the wall).
     c) build_recall_context labels it [CORROBORATED n tested in [2,20]] — the
        model sees the bound and knows NOT to blindly trust it on untested inputs.

  Contrast: a mathematical identity (from proof_gate) genuinely earns PROVEN tier,
  enters proven_skills(), and is labeled [PROVEN] — correctly trustable universally.

This demonstrates:
  - The gate works on a REAL agent task (code generation), not just math.
  - The CORROBORATED/PROVEN distinction prevents a concrete failure case.
  - The mechanism is structural and machine-enforced (not a human caveat).

Usage:
    python gate_demo.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HERE = Path(__file__).resolve().parent

from zall.core.experience_store import (
    TIER_CORROBORATED,
    TIER_PROVEN,
    ExperienceStore,
)
from zall.core.proof_gate import Poly, verify_egyptian_identity, corroborated
from zall.core.sandbox_verifier import SandboxVerifier


# ═══════════════════════════════════════════════════════════════════
# §1  The naive solution (passes bounded tests, fails edge cases)
# ═══════════════════════════════════════════════════════════════════

NAIVE_IS_PRIME = """\
def is_prime(n):
    \"\"\"Check if n is prime (naive implementation).\"\"\"
    return all(n % i != 0 for i in range(2, n))
"""

# Bounded test: checks n in [2, 20] — passes for all.
BOUNDED_TEST = """\
from solution import is_prime
# Positive cases (known primes)
for p in [2, 3, 5, 7, 11, 13, 17, 19]:
    assert is_prime(p), f"{p} should be prime"
# Negative cases (known composites)
for c in [4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20]:
    assert not is_prime(c), f"{c} should not be prime"
print("all tests passed")
"""

# Edge-case test: the naive solution FAILS here (n=1 returns True because range(2,1) is empty).
EDGE_TEST = """\
from solution import is_prime
assert not is_prime(1), "1 is NOT prime"
assert not is_prime(0), "0 is NOT prime"
assert not is_prime(-5), "-5 is NOT prime"
print("edge cases passed")
"""


# ═══════════════════════════════════════════════════════════════════
# §2  Run the demonstration
# ═══════════════════════════════════════════════════════════════════

def run(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = ExperienceStore(path=out_dir / "demo_experience.jsonl")
    verifier = SandboxVerifier(timeout=10.0)

    # ── Step 1: Bounded test PASSES (the function is "good enough" within bounds) ──
    bounded_result = verifier.verify(NAIVE_IS_PRIME, check=BOUNDED_TEST)
    bounded_pass = bounded_result.score == 1.0

    # ── Step 2: Edge-case test FAILS (the function is NOT universally correct) ──
    edge_result = verifier.verify(NAIVE_IS_PRIME, check=EDGE_TEST)
    edge_pass = edge_result.score == 1.0

    # ── Step 3: Record with the epistemic gate (tier-aware) ──
    # The bounded test passed → CORROBORATED (not PROVEN — bounded, not universal).
    store.record(
        task="is_prime implementation",
        outcome="naive implementation: all(n%i!=0 for i in range(2,n))",
        tier=TIER_CORROBORATED,
        bound=20,
        source="sandbox_bounded_test",
    )

    # Contrast: a genuinely PROVEN mathematical result (from proof_gate).
    z = Poly([1, 1]) * Poly([2, 1])
    cert = verify_egyptian_identity(q=2, r=2, x=Poly([1, 1]), y=Poly([2, 1]), z=z)
    store.record_certificate(cert, task="4/n solvable for all even n (identity proof)")

    # ── Step 4: The wall in action ──
    proven_tasks = {r.task for r in store.proven_skills()}
    all_skills = store.skills()
    recall_ctx = store.build_recall_context("is_prime primality check function")

    # ── Step 5: Assemble demonstration report ──
    report = {
        "title": "Agent Gate Demo: epistemic wall prevents reward-hacking",
        "scenario": {
            "solution": "naive is_prime: all(n%i!=0 for i in range(2,n))",
            "bounded_test_passed": bounded_pass,
            "bounded_test_bound": "n in [2, 20]",
            "edge_case_test_passed": edge_pass,
            "edge_case_failure": "is_prime(1)=True (wrong: range(2,1) is empty → all() returns True)",
        },
        "epistemic_gate_behavior": {
            "is_prime_tier": TIER_CORROBORATED,
            "is_prime_in_proven_skills": "is_prime implementation" in proven_tasks,
            "math_identity_in_proven_skills": "4/n solvable for all even n (identity proof)" in proven_tasks,
            "wall_enforced": "is_prime implementation" not in proven_tasks,
            "recall_labels_bound": "CORROBORATED" in recall_ctx and "20" in recall_ctx,
            "recall_does_not_say_proven": "[PROVEN]" not in recall_ctx,
        },
        "without_gate_risk": (
            "If is_prime were labeled PROVEN (as with a boolean verified=True gate), "
            "the self-improving loop would distill it as a universally-correct skill. "
            "On a future task requiring edge-case handling (n=1,0,-1), the agent would "
            "blindly reuse it → silent failure → reward-hacking degradation."
        ),
        "with_gate_protection": (
            "The epistemic gate labels it CORROBORATED(n<20). proven_skills() excludes it. "
            "The recall context explicitly warns the model: '[CORROBORATED n<20]'. "
            "The model has the information to NOT blindly trust it outside the tested bound."
        ),
        "quantitative_comparison": {
            "certifier_dependencies": "0 (Python stdlib only)",
            "certifier_lines_of_code": "~460 (proof_gate.py)",
            "verification_time_identity_ms": "<1",
            "verification_time_sandbox_ms": round(bounded_result.duration * 1000, 1),
            "lean_equivalent_dependencies": "Lean toolchain (~2GB), Mathlib",
            "note": "Lean covers broader claims but requires heavy toolchain; our decidable-fragment certifiers cover a narrow class exactly, with zero deps.",
        },
        "recall_context_sample": recall_ctx,
    }

    (out_dir / "GATE_DEMO_REPORT.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:  # pragma: no cover
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    report = run(_HERE / "results")
    print("=" * 66)
    print("AGENT GATE DEMO: EPISTEMIC WALL PREVENTS REWARD-HACKING")
    print("=" * 66)
    s = report["scenario"]
    print(f"  solution: {s['solution']}")
    print(f"  bounded test [2,20] passed: {s['bounded_test_passed']}")
    print(f"  edge-case test (n=1,0,-1) passed: {s['edge_case_test_passed']}")
    print(f"  failure: {s['edge_case_failure']}")
    g = report["epistemic_gate_behavior"]
    print(f"\n  EPISTEMIC GATE:")
    print(f"    is_prime tier: {g['is_prime_tier']}")
    print(f"    is_prime in proven_skills: {g['is_prime_in_proven_skills']}  <- THE WALL")
    print(f"    math identity in proven_skills: {g['math_identity_in_proven_skills']}  <- genuinely PROVEN")
    print(f"    recall labels bound: {g['recall_labels_bound']}")
    print(f"    recall does NOT say [PROVEN]: {g['recall_does_not_say_proven']}")
    print(f"\n  WITHOUT gate: {report['without_gate_risk'][:120]}...")
    print(f"  WITH gate:    {report['with_gate_protection'][:120]}...")
    q = report["quantitative_comparison"]
    print(f"\n  QUANTITATIVE: certifier deps={q['certifier_dependencies']}, "
          f"identity verify <{q['verification_time_identity_ms']}ms, "
          f"sandbox verify {q['verification_time_sandbox_ms']}ms")
    print(f"\n  report -> {_HERE / 'results' / 'GATE_DEMO_REPORT.json'}")


if __name__ == "__main__":  # pragma: no cover
    main()
