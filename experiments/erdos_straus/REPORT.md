# Erdős–Straus Conjecture: A Falsifiable Computational Exploration

> A Science Kit (E3) dogfood campaign run through zall's hypothesis → experiment → evidence → falsify/revise loop, with full cryptographic provenance.

## ⚠️ Honesty Statement (PR-0 self-falsifiability)

The Erdős–Straus conjecture is an **open problem** in number theory:

> For every integer n ≥ 2, there exist positive integers x, y, z such that 4/n = 1/x + 1/y + 1/z.

**This campaign does NOT claim a proof.** No LLM agent "solves" famous open problems by running a search loop. The conjecture has been verified by mathematicians to far larger bounds than reached here (n < 10¹⁴ and beyond).

The genuine value of this campaign is methodological: a **real, reproducible, falsifiable** computational exploration, recorded end-to-end through zall's Science Kit, with:
- A positive hypothesis (H1) and its falsifiable counterpart (H2).
- A real experiment with bounded search.
- Evidence with **real SHA-256 file provenance** (Part D), not placeholders.
- Results anchored to a chain-hashed RunRecorder timeline (Verifiability dimension).
- An honest **negative result** (I-10: equally valued): no counterexample found in [2, N).

Any counterexample found *would* be a genuine, major result — but none was found, exactly as expected at reachable scales.

## Result Summary

| Item | Value |
|---|---|
| Search interval | n ∈ [2, 50000) |
| Total n tested | 49,999 |
| Decompositions found | 49,999 |
| Counterexamples (none-within-bounds) | 0 |
| Support rate (H1) | 1.0 |
| Wall time | 145.97 s |
| H1 status | **CONFIRMED** (supported within interval — *not* a proof) |
| H2 status | **FALSIFIED** (no counterexample — negative result, I-10) |

## Hypotheses

- **H1** (positive): "For all integers 2 ≤ n < 50000, 4/n = 1/x+1/y+1/z has a solution in positive integers."
  - Prediction: support_rate = 1.0; no n with "none within bounds".
  - Outcome: CONFIRMED within the interval.
- **H2** (falsifiable counterpart): "There exists n in [2, 50000) that is a counterexample (no decomposition within search bounds)."
  - Prediction: at least one n with none=True.
  - Outcome: FALSIFIED — 0 counterexamples. This is the scientifically valuable **negative result**.

## Method

The solver (`solve.py`) uses a bounded, pruned Egyptian-fraction enumeration:
1. For candidate x in [⌊n/4⌋+1, n], compute R = 4/n − 1/x = (4x−n)/(nx).
2. For each x, search y in a bounded range and solve z = (n·x·y)/(num·y − den), checking integrality.
3. Every decomposition is **verified by exact integer arithmetic**: `4·x·y·z == n·(y·z + x·z + x·y)`.

This is a verify/search routine, not a proof. "None within bounds" means the bounded search was exhausted — it does **not** prove n is a counterexample.

## Provenance (real, not placeholder)

All hashes are real SHA-256 of actual files (Part D `hash_utils`), verified to match:

| Field | Hash |
|---|---|
| protocol_hash (solve.py + run_search.py) | `sha256:33ad8790…9e41144b` |
| data_hash (results/search_50k.jsonl) | `sha256:70dcb506…6c71bd86` |
| analysis_code_hash (science_campaign.py) | `sha256:0924a259…0735a3f47` |
| environment_hash (python + platform) | `sha256:6a20c69e…191f04830` |
| timeline_anchor | `anchor_ack_1784821974105_1` |

The provenance is anchored to a chain-hashed `RunRecorder` timeline (SHA-256 chain + the provenance hash enters the ANCHOR_ACK event payload), so the experimental record is tamper-evident and independently re-checkable — the Verifiability dimension of zall's six-dimensional ontology.

## Reproducibility

```bash
cd experiments/erdos_straus
python science_campaign.py --n-max 50000 \
    --results results/search_50k.jsonl \
    --store results/science_store
```

Re-running reproduces identical: search results (deterministic), provenance hashes (same files), and Science Kit records (append-only JSONL). The `protocol_hash` and `environment_hash` let a third party confirm the *same code + environment* produced the result.

## Science Kit Records

Stored as append-only JSONL under `results/science_store/`:
- `hypotheses.jsonl` — H1 (confirmed) + H2 (falsified), with version + evidence links.
- `experiments.jsonl` — 1 completed experiment with the search summary as result.
- `evidence.jsonl` — 2 evidence records (positive for H1, negative for H2), each carrying real provenance.

Negative result (H2 falsified) is stored with equal status to the positive (I-10: 负结果平等).

## What This Demonstrates About zall

This is a genuine dogfood of the Science Kit (E3) on a real open mathematical problem:
- **Hypothesis lifecycle**: propose → test → confirm/falsify, with the H2→falsified path exercising the negative-result-first-class-citizen invariant (I-10).
- **Real provenance** (Part D): file hashes are real, not `sha256:cli-manual` placeholders. Reproducibility is a verifiable fact.
- **Verifiability**: results anchored to a chain-hashed timeline.
- **PR-0 (self-falsifiability)**: the report explicitly states what would falsify the campaign's own claims (a counterexample) and does not overclaim.

## Limitations (honest)

- The search is **bounded**; "none within bounds" ≠ "no decomposition exists".
- Reachable N (50,000 here) is far below the conjecture's known verification bounds; this campaign contributes no new mathematical knowledge about the conjecture itself.
- The value is in the **framework demonstration** (Science Kit + provenance + verifiability), not in advancing the conjecture.
