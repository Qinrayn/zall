# Changelog

## [0.2.7] — 2026-07-16

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