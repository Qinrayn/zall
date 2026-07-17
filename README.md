<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/zall-v0.4.10-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
  <img alt="zall" src="https://img.shields.io/badge/zall-v0.4.10-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
</picture>

<p align="center">
  <em>A falsifiable, reproducible coding agent CLI — model-agnostic, engineering-grade</em>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python ≥3.10"></a>
  <a href="https://github.com/Qinrayn/zall/actions/workflows/ci.yml"><img src="https://github.com/Qinrayn/zall/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/zall/"><img src="https://img.shields.io/pypi/v/zall" alt="PyPI"></a>
  <a href="https://pypi.org/project/zall/"><img src="https://img.shields.io/pypi/dm/zall" alt="Downloads"></a>
  <a href="https://github.com/Qinrayn/zall/stargazers"><img src="https://img.shields.io/github/stars/Qinrayn/zall?style=flat" alt="GitHub Stars"></a>
  <br>
  <a href="#quick-start">Quick Start</a> •
  <a href="#features">Features</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#api-reference">API Reference</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## 📦 Installation

```bash
pip install zall
```

Requires Python 3.10+. For optional features:

```bash
pip install "zall[bs4]"      # web_fetch with BeautifulSoup HTML parsing
pip install "zall[images]"    # read_image with Pillow
pip install "zall[dev]"       # development tools (pytest, mypy, ruff)
pip install "zall[all]"       # everything
```

## 🚀 Quick Start

### 1. Set your API key

```bash
# OpenAI-compatible API
export ZALL_API_KEY="sk-..."
export ZALL_MODEL="gpt-4o"

# Or use Anthropic, Gemini, Ollama, or any OpenAI-compatible provider
export ZALL_PROVIDER="anthropic"
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 2. Run a task

```bash
# One-shot: fix a bug
zall "refactor the auth module to use async"

# One-shot with verbose output
zall "write a snake game in Python" --verbose

