"""One-command reproduction driver for the falsifiability-gated discovery artifact.

Runs all three Proof-Gate demonstrations exactly as documented (each as its own
subprocess, in its own directory), reads their hash-anchored JSON reports,
verifies every evidence chain, optionally runs the pinning invariant tests, and
writes a single consolidated REPRODUCIBILITY_MANIFEST.json.

Usage:
    python experiments/reproduce_all.py                 # full (residual bound 20000)
    python experiments/reproduce_all.py --quick         # fast (residual bound 3000)
    python experiments/reproduce_all.py --skip-tests    # demos only

Exit code is non-zero if any demonstration fails or any evidence chain does not
verify — so this doubles as a CI reproducibility check.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # .../experiments
_REPO_ROOT = _HERE.parent

# Each demonstration: (label, working dir, argv after the script, report path).
DEMOS = {
    "erdos_straus": (_HERE / "erdos_straus", "prove.py", "results/PROOF_REPORT.json"),
    "diophantine": (_HERE / "diophantine", "certify.py", "results/DIOPHANTINE_REPORT.json"),
    "covering": (_HERE / "covering", "cover.py", "results/COVERING_REPORT.json"),
}

TEST_FILES = [
    "tests/test_proof_gate_invariants.py",
    "tests/test_proof_gate_square_invariants.py",
    "tests/test_covering_invariants.py",
    "tests/test_experience_tier_gate_invariants.py",
]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, encoding="utf-8",
        errors="replace", env=env, timeout=900,
    )


def _demo_argv(label: str, residual_bound: int) -> list[str]:
    if label == "erdos_straus":
        return ["--residual-bound", str(residual_bound), "--out", "results"]
    if label == "diophantine":
        return ["--coef-max", "6", "--out", "results"]
    return ["--out", "results"]  # covering


def run_demos(residual_bound: int) -> dict:
    results: dict[str, dict] = {}
    for label, (wd, script, report_rel) in DEMOS.items():
        argv = [sys.executable, script, *_demo_argv(label, residual_bound)]
        proc = _run(argv, wd)
        report_path = wd / report_rel
        entry: dict = {"ran": proc.returncode == 0}
        if proc.returncode != 0:
            entry["error"] = (proc.stderr or proc.stdout or "").strip()[-500:]
        elif report_path.exists():
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            prov = rep.get("provenance", {})
            entry["report"] = str(report_path.relative_to(_REPO_ROOT)).replace("\\", "/")
            entry["data_hash"] = prov.get("data_hash")
            entry["timeline_anchor"] = prov.get("timeline_anchor")
            entry["chain_verified"] = bool(prov.get("chain_verified"))
            entry["summary"] = _summarize(label, rep)
        else:
            entry["ran"] = False
            entry["error"] = f"report not found: {report_path}"
        results[label] = entry
    return results


def _summarize(label: str, rep: dict) -> dict:
    if label == "erdos_straus":
        return {
            "proven_density": rep.get("proven_density"),
            "residual_mod_12": rep.get("residual_residues_mod_12"),
            "residual_counterexamples": rep.get("residual_search", {}).get("none_within_bounds"),
        }
    if label == "diophantine":
        return {
            "euler_family_tier": rep.get("euler_quadruple_family", {}).get("tier"),
            "linear_triple_families": rep.get("linear_triple_search", {}).get("count"),
        }
    return {
        "erdos_1950_tier": rep.get("erdos_1950", {}).get("tier"),
        "near_miss_tier": rep.get("near_miss_drop_last", {}).get("tier"),
        "coverings_found": rep.get("distinct_modulus_enumeration", {}).get("coverings_found"),
    }


def run_tests() -> dict:
    proc = _run([sys.executable, "-m", "pytest", *TEST_FILES, "-q"], _REPO_ROOT)
    out = (proc.stdout or "") + (proc.stderr or "")
    import re
    m = re.search(r"(\d+)\s+passed", out)
    passed = int(m.group(1)) if m else 0
    return {"passed": passed, "ok": proc.returncode == 0, "command": "pytest " + " ".join(TEST_FILES)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Reproduce all Proof-Gate demonstrations")
    ap.add_argument("--residual-bound", type=int, default=20000,
                    help="Erdős–Straus residual corroboration bound (paper uses 20000)")
    ap.add_argument("--quick", action="store_true", help="fast run (residual bound 3000)")
    ap.add_argument("--skip-tests", action="store_true", help="skip the invariant tests")
    args = ap.parse_args()
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    residual_bound = 3000 if args.quick else args.residual_bound
    t0 = time.time()
    print("=" * 66)
    print("FALSIFIABILITY-GATED DISCOVERY - ONE-COMMAND REPRODUCTION")
    print("=" * 66)

    demos = run_demos(residual_bound)
    tests = None if args.skip_tests else run_tests()

    all_chains = all(d.get("chain_verified") for d in demos.values())
    all_ran = all(d.get("ran") for d in demos.values())
    tests_ok = True if tests is None else tests["ok"]

    manifest = {
        "generated_at": int(t0),
        "python": sys.version.split()[0],
        "residual_bound": residual_bound,
        "demonstrations": demos,
        "tests": tests,
        "all_demos_ran": all_ran,
        "all_chains_verified": all_chains,
        "tests_ok": tests_ok,
        "reproducible": bool(all_ran and all_chains and tests_ok),
    }
    (_HERE / "REPRODUCIBILITY_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for label, d in demos.items():
        status = "OK " if d.get("ran") else "FAIL"
        chain = "chain=verified" if d.get("chain_verified") else "chain=UNVERIFIED"
        print(f"  [{status}] {label:14s} {chain}  {d.get('summary', d.get('error', ''))}")
    if tests is not None:
        print(f"  [{'OK ' if tests['ok'] else 'FAIL'}] invariant tests: {tests['passed']} passed")
    print("-" * 66)
    print(f"  reproducible: {manifest['reproducible']}   ({time.time() - t0:.1f}s)")
    print(f"  manifest -> {(_HERE / 'REPRODUCIBILITY_MANIFEST.json').relative_to(_REPO_ROOT)}")

    sys.exit(0 if manifest["reproducible"] else 1)


if __name__ == "__main__":
    main()
