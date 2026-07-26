# Agent-Driven Erdős–Straus Exploration (Real Demo)

> A genuine "researcher uses zall" demo: deepseek-v4-flash drives the zall agent loop, autonomously writing a solver, running a search, recording hypotheses/evidence via the science tool, and reporting honestly.

## How this differs from `experiments/erdos_straus/`

That earlier campaign (`science_campaign.py`) called zall's `ScienceStore`/`Hypothesis` API **programmatically** — it tested the data structures and provenance, but bypassed the LLM-driven agent loop. **This** demo is the real thing: the agent itself decides and acts through tools.

## The run

- **Driver**: `zall` one-shot, model `deepseek-v4-flash` (SenseNova), 8 steps, 11 tool calls.
- **Agent actions** (all autonomous):
  1. `bash`: wrote `es_solve.py` (bounded search + integer verification, pure Python).
  2. `bash`: ran the sweep over n ∈ [2, 20000] → `es_results.jsonl` (19999 lines, ~63s).
  3. `science new_hypothesis`: H1 "For all 2≤n<20000, 4/n decomposes into 3 unit fractions."
  4. `science add_evidence`: support_rate=0.99995, supports=true, **with real file provenance** (protocol_hash, data_hash).
  5. Reported honestly.

## Result (honest, with a real finding)

| Metric | Value |
|---|---|
| Range | n ∈ [2, 20000) |
| Decompositions found | 19,998 / 19,999 |
| "Counterexample" | n = 1801 (none within the agent's search bounds) |
| Agent's diagnosis | **"likely a search-bound artifact, not a genuine counterexample"** |

**The agent was right.** Independent verification with a wider search finds n=1801 *does* decompose: `4/1801 = 1/451 + 1/270754 + 1/19992746114` (integer-verified). The agent's solver used `max_xy_ratio=10`, too tight for n=1801's solution (y ≈ 600×x). This is exactly how real research works: a negative result, an honest diagnosis, and an explanation — not a fake "solved it."

## What this demonstrates

- **Agent autonomy**: the model drove the full hypothesis → experiment → evidence loop via tool calls, not a script.
- **Science Kit end-to-end**: hypothesis + evidence with **real SHA-256 provenance** (verified to match actual files), stored in append-only JSONL.
- **Honesty (PR-0)**: the agent flagged its own negative result as a likely artifact rather than claiming a counterexample — falsifiable, not hallucinated.
- **Reproducibility**: `es_solve.py` and `es_results.jsonl` are real artifacts; provenance hashes let a third party confirm the same code produced the result.

## Reproduce

```bash
cd experiments/es_agent_demo
python es_solve.py   # regenerates es_results.jsonl deterministically
```

## Honest limitation

The agent's solver was less thorough than the hand-written one in `experiments/erdos_straus/` (it produced a false negative at n=1801). This is a genuine property of agent-driven exploration: the model's code isn't always optimal. The value is the **autonomous, traced, honest loop**, not mathematical optimality.
