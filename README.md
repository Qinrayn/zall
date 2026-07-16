<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/zall-v0.2.1-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
    <img alt="zall" src="https://img.shields.io/badge/zall-v0.2.1-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
  </picture>
</p>

<p align="center">
  <em>A falsifiable, reproducible coding agent CLI — model-agnostic by design</em>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python ≥3.10"></a>
  <a href="https://github.com/qinrayn/zall/actions/workflows/test.yml"><img src="https://github.com/qinrayn/zall/actions/workflows/test.yml/badge.svg" alt="CI"></a>
  <a href="#"><img src="https://img.shields.io/badge/status-alpha-yellow" alt="Alpha status"></a>
  <br>
  <a href="#architecture">Architecture</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#core-concepts">Core Concepts</a> •
  <a href="#comparison">Comparison</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## zall — Intent before action. No hallucination.

zall is a **model-agnostic coding agent CLI** built from first principles. Unlike other agents that patch prompts to reduce hallucinations, zall has **falsifiability** and **reproducibility** in its architecture:

- Every tool call is **gate-checked** (whitelist / greylist / blacklist)
- Every session is **chain-hashed** and **replayable**
- Every evaluation metric is paired with an **anti-metric** (Goodhart-resistant)
- Fully **model-agnostic** — no SDK dependency in core

### Why another coding agent?

| Other agents | zall |
|---|---|
| Prompt-engineering to reduce hallucinations | **Architecture-enforced**: `stop_reason=STOP` with no `tool_calls` → hallucination flag |
| Session history as a log file | **Chain-hash timeline** (RunRecorder) + **Replay** without real model/tools |
| "Trust me, it passed" | **5-dimensional R-Metric** with anti-metric pairs |
| Cross-session context leaks | **Context cut**: no automatic cross-run history carry |
| Ad-hoc design | **DESIGN.md → IMPL.md → code** full traceability |

---

## Quick Start

```bash
# Install
pip install zall

# Set API key (OpenAI-compatible)
export ZALL_API_KEY="sk-..."
export ZALL_MODEL="gpt-4o"

# One-shot task
zall "refactor the auth module to use async"

# Initialize project config
zall init

# Interactive REPL
zall
> write a function that validates email addresses
> now add unit tests for it
> /eval          # evaluate all sessions
> /replay <id>   # replay a session (no model/tools needed)
> /sessions      # list history
> /resume <id>   # continue a session
> /cost          # show token usage
> /compact       # compress context (timeline preserved)
```

### Install from source

```bash
git clone https://github.com/qinrayn/zall.git
cd zall
pip install -e .
```

---

## Architecture

```
src/zall/
├── core/             # Primitives: model, action, gate, goal, safety, tool, verifiability
│   ├── loop.py       # AgentLoop orchestrator
│   ├── safety.py     # Three-state context_judge (whitelist/greylist/blacklist)
│   ├── gate.py       # ConfirmGate state machine
│   ├── verifiability.py  # RunRecorder (chain-hash) + TrustAnchor (ed25519)
│   ├── model.py      # ModelAdapter Protocol (no SDK dependency)
│   ├── goal.py       # Goal triple system + downgrade
│   ├── compactor.py  # Context compression (reactive, model-agnostic)
│   ├── memory.py     # Cross-session memory persistence
│   ├── checkpoint.py # File-state snapshot manager
│   ├── accountability.py  # Judge + Evidence system
│   └── events.py     # EventBus (pub/sub)
├── cli/              # CLI: rich renderer, responder, REPL, replay, commands
├── tools/            # Tools: read_file, write_file, edit_file, bash, grep, glob, …
├── adapters/         # Providers: OpenAI-compat, Anthropic, Gemini, Ollama
├── safety/           # Rule loader (TOML), config
├── mcp/              # MCP client & tool wrapper
├── eval/             # 5-dimensional R-Metric evaluation
└── skills/           # Skill loader & executor
```

---

## Core Concepts

### PR-0: No Hallucination (Architectural, not prompt-based)

The agent loop explicitly checks `stop_reason` vs `tool_calls`. If the model says "I read the file and it contains..." but `stop_reason=STOP` (no tool was called), the loop flags it as a hallucination. This is **architectural** — no amount of prompt engineering can bypass it.

### Three-State Philosophy

Every design primitive has exactly 3 states:
- `SafeLevel`: whitelist / greylist / blacklist
- `StopReason`: stop / tool_use / length
- `TerminationState`: met / not_met / undecidable

No 4-state over-engineering. "Unresolvable" is a sub-status of greylist, not a separate state.

### Chain-Hash Timeline (Reproducibility)

Every session is recorded as a chain-hash timeline (RunRecorder). Each event links to the previous via cryptographic hash. Sessions can be **replayed** without re-calling the model or executing real tools — making "did the agent actually do X?" a falsifiable question.

