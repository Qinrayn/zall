# Falsifiability-Gated Discovery: Machine-Checkable Epistemic Tiers for Self-Improving Agents

**Draft — workshop/short-paper length. Author: Yuhan Zhang (张宇涵). Status: reproducible artifact + honest positioning.**

---

## Abstract

Self-improving agents that learn from their own outputs are safe only where
outcomes are *verifiable*; where the reward signal is not grounded, self-improvement
degenerates into reward-hacking. We argue that a missing primitive in current
self-improving-agent designs is an **explicit epistemic gate** that a machine can
enforce — one that distinguishes a claim that has merely been *corroborated* on
finitely many instances from one that has been *proven* for all instances.

We present the **Proof Gate**: a first-class `VerificationTier`
(`REFUTED` / `CORROBORATED` / `PROVEN` / `UNKNOWN`) together with three
dependency-free (Python standard library only) **decidable-fragment certifiers** —
for (i) rational-function identities, (ii) perfect-square polynomials, and
(iii) covering systems — each of which emits a machine-checkable certificate for a
*universal* claim and, on false input, a **grounded counterexample**. Certificates
are bound to SHA-256 provenance of the verifier, the prover, and the environment,
and anchored to a chain-hashed timeline, so every result is independently
re-checkable and tamper-evident. The gate is **wired into the agent's persistent,
cross-session experience store** (its learning gate): only `PROVEN` (or explicitly-
bounded `CORROBORATED`) items are distilled as reusable skills, `REFUTED` never, and a
`CORROBORATED` item is never promoted to or rendered as `PROVEN` — a machine-enforced
wall against reward-hacking, backward-compatible and pinned by counterexample tests.

We demonstrate the gate end-to-end on four scenarios: three classical mathematical
problems (a machine-checked density-5/6 proof of the Erdős–Straus conjecture;
certification of Euler’s infinite Diophantine quadruple family; verification of
Erdős’s 1950 covering system) and one **real agent scenario** (a code-generation
output that passes bounded tests but fails on edge cases — the gate structurally
prevents it from being distilled as a proven skill, while a genuinely-proven
identity is correctly admitted).
We are explicit that **the mathematics in these demonstrations is known** — the
contribution is the *epistemic discipline* and the *reproducible, self-falsifying
pipeline*, not new theorems. Finally, we show that a proof assistant (Lean/Coq) is
**unnecessary** for the decidable fragment and argue it belongs only as an opt-in
adapter behind a narrow prover interface.

---

## 1. Motivation: verifiability is the bottleneck, not capability

A recurring empirical finding across recent work on self-improving code and math
agents is that self-improvement *only* works where the outcome is verifiable, and
that on weakly-verifiable objectives the agent learns to game its own reward. The
practical consequence is that the binding constraint on an open-endedly
self-improving agent is not the model or the loop; it is the **reward (verification)** (Amodei et al., 2016; Skalse et al., 2022; Silver & Sutton, 2025).

There is a sharper, older reason this must be so. For a *universal* claim
`∀n. P(n)`:

- **Refutation** requires a single counterexample. This is decidable and cheap:
  run the candidate, observe a failure. A sandbox executing real code is a correct
  grounded refuter.
- **Confirmation** can never be obtained from finitely many executions. This is
  Hume's problem of induction (Hume, 1739), and Popper's response (Popper, 1959): no number of passing tests
  *proves* a universal; it only *corroborates* it within the tested bound.

Most agent stacks blur these two. A bounded search that finds no counterexample is
recorded as "confirmed", and the caveat "not a proof" is appended by a human, in
prose, if at all. The machine's own state does not distinguish *corroborated-within-
bound* from *proven*. That blur is exactly the surface on which a self-improving
loop reward-hacks: a bounded corroboration, distilled as a reusable "skill" or a
`CONFIRMED` fact, silently becomes a false universal.

**Claim of this paper.** The fix is a small, first-class primitive: an *epistemic
tier* that the machine assigns and enforces, and, for the fragment where universal
claims *are* decidable, a set of certifiers that emit machine-checkable proofs.
This is cheap to build, model-agnostic, and closes a concrete reward-hacking hole.

