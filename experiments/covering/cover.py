"""Covering systems: machine-checked certification via the Proof Gate.

A covering system {a_i (mod m_i)} covers every integer. This is a decidable
universal claim, certified exactly by core.proof_gate.verify_covering_system
(PROVEN if every residue mod lcm is covered; REFUTED with an explicit uncovered
residue otherwise). Covering systems are the classical engine of Romanov-/Erdős-
type non-representability results.

Honesty (PR-0):
  - We machine-VERIFY the famous Erdős (1950) covering system (a real, exact,
    reproducible certificate) and a REFUTED near-miss.
  - We exhaustively enumerate distinct-modulus coverings over a small fixed
    modulus set (completing search; reproduces known coverings).
  - We do NOT claim to attack the Erdős–Selfridge odd-covering problem: an
    exhaustive odd-covering search is infeasible (that is precisely why it is
    open). Overclaiming there would be dishonest.

Usage:
    python cover.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import product
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HERE = Path(__file__).resolve().parent

from zall.core.proof_gate import VerificationTier, verify_covering_system
from zall.core.provenance import ScienceProvenance
from zall.core.verifiability import RunRecorder
from zall._util.hash_utils import environment_hash, hash_bytes, hash_files, hash_file

PROOF_GATE_PY = _REPO_ROOT / "src" / "zall" / "core" / "proof_gate.py"
THIS = Path(__file__).resolve()

ERDOS_1950 = [(0, 2), (0, 3), (1, 4), (3, 8), (7, 12), (23, 24)]


def enumerate_distinct_modulus_coverings(moduli: list[int], *, limit: int = 5) -> dict:
    """Exhaustively enumerate residue assignments over a FIXED distinct-modulus
    set and return which ones form covering systems (machine-verified).

    Completing search: prod(moduli) assignments. Keeps up to `limit` examples.
    """
    total = 1
    for m in moduli:
        total *= m
    found = 0
    examples: list[list[list[int]]] = []
    for residues in product(*[range(m) for m in moduli]):
        system = [(a, m) for a, m in zip(residues, moduli)]
        if verify_covering_system(system).tier is VerificationTier.PROVEN:
            found += 1
            if len(examples) < limit:
                examples.append([[a, m] for a, m in system])
    return {
        "moduli": moduli,
        "assignments_searched": total,
        "coverings_found": found,
        "examples": examples,
    }


def run(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Verify the classic Erdős (1950) covering system (PROVEN).
    erdos = verify_covering_system(ERDOS_1950)

    # 2. REFUTED near-miss: drop the (23 mod 24) congruence -> residue 23 uncovered.
    near_miss = verify_covering_system(ERDOS_1950[:-1])

    # 3. Exhaustive distinct-modulus covering enumeration over {2,3,4,6,12}.
    enum = enumerate_distinct_modulus_coverings([2, 3, 4, 6, 12], limit=5)

    payload = {
        "erdos_1950": {
            "system": [[a, m] for a, m in ERDOS_1950],
            "tier": erdos.tier.value,
            "lcm": erdos.data.get("lcm"),
        },
        "near_miss_drop_last": {
            "tier": near_miss.tier.value,
            "uncovered_residue": near_miss.data.get("uncovered_residue"),
        },
        "distinct_modulus_enumeration": enum,
        "erdos_selfridge_note": (
            "Odd distinct-modulus covering (Erdős–Selfridge) is OPEN; an exhaustive "
            "search is infeasible. Not attempted here (no overclaim)."
        ),
    }
    payload_hash = hash_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    prov = ScienceProvenance(
        protocol_hash=hash_files([PROOF_GATE_PY, THIS]),
        data_hash=payload_hash,
        analysis_code_hash=hash_file(THIS),
        environment_hash=environment_hash(extra="covering_systems_v1"),
    )
    recorder = RunRecorder(run_id=f"covering_{int(time.time())}")
    anchor = prov.anchor_to(recorder)

    report = {
        "title": "Covering systems: machine-checked certification (Proof Gate)",
        "honesty": (
            "Verifies the classic Erdős 1950 covering and enumerates small distinct-"
            "modulus coverings (known math, exact machine checks). Erdős–Selfridge "
            "odd-covering problem NOT attempted (infeasible search)."
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
    (out_dir / "COVERING_REPORT.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Covering-system certifier demo")
    ap.add_argument("--out", type=str, default="results")
    args = ap.parse_args()
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    report = run(_HERE / args.out)
    print("=" * 64)
    print("COVERING SYSTEMS - MACHINE-CHECKED CERTIFICATION")
    print("=" * 64)
    e = report["erdos_1950"]
    print(f"  Erdos 1950 {e['system']}")
    print(f"    -> tier {e['tier'].upper()} (covers all residues mod {e['lcm']})")
    nm = report["near_miss_drop_last"]
    print(f"  near-miss (drop last congruence): tier {nm['tier'].upper()}, uncovered residue {nm['uncovered_residue']}")
    en = report["distinct_modulus_enumeration"]
    print(f"  distinct-modulus enum over {en['moduli']}: searched {en['assignments_searched']}, "
          f"found {en['coverings_found']} coverings")
    for ex in en["examples"][:3]:
        print(f"      covering: {ex}")
    p = report["provenance"]
    print(f"\n  evidence chain: data_hash {p['data_hash'][:30]}...  anchor {p['timeline_anchor']}  chain {p['chain_verified']}")
    print(f"\n  full report -> {_HERE / args.out / 'COVERING_REPORT.json'}")
    print("  NOTE: known mathematics, exact machine checks. Erdos-Selfridge NOT attempted.")


if __name__ == "__main__":  # pragma: no cover
    main()