### Context Cut (§4.3)

The Context primitive does NOT carry tool call history across runs. This prevents context pollution and "hidden state" that makes debugging impossible. Users can explicitly resume sessions (`/resume`), but the agent cannot secretly carry state across runs.

### R-Metric Evaluation

Five dimensions, each paired with an anti-metric to prevent Goodhart effects:
- **goal_achievement** ↔ decline_rate
- **boundary_violation** ↔ proactivity
- **falsifiability** ↔ baseline_mutation
- **reproducibility** ↔ tamper_detected
- **resource_efficiency** ↔ shortcut_signal

### ConfirmGate (Three-Layer Safety)

Every tool call passes through:
1. **context_judge** — declarative rule engine → whitelist/greylist/blacklist
2. **ConfirmGate** — state machine: auto-execute / ask user / offer equivalence
3. **Override audit** — blacklist overrides are signed and recorded

---

## Comparison

### vs Claude Code / GitHub Copilot

| Feature | zall | Claude Code |
|---------|------|------------|
| Hallucination detection | Architectural (PR-0) | Prompt-based |
| Session reproducibility | Chain-hash + Replay | Log files only |
| Safety model | 3-layer gate (rules + confirm + override) | Implicit |
| Model independence | Protocol-based, 4 adapters | Anthropic-only |
| Evaluation | 5-dim R-Metric with anti-metrics | None |
| Context compression | Reactive, model-agnostic | Fixed window |
| Open source | ✅ MIT | ❌ Proprietary |

### vs Pi

| Feature | zall | Pi |
|---------|------|------|
| Falsifiability | PR-0 architecture | No |
| Goal system | GoalTriple + Downgrade | No |
| TUI | Rich REPL | Full diff-rendering TUI |
| Extensions | EventBus (basic) | ExtensionFactory + 13 hooks |
| Session tree | Linear | Tree (fork/clone/branch) |
| Skills | Full load | Progressive disclosure |
| MCP | Basic client | Namespaced + permissioned |
| Evaluation | R-Metric suite | Share-to-dataset |

---

## Supported Providers

- **OpenAI-compatible**: OpenAI, DeepSeek, GLM, Qwen, Yi, Together AI, Anyscale, Groq, Fireworks, etc.
- **Anthropic**: Claude (Opus, Sonnet, Haiku)
- **Google**: Gemini (Pro, Flash)
- **Ollama**: Local models (Llama, Mistral, Qwen, DeepSeek)

Simply set `ZALL_API_KEY` and `ZALL_MODEL` to get started. Provider auto-detection works for most endpoints.

---

## Commands

### Built-in slash commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model` | Switch model or list available |
| `/plan` | Toggle plan mode (read-only) |
| `/add` | Add file(s) to context |
| `/drop` | Remove file(s) from context |
| `/diff` | Show git working-tree diff |
| `/search` | Search the web |
| `/web` | Fetch a URL |
| `/git` | Run git commands (safe subcommands only) |
| `/commit` | Stage + commit with message |
| `/sessions` | List all sessions |
| `/resume` | Resume a session |
| `/eval` | Evaluate current session |
| `/replay` | Replay a session (no model needed) |
| `/cost` | Token usage and cost estimate |
| `/compact` | Compress context (preserves timeline) |
| `/undo` | Undo last tool effect |
| `/retry` | Retry last assistant response |
| `/skill` | List or run a skill |
| `/remember` | Save to project memory (AGENTS.md) |
| `/forget` | Remove from project memory |
| `/doctor` | Diagnose config / dependencies |
| `/checkpoint` | Manage file snapshots |
| `/revert` | Revert to a checkpoint |
| `/clear` | Clear conversation |

---

## Project Status

zall is in **active alpha** — the core architecture is solid and tested (840+ tests), but the UI/UX and extension system are still evolving.

### Roadmap

| Phase | Focus |
|-------|-------|
| Current (v0.2.0) | Core stability, bug fixes, open-source release |
| Next (v0.3.0) | Extension system, session tree (fork/clone), improved TUI |
| Future (v0.4.0+) | Progressive skill disclosure, evaluation data export, community plugins |

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for guidelines.

- **Bug reports**: [GitHub Issues](https://github.com/qinrayn/zall/issues)
- **Feature requests**: [Discussions](https://github.com/qinrayn/zall/discussions)
- **Code**: PRs welcome with DESIGN.md section references (see IPR-1 in [IMPL.md](IMPL.md))

### Development

```bash
# Setup
git clone https://github.com/qinrayn/zall.git
cd zall
pip install -e ".[dev,bs4]"

# Run tests
python -m pytest tests/

# Type check
python -m mypy src/zall/

# Lint
python -m ruff check src/zall/
```

---

## License

[MIT](LICENSE) © 2025 zall contributors