---

## 2. The epistemic gate

We introduce a four-valued status attached to every claim a learning agent might
retain:

| Tier | Meaning | Granted by |
|---|---|---|
| `REFUTED` | a counterexample has been exhibited | execution / search (decidable) |
| `CORROBORATED` | survived bounded testing; **not** proven (carries the bound `N`) | sandbox / bounded search |
| `PROVEN` | a machine-checkable certificate for the universal claim exists | Proof Gate |
| `UNKNOWN` | none of the above | — |

The single hard invariant is a **wall between `CORROBORATED` and `PROVEN`**: a
bounded corroboration may never be promoted to, distilled as, or substituted for a
proof. In a continual-learning agent whose memory gate admits only `PROVEN` (or
suitably-flagged `CORROBORATED`) items, this wall is what prevents a false universal
from compounding across sessions.

This tier is deliberately *orthogonal* to the model. It is computed by
deterministic, offline code and is therefore itself auditable.

### 2.1 Integration into the learning loop

The gate is not a standalone checker; it is wired into the agent's persistent,
cross-session experience store — the component that decides what the agent *retains*
and *reuses*. Concretely: a recorded claim now carries a tier; `proven_skills()`
returns only `PROVEN` items (a `CORROBORATED` item is structurally excluded — the
wall); the recall context injected into prompts labels each item `[PROVEN]` or
`[CORROBORATED n<N]`, so a bounded corroboration cannot masquerade as a proof to the
model; `REFUTED` items are never recalled; and a bridge maps a `ProofCertificate`
directly into the store by its tier. The change is backward-compatible with the
pre-existing boolean gate and is pinned by counterexample-bearing invariant tests
(a corroborated item must never enter the proven set).

---

## 3. Decidable-fragment certifiers

A universal claim is *decidable* when it can be reduced to a finite, exact check.
For three such fragments we implement certifiers in the Python standard library
only (integer/`fractions` arithmetic; no CAS, no SMT, no proof assistant). Each
certifier is **self-falsifying**: given a false claim it returns `REFUTED` with an
explicit witness, and this property is pinned by counterexample-bearing invariant
tests.

### 3.1 Rational-function identities

To prove that an Egyptian-fraction identity holds for an entire arithmetic
progression, e.g.

```
∀k ≥ 0:  4 / (q·k + r) = 1/X(k) + 1/Y(k) + 1/Z(k),
```

we cross-multiply to a single polynomial identity and check that the **difference
polynomial is identically zero** by exact coefficient comparison. Two rational
functions are equal iff this numerator vanishes as a polynomial; no sampling is
involved, so the check is a proof, not evidence. Positivity/integrality of the
denominators for all `k ≥ 0` is certified by a sufficient condition (non-negative
coefficients with positive constant term). A non-vanishing difference polynomial
yields `REFUTED`.

### 3.2 Perfect-square polynomials

A polynomial family of Diophantine `m`-tuples requires "`a(t)·b(t) + n` is a perfect
square for all integer `t`". By the Davenport–Lewis–Schinzel phenomenon (Davenport, Lewis & Schinzel, 1964), a
polynomial that is a perfect square at all large integers is the square of a
polynomial; so it suffices to compute an **exact integer polynomial square root**
`Q` with `Q² = P` (determined top-down, then verified). If `P` is not a square
polynomial, the certifier searches small `t` for an explicit value `P(t)` that is
not a perfect square and returns `REFUTED` with that `t` — a grounded counterexample
that does not appeal to the theorem above.

### 3.3 Covering systems

A covering system `{a_i (mod m_i)}` covers every integer. "Every integer is covered"
is decidable: it holds iff **every residue modulo `L = lcm(m_i)` is covered**. The
certifier returns `PROVEN` when all residues are covered, and otherwise `REFUTED`
with the smallest uncovered residue `r` (the integer `r` is a witness of
non-covering). Covering systems are the classical engine behind Romanov-/Erdős-type
non-representability results, so this certifier opens a third demonstration area.

