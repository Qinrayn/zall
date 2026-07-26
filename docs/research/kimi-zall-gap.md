# kimi ↔ zall 差距分析与优化清单

> 输入: `docs/research/kimi-cli-study.md`（kimi 研读报告）+ zall 文档（MASTER §12 / IMPL / CHANGELOG / PARADIGM）+ 代码抽查。
> 输出: 按优先级排序的可执行优化清单，供 bugfix / optimize-ui / engineering 轮次直接消费。
> 约束: 所有落码遵守 IPR-0（不变量+反例测试）、IPR-1（文档对应）、IPR-3（core/ 零模型 SDK）。
> 评级: ✅ zall 已具备（含更优）· ◐ 部分具备 · ✗ 缺失。

---

## 0. zall 已领先/持平项（不需要动）

| 项 | 证据 |
|---|---|
| ✅ ASCII glyph 回退（防 tofu） | `render.py` `_G_ASCII`/`use_ascii_glyphs`——kimi 无此机制 |
| ✅ 原子写入 | `_util/file.py`、session.py、experience_store.py 等多处 tmp+os.replace(+fsync) |
| ✅ @ 文件注入（比 kimi 更进一步：直接展开内容） | `file_complete.expand_at_references`，三路径全齐 |
| ✅ steer + 消息队列 | Ctrl+S steer / Enter 排队（TUI），对齐 kimi steer |
| ✅ 中断保留部分输出 | `tui/app.py` `_flush_live(interrupted=True)` + `[Interrupted]` 标记 |
| ✅ 瞬态重试三路径统一 | `core/loop.py` `is_transient_error` + `_run_retry_transient` |
| ✅ 链式哈希/可复现/科研套件 | kimi 完全没有——zall 的护城河，不动 |
| ✅ 配置自愈 | `test_config_selfheal`——kimi 无 |

---

## 1. P0 —— 直接提升日常体验（优先落地）

### G1. Rich diff 渲染三形态 ✓ (2026-07-26 完成: cli/diff_render.py 双来源三形态 + 词级高亮 + 审批盲区修复 + 真实行号)
- **kimi**: `utils/rich/diff_render.py`——`SequenceMatcher.get_grouped_opcodes(n=3)` 直接建 hunk；词级内联高亮（`ratio()<0.5` 跳过）；3 列 Table（行号×2 + 标记 + 内容）整行背景色；`render_diff_preview` 只显改动行上限 6 行 + "ctrl-e to expand"；超大文件降级 summary。
- **zall 现状**: `tools/_diff.py` 仅 `difflib.unified_diff` 纯文本；渲染层只按 +/- 着色，无行号、无词级高亮、无折叠。
- **方案**: 新建 `cli/diff_render.py`（渲染层，非 core）移植三形态 + offset map tab 处理；`edit_file`/`batch_edit` 审批面板与结果展示接入。TUI 用可折叠 preview（键位 ctrl+e 或点击展开）。
- **测试**: 词级高亮配对/ratio 跳过/大文件降级/CJK 行宽 各含反例。

### G2. 审批 UX：feedback 拒绝语义 ✓
- **kimi**: y / always(session) / no + **自由文本 feedback**；带 feedback 的拒绝把理由给模型继续 turn，纯拒绝停 turn；`approve_for_session` 批量 resolve 同 action 的 pending。
- **zall 现状**: y-n-a-e-s 决策逻辑（`responder.py`）+ always_allow 持久化已有；但拒绝时**无 feedback 通道**（模型只知道被拒，不知道为什么/该怎么改）。
- **方案**: `CliUserResponder.ask()` 拒绝分支支持输入理由 → 注入 tool_result（"User rejected: {reason}. Try a different approach..."）；TUI 面板加 feedback 输入。
- **测试**: feedback 拒绝继续 / 纯拒绝停止 反例对。
- **完成 (2026-07-26)**: gate `UserResponse.feedback` + REJECT reason 携理由入 tool_result; responder greylist `f` 选项 (选择菜单同源, TUI 免接线); 8 不变量测试。

### G3. 错误分类 → 用户可操作建议 ✓（2026-07-26 完成）
- **kimi**: 异常映射表——401→"重新认证"、402→"续费"、Connection→"检查网络"、Timeout→"服务器慢"、EmptyResponse→"临时问题请重试"、MaxSteps→"再发消息继续"；重试时 UI 显示 "retrying (2/3) in 1.2s: 503"。
- **落地**: `_ERROR_MAP` 补 402；`RETRY_REASON` 单一真相源；`set_retry_callback` → loop `retry` 事件 → REPL spinner 标签/非 TTY 落行/TUI dim 行；顺带修复 Retry-After 预算泄漏（无限重试 bug）。见 CHANGELOG 2026-07-26 轮 + `tests/test_retry_visibility_invariants.py`。