# Interactive REPL
zall
```

### 3. Interactive REPL

```
> /help                    # Show all commands
> /model gpt-4o            # Switch model
> /plan on                 # Read-only mode
> /lsp status              # Live code diagnostics
> /codegraph search MyClass  # Find symbols
> /sandbox process         # Isolated execution
> /eval                    # Evaluate sessions
> /replay <id>             # Replay a session
> /cost                    # Token usage
> /compact                 # Compress context
> /doctor                  # Diagnose setup
```

## ✨ Features

### 🧠 Code Intelligence

| Feature | Description |
|---------|-------------|
| **LSP Integration** | Live diagnostics, go-to-definition, hover info, completions. Supports pyright, typescript-language-server, rust-analyzer, gopls, clangd |
| **CodeGraph** | Multi-language symbol indexer for Python, JS, TS, Rust, Go, Java, C++, Ruby, PHP, Swift |
| **`code_understanding`** | Combine search + outline + read in one agent call |

### 🛡️ Safety & Reproducibility

| Feature | Description |
|---------|-------------|
| **PR-0 Hallucination Detection** | Architectural detection — `stop_reason=STOP` with no tool calls gets flagged |
| **Chain-hash Timeline** | Every session is cryptographically chained and replayable |
| **ConfirmGate** | Three-layer safety: rule engine + gate + override audit |
| **Sandbox** | Process isolation with worktree/process/bwrap/container modes |
| **ToolKind Classification** | 19 semantic tool kinds with read/write detection |

### 🔌 Extensibility

| Feature | Description |
|---------|-------------|
| **Plugin System** | Manifest-based plugins with git install, Python entry points |
| **21 Agent Tools** | read/write/edit/bash/grep/glob/list_dir/search/web_fetch/spawn_subagent + LSP + CodeGraph |
| **MCP Support** | Connect any MCP server (Model Context Protocol) |
| **AgentDefinition** | YAML-based agent profiles with toolset presets |
| **5 Toolset Presets** | `zall`, `explore`, `plan`, `codex`, `opencode` |

### 🎯 Agent Architecture

| Feature | Description |
|---------|-------------|
| **ChatState** | Actor-based message management with events, usage tracking, compaction |
| **AgentBuilder** | Fluent builder for AgentLoop construction |
| **Subagent** | Typed sub-agents (general-purpose, explore, plan) with capability isolation |
| **Self-Evolution** | Extension hooks for auto-learning, usage tracking, pattern discovery; `/suggest` and `/learn` commands for insight and application; high-confidence K-value auto-adjustment; cross-session learned memory injection |

## 🏗️ Architecture

```
zall/
├── core/              # Primitives: model, agent, chat_state, gate, goal, safety, tool
│   ├── loop.py        # AgentLoop orchestrator (synchronous main controller)
│   ├── loop_config.py # AgentConfig — unified configuration dataclass
│   ├── loop_events.py # LoopEvent, RunEgress, StepResult
│   ├── loop_errors.py # ToolNotFound, AgentRunaway, ContextLimitExceeded
│   ├── tool_kind.py   # ToolKind taxonomy — 19 semantic kinds
│   ├── policies.py    # CompactionPolicy, ReminderPolicy
│   ├── agent.py       # AgentDefinition + ToolsetPreset + CapabilityMode
│   ├── chat_state.py  # Actor-based message management
│   ├── safety.py      # Three-state context_judge (whitelist/greylist/blacklist)
│   ├── gate.py        # ConfirmGate state machine (8-state)
│   └── verifiability.py  # RunRecorder (chain-hash) + TrustAnchor (ed25519)
├── cli/               # Rich REPL, 25+ slash commands, replay, session management
├── tools/             # 21 tools: read/write/edit/bash/grep/lsp/codegraph/…
├── adapters/          # OpenAI-compat, Anthropic, Gemini, Ollama
├── codegraph/         # Multi-language symbol indexer
├── lsp/               # LSP client (pyright, rust-analyzer, gopls, clangd)
├── sandbox/           # Process isolation (worktree/process/bwrap/container)
├── plugin/            # Plugin system with marketplace
├── mcp/               # MCP client for Model Context Protocol
├── safety/            # Rule loader and config management
├── eval/              # 5-dimensional R-Metric evaluation
└── skills/            # Skill loader and executor
```

### Key Design Principles

1. **Model Agnostic (IPR-3)**: Core never imports model SDKs. `ModelAdapter` is a Protocol — adapters are pluggable.
2. **Immutable First**: All Pydantic models are `frozen=True`. `AgentConfig` is a frozen dataclass.
3. **Declarative Safety**: `context_judge` uses rule matching (fnmatch glob) — no model calls, no arbitrary code.
4. **Dual Safety Nets**: GitProtect (git stash) + CheckpointManager (filesystem snapshots).
5. **Full Audit Trail**: `RunRecorder` + chain-hash SHA-256 + `TrustAnchor` ed25519 signing.
6. **Self-Falsifying (PR-0)**: Architectural hallucination detection, not prompt-based.

## ⚙️ Configuration

### config.toml

Create `~/.zall/config.toml`:

```toml
[general]
default_model = "gpt-4o"
timeout = 300  # seconds (default: 300, was 120 in v0.4.2)

[openai]
api_key = "sk-..."
api_base = "https://api.openai.com/v1"

[anthropic]
api_key = "sk-ant-..."

[gemini]
api_key = "..."

[ollama]
api_base = "http://localhost:11434"
default_model = "llama3"
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ZALL_API_KEY` | API key (highest priority) |
| `ZALL_MODEL` | Model name override |
| `ZALL_PROVIDER` | Provider: `openai`, `anthropic`, `gemini`, `ollama` |
| `ZALL_API_BASE` | Custom API base URL |
| `ZALL_TIMEOUT` | Request timeout in seconds |
| `ZALL_VERBOSE` | Enable verbose output |
| `ZALL_PLAN_MODE` | Enable read-only plan mode |

### Agent Definition Files

Place `.md` files in `.zall/agents/` with YAML frontmatter:

```yaml
---
name: my-agent
description: Custom agent for Python development
toolset: zall
permission_mode: auto
model: gpt-4o
---
Your custom system prompt here...
```

## 📖 API Reference

### Python API

```python
from zall.core.builder import AgentBuilder
from zall.core.loop_config import AgentConfig
from zall.core.goal import GoalType
from zall.cli.orchestrator import run