Each certifier is exact, deterministic, and reduces a universal statement to a
finite computation — the operational meaning of "decidable fragment".

---

## 4. Provenance and reproducibility

A certificate is only as trustworthy as the pipeline that produced it. Every run
records a provenance record with SHA-256 hashes of (a) the certifier source, (b) the
prover/experiment source, (c) the result payload, and (d) an environment snapshot.
The provenance hash is appended to a **chain-hashed timeline** (each event carries
the SHA-256 of the previous), and the tail is signed/anchored, making the record
tamper-evident and independently re-checkable.

Two engineering points matter for reproducibility:

1. **The hashed payload excludes wall-time and other nondeterministic fields.** Two
   runs at the same parameters therefore produce an identical `data_hash`. We verify
   this empirically (identical `data_hash` across runs).
2. **All arithmetic is exact.** No floating point enters any certificate; identities
   are decided over `ℤ`/`ℚ`.

---

## 5. Demonstrations (known mathematics, exact machine checks)

We stress up front: none of the following is a new theorem. Each is a *reproducible,
machine-checked* artifact chosen to exercise a certifier and the epistemic gate.

### 5.1 Erdős–Straus: a density-5/6 machine proof with an honest residual

Using the rational-identity certifier (§3.1) we prove three parametric identities:

- even `n = 2k+2`: `4/n = 1/(n/2) + 1/(n/2+1) + 1/((n/2)(n/2+1))`;
- `3 | n`, `n = 3k+3`: `4/n = 1/(n/3) + 1/(n+1) + 1/(n(n+1))`;
- `n ≡ 3 (mod 4)`, `n = 4k+3`: `4/n = 1/((n+1)/4) + 1/(M+1) + 1/(M(M+1))`, `M = n(n+1)/4`.

Their union covers every residue mod 12 except `{1, 5}`, i.e. a set of integers of
natural **density 5/6**, each `n` settled by an exact `∀k` certificate. The residual
`{n ≡ 1, 5 (mod 12)}` is reported as `CORROBORATED` up to a search bound (no
counterexample), never `PROVEN`; we note that no single polynomial identity can
cover it (the required Egyptian-split multiplier grows with `n`). During
development the certifier `REFUTED` a mis-transcribed candidate identity — the gate
catching a human error, as designed.

### 5.2 Diophantine quadruples: certifying Euler's family

Using the square-polynomial certifier (§3.2) we certify Euler's family

```
{t, t+2, 4t+4, 16t³+48t²+44t+12}   (at t=1: Fermat's {1,3,8,120})
```

as a `D(1)`-quadruple family for all `t`: all **6 of 6** pairwise products `+1` are
`PROVEN` perfect-square polynomials (e.g. `a·d+1 = (4t²+6t+1)²`). An exhaustive
search over linear entries returns a **single** linear `D(1)`-triple family,
`{t, t+2, 4t+4}`, and nothing else — an independent exact corroboration of the
Dujella–Fuchs theorem that all polynomial `D(1)`-quadruples are regular.

### 5.3 Covering systems: Erdős 1950 and near-miss

Using the covering certifier (§3.3): the Erdős (1950) system
`{0(2), 0(3), 1(4), 3(8), 7(12), 23(24)}` is `PROVEN` (covers all residues mod 24);
dropping its last congruence yields `REFUTED` with uncovered residue `23`; and an
exhaustive enumeration over the distinct-modulus set `{2,3,4,6,12}` (1728
assignments) finds `24` covering systems, each machine-verified. We explicitly do
**not** attempt the open Erdős–Selfridge odd-covering problem: an exhaustive search
there is infeasible, which is precisely why it is open.

---

### 5.4 Agent scenario: code generation with edge-case failure