### G4. 空响应/think-only 检测 ◐（复查后下调）
- **kimi**: kosong 无 content 无 tool_calls → APIEmptyResponseError（可重试）；**只有 think 无正文**也算空响应（max_tokens 在推理阶段耗尽的特征）。
- **zall 现状**（复查）: `context_manager.is_empty_stop`（STOP+空 content）+ `handle_empty_stop` nudge 重试一次已存在；reasoning 独立字段故 think-only 已被覆盖；TOOL_USE+空 tool_calls 已有 PR-0 error。
- **剩余缺口**: stop_reason=LENGTH 且 content 空（推理阶段耗尽 max_tokens）未归入空响应路径；低优先，随 G16 钳制一并观察。

### G5. 大段粘贴折叠占位符 ✓
- **kimi**: bracketed paste 多行不触发提交；大段粘贴折叠为 `[Pasted text +N lines]` 占位符，提交时展开。
- **zall 现状**: REPL `multiline=True` + 反斜杠续行 + 双 Enter 提交；TUI TextArea 原生多行。但大段粘贴撑满输入区、REPL 粘贴多行会被 Enter 键绑定逐行解释。
- **方案**: TUI 输入框粘贴 >8 行折叠显示占位符（内部保留全文）；REPL 开启 bracketed paste 检测。
- **完成 (2026-07-26)**: `cli/paste_fold.py` (PasteFolder, 阈值 1000 字符/15 行 env 可调) + REPL BracketedPaste eager 绑定 + TUI ChatTextArea._on_paste 拦截; 提交/steer 展开; 11 不变量测试。

---

## 2. P1 —— 渲染与输入的质感（希腊美学轮的地基）

### G6. 主题系统：语义 token → 色板两级映射 ✓ (2026-07-26 完成: cli/theme.py 单一色源 + obsidian/attic 双主题 + /theme 命令; 顺带修 4 处 ANSI 色号错误与 widgets 值拷贝 bug)
- **kimi**: theme.py 集中语义色（accent/dim/error/diff add_bg…）dark/light 两套，渲染代码只引用语义名。
- **zall 现状**: `_C` 单一 Obsidian 硬编码（256 色名）+ `_ANSI_MAP` 手工重复维护；TUI CSS 又一套硬编码 hex——**三处色源，改肤要动三处**。
- **方案**: 新 `cli/theme.py`：`Theme` dataclass（语义槽位）+ 内置 `obsidian`（现状）与 `attic`（希腊美学新主题：大理石白/爱琴海蓝/月桂金，黄金比例间距）；`_C`/`_ANSI_MAP`/TUI CSS 全部从 Theme 派生；config `[ui].theme` 切换。
- **这是 optimize-ui 轮的第一块砖**。

### G7. ANSI-16 语法主题（终端适配性） ✓
- **kimi**: `KIMI_ANSI_THEME` 纯 16 色语法主题，自动适配用户终端配色。
- **zall 现状**: `CODE_THEME="one-dark"` + 硬编码 `#1e1e1e` 背景——浅色终端下不可读。
- **方案**: 移植 ANSISyntaxTheme 映射为默认，one-dark 作为 truecolor 主题的可选项（跟随 G6 主题切换）。
- **完成 (2026-07-26)**: `cli/syntax_theme.py` (ZALL_ANSI_THEME + resolve_code_theme/bg) + 第三主题 `ansi`; widgets/render 全接线 (顺带修 REPL Markdown 从未传 code_theme); 10 不变量测试含 ANSI 纯净性断言。

### G8. Markdown 安全边界增量固化（REPL 流式路径） ✓ (2026-07-26 评估关闭)
- **kimi**: markdown-it 找 top-level block 边界，完成块 print 固化，tail 留 Live 区——无闪烁 + 无限滚动。
- **评估结论 (代码核实)**: gap 描述"REPL 流式仍是整块节流重绘"与代码不符——`_render_model_token` → `_StreamBuffer` → `_write_stream_text` 是 **append-only 原文直写**（`_raw_stream.write`，无 Markdown 重渲染、无重绘、无重排）；`_render_model_call` 中 `_streamed_step == step` 分支仅 `_flush_stream_buffer()` + 换行，**不重打全文**。kimi 两段式固化解决的是 Live 区重绘成本，zall REPL 无 Live 区，痛点不存在。fence 补全 + 50ms 合并 + 断句点 flush 已覆盖防闪烁。无移植必要。

