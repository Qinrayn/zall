# Erdős–Straus: A Machine-Verified Covering-System Proof (density 5/6)

> A Proof Gate (Popperian-Gate formal refinement) dogfood run through zall's
> `core/proof_gate.py` → covering system → certificates → provenance → chain-hashed
> timeline. The companion to `REPORT.md` (which only *corroborated* within a bounded
> interval); this report *proves* an infinite family and is honest about the residual.

## ⚠️ Honesty Statement (PR-0 self-falsifiability)

The Erdős–Straus conjecture is an **open problem**: for every integer `n ≥ 2`, there
exist positive integers `x, y, z` with `4/n = 1/x + 1/y + 1/z`.

**This does NOT prove the conjecture.** It **proves** it for a set of integers of
**natural density 5/6** — every even `n`, every multiple of 3, and every `n ≡ 3 (mod 4)` —
via three parametric identities, each **machine-verified by exact integer arithmetic**
(no Lean, no external prover). The residual `{n ≡ 1, 5 (mod 12)}` (density 1/6) is
honestly reported as **CORROBORATED** (bounded search, no counterexample), **not proven**.

Unlike a bounded search, a proven identity settles **infinitely many `n` at once**.

## Result Summary

| Item | Value |
|---|---|
| Identities checked | 3 |
| Identities `PROVEN` | 3 / 3 |
| **Proven density (exact ∀-proof)** | **10/12 = 5/6** |
| Covered residues (mod 12) | `{0,2,3,4,6,7,8,9,10,11}` |
| Residual residues (mod 12) | `{1,5}` → **CORROBORATED**, not proven |
| Residual search bound | `n < 20000` |
| Residual `n` checked | 3333 |
| Residual counterexamples | 0 |
| Timeline chain verified | `true` |

## The three proven identities (each an exact ∀-proof)

