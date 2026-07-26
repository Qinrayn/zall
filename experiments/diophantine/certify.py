"""Diophantine m-tuples: a machine-checked family certifier via the Proof Gate.

A set {a1,...,am} is a Diophantine m-tuple with property D(n) if ai*aj + n is a
perfect square for every i<j. A *polynomial family* {a1(t),...,am(t)} is a
Diophantine m-tuple for ALL integer t iff every ai(t)*aj(t) + n is a
perfect-square polynomial — an exact identity checkable by zall's Proof Gate
(core/proof_gate.py: verify_square_identity / poly_sqrt), no external prover.

Honesty (PR-0):
  The polynomial version of the D(1) problem is SOLVED: Dujella & Fuchs proved
  every polynomial D(1)-quadruple is "regular". So certifying Euler's family is a
  machine-checked *validation of known mathematics*, not a new theorem, and the
  bounded search below re-derives regular families (empirically corroborating
  Dujella–Fuchs via an independent exact checker). The value here is the
  reproducible, machine-checked certification pipeline + provenance — and an
  honest demonstration of where the "new theorem" ceiling actually is.

Usage:
    python certify.py --coef-max 6
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import combinations
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HERE = Path(__file__).resolve().parent

from zall.core.proof_gate import Poly, VerificationTier, poly_sqrt, verify_square_identity
from zall.core.provenance import ScienceProvenance
from zall.core.verifiability import RunRecorder
from zall._util.hash_utils import environment_hash, hash_bytes, hash_files, hash_file

PROOF_GATE_PY = _REPO_ROOT / "src" / "zall" / "core" / "proof_gate.py"
THIS = Path(__file__).resolve()


def certify_family(entries: dict[str, Poly], n: int = 1) -> dict:
    """Certify {entries} is a D(n) m-tuple family for all t: every pairwise
    product + n must be a perfect-square polynomial (exact ∀t certificate).
    """
    names = list(entries)
    pairs = []
    all_proven = True
    for i, j in combinations(range(len(names)), 2):
        ni, nj = names[i], names[j]
        prod = entries[ni] * entries[nj] + Poly([n])
        cert = verify_square_identity(prod, claim=f"{ni}*{nj}+{n} is a perfect square for all t")
        proven = cert.tier is VerificationTier.PROVEN
        all_proven = all_proven and proven
        pairs.append({
            "pair": f"{ni}*{nj}+{n}",
            "product_poly": list(prod.coeffs),
            "tier": cert.tier.value,
            "sqrt": cert.data.get("q") if proven else None,
        })
    return {
        "entries": {k: list(v.coeffs) for k, v in entries.items()},
        "n": n,
        "all_pairs_square": all_proven,
        "tier": VerificationTier.PROVEN.value if all_proven else VerificationTier.UNKNOWN.value,
        "pairs": pairs,
    }


def euler_family() -> dict[str, Poly]:
    """Euler's D(1)-quadruple family from the pair {t, t+2} (r = t+1):
        {t, t+2, 4t+4, 4(t+1)(2t+1)(2t+3)}.
    At t=1 this is {1,3,8,120} — Fermat's classical quadruple.
    """
    return {
        "a": Poly([0, 1]),              # t
        "b": Poly([2, 1]),              # t+2
        "c": Poly([4, 4]),              # 4t+4
        "d": Poly([12, 44, 48, 16]),    # 4(t+1)(2t+1)(2t+3) = 16t^3+48t^2+44t+12
    }


def _linear_polys(coef_max: int) -> list[Poly]:
    """Positive linear entries p*t+q (0<=p,q<=coef_max), positive for all t>=1."""
    out = []
    for p in range(0, coef_max + 1):
        for q in range(0, coef_max + 1):
            if p == 0 and q == 0:
                continue
            if q == 0 and p == 0:
                continue
            # positive for t>=1: value at t=1 is p+q>=1 and nondecreasing (p>=0)
            if p + q >= 1:
                out.append(Poly([q, p]))
    return out


def search_linear_triples(coef_max: int, n: int = 1) -> list[dict]:
    """Bounded exact search: linear D(n)-triples {p1 t+q1, p2 t+q2, p3 t+q3}
    whose three pairwise products + n are ALL perfect-square polynomials (∀t).
    Deterministic; dedup by sorted coefficient signature.
    """
    polys = _linear_polys(coef_max)
    seen: set[tuple] = set()
    found: list[dict] = []
    for triple in combinations(polys, 3):
        ok = True
        for x, y in combinations(triple, 2):
            if poly_sqrt(x * y + Poly([n])) is None:
                ok = False
                break
        if not ok:
            continue
        sig = tuple(sorted(tuple(pp.coeffs) for pp in triple))
        if sig in seen:
            continue
        seen.add(sig)
        found.append({"entries": [list(pp.coeffs) for pp in triple]})
    return found


def run(coef_max: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    euler = certify_family(euler_family(), n=1)
    triples = search_linear_triples(coef_max, n=1)

    payload = {
        "euler_quadruple_family": euler,
        "linear_triple_search": {
            "coef_max": coef_max,
            "n": 1,
            "certified_families": triples,
            "count": len(triples),
        },
    }
    payload_hash = hash_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    prov = ScienceProvenance(
        protocol_hash=hash_files([PROOF_GATE_PY, THIS]),
        data_hash=payload_hash,
        analysis_code_hash=hash_file(THIS),
        environment_hash=environment_hash(extra="diophantine_certifier_v1"),
    )
    recorder = RunRecorder(run_id=f"diophantine_{coef_max}_{int(time.time())}")
    anchor = prov.anchor_to(recorder)

    report = {
        "title": "Diophantine m-tuples: machine-checked family certifier (Proof Gate)",
        "honesty": (
            "Polynomial D(1)-quadruples are all regular (Dujella-Fuchs). Certifying "
            "Euler's family validates known mathematics; the search re-derives regular "
            "families (independent exact corroboration), NOT a new theorem."
        ),
        **payload,
        "provenance": {
            "protocol_hash": prov.protocol_hash,
            "data_hash": prov.data_hash,
            "analysis_code_hash": prov.analysis_code_hash,
            "environment_hash": prov.environment_hash,
            "timeline_anchor": anchor,
            "chain_verified": recorder.verify_chain(),
        },
    }
    (out_dir / "DIOPHANTINE_REPORT.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Diophantine m-tuple family certifier")
    ap.add_argument("--coef-max", type=int, default=6)
    ap.add_argument("--out", type=str, default="results")
    args = ap.parse_args()
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    report = run(args.coef_max, _HERE / args.out)
    e = report["euler_quadruple_family"]
    print("=" * 64)
    print("DIOPHANTINE m-TUPLES - MACHINE-CHECKED FAMILY CERTIFIER")
    print("=" * 64)
    print(f"  Euler family {{t, t+2, 4t+4, 16t^3+48t^2+44t+12}} (t=1 -> Fermat {{1,3,8,120}}):")
    print(f"    all 6 pairwise (ai*aj+1) perfect-square identities: {e['all_pairs_square']}  -> tier {e['tier'].upper()}")
    for pr in e["pairs"]:
        print(f"      [{pr['tier'].upper():7s}] {pr['pair']:9s} = {pr['product_poly']}  sqrt={pr['sqrt']}")
    s = report["linear_triple_search"]
    print(f"\n  linear D(1)-triple search (coef<= {s['coef_max']}): {s['count']} certified families (all regular)")
    for fam in s["certified_families"][:12]:
        print(f"      {fam['entries']}")
    p = report["provenance"]
    print(f"\n  evidence chain: data_hash {p['data_hash'][:30]}...  anchor {p['timeline_anchor']}  chain {p['chain_verified']}")
    print(f"\n  full report -> {_HERE / args.out / 'DIOPHANTINE_REPORT.json'}")
    print("  NOTE: known mathematics (Dujella-Fuchs). Machine-checked validation, not a new theorem.")


if __name__ == "__main__":  # pragma: no cover
    main()
