"""Erdős–Straus: a machine-verified covering-system proof for density 5/6.

Honesty statement (PR-0 self-falsifiability):
  This does NOT prove the full Erdős–Straus conjecture (an OPEN problem). It
  PROVES an infinite family — density 5/6 of all integers n≥2 — via three
  machine-checked parametric Egyptian-fraction identities, each certified by
  zall's Proof Gate (core/proof_gate.py) using exact integer arithmetic
  (no Lean, no external prover). The residual {n ≡ 1, 5 (mod 12)} is honestly
  reported as CORROBORATED (bounded search, no counterexample) — NOT proven.
  It is a classical fact that no single polynomial identity can cover these
  classes (the required Egyptian-split multiplier grows with n), which is
  exactly where genuine new mathematics — or a proof assistant to formalise a
  hypothetical proof — would be needed.

The point (framework dogfood): the SAME problem the Science Kit previously only
CORROBORATED within a bounded interval is now, for 5/6 of it, genuinely PROVEN —
the machine itself distinguishes the two epistemic tiers (VerificationTier),
instead of a human bolting "NOT a proof" onto the report by hand.

Evidence chain: proof certificates + SHA-256 provenance of the verifier + the
prover + the solver + environment, anchored to a chain-hashed RunRecorder
timeline (tamper-evident, independently re-checkable).

Usage:
    python prove.py --residual-bound 20000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Make the zall package importable when run from the experiments dir.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from zall.core.proof_gate import (
    Poly,
    ProofCertificate,
    VerificationTier,
    corroborated,
    verify_egyptian_identity,
)
from zall.core.provenance import ScienceProvenance
from zall.core.verifiability import RunRecorder
from zall._util.hash_utils import environment_hash, hash_bytes, hash_files, hash_file

from solve import solve  # bounded Egyptian-fraction search (for residual corroboration)


# lcm(2, 3, 4) = 12: the coarsest modulus that resolves all three identities' classes.
L = 12
PROOF_GATE_PY = _REPO_ROOT / "src" / "zall" / "core" / "proof_gate.py"
SOLVE_PY = _HERE / "solve.py"
THIS = Path(__file__).resolve()


@dataclass(frozen=True)
class CoveringIdentity:
    """One parametric identity covering the arithmetic progression n = q·k + r."""

    name: str
    q: int
    r: int
    x: Poly
    y: Poly
    z: Poly
    human: str  # human-readable identity in terms of n


def build_covering() -> list[CoveringIdentity]:
    """The covering system (three rigorously-derived, machine-checkable identities).

    Poly coeffs are index=power (so 4k+3 == Poly([3, 4])).
    """
    # (1) n even: n = 2k+2, m = n/2 = k+1.
    #     4/n = 2/m = 1/m + 1/(m+1) + 1/(m(m+1)).
    even = CoveringIdentity(
        name="even  (n = 2k+2)",
        q=2, r=2,
        x=Poly([1, 1]),               # m   = k+1
        y=Poly([2, 1]),               # m+1 = k+2
        z=Poly([1, 1]) * Poly([2, 1]),  # m(m+1)
        human="4/n = 1/(n/2) + 1/(n/2+1) + 1/((n/2)(n/2+1))",
    )
    # (2) 3 | n: n = 3k+3, s = n/3 = k+1.
    #     4/n = 3/n + 1/n = 1/s + [1/(n+1) + 1/(n(n+1))].
    mult3 = CoveringIdentity(
        name="3 | n  (n = 3k+3)",
        q=3, r=3,
        x=Poly([1, 1]),               # s   = k+1
        y=Poly([4, 3]),               # n+1 = 3k+4
        z=Poly([3, 3]) * Poly([4, 3]),  # n(n+1) = (3k+3)(3k+4)
        human="4/n = 1/(n/3) + 1/(n+1) + 1/(n(n+1))",
    )
    # (3) n ≡ 3 (mod 4): n = 4k+3, x = (n+1)/4 = k+1, M = n·x = (4k+3)(k+1).
    #     4/n = 1/x + 1/M = 1/x + 1/(M+1) + 1/(M(M+1)).
    m_poly = Poly([3, 4]) * Poly([1, 1])   # M = (4k+3)(k+1) = 4k^2+7k+3
    m_plus_1 = m_poly + Poly([1])
    n3mod4 = CoveringIdentity(
        name="n≡3(mod4) (n = 4k+3)",
        q=4, r=3,
        x=Poly([1, 1]),               # x   = (n+1)/4 = k+1
        y=m_plus_1,                   # M+1
        z=m_poly * m_plus_1,          # M(M+1)
        human="4/n = 1/((n+1)/4) + 1/(M+1) + 1/(M(M+1)), where M = n(n+1)/4",
    )
    return [even, mult3, n3mod4]


def prove_covering() -> tuple[list[tuple[CoveringIdentity, ProofCertificate]], set[int], list[int], str]:
    """Run every identity through the Proof Gate; compute union coverage mod L."""
    results: list[tuple[CoveringIdentity, ProofCertificate]] = []
    covered: set[int] = set()
    for idc in build_covering():
        cert = verify_egyptian_identity(
            idc.q, idc.r, idc.x, idc.y, idc.z,
            claim=f"forall n in {{{idc.q}k+{idc.r} : k>=0}}: {idc.human}",
        )
        results.append((idc, cert))
        if cert.tier is VerificationTier.PROVEN:
            # residues mod L this progression hits (iterate a full period)
            for k in range(L):
                covered.add((idc.q * k + idc.r) % L)
    residual = sorted(set(range(L)) - covered)
    density = f"{len(covered)}/{L}"
    return results, covered, residual, density


def corroborate_residual(n_max: int, residual: list[int]) -> dict:
    """Bounded search over the residual residues; report CORROBORATED, not proven.

    A single n with no decomposition within bounds would be a candidate
    counterexample (a major result) — we report the first one if found.
    """
    t0 = time.time()
    checked = 0
    none_within_bounds = 0
    first_none: int | None = None
    for n in range(2, n_max):
        if (n % L) in residual:
            checked += 1
            if solve(n) is None:
                none_within_bounds += 1
                if first_none is None:
                    first_none = n
    return {
        "n_max": n_max,
        "residual_residues_mod_12": residual,
        "checked": checked,
        "none_within_bounds": none_within_bounds,
        "first_candidate_counterexample": first_none,
        "elapsed_sec": round(time.time() - t0, 2),
    }


def run(n_max: int, out_dir: Path) -> dict:
    """Full proof campaign + evidence chain. Returns the report dict."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Machine-check the covering identities (the PROVEN tier) ──
    results, covered, residual, density = prove_covering()
    identity_report = []
    all_proven = True
    for idc, cert in results:
        identity_report.append({
            "name": idc.name,
            "progression": f"n = {idc.q}k + {idc.r}",
            "identity": idc.human,
            "tier": cert.tier.value,
            "difference_poly": cert.data.get("difference_poly"),
            "detail": cert.detail,
        })
        all_proven = all_proven and (cert.tier is VerificationTier.PROVEN)

    # ── 2. Corroborate (NOT prove) the residual via bounded search ──
    residual_search = corroborate_residual(n_max, residual)
    residual_cert = (
        corroborated(
            claim=f"4/n solvable for all n≡{residual} (mod {L}) — residual class",
            bound=n_max,
            detail=(
                f"bounded search checked {residual_search['checked']} residual n in "
                f"[2,{n_max}); {residual_search['none_within_bounds']} had no "
                f"decomposition within bounds. This CORROBORATES but does NOT prove "
                f"the residual (no finite polynomial identity covers these classes)."
            ),
        )
        if residual_search["none_within_bounds"] == 0
        else None  # a counterexample would flip this to REFUTED — a major result.
    )

    # ── 3. Evidence chain: provenance hashes + chain-hashed timeline anchor ──
    # Hashed payload must be DETERMINISTIC (exclude wall-time so data_hash is reproducible).
    residual_deterministic = {k: v for k, v in residual_search.items() if k != "elapsed_sec"}
    payload = {
        "identities": identity_report,
        "covered_residues_mod_12": sorted(covered),
        "residual_residues_mod_12": residual,
        "proven_density": density,
        "residual_search": residual_deterministic,
    }
    payload_hash = hash_bytes(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
    prov = ScienceProvenance(
        protocol_hash=hash_files([PROOF_GATE_PY, THIS, SOLVE_PY]),
        data_hash=payload_hash,
        analysis_code_hash=hash_file(THIS),
        environment_hash=environment_hash(extra="proof=erdos_straus_covering_v1"),
    )
    recorder = RunRecorder(run_id=f"erdos_straus_proof_{n_max}_{int(time.time())}")
    anchor = prov.anchor_to(recorder)
    chain_ok = recorder.verify_chain()

    report = {
        "title": "Erdős–Straus: machine-verified covering-system proof (density 5/6)",
        "honesty": (
            "NOT a proof of the full conjecture. Density 5/6 is PROVEN by 3 "
            "machine-checked identities; residual {n≡1,5 mod 12} is CORROBORATED "
            "(bounded search), not proven."
        ),
        "all_identities_proven": all_proven,
        "proven_density": density,
        "epistemic_tiers": {
            "PROVEN": f"{density} of integers (three ∀-identities, exact-arithmetic certificates)",
            "CORROBORATED": (
                residual_cert.detail if residual_cert is not None
                else "RESIDUAL HAS A CANDIDATE COUNTEREXAMPLE — investigate (major result)"
            ),
        },
        **payload,
        "provenance": {
            "protocol_hash": prov.protocol_hash,
            "data_hash": prov.data_hash,
            "analysis_code_hash": prov.analysis_code_hash,
            "environment_hash": prov.environment_hash,
            "timeline_anchor": anchor,
            "chain_verified": chain_ok,
        },
    }
    # Report keeps full timing (informational); the hashed payload above excluded it.
    report["residual_search"] = residual_search

    (out_dir / "PROOF_REPORT.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Erdős–Straus covering-system proof")
    ap.add_argument("--residual-bound", type=int, default=20000,
                    help="upper bound N for corroborating the residual classes")
    ap.add_argument("--out", type=str, default="results",
                    help="output dir for PROOF_REPORT.json")
    args = ap.parse_args()

    # Windows 控制台默认 GBK, 无法编码 ≡/∀/— 等字符; 切到 utf-8 (报告文件本就 utf-8)。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    report = run(args.residual_bound, _HERE / args.out)

    print("=" * 66)
    print("ERDŐS–STRAUS — MACHINE-VERIFIED COVERING-SYSTEM PROOF")
    print("=" * 66)
    for idr in report["identities"]:
        print(f"  [{idr['tier'].upper():11s}] {idr['name']:22s} {idr['identity']}")
    print(f"\n  PROVEN density (exact ∀-proof): {report['proven_density']} of all integers")
    print(f"  covered residues (mod 12): {report['covered_residues_mod_12']}")
    print(f"  residual  residues (mod 12): {report['residual_residues_mod_12']}  (CORROBORATED, not proven)")
    rs = report["residual_search"]
    print(f"  residual search: checked {rs['checked']} n in [2,{rs['n_max']}), "
          f"{rs['none_within_bounds']} counterexamples, {rs['elapsed_sec']}s")
    p = report["provenance"]
    print("\n  evidence chain:")
    print(f"    protocol_hash     : {p['protocol_hash'][:34]}…")
    print(f"    data_hash         : {p['data_hash'][:34]}…")
    print(f"    timeline_anchor   : {p['timeline_anchor']}")
    print(f"    chain_verified    : {p['chain_verified']}")
    print(f"\n  full report -> {_HERE / args.out / 'PROOF_REPORT.json'}")
    print("\n  NOTE: 5/6 is a genuine proof; the conjecture as a whole remains OPEN.")


if __name__ == "__main__":  # pragma: no cover
    main()