To address the concern that the demonstrations above are purely mathematical, we
include a **concrete code-generation scenario**. A naive `is_prime(n)` implementation
(`all(n%i!=0 for i in range(2,n))`) is proposed by a simulated Blue agent. The
`SandboxVerifier` runs it against bounded test cases (primes and composites in
[2, 20]) — it **passes all** (score 1.0). However, the same function **fails** on
edge cases: `is_prime(1)` returns `True` (because `range(2,1)` is empty, so `all()`
vacuously returns `True`).

Without the epistemic gate (boolean `verified=True`), this function would enter the
skill pool as if universally correct. On a future task requiring edge-case handling,
the agent would blindly reuse it — a concrete reward-hacking degradation.

With the `VerificationTier` gate: the function is recorded as `CORROBORATED(bound=20)`.
`proven_skills()` structurally **excludes** it (the wall). The recall context labels it
`[CORROBORATED n<20]` — the model sees the bound and has the information to not
blindly trust it outside the tested range. In contrast, a mathematical identity
(e.g. the Erdős–Straus even-class proof) correctly earns `PROVEN` and enters
`proven_skills()`.

This demonstrates that the gate operates on **real agent outputs** (code), not
only on mathematical identities, and that the `CORROBORATED`/`PROVEN` distinction
prevents a concrete, reproducible failure case.

---

## 6. On the necessity of a proof assistant (Lean/Coq)

A natural question is whether these certificates should be produced or checked by a
proof assistant. Our position, evidenced by §5:

- **Decidable fragment (identities, finite instance checks): a proof assistant is
  unnecessary.** Exact standard-library arithmetic already yields a machine-checkable
  universal proof, while remaining offline, model-agnostic, and dependency-free.
