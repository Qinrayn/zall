# Falsifiability-Gated Discovery — Reproducible Artifact

Companion artifact for [`FALSIFIABILITY_GATED_DISCOVERY.md`](./FALSIFIABILITY_GATED_DISCOVERY.md).
A machine-checkable **epistemic gate** (`VerificationTier` = `REFUTED` / `CORROBORATED` /
`PROVEN` / `UNKNOWN`) with three pure-standard-library certifiers, cryptographic
provenance, and a chain-hashed timeline — wired into a self-improving agent's learning
gate so that *corroboration can never masquerade as proof*.

> **Honest scope.** The mathematics in the demonstrations is **classical and known**.
> The contribution is the *epistemic discipline* and the *reproducible, self-falsifying
> pipeline* — **not** new theorems.

## One command

```bash
# from the repository root
pip install -e .                      # deps for the evidence-chain timeline (pydantic, cryptography)
python experiments/reproduce_all.py   # runs all 3 demos + invariant tests, verifies every chain
```

- `--quick` uses a smaller Erdős–Straus residual bound (≈12 s total).
- `--skip-tests` runs the demonstrations only.
- Exit code is **non-zero** unless every demo ran, every evidence chain verified, and
  the invariant tests passed — so this doubles as a CI reproducibility check. Results are
  written to `experiments/REPRODUCIBILITY_MANIFEST.json`.

The certifiers themselves ([`src/zall/core/proof_gate.py`](../../src/zall/core/proof_gate.py))
are **pure Python standard library** — no CAS, no SMT solver, no proof assistant. The
package install is only needed for the provenance/timeline machinery used by the demos.

## What gets checked

| Demonstration | Machine-checked outcome | Tier |
|---|---|---|
| **Erdős–Straus** (3 parametric identities) | covers `{0,2,3,4,6,7,8,9,10,11} mod 12` → **density 5/6** | `PROVEN` |
| Erdős–Straus residual | `{1,5} mod 12`, 0 counterexamples below the bound | `CORROBORATED` |
| **Diophantine** — Euler quadruple family | 6/6 pairwise `ab+1` are perfect-square polynomials | `PROVEN` |
| Linear `D(1)`-triples (exhaustive) | unique family `{t, t+2, 4t+4}` (corroborates Dujella–Fuchs) | — |
| **Covering** — Erdős 1950 system | covers all residues mod 24 | `PROVEN` |
| Erdős 1950 minus one congruence | residue 23 uncovered | `REFUTED` |

Each demo emits a hash-anchored `*_REPORT.json` (SHA-256 of certifier + prover + result +
environment, anchored to a chain-hashed timeline; `chain_verified: true`).

## Individual commands

```bash
python experiments/erdos_straus/prove.py --residual-bound 20000   # -> results/PROOF_REPORT.json
python experiments/diophantine/certify.py --coef-max 6            # -> results/DIOPHANTINE_REPORT.json
python experiments/covering/cover.py                              # -> results/COVERING_REPORT.json
```

## Invariant tests (each pinned by a counterexample, IPR-0)

```bash
python -m pytest \
  tests/test_proof_gate_invariants.py \
  tests/test_proof_gate_square_invariants.py \
  tests/test_covering_invariants.py \
  tests/test_experience_tier_gate_invariants.py -q
```

A false identity, a non-square polynomial, a non-covering set, and a corroborated claim
that tries to enter the proven set **must** all fail the gate — the certifiers falsify
themselves on bad input.

## Layout

```
src/zall/core/proof_gate.py            # VerificationTier + 3 certifiers + Prover protocol (pure stdlib)
src/zall/core/experience_store.py      # learning gate: consumes VerificationTier, enforces the wall
experiments/erdos_straus/prove.py      # density-5/6 machine proof + honest residual
experiments/diophantine/certify.py     # Euler / polynomial Diophantine family certifier
experiments/covering/cover.py          # Erdős 1950 covering + enumeration
experiments/reproduce_all.py           # one-command driver -> REPRODUCIBILITY_MANIFEST.json
tests/test_*_invariants.py             # counterexample-bearing invariant tests
docs/PARADIGM.md §5.1                   # design note (Proof Tier + learning-gate wiring)
```

## Requirements

- Python **3.9+** (`math.lcm`, `math.isqrt`).
- `pip install -e .` for the evidence-chain timeline (`pydantic`, `cryptography`). The
  certifier core needs neither.

## Limitations (honest)

- **No new theorems.** Every mathematical statement is known; the reusable object is the
  falsifiability-gated pipeline.
- **`PROVEN` only in the decidable fragment** (identities / finite exact checks).
  Everything else is at best `CORROBORATED` (bounded — "no counterexample below `N`" is
  not "no counterexample").
- Provenance is agent-process tamper-evidence, not defense against the machine owner/root.
- Single machine, moderate compute — no computational-record claims.
