# Changelog

## [0.4.0] — 2026-07-17

### Added
- **ChatState 管理层** — Actor 模式的消息管理 (`src/zall/core/chat_state.py`). 借鉴 Grok Build 的 `xai-chat-state`. 支持事件追踪 (`StateEvent`)、用量分类账 (`UsageLedger`)、摘要压缩 (`SummaryCompaction`)、快照保存/恢复 (`Snapshot`)、可插拔持久化 (`ChatPersistence`).
- **ChatState → AgentLoop 集成** — `AgentLoop` 新增 `chat_state` 属性和 `get_chat_state()` 方法. `AgentConfig` 新增 `chat_state` 参数. 向后兼容.
- **LSP 集成** — `src/zall/lsp/__init__.py`. 多语言语言服务器 (pyright/typescript-language-server/rust-analyzer/gopls/clangd). JSON-RPC 传输层, go-to-definition, hover, completions, diagnostics.
- **LSP Agent 工具** — `src/zall/tools/lsp_diagnostics.py`. Agent 可直接调用: `lsp_diagnostics`, `lsp_hover`, `lsp_goto_definition`.
- **CodeGraph Agent 工具** — `src/zall/tools/codegraph.py`. Agent 可直接调用: `codegraph_search`, `codegraph_outline`, `codegraph_stats`, `codegraph_index`.
- **沙箱模式** — `src/zall/sandbox/__init__.py`. 三种隔离级别: NONE, WORKTREE (Git worktree), PROCESS (子进程). `ResourceLimits` 控制超时/输出/网络/写入.
- **CLI 命令** — `/lsp`, `/sandbox`, `/codegraph`, `/chatstate`, `/plugin` — 控制 v0.4.0 新系统.
- **系统提示注入** — `PromptBuilder.add_lsp_diagnostics()` 注入实时诊断摘要, `add_codegraph_context()` 注入代码结构概览.

### Changed
- Version bumped to `0.4.0`
- `AgentConfig` 新增 `chat_state` 字段
- `AgentLoop.__init__` 初始化 `ChatState` 实例, 通过 `self.chat_state` 属性访问
- `loop.messages` 属性在 ChatState 可用时返回 ChatState 的消息快照

### New files
- `src/zall/core/chat_state.py` — ChatState 管理层
- `src/zall/lsp/__init__.py` — LSP 集成
- `src/zall/sandbox/__init__.py` — 沙箱模式
- `tests/test_chat_state_invariants.py` — 25 个 ChatState 测试
- `tests/test_lsp_invariants.py` — 21 个 LSP 测试
- `tests/test_sandbox_invariants.py` — 25 个沙箱测试

## [0.3.0] — 2026-07-16

### Added
- **AgentDefinition system** — YAML frontmatter agent definitions from `.zall/agents/*.md` files, inspired by Grok Build's `AgentDefinition`. Supports toolset presets, permission modes, capability modes, model overrides, and MCP server configuration.
- **ToolsetPreset system** — Five built-in toolset presets: `zall` (full), `explore` (read-only), `plan` (read-only+todo), `codex` (Codex-compatible), `opencode` (OpenCode-compatible). Enables role-specific tool configurations.
- **SubagentCapabilityMode** — Three capability modes for sub-agents: `read_only`, `plan_only`, `no_bash`. Filter tools at spawn time for security isolation.
- **Default agent files** — `.zall/agents/explore.md` and `.zall/agents/plan.md` with full system prompt bodies.
- **Agent discovery** — `discover_agents()` searches `.zall/agents/` (project, user, bundled scopes) with proper priority ordering.
- `AgentBuilder.with_agent_definition()` and `with_agent_file()` — construct AgentLoop directly from AgentDefinition.
- `orchestrator.build_tools_for_preset()` — build ToolRegistry from a preset name.
- `orchestrator.run()` now accepts `agent_definition` and `toolset_preset` parameters.

### Changed
- Version bumped to `0.3.0`
- `pyproject.toml` — added `pyyaml>=6.0` dependency for YAML frontmatter parsing
- `SpawnSubagentTool` now supports `subagent_type` parameter (`general-purpose`, `explore`, `plan`) with capability-appropriate tool sets and system prompts.
- `zall.core.__init__` now exports all new types from `agent` and `toolset` modules.

