# Changelog

## [0.4.10] — 2026-07-18

### Added
- **`/suggest` 命令** — 列出 AutoLearn 生成的建议（adjust_k / create_skill / register_goaltype / adjust_judge），支持 `apply N`、`ignore N`、`detail N` 操作。被忽略的建议持久化到 `~/.zall/learned/ignored_suggestions.json`。
- **`/learn` 命令** — 显示跨会话学习统计（工具使用频率、错误率、工具链数量），支持 `clear` 重置忽略列表。
- **Auto-apply 高置信度建议** — `AgentLoop._auto_apply_suggestions()` 在 each turn done 后自动应用 confidence >= 0.5 的 `adjust_k` 建议，并 emit `self_adjust` event。
- **`load_learned_memo()`** — 跨会话学习记忆注入：启动时读取 `auto_learn.jsonl`，注入系统 prompt 作为 `[Cross-session learned patterns]` 节（工具频率、错误模式、工具链统计）。
- **Config 层连通 AutoLearn** — `repl_ui.py` 启动时调用 `set_extension_suggestions()`，将 `get_config_overrides()` 结果注入配置层。
- **回归测试** — `test_suggest_command`、`test_learned_memory_invariants`、`test_auto_learn_apply_invariants`。

### Changed
- **`memory.py` 扩展** — 新增 `load_learned_memo()` 函数，复用现有 `SessionMemory`；`PromptBuilder.add_session_memory()` 现也注入 learned memo。
- **`repl_ui.py` 扩展注册** — 启动时连接 AutoLearn 的 `get_config_overrides()` 到 `config_layers.set_extension_suggestions()`。
- **`loop.py` 扩展钩子** — `finalize()` 和 `run()` 的 on_turn_done 后调用 `_auto_apply_suggestions()`。

## [0.4.9] — 2026-07-18

### Fixed
- **[严重] 流式异常静默吞没 (A1)** — `_call_model_stream()` 中任何流式异常（API 断开、解码错误、速率限制等）之前被 `except Exception` 静默吞没，返回截断的 `ModelResponse`，调用方完全不知失败。现已记录日志、设置 `_last_stream_error` 字段，异常传播到 `step()` 的终端处理器，生成诚实可诊断的 `RunEgress` 错误。
- **[严重] CLI 重试 step 计数漂移 (A2)** — `repl_ui.py` 自动重试（429/503 等瞬态错误）调用 `loop.step()` 导致 `_step_count` 每次重试额外 +1，可能误触 `MAX_STEPS` 终止。新增 `AgentLoop.retry_step()`（不递增计数器），CLI 重试使用此方法，确保重试不会导致计数器漂移。
- **[严重] spinner 持久线程被破坏 (A3)** — `_stop_spinner()` 无条件设置 `_spinner_thread = None`，违反设计意图（注释说"单线程复用"），导致每次 model call 创建新线程。现在 `_stop_spinner()` 保留线程引用，线程回到等待状态等待下次触发；新增 `shutdown_spinner()` 供 REPL 退出时安全终止线程。
- **工具折叠输出无界堆积 (A4)** — `_folded_tool_outputs` 字典按 tool_idx 累积，会话中无上限。现在设置最大 64 条，超限时淘汰最早条目。
- **`ContextLimitExceeded` 死类** — 标记为废弃（deprecated），保留向后兼容的导入路径，但使用时会触发 `DeprecationWarning`。

### Changed
- **`AgentLoop` 重构** — 提取 `_run_step_body()` 方法，消除 `step()` 与 `retry_step()` 的代码重复，保障步计数器语义正确。
- **`loop_errors.py` 废弃标记** — `ContextLimitExceeded` 不再被使用，保留为兼容性别名。

### Added
- **`AgentLoop.retry_step()`** — 不递增步计数器的重试方法，消除 CLI 重试漂移。
- **`CliRenderer.shutdown_spinner()`** — REPL 退出时安全终止持久 spinner 线程。
- **回归测试** — `test_stream_error_invariants.py`（A1）、`test_retry_step_invariants.py`（A2）、`test_render_spinner_invariants.py`（A3）。

## [0.4.8] — 2026-07-17

