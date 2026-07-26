"""Batch search for Erdős–Straus decompositions over a range of n.

Sweeps n in [2, N], records for each n either a decomposition or "none within
bounds", and writes the results as JSONL (append-only, one line per n).

Output line schema:
    {"n": 5, "x": 2, "y": 4, "z": 20, "ok": true}
    {"n": <n>, "none": true, "ok": false}      # no decomposition within bounds

A "none" entry is a NEGATIVE result within the bounded search -- it does NOT
prove n is a counterexample to the conjecture. Any "none" for n would be
investigated further (it would be a candidate counterexample, a major result).

Usage:
    python run_search.py --n-max 100000 --out results/search_100k.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from solve import solve, verify


def run(n_max: int, out_path: Path, *, progress_every: int = 5000) -> dict:
    """Sweep n in [2, n_max], write JSONL results, return summary stats."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    total = n_max - 1  # n from 2..n_max inclusive
    found = 0
    none_count = 0
    verified = 0
    with out_path.open("w", encoding="utf-8") as f:
        for n in range(2, n_max + 1):
            decomp = solve(n)
            if decomp is None:
                none_count += 1
                rec = {"n": n, "none": True, "ok": False}
            else:
                found += 1
                x, y, z = decomp
                ok = verify(n, decomp)
                if ok:
                    verified += 1
                rec = {"n": n, "x": x, "y": y, "z": z, "ok": ok}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (n - 1) % progress_every == 0:
                elapsed = time.time() - t0
                rate = n / elapsed if elapsed > 0 else 0
                print(f"  n={n} ({n/n_max*100:.1f}%) elapsed={elapsed:.1f}s "
                      f"rate={rate:.0f}n/s found={found} none={none_count}")
    elapsed = time.time() - t0
    summary = {
        "n_max": n_max,
        "total": total,
        "found": found,
        "verified": verified,
        "none_within_bounds": none_count,
        "elapsed_sec": round(elapsed, 2),
        "support_rate": round(found / total, 6) if total else 0.0,
    }
    return summary


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Erdos-Straus batch search")
    ap.add_argument("--n-max", type=int, default=100000)
    ap.add_argument("--out", type=str, default="results/search.jsonl")
    ap.add_argument("--progress-every", type=int, default=5000)
    args = ap.parse_args()
    out = Path(args.out)
    print(f"Searching n in [2, {args.n_max}], output -> {out}")
    summary = run(args.n_max, out, progress_every=args.progress_every)
    print("\nSUMMARY:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    # Write summary alongside results.
    (out.parent / (out.stem + "_summary.json")).write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"\nSummary written to {out.parent / (out.stem + '_summary.json')}")


if __name__ == "__main__":  # pragma: no cover
    main()
