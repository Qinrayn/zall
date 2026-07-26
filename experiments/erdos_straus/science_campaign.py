"""Science Kit campaign: Erdős–Straus conjecture exploration.

Honesty note (PR-0 self-falsifiability):
  The Erdős–Straus conjecture is an OPEN problem. No LLM agent "solves" it.
  This campaign does NOT claim a proof. It performs a real, reproducible,
  falsifiable COMPUTATIONAL EXPLORATION recorded through zall's Science Kit
  (hypothesis -> experiment -> evidence -> falsify/revise), with full
  provenance hashing. Any counterexample found would be a genuine major result
  (extremely unlikely at reachable scales). The honest output is: "supported
  within the searched interval [2, N], no counterexample" -- a negative result
  (I-10: equally valued).

Hypotheses:
  H1: "For all 2 <= n < N, 4/n decomposes into 3 unit fractions."
      prediction: support_rate = 1.0 (every n in range has a decomposition)
  H2: "There exists n in [2, N) that is a counterexample (no decomposition)."
      prediction: at least one n with none-within-bounds.
      -> If H1 holds, H2 is FALSIFIED as a negative result.

Provenance: real SHA-256 of the solver script, the results file, and the
environment (Part D hash_utils), anchored to a RunRecorder timeline.

Usage:
    python science_campaign.py --n-max 100000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

# Ensure the zall package is importable when run from the experiments dir.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zall.core.evidence import Evidence, EvidenceType
from zall.core.experiment import ExperimentGoal, ExperimentStatus
from zall.core.hypothesis import Hypothesis, HypothesisStatus
from zall.core.provenance import ScienceProvenance
from zall.core.verifiability import RunRecorder
from zall.extensions.science.store import ScienceStore
from zall._util.hash_utils import environment_hash, hash_file, hash_files

from run_search import run as run_search


HERE = Path(__file__).resolve().parent
SOLVE_SCRIPT = HERE / "solve.py"
SEARCH_SCRIPT = HERE / "run_search.py"


def _real_provenance(results_path: Path) -> ScienceProvenance:
    """Build ScienceProvenance with REAL file hashes (Part D)."""
    return ScienceProvenance(
        protocol_hash=hash_files([SOLVE_SCRIPT, SEARCH_SCRIPT]),
        data_hash=hash_file(results_path),
        analysis_code_hash=hash_file(__file__),
        environment_hash=environment_hash(extra="solver=erdos_straus_v1"),
    )


def run_campaign(n_max: int, store: ScienceStore, results_path: Path) -> dict:
    """Run the hypothesis-driven exploration campaign.

    Returns a summary dict with hypothesis statuses and evidence recorded.
    """
    now = int(time.time())
    recorder = RunRecorder(run_id=f"erdos_straus_{n_max}_{now}")

    # ── H1: positive hypothesis ──
    h1 = Hypothesis(
        id=uuid4(),
        claim=f"For all integers 2 <= n < {n_max}, 4/n = 1/x+1/y+1/z has a "
              f"solution in positive integers (Erdos-Straus, bounded interval).",
        prediction=f"support_rate = 1.0 over [2, {n_max}); no n with "
                   f"'none within bounds'.",
        confidence=0.95,  # high prior: conjecture verified to far larger bounds
        created_by="erdos_straus_campaign",
        created_at=now,
    )
    store.add_hypothesis(h1)

    # ── H2: counterexample hypothesis (falsifiable counterpart) ──
    h2 = Hypothesis(
        id=uuid4(),
        claim=f"There exists n in [2, {n_max}) that is a counterexample to "
              f"Erdos-Straus (no decomposition within search bounds).",
        prediction="at least one n with none=True in results.",
        confidence=0.05,
        created_by="erdos_straus_campaign",
        created_at=now,
    )
    store.add_hypothesis(h2)

    # ── Experiment: run the batch search ──
    exp = ExperimentGoal(
        id=uuid4(),
        hypothesis_id=h1.id,
        protocol=f"run_search.solve bounded enumeration; see {SEARCH_SCRIPT.name}",
        data_snapshot="(none: input is the integer range, no external data)",
        status=ExperimentStatus.RUNNING,
        started_at=now,
    )
    store.add_experiment(exp)

    print(f"[campaign] running search n in [2, {n_max}] ...")
    search_summary = run_search(n_max, results_path)
    print(f"[campaign] search done: {search_summary}")

    # Record real provenance + anchor to timeline.
    prov = _real_provenance(results_path)
    anchor = prov.anchor_to(recorder)
    print(f"[campaign] provenance anchored to timeline: {anchor[:40]}...")

    # Complete experiment with result.
    exp.result = search_summary
    exp.status = ExperimentStatus.COMPLETED
    exp.completed_at = int(time.time())
    store.update_experiment(exp)

    support_rate = search_summary["support_rate"]
    none_count = search_summary["none_within_bounds"]

    # ── Evidence for H1 ──
    # H1 is supported-within-interval if every n decomposed (none_count==0).
    h1_supported = (none_count == 0)
    ev1 = Evidence(
        id=uuid4(),
        hypothesis_id=h1.id,
        experiment_id=exp.id,
        type=EvidenceType.POSITIVE if h1_supported else EvidenceType.NEGATIVE,
        metric="support_rate",
        value=support_rate,
        threshold=1.0,
        supports=h1_supported,
        provenance=prov,
        created_at=int(time.time()),
    )
    store.add_evidence(ev1)
    h1.add_evidence(ev1.id, supports=h1_supported)
    if h1_supported:
        h1.status = HypothesisStatus.CONFIRMED  # within interval
    else:
        h1.status = HypothesisStatus.FALSIFIED
        h1.falsified_by = ev1.id
    store.update_hypothesis(h1)

    # ── Evidence for H2 (counterexample hypothesis) ──
    # H2 predicted >=1 counterexample. If none_count==0, H2 is FALSIFIED.
    h2_supported = (none_count > 0)
    ev2 = Evidence(
        id=uuid4(),
        hypothesis_id=h2.id,
        experiment_id=exp.id,
        type=EvidenceType.POSITIVE if h2_supported else EvidenceType.NEGATIVE,
        metric="counterexample_count",
        value=float(none_count),
        threshold=1.0,
        supports=h2_supported,
        provenance=prov,
        created_at=int(time.time()),
    )
    store.add_evidence(ev2)
    h2.add_evidence(ev2.id, supports=h2_supported)
    if h2_supported:
        h2.status = HypothesisStatus.CONFIRMED  # found a candidate counterexample!
    else:
        h2.status = HypothesisStatus.FALSIFIED  # no counterexample -> negative result
        h2.falsified_by = ev2.id
    store.update_hypothesis(h2)

    return {
        "n_max": n_max,
        "H1": {
            "id": str(h1.id), "status": h1.status.value,
            "supported_within_interval": h1_supported,
        },
        "H2": {
            "id": str(h2.id), "status": h2.status.value,
            "counterexamples_found": none_count,
        },
        "search_summary": search_summary,
        "provenance": {
            "protocol_hash": prov.protocol_hash,
            "data_hash": prov.data_hash,
            "analysis_code_hash": prov.analysis_code_hash,
            "environment_hash": prov.environment_hash,
            "timeline_anchor": anchor,
        },
        "experiment_id": str(exp.id),
    }


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Erdos-Straus Science Kit campaign")
    ap.add_argument("--n-max", type=int, default=100000)
    ap.add_argument("--results", type=str, default="results/search.jsonl")
    ap.add_argument("--store", type=str, default="results/science_store",
                    help="ScienceStore base dir for this campaign")
    args = ap.parse_args()

    store = ScienceStore(base_dir=HERE / args.store)
    results_path = HERE / args.results
    summary = run_campaign(args.n_max, store, results_path)

    print("\n" + "=" * 60)
    print("CAMPAIGN SUMMARY")
    print("=" * 60)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    report_path = HERE / "REPORT.json"
    report_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nFull summary -> {report_path}")
    print(f"Science records -> {HERE / args.store}")
    print("\nNOTE: 'supported within interval' is NOT a proof of the conjecture.")
    print("It is a reproducible, falsifiable computational result (negative result).")


if __name__ == "__main__":  # pragma: no cover
    main()