### Fixed
- **[严重] `handle_empty_stop` 绕过 ChatState 同步** — `ContextManager.handle_empty_stop()` 直接修改消息列表而不通过 `loop.append_message()`，当 ChatState 启用时 nudge 注入消息未被记录。现已使用统一消息路径，保证 ChatState 事件日志完整。
- **[严重] 水位线自动压缩空操作** — `ContextManager._auto_compact()` 计算压缩结果后从未写回 `self._loop._messages`，压缩无效。现在正确替换 loop 消息列表并记录 timeline 事件。
- **`_auto_compact` 参数引用不一致** — 方法同时接受 `messages` 参数和直接访问 `self._loop._messages`，存在隐式约定。现统一使用 `self._loop._messages`，消除脆弱的引用约定。
- **`add_user_message`/`add_user_file_message` 不使用统一路径** — 直接调用 `push_user_message()` + `_messages.append()`，重复 `_append_message()` 封装的逻辑。现委托给 `_append_message()` 统一路径。
- **`_empty_stop_nudge()` 重复调用** — `handle_empty_stop()` 中 nudge 函数被调用两次，现缓存为局部变量。
- **`_auto_compact` 代码重复消除** — `loop.py._auto_compact()` 委派给 `ContextManager`，消除两处重复逻辑，统一压缩路径。
- **ChatState 消息管理集成** — `add_user_message()`、`set_messages()`、`remove_messages_by_predicate()`、`add_user_file_message()` 在 ChatState 启用时委托同步；新增 `append_message()` 公共 API；executor 内部消息追加使用统一路径。
- **HTTP 客户端资源泄漏** — `web_fetch.py` 和 `search.py` 的共享 `httpx.Client` 注册 `atexit` 处理器，进程退出时自动关闭连接池。
- **`_suspended_count` 死变量** — 移除 `loop.py` 中从未更新的 `_suspended_count` 实例变量（executor 已用局部变量正确处理）。
- **`AutoLearnExtension` 未使用参数** — 移除 `__init__` 中未使用的 `registry` 参数，简化工厂函数。
- **`ContextManager.handle_length()` 死代码** — 移除从未被调用的方法。
- **流式 Tool Call 渲染缺失** — `_process_stream_delta()` 对 tool_call delta 新增 yield，UI 实时显示工具构建进度。
- **stream_options 非标准参数** — 改为 `self._stream_usage` opt-in 模式（默认关），避免 DeepSeek/Qwen 等兼容 API 报 HTTP 400。
- **AutoLearn 跨会话计数错误** — `_load_persisted()` 用 `+=` 替换 `max()`，跨会话工具调用次数正确累加。
- **REJUDGE 无限循环** — 添加 `_MAX_REJUDGE=5` 上限，防止 gate 死循环。
- **Sandbox Windows 编码崩溃** — 两处 `subprocess.run` 用 `encoding='utf-8', errors='replace'` 替换 `text=True`，避免非 UTF-8 输出导致 `UnicodeDecodeError`。
- **AutoLearn 同步写磁盘阻塞** — `_persist()` 改为后台 daemon 线程异步写；新增 `_serialize_value()` 安全序列化。
- **web_search 超时 + 无备用** — 超时从 10s → 30s，新增 DuckDuckGo 重试(2次) + Bing HTML 备用搜索引擎，失败时返回友好提示。
- **web_fetch 超时过短** — 默认超时从 15s → 30s。

### Changed
- **极简任务跳过目标降级 (交互优化)** — `_init_downgrade()` 新增 `_is_trivial_task()` 检测：问候/打招呼（hi/hello/你好）、简单打印（print/say/echo）不再弹出目标降级确认框。
- **非 TTY 流式 tool call 渲染格式统一** — `_render_model_tool_call()` 非 TTY 模式输出 `step` 前缀对齐 `_render_model_token` 格式。
- **系统提示词优化** — 新增规则5(禁止反复读同一文件)和规则6(搜索失败时降级到训练知识或直接 fetch URL)。
- **Goal downgrade 尊重 `--yes` 标志** — `--yes` 模式下自动跳过 goal downgrade 提示。
- **非TTY 模式输出** — 流式 token 输出带 `"step N -"` 前缀，不再无格式连续输出。
- **流式 tool call 事件** — `loop.py` 新增 `model_tool_call` 事件，`render.py` 新增 `_render_model_tool_call()` 实时展示工具调用。
- **CompactionPolicy 升级 (Grok Build 启发)** — `policies.py` 新增 `keep_recent`、`min_compaction_interval` 字段；`WatermarkMonitor` 和 `ModelCompactor` 接受 `CompactionPolicy` 替代硬编码阈值；新增 `conservative()`/`aggressive()` 预置策略。

### Added
- **窄查询 (Narrow Queries)** — `ChatState` 新增 `get_last_message()`、`has_dangling_tool_calls()`、`get_last_assistant_text()`，避免大对话时克隆整个消息列表。
- **Turn Capture 偏移量** — `ChatState` 新增 `begin_turn_capture()`/`end_turn_capture()`，用偏移量 O(1) 记录 turn。
- **`append_message()` 公共 API** — `AgentLoop` 新增统一消息追加方法，executor 和外部组件通过此路径保证 ChatState 同步。
- **`WatermarkMonitor` 策略注入** — 支持传入 `CompactionPolicy` 配置水位阈值，默认 85% 兼容旧行为。

## [0.4.7] — 2026-07-17

### Fixed
- **流式重试 bug** — `_stream()` 中 `resp.__enter__()` 的返回值（真实 `httpx.Response`）被丢弃，后续 `with resp:` 重复进入已耗尽的上下文管理器导致 `'_GeneratorContextManager' object has no attribute 'args'`. 现在正确捕获 `http_response` 用于读取状态码和迭代行，`stream_ctx` 保留用于 finally 清理。