- **Empirical / algorithmic claims** ("this code passes these tests", "no
  counterexample in `[2, N)`"): a proof assistant is the *wrong* tool; a sandbox
  executing the artifact is the correct grounded verifier.
- **A proof assistant earns its place only** when a claim leaves the decidable
  fragment — when it needs induction, lemmas, or case analysis not reducible to a
  single finite check.

Accordingly, a proof assistant should **not** be a core dependency. We expose a
narrow `Prover` interface; the pure-Python certifiers implement it, and a Lean/Coq/
SMT backend can be added as an **opt-in adapter** behind the same interface, invoked
only for claims that require it.

---

## 7. Related work

**Concurrent 2026 work on falsifiability/certificates for AI agents.** Several
independent efforts in 2026 address related themes, which we cite and differentiate:

- Soni (2026), *Falsifiable Release Gates for Self-Improving Systems* (arXiv:2607.13070):
  proposes pre-declared machine-checkable acceptance criteria ("standing invariants")
  for self-improving system releases. Key difference: Soni’s gates operate at
  **release time** (deployment), are binary pass/fail (no epistemic hierarchy), and
  rely on pre-declared tests (≈ our `CORROBORATED` tier). We operate at **learning/
  distillation time**, distinguish four epistemic tiers, and provide exact
  decidable-fragment **proofs** (not just bounded tests) for the `PROVEN` tier.
- Yanglet, Liu, Wang & Capponi (2026), *No Certificate, No Execution: Certified
  Traces* (arXiv:2605.24462): requires per-action certificates at the **execution
  layer** ("computable action → permissible action"). We gate at the **knowledge
  retention layer** (what the agent distils and reuses across sessions).
- ICLR 2026 P-AGI Workshop, *Post-AGI Systems Need a “Truth Stack”*: a position
  paper calling for epistemic guarantees and scalable verification. We provide a
  concrete, working implementation of one such stack (narrow but exact).

These works confirm that falsifiability-gated AI is a timely concern; our
differentiation is the **four-tier epistemic hierarchy**, **exact decidable-fragment
proofs** (not just testing), and the **learning-gate integration** (distillation,
not deployment or execution).

Prior established work:

- **Verifier-guided search / test-time compute** (e.g. AlphaProof; Google DeepMind, 2024):
  use a verifier as ground truth to guide search. We share the "verifier as ground
  truth" stance but focus on a lightweight, model-agnostic gate and on the *epistemic
  status* attached to retained knowledge.
- **Proof assistants and certified computation** (e.g. Lean; de Moura & Ullrich, 2021): provide the gold standard for
  machine-checked proof. We argue for a graduated approach where the cheapest
  adequate certifier is used, and a proof assistant is reserved for the
  non-decidable case.
- **Self-improving agents and reward-hacking** (Amodei et al., 2016; Skalse et al., 2022; Zelikman et al., 2022; Silver & Sutton, 2025): motivate the need for a grounded
  reward; we contribute a concrete, enforceable primitive (the tier wall) that makes
  "only distil the verifiable" a machine invariant rather than a guideline.
- **The specific mathematics** (Erdős–Straus covering identities — Mordell, 1969, Elsholtz & Tao, 2013; Dujella–Fuchs
  polynomial Diophantine tuples — Dujella & Fuchs, 2004; Erdős covering systems — Erdős, 1950, Hough, 2015) is classical and cited here
  only as substrate for the pipeline.

---

## 8. Limitations (honest)

- **No new theorems.** Every mathematical statement demonstrated is known; the
  contribution is methodological (the tier discipline and the reproducible,
  self-falsifying certifier pipeline).
- **Decidable fragment only.** The `PROVEN` tier is reachable exactly where a
  universal claim reduces to a finite exact check. Everything else is at best
  `CORROBORATED`.
- **Corroboration is bounded.** "No counterexample below `N`" is not "no
  counterexample"; a later counterexample flips the tier to `REFUTED`.
- **Provenance threat model is modest** (agent-process tamper-evidence, not defense
  against the machine owner or root).
- **Single-machine, moderate compute.** We make no computational-record claims.

---

## 9. Reproducibility

All three demonstrations are single-command and deterministic:

```bash
# Erdős–Straus density-5/6 proof + honest residual
python experiments/erdos_straus/prove.py --residual-bound 20000

# Diophantine quadruple family certification
python experiments/diophantine/certify.py --coef-max 6

# Covering-system certification
python experiments/covering/cover.py
```

Invariant tests (each with counterexamples) pin the certifiers:

```bash
python -m pytest tests/test_proof_gate_invariants.py \
                 tests/test_proof_gate_square_invariants.py \
                 tests/test_covering_invariants.py -q
```

Representative machine-checked outcomes (see each run's `*_REPORT.json` for full,
hash-anchored records):

| Demonstration | Result | Tier |
|---|---|---|
| Erdős–Straus, 3 identities | covers `{0,2,3,4,6,7,8,9,10,11} mod 12` → density 5/6 | `PROVEN` |
| Erdős–Straus residual | `{1,5} mod 12`, no counterexample `< 20000` | `CORROBORATED` |
| Euler `D(1)`-quadruple family | 6/6 pairwise `ab+1` perfect-square polynomials | `PROVEN` |
| Linear `D(1)`-triples (exhaustive) | unique family `{t, t+2, 4t+4}` | — |
| Erdős 1950 covering | covers all residues mod 24 | `PROVEN` |
| Erdős 1950 minus one congruence | residue 23 uncovered | `REFUTED` |

---

## 10. Conclusion

The bottleneck for self-improving agents is verification, and the specific missing
piece is an **enforceable distinction between corroboration and proof**. We showed
this can be added cheaply: a four-valued epistemic tier with a hard wall, plus
dependency-free certifiers that emit machine-checkable proofs for the decidable
fragment and grounded counterexamples otherwise, all under cryptographic provenance.
A proof assistant is not required for this fragment and is best reserved, as an
opt-in adapter, for claims that genuinely leave it. The demonstrations are of known
mathematics by design; the reusable object is the falsifiability-gated pipeline.

---

## Acknowledgments

Built within the zall project (a falsifiable-experience-machine agent framework). The
mathematics used in the demonstrations is classical and due entirely to the cited
authors; the contribution here is the falsifiability-gated pipeline and its epistemic
discipline.

## References

*Bibliographic details verified against primary sources where practical; confirm final
page/venue metadata before submission.*

- Amodei, D., Olah, C., Steinhardt, J., Christiano, P., Schulman, J., & Mané, D. (2016).
  Concrete Problems in AI Safety. arXiv:1606.06565.
- Davenport, H., Lewis, D. J., & Schinzel, A. (1964). Polynomials of certain special
  types. *Acta Arithmetica*, 9, 107–116.
- de Moura, L., & Ullrich, S. (2021). The Lean 4 Theorem Prover and Programming
  Language. *CADE-28*.
- Dujella, A. (2024). *Diophantine m-tuples and Elliptic Curves*. Springer.
- Dujella, A., & Fuchs, C. (2004). Complete solution of the polynomial version of a
  problem of Diophantus. *Journal of Number Theory*, 106, 326–344.
- Elsholtz, C., & Tao, T. (2013). Counting the number of solutions to the Erdős–Straus
  equation on unit fractions. *J. Australian Math. Soc.*, 94(1), 50–105. (arXiv:1107.1010.)
- Erdős, P. (1950). On integers of the form 2^k + p and some related problems.
  *Summa Brasiliensis Mathematicae*, 2, 113–123.
- Google DeepMind (2024). AI achieves silver-medal standard solving International
  Mathematical Olympiad problems (AlphaProof & AlphaGeometry 2).
- Guo, S., & Sun, Z.-W. (2005). On odd covering systems with distinct moduli.
  *Advances in Applied Mathematics*, 35(2). (arXiv:math/0412217.)
- Hough, B. (2015). Solution of the minimum modulus problem for covering systems.
  *Annals of Mathematics*, 181(1), 361–382.
- Hume, D. (1739). *A Treatise of Human Nature*.
- Mordell, L. J. (1969). *Diophantine Equations*. Academic Press.
- Popper, K. (1959). *The Logic of Scientific Discovery*. Hutchinson.
- Silver, D., & Sutton, R. S. (2025). The Era of Experience. (Position paper.)
- Skalse, J., Howe, N. H. R., Krasheninnikov, D., & Krueger, D. (2022). Defining and
  Characterizing Reward Hacking. *NeurIPS 2022*. arXiv:2209.13085.
- Sun, Z.-W. (2007). A connection between covers of the integers and unit fractions.
  *Advances in Applied Mathematics*, 38(2), 267–274.
- Sutton, R. S. (2019). The Bitter Lesson. (Essay.)
- Vaughan, R. C. (1970). On a problem of Erdős, Straus and Schinzel. *Mathematika*,
  17, 193–198.
- Zelikman, E., Wu, Y., Mu, J., & Goodman, N. D. (2022). STaR: Bootstrapping Reasoning
  with Reasoning. *NeurIPS 2022*. arXiv:2203.14465.

*Concurrent 2026 works (cited in §7):*

- ICLR 2026 P-AGI Workshop (2026). Post-AGI Systems Need a “Truth Stack,” Not Just
  Better Models. OpenReview Submission 34.
- Soni, D. (2026). Falsifiable Release Gates for Self-Improving Systems: Standing
  Invariants at Scale. arXiv:2607.13070.
- Yanglet, X.-Y. Liu, X. Wang, & A. Capponi (2026). No Certificate, No Execution:
  Certified Traces as a Foundation for Trustworthy AI Agents. arXiv:2605.24462.

---

### Appendix A. Certifier interfaces (as implemented)

- `verify_egyptian_identity(q, r, X, Y, Z) -> ProofCertificate` — §3.1
- `verify_square_identity(P) -> ProofCertificate`, `poly_sqrt(P) -> Poly | None` — §3.2
- `verify_covering_system([(a_i, m_i)]) -> ProofCertificate` — §3.3
- `VerificationTier ∈ {REFUTED, CORROBORATED, PROVEN, UNKNOWN}`; helpers
  `corroborated(claim, bound)`, `refuted(claim, counterexample)`
- `Prover` protocol (`name`, `prove(claim) -> ProofCertificate`) — opt-in backend seam

All in a single ~450-line, standard-library-only module, pinned by 33
counterexample-bearing invariant tests.