### G9. thinking 滚动预览窗 ✓ (2026-07-26 评估关闭)
- **kimi**: 流式 thinking 只显示末尾 6 行（deque 截尾），完成后整块折叠提交。
- **评估结论**: 流式路径已有滚动预览行（`_render_model_thinking`: 断句点/8char/20char 三条件 flush + 末尾截尾 + 计时）；非流式（同步 complete）路径 reasoning 整块到达，阻塞 HTTP 期间无增量数据，滚动预览**物理不可行**；三路径终态统一由 `_render_thinking_block` 折叠展示（前 12 行 + N more）覆盖。与 kimi 已持平，无移植必要。

### G10. `ensure_new_line` 光标列探测 ✓ (2026-07-26 完成)
- **kimi**: `\x1b[6n` 查询光标列（Unix 非阻塞+0.2s deadline；Windows GetConsoleScreenBufferInfo），子进程输出无尾换行时 prompt 不错位。
- **zall 现状**: 无——bash 工具输出无尾换行时提示符会接在行尾。
- **实现**: `_util/term.py` `ensure_new_line()`（Windows GetConsoleScreenBufferInfo / Unix ESC[6n+200ms 超时），修正 kimi win32 分支 off-by-one（1-indexed 却判 0，行首误插空行）；接线 repl_ui 主循环提示符前；9 测试 `test_term_invariants.py`。

### G11. shorten 词边界 + CJK 硬切 ✓ (2026-07-26 完成)
- **kimi**: `shorten` 优先词边界截断，CJK 无空格时硬切，不塌缩成省略号。
- **zall 现状**: 各处截断多为 `text[:N] + "..."`（对 CJK 双宽不感知，rich 表格可能溢出）。
- **实现**: `_util/string.py` 加 `display_width`/`shorten`/`truncate`/`shorten_middle`（cell 宽度感知，超越 kimi 纯字符版）；替换 responder/_preview_args、widgets/ThinkWidget、render.py ×3 共 5 处消费点；顺手修 ThinkWidget from_markup 未转义崩溃隐患；17 测试 `test_shorten_invariants.py`。

---

## 3. P2 —— 架构强化（engineering 轮）

### G12. 会话持久化：`_` 前缀特殊行 + 首行版本头 ✓ (2026-07-26 完成)
- **kimi**: context.jsonl 用 `_system_prompt`/`_checkpoint`/`_usage` 特殊行；wire.jsonl 首行 protocol_version；坏行跳过。
- **zall 现状**: autosave 原子写 + timeline JSONL 已有；但 session 文件无版本头、坏一行全弃。
- **实现**: `_util/jsonl.py`（make_metadata/read_jsonl/read_metadata，legacy 无头兼容）；_save_session 首行写头；两读取端接线 + 坏行 skip；12 测试 `test_jsonl_header_invariants.py`。

### G13. 重试策略升级：指数抖动退避 ✓ (2026-07-26 完成)
- **kimi**: `wait_exponential_jitter(initial=0.3, max=5, jitter=0.5)`；仅 429/5xx/空响应重试。
- **zall 现状**: 固定 2s/4s/6s。可用但慢端点下体感僵硬，且三次固定值在多客户端同时重试时有同步惊群。
- **实现**: `_util/backoff.py` `backoff_delay`（指数 2/4/8 封顶 + 中心对称均匀抖动，rng 可注入）；三消费点统一接线；9 测试 `test_backoff_invariants.py`（含架构守卫）。

### G14. asyncio/线程任务防泄漏审查 ✓
- **kimi**: 强引用集合 + done_callback 模式（防 GC "Exception None"）。
- **zall 现状**: worker 是线程模型（Textual `run_worker`）风险面不同；但 MCP 子进程/后台 timer 需一次审查。
- **审查结论**（全量 grep `create_task|Thread(|ThreadPoolExecutor` 10 处逐点核实）: spawn_subagent（close+`__del__`+幂等）/ mcp/client（stop Event+join(2.0)+daemon）/ render spinner（持久线程+shutdown Event+join）/ environment（短命 with 池）/ anchor server（per-conn daemon）/ update+repl_ui（单发 daemon）/ coordinator（有界批量 with 池）均健全。
- **唯一真缺陷已修**: `tools/grep.py` 退化路径 `with ThreadPoolExecutor` 在 timeout 后 `__exit__` 会 `shutdown(wait=True)` **阻塞等待灾难性回溯的失控线程** — timeout 保护形同虚设，且非 daemon 线程阻止进程退出。改 daemon 线程 + join(timeout) + `_cancel` 协作停止标志（逐文件/逐行检查）；`test_grep_invariants.py` 新增 3 测试（超时即返+快搜索反例+架构守卫）。

### G15. chaos/scripted provider（E2E 设施） ✓
- **kimi**: `_chaos`（error_probability 故障注入）+ `_scripted_echo`（脚本回放）provider。
- **zall 现状**: 测试用 mock adapter 存在于 tests/，但**无用户可配的故障注入/回放 provider**——E2E 全流程检验（外层目标）正需要。
- **已落地**: `adapters/scripted.py`（JSON 脚本回放，构造期全量解析即失败，流式≡阻塞语义，calls 记录供断言）+ `adapters/chaos.py`（包装真 adapter 按概率注入 429/500/503/transport，注入形态与真实错误路径同构，max_consecutive 前进性护栏，rng 可注入，`__getattr__` 委托保 hasattr 探测语义）；`_build_adapter` 接线 `model=scripted:<path>` / env `ZALL_SCRIPT` / `ZALL_CHAOS`+`ZALL_CHAOS_MODES`；15 测试 + 全链路冒烟（scripted 驱动 `zall --no-tui` 完整 pipeline 含会话持久化）。

### G16. completion token 动态上限 ✓
- **kimi**: `estimate_request_tokens`（ASCII÷4、CJK×1、媒体 2000）→ `min(requested, max_ctx - input)` 防 400。
- **zall 现状**: 已有真实 token 水位（usage 优先）；但发请求前无 max_tokens 动态钳制。
- **已落地**: `_util/tokens.py`（estimate_text_tokens CJK 感知 / estimate_body_tokens 整体 json.dumps 估算 / clamp_completion_tokens，margin=1024 kimi 同值，window 未知不钳）；openai_compat + anthropic `_build_body` 接线（anthropic 在 tools 注入后、thinking 前）；`test_token_clamp_invariants.py` 11 测试含反例。

### G17. 命名空间/别名斜杠命令 ✓（display 层已落地；命名空间留 plugin 生态）
- **kimi**: `plugin:cmd` 命名空间、别名展示 `/name (alias)`、非命令宽容降级为普通文本。
- **zall 现状**: `@slash_command` 注册 + did-you-mean(OSA) 已好；别名有（/lab=/selfplay）但补全不展示别名关系；无命名空间。
- **已落地**（展示层派生，description 源不动）: `get_command_meta` 别名条目标 `→ /规范名`、规范名尾附 `(alias: ...)`；`get_palette_commands` desc 尾附别名 — 副作用：面板 fuzzy desc 命中可经别名召回（搜 "selfplay" 出 /lab）。`TestAliasAnnotation` 4 测试含无别名不污染反例。命名空间留待 plugin 生态需要时再做。

---

## 4. 明确不移植项（决策记录）

| kimi 特性 | 不移植理由 |
|---|---|
| wire/ SPMC 双队列协议 | zall 是单进程线程模型，LoopEvent 总线已覆盖；引入 wire 是过度工程 |
| 后台任务文件式状态机 | zall 无跨进程 worker 需求；`/lab` 沙盒已覆盖后台执行位 |
| hooks exit-2 协议 | zall 的 gate/judge 体系语义更强；Claude 兼容 hooks 无用户需求 |
| kosong Protocol 抽象层 | zall ModelAdapter Protocol 已等价（IPR-3 更严格） |
| OAuth 恢复链 | zall 用户是 API-key 模式 |
| subagent 审批穿透 | zall subagent 是能力隔离模式（capability isolation），无跨层审批需求；若未来 subagent 可写文件再引入 |

---

## 5. 执行顺序（映射到外层计划）

1. **bugfix 轮**: G4（空响应）+ G3（错误建议补全）+ 既有 bug 排查
2. **optimize-ui 轮**: G6（主题系统+attic 希腊主题）→ G1（diff 三形态）→ G7（ANSI-16）→ G5（粘贴折叠）→ G2（审批 feedback）→ G11（shorten）→ G10（ensure_new_line）
3. **engineering 轮**: G8（REPL 增量固化）+ G12（版本头）+ G13（退避抖动）+ G14（任务审查）+ G16（token 钳制）+ G9/G17 顺带
4. **e2e 轮前置**: G15（scripted/chaos provider）