# One-shot execution
egress = run(
    "refactor the auth module",
    model="gpt-4o",
    judge_mode="none",
    stream=True,
)

# Programmatic agent loop
loop = (
    AgentBuilder()
    .with_model(adapter)
    .with_tools(tools)
    .with_goal(goal)
    .with_config(AgentConfig(max_steps=30, stream=True))
    .build()
)
egress = loop.run(system_prompt="You are a coding assistant...")
```

### CLI Reference

```
zall [task] [options]

Options:
  --model TEXT       Model name (overrides config)
  --yes, -y          Auto-accept greylist actions
  --judge MODE       Judge mode: none (default), system
  --json             Output events as NDJSON
  --no-stream        Disable token streaming
  --max-steps N      Maximum steps before termination
  --init             Initialize .zall/ config in current directory
  --verbose          Show full tool output
  --version, -V      Show version

Commands in REPL:
  /help, /model, /plan, /lsp, /codegraph, /sandbox,
  /chatstate, /plugin, /add, /drop, /diff, /search,
  /web, /git, /commit, /sessions, /resume, /replay,
  /eval, /cost, /compact, /undo, /retry, /doctor,
  /checkpoint, /clear, /suggest, /learn
```

## 🆚 Comparison

### vs Claude Code / Copilot / Cursor

| Feature | zall | Claude Code | GitHub Copilot | Cursor |
|---------|------|-------------|----------------|--------|
| Hallucination detection | **Architectural** (PR-0) | Prompt-based | None | None |
| Reproducibility | Chain-hash + Replay | Log files only | None | None |
| Safety | 3-layer gate + audit | Implicit | None | Implicit |
| Model independence | 4 adapters (Protocol) | Anthropic-only | OpenAI/Gemini | OpenAI/Anthropic |
| Code intelligence | LSP + CodeGraph | Limited | Built-in | Built-in |
| Sandbox isolation | Process/Worktree modes | None | None | None |
| Plugin system | Manifest-based | None | Extensions | Extensions |
| Audit trail | Chain-hash + ed25519 | None | None | None |
| Open source | ✅ MIT | ❌ | ❌ | ❌ |
| Self-hostable | ✅ | ❌ | ❌ | ❌ |
| Local models | ✅ (Ollama) | ❌ | ❌ | ❌ |

## 🧪 Development

```bash
# Clone and install
git clone https://github.com/Qinrayn/zall.git
cd zall
pip install -e ".[dev,bs4,images]"

# Run tests
python -m pytest tests/ -q

# Type check
mypy src/zall/

# Lint
ruff check src/

# Build
python -m build
```

### Test Structure

The project follows **IPR-0**: every invariant has a counterexample test. Tests are organized by component:

```
tests/
├── test_loop_invariants.py          # AgentLoop core invariants
├── test_safety_invariants.py        # context_judge rules
├── test_gate_invariants.py          # ConfirmGate state machine
├── test_verifiability_invariants.py # Chain-hash timeline
├── test_read_file_invariants.py     # ReadFileTool invariants
├── test_bash_invariants.py          # BashTool invariants
├── test_chat_state_invariants.py    # ChatState actor
├── test_lsp_invariants.py           # LSP integration
├── test_sandbox_invariants.py       # Sandbox isolation
└── ...
```

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for guidelines.

- **Report bugs**: [github.com/Qinrayn/zall/issues](https://github.com/Qinrayn/zall/issues)
- **Feature requests**: Open an issue with the `enhancement` label
- **Pull requests**: PRs are reviewed within 48 hours
- **Security issues**: See [SECURITY.md](SECURITY.md)

## 📄 License

[MIT](LICENSE) © 2026 zall contributors

## 🙏 Acknowledgements

- **xAI Grok Build** — Architecture inspiration for agent definition, tool taxonomy, and modular design
- **Claude Code** — Interaction design patterns
- **OpenAI Function Calling** — API compatibility
- **MCP Specification** — Model Context Protocol integration