All three are verified by [`verify_egyptian_identity`](file:///c:/Users/云丘/zall/src/zall/core/proof_gate.py):
cross-multiply `1/X + 1/Y + 1/Z = 4/(qk+r)` to a polynomial identity, then check the
**difference polynomial is identically zero** (exact coefficient comparison — a proof, not
sampling) and that `X, Y, Z` are positive integers for all `k ≥ 0`.

1. **Even** `n = 2k+2` (let `m = n/2`):
   `4/n = 2/m = 1/m + 1/(m+1) + 1/(m(m+1))`. Covers `n ≡ 0 (mod 2)`.

2. **Multiple of 3** `n = 3k+3` (let `s = n/3`):
   `4/n = 3/n + 1/n = 1/s + 1/(n+1) + 1/(n(n+1))`. Covers `n ≡ 0 (mod 3)`.

3. **`n ≡ 3 (mod 4)`** `n = 4k+3` (let `x = (n+1)/4`, `M = n·x`):
   `4/n = 1/x + 1/M = 1/x + 1/(M+1) + 1/(M(M+1))`. Covers `n ≡ 3 (mod 4)`.

Union of the covered residues mod `lcm(2,3,4)=12` is `{0,2,3,4,6,7,8,9,10,11}` — everything
except `{1,5}`. Hence **density 5/6** is proven.

> During development the Proof Gate **REFUTED** a first, mis-transcribed polynomial for
> identity (3) — the difference polynomial was non-zero. The checker falsifies wrong
> certificates exactly as designed (IPR-0). Only the corrected identity earned `PROVEN`.

## The residual `{n ≡ 1, 5 (mod 12)}` — why it is only CORROBORATED

No **single** polynomial (Egyptian) identity covers these classes. Taking the first term
`x = (n+3)/4` (integer for `n ≡ 1 mod 4`) leaves a remainder `3/(nx)`; splitting it into two
unit fractions requires a multiplier that **grows with `n`** (e.g. `4/5` needs `1/4+1/20`,
`4/17` needs `1/30+1/510`), so no fixed identity in `k` closes the class. This is precisely
where the decidable-identity fragment ends — and where genuine new mathematics, or a proof
assistant to formalise a hypothetical proof, would be required. We therefore report the
residual as **CORROBORATED** with its bound, never as proven.

## Epistemic Tiers (the framework contribution)

zall's Popperian Gate previously collapsed *corroborated* and *proven*: a bounded search set
`H1 = CONFIRMED` and the report bolted on "not a proof" by hand. The new
[`VerificationTier`](file:///c:/Users/云丘/zall/src/zall/core/proof_gate.py) makes the
distinction machine-enforced:

| Tier | Here | Granted by |
|---|---|---|
| `PROVEN` | density 5/6 (3 identities) | Proof Gate (exact arithmetic) |
| `CORROBORATED` | residual `{1,5 mod 12}`, `n<20000` | bounded search |
| `REFUTED` | (none found) a counterexample would land here | sandbox / search |
| `UNKNOWN` | — | — |

A self-improving loop can now refuse to distil a `CORROBORATED` universal as if it were
`PROVEN` — closing the reward-hacking surface `PARADIGM.md §5` warns about.

## Evidence Chain (real, reproducible)

From [`results/PROOF_REPORT.json`](file:///c:/Users/云丘/zall/experiments/erdos_straus/results/PROOF_REPORT.json)
(all SHA-256, verified to match):

| Field | Hash / value |
|---|---|
| protocol_hash (proof_gate.py + prove.py + solve.py) | `sha256:41225af9…dddcbf4a` |
| data_hash (deterministic result payload) | `sha256:0402572a…407b14f6` |
| analysis_code_hash (prove.py) | `sha256:8e2f31e5…89310cf4` |
| environment_hash (python + platform) | `sha256:c2a66f1d…5e6ece45` |
| timeline_anchor | `anchor_ack_1784898718661_1` |
| chain_verified | `true` |

The provenance is anchored to a chain-hashed [`RunRecorder`](file:///c:/Users/云丘/zall/src/zall/core/verifiability.py)
timeline (the same Verifiability mechanism as `REPORT.md`). The **hashed payload excludes
wall-time**, so `data_hash` is reproducible: two runs at the same bound produced identical
`data_hash` (verified during the campaign).

## Reproducibility

```bash
cd experiments/erdos_straus
python prove.py --residual-bound 20000
```

Deterministic: identity certificates (exact arithmetic), covered/residual residues, residual
search counts, and `protocol_hash` / `data_hash` / `environment_hash` all reproduce.

## What this demonstrates about zall

- **Proof Tier primitive** ([`core/proof_gate.py`](file:///c:/Users/云丘/zall/src/zall/core/proof_gate.py)):
  a machine-checkable proof gate for the decidable fragment, IPR-3-clean (stdlib only), with
  IPR-0 invariant tests (a false identity **must** be `REFUTED`).
- **Grounded refutation vs. grounded proof**: `sandbox_verifier` grounds *refutation*;
  the Proof Gate grounds *universal proof* for the decidable fragment — the two halves of
  Popper made machine-distinct.
- **Honest boundary**: 5/6 proven, 1/6 corroborated, and a stated reason the residual resists
  a single identity. No overclaim.

## Is Lean necessary?

- **Decidable fragment** (rational-function identities, finite instance checks): **No.** Exact
  Python arithmetic gives a machine-checkable universal proof — demonstrated here (density 5/6,
  zero external tooling).
- **Empirical / algorithmic claims** ("code passes tests", "no counterexample in `[2,N)`"):
  Lean is the wrong tool; sandbox execution is the correct grounded verifier.
- **Lean earns its place only** when a claim leaves the decidable fragment (needs
  induction / lemmas / case analysis) — e.g. a hypothetical proof of the residual classes.
- **Architecture**: never a core dependency (breaks IPR-3 / offline / model-agnostic). Add it
  as an **opt-in** `Prover` adapter behind the `Prover` Protocol in `proof_gate.py`, alongside
  the pure-Python identity prover — invoked only when a claim needs it.

## Limitations (honest)

- 5/6 is genuinely **proven**; the conjecture **as a whole remains open**.
- The residual is **corroborated only up to the search bound**; "none within bounds" ≠ "no
  decomposition exists". A counterexample would be a major result and flip the tier to `REFUTED`.
- The covering system here is intentionally small (3 identities). Larger classical covering
  systems shrink the residual further but never to empty — that is the mathematical frontier.