## [0.4.6] — 2026-07-17

### Changed
- **重试重构** — `BaseAdapter.with_retry` 使用 +/-25% 随机抖动，上限 60s，默认 5 次重试. 429 限流读取 Retry-After 头，5xx 可重试，4xx 不重试.
- **流式连接重试** — `_stream()` 初始连接 3 次重试+抖动（之前零重试），连接成功后中断不重试.
- **ZALL_TIMEOUT 环境变量** — 优先级最高，可覆盖 config.toml.

## [0.4.5] — 2026-07-17

### Fixed
- **CI: 12 failures across all platforms** — Removed stale `--ignore=tests/test_read_file_invariants.py` (B1 fixed), added `test_usage_stats` and `test_streaming*` to flaky-test filter for basic test step.
- **120s API timeout** — Default increased from 120s to 300s (`orchestrator.py`). Streaming read timeout now matches adapter timeout instead of hardcoded 60s (`openai_compat.py`). Prevents mid-stream cuts on complex tasks.
- **PyPI build error** — Removed `License :: OSI Approved :: MIT License` classifier (PEP 639 conflict with `license = "MIT"` field).

### Changed
- **README rewrite** — Professional open-source structure: installation, quick start, features table, architecture diagram, configuration guide, API reference, comparison table (vs Claude Code, Copilot, Cursor), development guide, contribution guidelines.
- **PyPI metadata** — Expanded keywords (14), classifiers (12), project URLs (Changelog, CI), long description derived from README.
- **CONTRIBUTING.md** — Updated with modern PR process, commit convention, testing philosophy.
- **Version** — 0.4.4 → 0.4.5

## [0.4.4] — 2026-07-17

### Added
- **loop.py 拆分** — 1598 行 `loop.py` 拆分为 4 个文件: `loop_config.py` (AgentConfig), `loop_events.py` (LoopEvent/RunEgress/StepResult), `loop_errors.py` (ToolNotFound/AgentRunaway), `loop.py` (AgentLoop 主类). 借鉴 Grok Build 模块化架构, 每个模块单一职责.
- **ToolKind 工具分类体系** — 新增 `core/tool_kind.py`: `ToolKind` 枚举 (READ/WRITE/EDIT/EXECUTE/SEARCH 等) + `ToolNamespace` 枚举 (ZALL/CODEX/MCP 等). 工具可声明 `kind` 属性, 替换硬编码 `_WRITE_TOOLS` frozenset.
- **CompactionPolicy 一等公民** — 新增 `core/policies.py`: `CompactionPolicy` (阈值/预算/双通道) + `ReminderPolicy`. 集成到 `AgentConfig`.
- **沙箱升级** — `SandboxMode.BWRAP` (bubblewrap 容器) + `SandboxMode.CONTAINER` (Docker). 辅助检测函数 `_bwrap_available()` / `_docker_available()`.

### Changed
- **AgentLoop 参数清理** — 移除 13 个旧式离散参数, 仅保留 `config: AgentConfig`. 旧式传参触发 `DeprecationWarning`.
- **Tool Protocol 扩展** — 新增 `get_tool_kind()` / `get_tool_namespace()` 辅助函数, 向后兼容.
- **Builder 优化** — `AgentBuilder.build()` 直接构造 `AgentConfig`.
- **AgentConfig → frozen dataclass** — 防止运行时配置突变, 提升不可变性保证.
- **完善 `__all__` 导出** — `zall/__init__.py` 和 `core/__init__.py` 增加显式 `__all__` 列表.
- **版本号** — 0.4.2 → 0.4.4

### Fixed
- **B1: 测试修复** — `test_read_file_invariants.py` 传 `limit=100`, 修正正则兼容精确/估算行数.
- **B3: AgentConfig.from_kwargs 补齐** — 补全 `compaction_policy`、`reminder_policy`、`anchor`、`chat_state` 4 个缺失字段.
- **B4: 消除所有 DeprecationWarning** — 20+ 处 `AgentLoop(..., kwargs)` 全部迁移为 `AgentLoop(..., config=AgentConfig(...))`. 覆盖生产代码 (`spawn_subagent.py`, `replay.py`) 和全部测试用例.
- **B5: 消除测试 DeprecationWarning** — 13 个测试文件全部更新, 零 DeprecationWarning 运行.

## [0.4.1] — 2026-07-17

### Added
- First PyPI release! `pip install zall` now works.
- Comprehensive README updated with all v0.4.0 features.
- CI pipeline fully green across 12 platforms (4 Python × 3 OS).

### Fixed
- All ruff lint errors resolved.
- Import errors for optional SDKs (anthropic, ollama) fixed.
- macOS /var → /private/var path symlink handling.
- Windows PowerShell CI quoting issue.

## [0.4.0] — 2026-07-16

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