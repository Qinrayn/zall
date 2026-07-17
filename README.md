<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/zall-v0.4.1-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
    <img alt="zall" src="https://img.shields.io/badge/zall-v0.4.1-blue?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e">
  </picture>
</p>

<p align="center">
  <em>A falsifiable, reproducible coding agent CLI — model-agnostic, engineering-grade</em>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python ≥3.10"></a>
  <a href="https://github.com/Qinrayn/zall/actions/workflows/ci.yml"><img src="https://github.com/Qinrayn/zall/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/zall/"><img src="https://img.shields.io/pypi/v/zall" alt="PyPI"></a>
  <br>
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#features">Features</a> •
  <a href="#commands">Commands</a> •
  <a href="#comparison">Comparison</a>
</p>

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

# Interactive REPL
zall
> /help
> /lsp status          # live code diagnostics
> /codegraph index     # build symbol index
> /codegraph search MyClass  # find symbols
> /sandbox process     # isolated execution
> /eval                # evaluate sessions
> /replay <id>         # replay a session
```

---

## Features

### 🧠 Code Intelligence
- **LSP Integration** — live diagnostics, go-to-definition, hover info, completions. Supports pyright, typescript-language-server, rust-analyzer, gopls, clangd.
- **CodeGraph** — multi-language symbol indexer for Python/JS/TS/Rust/Go/Java/C++/Ruby/PHP/Swift. Search, outline, and navigate your codebase.
- **`code_understanding` tool** — combine search + outline + read in one agent call.

### 🛡️ Safety & Reproducibility
- **PR-0**: Architectural hallucination detection — `stop_reason=STOP` with no `tool_calls` → flagged.
- **Chain-hash timeline** — every session is cryptographically chained and replayable.
- **ConfirmGate** — three-layer safety (rule engine + gate + override audit).
- **Sandbox** — process isolation with worktree/process modes, resource limits.

### 🔌 Extensibility
- **Plugin system** — manifest-based plugins with git install, Python entry points.
- **21 agent tools** — read/write/edit/bash/grep/glob/list_dir/search/web_fetch/spawn_subagent + LSP + CodeGraph.
- **MCP support** — connect any MCP server.
- **AgentDefinition** — YAML-based agent profiles with toolset presets.

### 🎯 Agent Architecture
- **ChatState** — actor-based message management with events, usage tracking, compaction.
- **AgentBuilder** — fluent builder for AgentLoop construction.
- **Subagent** — typed sub-agents (general-purpose, explore, plan) with capability isolation.
- **5 toolset presets** — `zall`, `explore`, `plan`, `codex`, `opencode`.

---

## Architecture

```
zall/
├── core/              # Primitives: model, agent, chat_state, gate, goal, safety, tool
│   ├── loop.py        # AgentLoop orchestrator
│   ├── agent.py       # AgentDefinition + ToolsetPreset + CapabilityMode
│   ├── chat_state.py  # Actor-based message management (NEW)
│   ├── safety.py      # Three-state context_judge
│   ├── gate.py        # ConfirmGate state machine
│   └── verifiability.py  # RunRecorder (chain-hash) + TrustAnchor
├── cli/               # Rich REPL, 25+ slash commands, replay
├── tools/             # 21 tools: read/write/edit/bash/grep/lsp/codegraph/…
├── adapters/          # OpenAI-compat, Anthropic, Gemini, Ollama
├── codegraph/         # Multi-language symbol indexer (NEW)
├── lsp/               # LSP client (pyright, rust-analyzer, etc.) (NEW)
├── sandbox/           # Process isolation (NEW)
├── plugin/            # Plugin system (NEW)
├── mcp/               # MCP client
├── safety/            # Rule loader
├── eval/              # 5-dimensional R-Metric evaluation
└── skills/            # Skill loader
```

---

## Commands

### Built-in (25+)

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model` | Switch model |
| `/plan` | Toggle plan mode |
| `/lsp` | LSP diagnostics & servers (NEW) |
| `/codegraph` | Code search & outline (NEW) |
| `/sandbox` | Isolation mode control (NEW) |
| `/chatstate` | ChatState diagnostics (NEW) |
| `/plugin` | Plugin management (NEW) |
| `/add` | Add file(s) to context |
| `/drop` | Remove file(s) |
| `/diff` | Show git diff |
| `/search` | Web search |
| `/web` | Fetch URL |
| `/git` | Git commands |
| `/commit` | Stage + commit |
| `/sessions` | List sessions |
| `/resume` | Resume session |
| `/replay` | Replay session |
| `/eval` | Evaluate session |
| `/cost` | Token usage |
| `/compact` | Compress context |
| `/undo` | Undo last tool |
| `/retry` | Retry last response |
| `/doctor` | Diagnose setup |
| `/checkpoint` | File snapshots |
| `/clear` | Clear conversation |

### Agent Tools (21)

| Tool | Purpose |
|------|---------|
| `read_file` | Read file content |
| `write_file` | Create/overwrite file |
| `edit_file` | Targeted string replacement |
| `batch_edit` | Multi-file batch edit |
| `bash` | Shell commands |
| `grep` | Search file contents |
| `glob` | Find files by pattern |
| `list_dir` | List directory |
| `web_fetch` | Fetch web page |
| `search` | Web search |
| `read_image` | Read image content |
| `spawn_subagent` | Delegate sub-task |
| `todo_list` | Track progress |
| `lsp_diagnostics` | Code errors/warnings (NEW) |
| `lsp_hover` | Symbol info (NEW) |
| `lsp_goto_definition` | Jump to definition (NEW) |
| `codegraph_search` | Find symbols (NEW) |
| `codegraph_outline` | File structure (NEW) |
| `codegraph_index` | Build index (NEW) |
| `code_understanding` | Deep code analysis (NEW) |
| `project_analysis` | Project stats (NEW) |

---

## Comparison

### vs Claude Code / Copilot

| Feature | zall | Claude Code |
|---------|------|------------|
| Hallucination detection | **Architectural** (PR-0) | Prompt-based |
| Reproducibility | Chain-hash + Replay | Log files only |
| Safety | 3-layer gate | Implicit |
| Model independence | 4 adapters (Protocol) | Anthropic-only |
| Code intelligence | LSP + CodeGraph | Limited |
| Sandbox isolation | Process/Worktree modes | None |
| Plugin system | Manifest-based | None |
| Open source | ✅ MIT | ❌ |

---

## Contributing

```bash
git clone https://github.com/Qinrayn/zall.git
cd zall
pip install -e ".[dev,bs4]"
python -m pytest tests/
```

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for guidelines.

---

## License

[MIT](LICENSE) © 2026 zall contributors