### Added
- Unified logging module (`zall._util.logging`) — replaces silent `except Exception: pass` with observable warnings, strengthening IPR-0 self-falsifiability across all CLI and core modules
- `AgentBuilder` now fully adopted by both `orchestrator.run()` and REPL `build_repl_loop()`, eliminating duplicated `AgentLoop` construction logic

### Changed
- Version bumped to `0.2.7`
- `cli/app.py` cleaned up — removed 42-line backward-compat re-export block and unused test-compat imports, aligning with composition-root principle
- `core/builder.py` fixed boolean field propagation (`stream=False`, `allow_downgrade=False`, `plan_mode=False` no longer silently coerced to `None`)
- All critical `except Exception: pass` sites now log via `get_zall_logger()` before fallback, preserving IPR-0 safety while making errors observable

### Removed
- Legacy `from zall.cli.app import ...` re-exports — all consumers migrated to direct module imports

## [0.2.1] — 2026-07-16

### Fixed
- **Critical:** `pyproject.toml` URLs pointed to `github.com/zall/zall` (404) — corrected to `github.com/qinrayn/zall` (#1)
- **Critical:** `read_file.py` hardcoded UTF-8 encoding — non-UTF-8 files (e.g., GBK/CP936 on Chinese Windows) produced garbled output. Now uses system preferred encoding (#2)
- **High:** `bash.py` self-protection missed `shutdown /s` (with space) in compound commands (e.g., `echo foo; shutdown /s`) — now covered by `"shutdown /"` pattern (#3)
- **High:** `_util/file.py` `read_text_file()` and `atomic_write()` defaulted to UTF-8 encoding — now uses system preferred encoding (#4)
- **High:** `batch_edit.py` hardcoded UTF-8 for temp file writing and reading — now uses system preferred encoding (#5)
- `list_dir.py` had redundant `_SKIP_DIRS` set duplicating centralized `NOISE_DIRS` — removed, uses `NOISE_DIRS` directly (#6)
- `cli/app.py` duplicated `REPL_MAX_STEPS` from `cli/repl_ui.py` — removed duplicate (#7)
- `LICENSE` copyright year updated to `2025-2026` (#8)
- `__version__` in `__init__.py` corrected from `0.1.0` to `0.2.1` (#9)
- `bash.py` `_truncate_at_bytes()` hardcoded UTF-8 — now uses system preferred encoding (#10)

### Changed
- Version bumped to `0.2.1`
- Encoding-sensitive functions now consistently use `locale.getpreferredencoding()` instead of hardcoded UTF-8, improving cross-platform compatibility (especially Chinese Windows with GBK/CP936)

## [0.2.0] — 2025-07-15

### Added
- Open-source release with MIT license
- Extension system (EventBus-based Pi-style hooks):
  - `AutoLearnExtension` — cross-session pattern learning
  - `UsageTrackerExtension` — step-level usage statistics
- Skill loader & executor
- MCP client and tool wrapper
- 5-dimensional R-Metric evaluation suite
- Windows encoding support (cp936/GBK)

### Fixed
- GoalDowngrade silently disabled (`loop.py:442` — `_allow_downgrade` now correctly resolved)
- `_build_adapter` kwargs shadowing (`config.py:206` — extra kwargs merged properly)
- Watermark compaction record called even when compaction failed
- Sub-agent race condition on `close()` clearing `_subagents` concurrently with `_on_done` callback
- Adapter resource leak on `/doctor` exception path
- Default safety rules dropped when user custom rules exist
- Hand-rolled TOML parser now supports dotted keys (`cwd_meta.git_branch`)
- Python grep fallback uses system encoding (not hardcoded UTF-8)
- `/cost` KeyError on incomplete usage data
- HTTP response resource leak on streaming exception
- MCP config parser now handles inline comments
- 206 ruff auto-fixable issues cleaned up

### Changed
- mypy strict mode: 0 errors across 87 source files
- Extension Protocol uses `@property` for `name` and `hooks`

## [0.1.0] — 2025-06-?? (Pre-release)

### Added
- Initial core primitives: ModelAdapter Protocol, ToolRegistry, RuleSet, Context
- AgentLoop orchestrator (max_steps, streaming, goal-downgrade)
- Three-state safety model (whitelist/greylist/blacklist)
- ConfirmGate state machine with SUSPENDED timeout
- Chain-hash timeline (RunRecorder) + Ed25519 TrustAnchor
- Replay system (session replay without model/tools)
- 4 model adapters: OpenAI-compat, Anthropic, Gemini, Ollama
- Rich REPL with thinking display
- 840+ invariant tests with counterexamples