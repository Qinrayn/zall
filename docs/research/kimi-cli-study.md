# kimi CLI 源码深度研读报告（面向 zall 移植）

> 研读对象：`C:\Users\云丘\Desktop\kimi-cli-main`（src/kimi_cli 主包 + packages/kosong LLM 抽象层）
> 目标：提炼每一个可被 zall（prompt_toolkit + rich 终端 agent）借鉴的技术细节。
> 每模块按 (a) 核心设计思想 / (b) 关键实现技巧 / (c) 值得移植到 zall 的点 组织。

---

## 1. ui/shell/ —— 输入、渲染与交互中枢

### 1.1 prompt.py（输入框）

**(a) 设计思想**：单一 PromptSession 常驻，通过状态机（idle / running-accepts-steer / modal）复用同一个输入框，而不是每次重建。

**(b) 关键实现**：
- **多行输入**：`Esc+Enter` / `Alt+Enter` 插入换行；单独 Enter 提交。判断依据 buffer 是否 multiline 由 `Condition` 动态过滤器决定。
- **粘贴处理**：bracketed paste 事件中检测 `\n`，多行粘贴不触发提交而是整体插入；大段粘贴折叠为 `[Pasted text +N lines]` 占位符（placeholders.py），提交时展开真实内容。图片粘贴走 clipboard.py 落盘为临时文件后以 `@path` 引用。
- **历史**：`FileHistory` 持久化，`auto_suggest=AutoSuggestFromHistory()` 灰色内联建议。
- **@ 文件引用**：输入 `@` 触发文件路径补全器（模糊匹配工作区文件，遵守 gitignore 类过滤 file_filter.py）。
- **/ 命令补全**：输入行首 `/` 时切到 slash 命令补全器，展示 `name — description`，别名显示为 `/name (alias)`。
- **Ctrl+C**：buffer 非空 → 清空 buffer；空 buffer → 抛 KeyboardInterrupt 交给上层（打印 "Tip: press Ctrl-D or send 'exit' to quit"，双击语义由上层实现）。
- **Ctrl+D**：空 buffer 时 EOFError → 优雅退出；流式期间收到 EOF 则设 `_exit_after_run=True` 等 run 结束再退。
- **Esc**：流式期间 Esc 触发中断（cancel_event.set()）。

**(c) 移植点**：占位符折叠大段粘贴；同一 session 的 steer 保活（流式期间 prompt 不关闭，可直接续 打字入队）；`@` 引用与 `/` 补全的双补全器切换。

### 1.2 visualize/（流式渲染核心）

**(a) 设计思想**：**双层渲染**。屏幕分为"已确认区"（直接 `console.print` 永久写入 scrollback）与"活跃区"（rich `Live(transient=True)` 反复重绘）。内容一旦确定即从 Live 提交到 scrollback，Live 区始终很小 → 无闪烁、可无限滚动。

**(b) 关键实现**：
- `Live(transient=True, refresh_per_second=10, vertical_overflow="visible")`；活跃区只保留正在流式的当前块。
- **增量 Markdown 提交**：`_find_committed_boundary` 用 markdown-it 解析 token 流，找 top-level block 的安全边界（未闭合的 code fence/列表不提交），已完成的 block 渲染后 print 固化，剩余 tail 留在 Live 区。
- `_reset_live_shape`：私有 hack `live._live_render._shape = None`，在终端 resize 或提交内容后强制 Live 重新测量，避免残影。
- **thinking 流**：默认 6 行滚动预览（deque 截尾），块结束后完整 markdown 提交历史；`show_thinking_stream=False` 时只显示 "Thinking ..." 指示器。
- `_blocks.py`：把 wire 消息映射为渲染块模型（TextBlock/ThinkBlock/ToolCallBlock/DiffDisplayBlock/TodoDisplayBlock/ShellDisplayBlock…），每种块有 compact（进行中）与 final（完成）两种渲染形态。
- `_input_router.py`：live view 激活时按键路由——审批面板/问题面板抢占输入；`ctrl-e` 展开 diff 预览。
- `_approval_panel.py`：面板内联渲染在 Live 区，选项 [y]es / [a]lways / [n]o + 自由文本 feedback；被外部（web端）解决时本地面板自动关闭。
- `_question_panel.py`：结构化问题（QuestionItem 2-4 选项 + 合成 "Other" 自由输入项），方向键选择。

**(c) 移植点**：双层渲染是 zall 消除闪烁的根本方案；markdown 安全边界增量提交算法；thinking 6 行滚动预览；ctrl-e 展开长 diff。

### 1.3 __init__.py（Shell 主循环，1552 行）

**(a) 设计思想**：单一 prompt 读取循环 `_route_prompt_events` + 事件队列，把"输入"从"执行"中解耦；运行期间 prompt 保活接收 steer/中断。

**(b) 关键实现**：
- `resume_prompt: asyncio.Event` 门控：set 在 await 之前 = 流式期间 prompt 继续活着（可打字 steer）；set 在 await 之后 = 阻塞直到 run 结束。
- `idle_events: Queue[_PromptEvent]`，kind ∈ {input, interrupt, eof, cwd_lost, error}。
- KeyboardInterrupt 在 running 且 `running_prompt_accepts_submission()` 时调用 `_running_interrupt_handler()`（= cancel_event.set），否则入队 interrupt 事件。
- `run_soul_command`：captured_view 闭包捕获 active view（finally 会清）；`cancel_event.clear()` 防 btw 期间的 Ctrl+C 污染下一 turn；结束后 `drain_queued_messages()` 循环执行排队消息（上限 `_MAX_DRAIN_GENERATIONS=20`），错误/取消时打印 "Queued message dropped"。
- **后台完成自动触发**：`_BackgroundCompletionWatcher` 监听 bg 完成事件 → 若 LLM 有未消费通知则自动跑一轮 soul；`_BG_AUTO_TRIGGER_INPUT_GRACE_S=0.75` 用户正在打字时推迟；连续失败 3 次熔断；resume 会话后必须先有一次用户 turn 才 arm。
- `_run_btw_modal`：/btw 旁路提问 = prompt-read task + LLM task + dismiss event 三方 `asyncio.wait(FIRST_COMPLETED)` 竞争，0.08s spinner。
- 异常分类打印：401→"/login 重新认证"、402→"续费"、Connection→"检查网络"、Timeout→"服务器慢"、EmptyResponse→"临时问题请重试"、MaxSteps→"再发消息继续"。
- 后台任务防 GC 模式：task 存入集合 + `add_done_callback(set.discard)`（asyncio 只持弱引用，否则 pending task 被 GC 导致 "Exception None"）。

**(c) 移植点**：resume_prompt 门控模式；queued-message drain；bg 自动触发的输入宽限期与熔断；异常→用户可读建议的映射表；task 强引用集合模式（zall 所有 create_task 处应审查）。

### 1.4 slash.py / slashcmd.py（斜杠命令系统）

- `SlashCommandRegistry` 装饰器注册：`@registry.command(name=..., aliases=[...])`，description 取 `func.__doc__`。
- `parse_slash_command_call` 正则 `^\/([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*)`，支持 `plugin:cmd` 命名空间；命令名后必须是空白否则不算命令（`/foo!` 不匹配 → 当普通文本发给 LLM）。
- `iter_command_entries()` 展开别名为 (trigger, cmd) 对供补全，`display_name(trigger)` 对别名显示 `/name (alias)`。

**移植点**：命名空间命令、别名机制、"非命令即普通消息"的宽容解析。

### 1.5 console.py / keyboard.py / startup.py / session_picker.py

- console.py：全局单例 Console，封装 `themed print`；宽度感知。
- keyboard.py：按键常量与 `format_key` 平台化显示（mac ⌘ 等）。
- startup.py：welcome panel 用 Table 布局 logo+提示文字；`replay_recent_history` 回放最近 wire 事件重建上屏历史。
- session_picker.py：交互式会话选择器（时间/标题/消息数），空会话过滤。

---

## 2. ui/theme.py 主题系统

**(a)** 集中定义语义色（accent/dim/error/warning/success/diff add/del bg…），dark/light 两套，运行时按 config.theme 切换。所有渲染代码只引用语义名不写死颜色。

**(b)** diff 背景色用低饱和 RGB（dark: 深绿/深红底），行内高亮再叠加更亮一档；light 主题独立调校。

**(c) 移植点**：zall 的希腊美学主题应同样以"语义 token → 颜色"两级映射实现，换肤只改一张表。

---

## 3. utils/rich/ —— 渲染定制

### 3.1 markdown.py
- 定制 rich Markdown：代码块用 KimiSyntax（ANSI 主题）、紧凑 heading、列表 bullet 风格统一；行内 code 样式化。

### 3.2 diff_render.py（482 行，渲染管线精华）
- `DiffLine(kind, old_num, new_num, code, content, is_inline_paired)` dataclass(slots)。
- `_build_diff_lines`：**直接** `SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_grouped_opcodes(n=3)` 构建 hunk（跳过 unified-diff 字符串往返）；replace 组先全 DELETE 后全 ADD。
- `_apply_inline_diff` 词级内联高亮：按位置配对 del/add 行（`paired=min(len,len)`）；`sm.ratio() < 0.5` 跳过（差异太大高亮无意义）；opcodes 中 delete/replace→del 高亮、insert/replace→add 高亮。
- `_build_offset_map(raw, rendered, tab_size)`：复刻 `str.expandtabs` 的列感知算法（`col += tab_size - col % tab_size`）建立 raw→rendered 偏移映射；不匹配时退化为有界单调线性插值 `[(i*rendered_len)//raw_len]`，**防越界崩溃**。
- `_highlight`：Pygments `ensurenl=True` 后只 crop 一个字符的尾换行，不 strip 有语义的尾空格。
- `render_diff_panel`：3 列 Table（右对齐行号 ×2、` + `/` - ` 标记、内容 ratio=1），hunk 间 `⋮` 分隔，整行背景 add_bg/del_bg。
- `render_diff_preview`：只显示改动行，上限 `MAX_PREVIEW_CHANGED_LINES=6`，超出显示 "... N more lines (ctrl-e to expand)"。
- `render_diff_summary_panel`：超大文件降级为 "File too large for inline diff (N lines → M lines)"。

### 3.3 syntax.py
- `KIMI_ANSI_THEME = ANSISyntaxTheme({...})`：**纯 16 色 ANSI** 语法主题（Comment=bright_black italic、Keyword=magenta、String=bright_blue、Name.Function=bright_cyan），自动适配用户终端配色，不假设 truecolor。
- `KimiSyntax(Syntax)` 子类注入默认主题；文件尾 `__main__` 自测。

### 3.4 columns.py
- `BulletColumns`：bullet+内容两列实现悬挂缩进；`_ShrinkToWidth` 通过 `__rich_measure__` 强约束子项宽度；`_strip_trailing_spaces` 逐行从尾部剥离空格 segment（遇 control segment 停）。

**(c) 移植点（整个模块几乎可整体移植）**：diff 三形态（panel/preview/summary）+ ctrl-e 展开；offset map 的 tab 处理与 fallback；ANSI-16 语法主题保证任何终端可读。

---

## 4. soul/ —— agent 核心循环与会话

### 4.1 会话持久化格式（session.py / session_state.py / context.py）

会话目录 `sessions_dir/{uuid}/`：
```
context.jsonl   # LLM 上下文（追加式）
wire.jsonl      # UI 事件流（首行 metadata: protocol_version）
state.json      # SessionState（approval/plan/todos/archive）
subagents/      # 子 agent 实例
uploads/        # 上传媒体
tasks/          # 后台任务（spec/runtime/control/consumer + output）
```
- **context.jsonl 特殊行**：role 以 `_` 前缀区分非消息记录——`_system_prompt`、`_checkpoint`、`_usage`。`is_empty()` 跳过 `_` 前缀 role。
- `write_system_prompt`：空文件直写；非空文件用 tmp 前置 + 64KB 分块流式复制 + 原子替换。
- 坏行容错：`_parse_context_line` 失败即跳过该行，不炸整个会话。
- state.json：`atomic_json_write`（mkstemp + fsync + os.replace）；保存前 reload 外部可变字段（读-改-写防并发覆盖）；损坏时 fallback 默认值。
- **checkpoint / D-Mail**：`Context.checkpoint()` 写 `_checkpoint` 行；`revert_to(id)` 先把旧文件旋转（`next_available_rotation`：正则扫描现有 `name_N.ext` 取 max+1，O_CREAT|O_EXCL 原子占位保证唯一），再重放到目标 checkpoint。`BackToTheFuture(checkpoint_id, messages)` 异常驱动主循环回滚+注入消息。
- **fork/undo**（session_fork.py）：`enumerate_turns` 扫 wire.jsonl 的 TurnBegin；`truncate_context_at_turn` 按真实 user 消息计数（排除 `^<system>CHECKPOINT \d+</system>$` 合成消息），best-effort；fork 出新会话复制截断文件+引用的视频，标题 "Fork: {src}"。

### 4.2 kimisoul.py（1964 行，agent 循环）

- **turn 生命周期**：OAuth ensure_fresh → UserPromptSubmit hook（可 block）→ TurnBegin → slash/ralph/普通 turn → Stop hook（最多 re-trigger 1 次，`_stop_hook_active` 防死循环）→ TurnEnd → 自动标题。finally 保证 TurnEnd 补发。
- **step 生命周期**：通知投递 → 动态注入（DynamicInjectionProvider 协议，各 provider 异常隔离，合并为一条 system_reminder user 消息）→ 历史归一化 → LLM 调用（带重试）→ usage 更新 → 工具执行 → `asyncio.shield(_grow_context)`（写入不可被取消打断）→ 结果解析（rejected/dmail/force_stop/no_tool_calls）。
- **steer**：`steer()` 入队 → `_consume_pending_steers()` 在步骤间注入为 user 消息 + wire 发 SteerInput；有 steer 强制继续下一步。
- **compaction**（compaction.py）：`estimate_text_tokens = chars//4`；触发条件 `tokens >= max*0.85` 或 `tokens + reserved(50k) >= max`；SimpleCompaction 保留最后 2 条 user/assistant，其余打包编号消息让 LLM 总结，`COMPACTION_OUTPUT_PREFIX` 重建历史；compact 后丢弃 ThinkPart。
- **错误恢复栈**（三层）：
  1. `_run_with_connection_recovery`：401 → OAuth 强刷重试一次；连接错误 → `RetryableChatProvider.on_retryable_error()`（重建 httpx client，保留 live client 上的 OAuth 刷新过的 key）恢复一次，`_kimi_recovery_exhausted` 防重复。
  2. tenacity：`wait_exponential_jitter(initial=0.3, max=5, jitter=0.5)` + `stop_after_attempt`；`_is_retryable_error` 只重试 429/500/502/503/504/空响应；每次重试发 `StepRetry` wire 事件（UI 显示 "retrying (2/3) in 1.2s: APIStatusError 503"）。
  3. `classify_api_error` 分类表（429→rate_limit、529→overloaded、4xx 含 "context length"→context_overflow）→ 用户可读消息。
- `_compute_completion_overrides`：`estimate_request_tokens`（system+tools schema+history+媒体 2000/个，ASCII 4字符/token、非 ASCII 1字符/token）→ `compute_max_completion_tokens = min(requested, max(1, max_ctx - input))`，防超上下文 400。
- **Ralph loop**：`FlowRunner.ralph_loop` 把同一 prompt 构造成 BEGIN→R1(task)→R2(decision: CONTINUE/STOP)→END 流程图，`<choice>` 标签解析，无效选择重试——"重复喂同一 prompt 直到 LLM 自己说停"。

### 4.3 soul/__init__.py（run_soul 编排）
- Wire 连接 soul 与 UI：ContextVar `_current_wire` + 全局 `wire_send()`；soul task 与 cancel_event 竞争等待；UI task 0.5s 超时收尾；通知泵 1s 周期（root only）。
- `format_token_count`：28.5k / 1.2m 格式，`rstrip("0").rstrip(".")`。

**(c) 移植点**：`_` 前缀特殊 JSONL 行；atomic write + 读改写；checkpoint 文件旋转重放；三层错误恢复（zall 目前大概率只有裸重试）；StepRetry 事件让 UI 显示重试进度；compaction 触发双条件；completion token 动态上限。

---

## 5. tools/ —— 工具设计

### 5.1 file/replace.py（StrReplaceFile）
- `Edit(old, new, replace_all)`，参数可单个或列表（一次审批多处编辑）。
- 核心就是 `content.replace(old, new, 1)`（**非 fuzzy**——宁可失败让模型重试，不做模糊匹配引入错误编辑）；无变化返回 "No replacements were made" 明确错误。
- 审批展示：`build_diff_blocks`（utils/diff.py）→ `SequenceMatcher.get_grouped_opcodes(n=3)` 生成 DiffDisplayBlock；>10000 行大文件跳过 diff 只给 summary block；CPU-bound 放 `asyncio.to_thread`。
- plan 文件路径免审批；工作区内外区分 EDIT / EDIT_OUTSIDE 两种 action（session 级"总是允许"分开记忆）。

### 5.2 file/read.py（ReadFile）
- 三重限制：MAX_LINES=1000、MAX_LINE_LENGTH=2000（超长行截断加 `...`）、MAX_BYTES=100KB。
- **负 offset tail 模式**：`line_offset=-N` 读末尾 N 行——`deque(maxlen=N)` + 反向字节预算扫描，头部截断保留起点信息。
- `_read_forward`：全文件迭代计 total_lines（collecting flag 停收集不停计数），message 报告 "N lines read... Total lines: M."。
- 行号格式 `f"{line_num:6d}\t"`（cat -n 风格）。
- 敏感文件阻断（is_sensitive_file）；媒体文件用 header 字节嗅探（MEDIA_SNIFF_BYTES）识别后转多模态。

### 5.3 todo / think / web/fetch
- SetTodoList：`todos=None` = 读取模式（返回文本给模型）；root 存 session state、subagent 存自己 instance state.json；malformed item 逐条跳过。
- Think：直接返回 "Thought logged"，纯上下文记录，零副作用。
- FetchURL：服务端 fetch（Bearer + X-Msh-Tool-Call-Id）失败 → 本地 aiohttp（`ClientTimeout(total=180, sock_read=60, sock_connect=15)`）+ trafilatura 提取（include_comments/tables, output txt）fallback；`text/plain|markdown` 直通不提取；浏览器 UA 伪装。

**(c) 移植点**：非 fuzzy replace 哲学；tail 读取的 deque 实现；三重截断上限与明确的截断提示语；trafilatura fallback 链。

---

## 6. approval_runtime / background / hooks / skill / subagents

### 6.1 审批（approval_runtime/ + soul/approval.py）
- **两层架构**：`Approval`（策略层：yolo/afk/session cache）+ `ApprovalRuntime`（机制层：请求登记、waiter future、事件发布、wire 桥接）。
- `ApprovalRequestRecord`：id/tool_call_id/sender/action/description/display/source/status/response/feedback/approved_via_session_cache/时间戳。
- `wait_for_response`：**共享 waiter + 引用计数**（`_waiter_counts`）支持多观察者；`asyncio.shield(waiter)` 防调用方取消把 future 弄脏；timeout 时只有最后一个观察者才 pop waiter 再 cancel（防 set_exception 到无人观察的 future）。
- `cancel_by_source(kind, id)`：来源（前台 turn / 后台 agent）生命周期结束时批量取消其 pending 请求。
- `ApprovalSource` 用 ContextVar 传播（子 agent 的请求自动携带 agent_id/subagent_type，UI 可显示来源）。
- `ApprovalResult.__bool__` 向后兼容 `if not result:`；拒绝时子 agent 收到"换方法/别重试/别绕过"引导文本；带 feedback 的拒绝 `has_feedback=True`（继续 turn），纯拒绝停 turn。
- `approve_for_session`：加入 auto_approve_actions 并**批量 resolve 同 action 的其他 pending**。
- afk 隐含 auto-approve；runtime_afk（--afk 本次调用）不持久化，关 afk 时一并清掉。

### 6.2 background/（后台任务）
- **文件即状态**：每任务目录下 spec.json（不可变）/ runtime.json（worker 写）/ control.json（管理端写 kill 请求）/ consumer.json（UI 已读 offset）/ output——**跨进程通信全靠文件**，无 IPC。
- bash 任务由独立 worker 进程执行（`python -m kimi_cli.cli __background-task-worker --task-dir ...`），worker 心跳写 runtime.heartbeat_at；agent 任务在本进程 asyncio task。
- 状态机：created→starting→running→awaiting_approval→(completed|failed|killed|lost)；`TERMINAL_TASK_STATUSES` 元组 + `is_terminal_status()`。
- `recover()`：心跳超时（15s）→ lost（区分"从未心跳"/"心跳过期"两种 failure_reason）；有 kill 请求的标 killed；**重读 runtime 缩小与 worker 的竞态窗口**（double-check）。
- `publish_terminal_notifications`：dedupe_key=`background_task:{id}:{terminal_reason}` 幂等发布；发布成功才 set completion_event。
- kill：Windows `taskkill /PID /T /F`；Unix 先 killpg(pgid) 再 kill(pid)；agent 任务 kill 时同步 `cancel_by_source("background_agent", task_id)` 取消其 pending 审批。
- timeout_s 显式 None 检查（`timeout_s if timeout_s is not None else default`，防 `0 or default` 吞掉 0）。

### 6.3 hooks/
- 双来源：config.toml shell 命令（服务端）+ wire 订阅（客户端），统一 `HookEngine.trigger(event, matcher_value, input_data)` 并行执行，`asyncio.gather` 聚合，**任一 block 即 block**。
- `run_hook` 协议：stdin 收 JSON payload；**exit 2 = block（stderr 为理由）**；exit 0 + stdout JSON 的 `hookSpecificOutput.permissionDecision=="deny"` 也算 block；其余 fail-open（超时/异常→allow）。
- matcher 是正则，空匹配一切；同 event 内命令去重。
- `fire_and_forget_trigger`：强引用集合 + done_callback 记录失败（again 防 GC）。
- 遥测在 fail-open try 之外——遥测失败绝不能吞掉 block 结果（安全关键注释）。
- 事件 payload 构造集中在 events.py（PreToolUse/PostToolUse/PostToolUseFailure/UserPromptSubmit/Stop/StopFailure/SessionStart/SessionEnd/SubagentStart/SubagentStop/PreCompact/PostCompact/Notification），字段与 Claude Code hooks 生态兼容。

### 6.4 skill/
- 多层发现：`--skills-dir`（override，priority 最高）> project（.kimi/.claude/.codex/.agents 下 skills，从 **git 根**找起）> user（~/.kimi > ~/.claude > ~/.codex；~/.config/agents > ~/.agents）> extra_skill_dirs（config，相对路径按项目根解析）> plugins > builtin。同名 first-wins。
- 两种布局并存：`<dir>/<name>/SKILL.md`（子目录形式，优先）与 `<dir>/<name>.md`（扁平形式）；顶层裸 SKILL.md 视为杂散文件跳过。
- description 三级 fallback：frontmatter → 正文首个非空行（截 240 字符 + …，跳过孤立 `---`）→ "No description provided."。
- flow skill：SKILL.md 里的 mermaid/d2 代码块解析成流程图驱动多步执行；解析失败降级为 standard。
- 系统提示注入按 scope 分组（### Project / User / Extra / Built-in），让模型能区分"项目里的技能"。
- 路径去重：本地后端先 `Path.resolve()` 解 symlink 再 canonical()。
- 手写 fenced code block 解析器（支持 ``` 与 ~~~、任意长 fence、`{lang}` info string）。

### 6.5 subagents/
- `run_soul_checked`：分类异常 → `SoulRunFailure(message, brief)`；只放行 CancelledError/RunCancelled；校验末条必须是 assistant 消息。
- **摘要延长**：最终回复 < 200 字符时用 SUMMARY_CONTINUATION_PROMPT 追问一次要求详细总结。
- 每次运行一个稳定的 `ApprovalSource(kind="foreground_turn", id=uuid, agent_id, subagent_type)` 经 ContextVar 设置，finally 里 `cancel_by_source` 兜底取消遗留审批。
- `_make_ui_loop_fn`：子 agent wire 消息全部写 output 文件；Approval/Question/ToolCallRequest **原样上抛**父 wire（穿透审批），其余包成 `SubagentEvent(parent_tool_call_id, agent_id, event)` 供 UI 嵌套渲染。
- 返回给模型的结构化文本：`agent_id: / resumed: / actual_subagent_type: / status: / [summary]`——模型可用 agent_id resume。
- 工具策略 `ToolPolicy(mode="inherit"|"allowlist", tools=(...))`。

**(c) 移植点**：审批 runtime 的 waiter 引用计数 + shield；文件式后台任务状态机 + 心跳恢复；hooks 的 exit-2 协议与 fail-open；skill 多层发现与 description fallback；subagent 摘要过短追问。

---

## 7. config.py / llm.py

- config：TOML（tomlkit 保留注释格式）+ JSON 双格式；legacy JSON 一次性迁移；`model_validator` 交叉校验 default_model∈models、model.provider∈providers；SecretStr 存 api_key + `field_serializer` 序列化时还原；`is_from_default_location`/`source_file` 用 `exclude=True` 不落盘。
- LoopControl：max_steps_per_turn=1000、max_retries_per_step=3、reserved_context_size=50000、compaction_trigger_ratio=0.85；`AliasChoices` 兼容旧字段名。
- llm.py：`create_llm` 按 provider.type match/case 工厂；环境变量覆盖（KIMI_BASE_URL/API_KEY/MODEL_NAME/...）返回 applied dict 供诊断显示；`derive_model_capabilities` 按模型名推断（含 "thinking"/"reason" → always_thinking）。
- 装饰器式 provider 包装：`_KimiRequestChatProvider`（请求级 generation overrides）、`_TraceCallbackChatProvider`（trace_id 回调）——**组合而非继承**，任意叠加。
- `_chaos` provider：包一层 `ChaosChatProvider(error_probability=0.8, error_types=[429,500,503])` 做故障注入测试；`_scripted_echo` 用脚本文件回放响应做 E2E 测试。

**(c) 移植点**：SecretStr + serializer；chaos/scripted-echo 测试 provider（zall 的 E2E 极需要）；环境变量 override 的 applied 记录。

---

## 8. packages/kosong（LLM 抽象层）

- **三层 API**：`ChatProvider.generate() → StreamedMessage`（provider 层）→ `kosong.generate()`（流合并层）→ `kosong.step()`（工具编排层）。
- `ChatProvider` 是 `@runtime_checkable Protocol`（非基类）：name/model_name/thinking_effort/generate/with_thinking。`RetryableChatProvider` 可选协议：`on_retryable_error(error) -> bool`（Kimi 实现 = 重建 httpx client）。
- **流式合并算法**（_generate.py）：`pending_part` 单缓冲 + `MergeableMixin.merge_in_place(other) -> bool`——TextPart 吸收 TextPart、ToolCall 吸收 ToolCallPart（arguments 串接）、ThinkPart 吸收 ThinkPart（有 encrypted 后拒绝合并）；merge 失败即 flush 前一块（此时 ToolCall 完整 → 触发 on_tool_call 回调，工具**在流式过程中就开始并行执行**）。
- 空响应检测：无 content 无 tool_calls → APIEmptyResponseError；**只有 ThinkPart 无正文** → 也算空响应（流中断/max_tokens 耗尽在推理阶段的特征）。
- `step()`：工具结果是 future dict，`StepResult.tool_results()` 逐个 await；异常时取消所有 future + gather(return_exceptions=True) 防悬挂任务。
- 错误层级：ChatProviderError → APIConnectionError / APITimeoutError / APIStatusError(status_code, request_id, trace_id) / APIEmptyResponseError；`convert_httpx_error` 统一转换。
- TokenUsage：input_other/output/input_cache_read/input_cache_creation，property input/total。
- Kimi provider 细节：`stream_options={"include_usage": True}`；trace_id 从响应头 x-trace-id；空 content + tool_calls 时**整个删掉 content 字段**（compat 层拒绝空 text part）；reasoning_content 空字符串也保留为 ThinkPart（"推理过但为空"≠"没推理"，preserved-thinking 后端要求每个 assistant turn 都带）；`$` 前缀工具序列化为 builtin_function；MCP schema 缺 type 的属性本地补全（ensure_property_types）。
- Message 序列化：单 TextPart 时序列化为纯字符串（省 token 且兼容），validator 反向兼容字符串→[TextPart]。
- ContentPart 用 `__init_subclass__` 注册表 + type 字段做多态反序列化。

**(c) 移植点**：Protocol 式 provider 抽象 + MergeableMixin 流合并（zall 可直接借用该模式）；think-only 空响应检测；工具流式并行启动；单 TextPart 字符串化。

---

## 9. wire/ 协议

- **Wire = SPMC 广播通道**：raw 队列（逐 chunk）+ merged 队列（soul_side.send 内嵌 merge buffer，用同一 MergeableMixin 合并后发布）。UI 可选订阅 raw（打字机效果）或 merged（整块）。
- `_WireRecorder` 订阅 merged 队列异步落盘 wire.jsonl → **会话回放/标题提取/fork 全部基于该文件**。
- `WireMessageEnvelope {type: 类名, payload: dict}`：以类名为 tag 的通用信封；`flatten_union(Event)` 自动从 union 类型生成 isinstance 元组与名字表；旧名 "ApprovalRequestResolved" 映射到 ApprovalResponse 做版本兼容。
- Request 类型（ApprovalRequest/QuestionRequest/ToolCallRequest/HookRequest）自带惰性 future：`wait()`/`resolve()`/`resolved`——**消息即信道**。
- wire.jsonl 首行 metadata `{"type":"metadata","protocol_version":"1.10"}`；缺失视为 legacy 1.1；`is_empty()` 跳过 metadata 行。
- jsonrpc.py：JSON-RPC 2.0 封装（initialize/prompt/steer/replay/set_plan_mode/cancel 入站；event/request 出站）；ClientCapabilities 协商（supports_question/supports_plan_mode）；自定义错误码 -32000~-32004（INVALID_STATE/LLM_NOT_SET/.../AUTH_EXPIRED）。

**(c) 移植点**：raw/merged 双队列；类名信封 + flatten_union；Request 自带 future；jsonl 首行版本头。

---

## 10. utils/ 巧思集

| 文件 | 要点 |
|---|---|
| string.py | `shorten`：先归一空白，优先词边界截断，**CJK 无空格时硬切**（不会塌缩成只剩省略号）；`shorten_middle` 中间省略 |
| term.py | `ensure_new_line`：ANSI `\x1b[6n` 查询光标列（Unix 非阻塞读 + 0.2s deadline + select 轮询；Windows GetConsoleScreenBufferInfo），不在 0 列则补 `\n`——**子进程输出无尾换行时 prompt 不会错位**；`ensure_tty_sane` 恢复 ISIG/ICANON/ECHO + VMIN/VTIME，防 raw 模式崩溃后 Ctrl+C 失灵 |
| diff.py | 见 5.1；`asyncio.to_thread` 包 CPU-bound diff |
| sensitive.py | 高置信模式列表（.env*/id_rsa/credentials）+ 豁免（.env.example）；`sensitive_file_warning` 聚合报告 |
| io.py | `atomic_json_write`：mkstemp 同目录 + fsync + os.replace |
| path.py | `next_available_rotation` O_EXCL 原子占位；`list_directory` 两层树形列表宽度上限（30/10）+ "... and N more"（控 token 预算）；`normalize_user_path` MSYS `/c/...`→`C:\...`；`sanitize_cli_path` 剥拖拽引号 |
| aioqueue.py | Python<3.13 的 Queue.shutdown polyfill（哨兵对象 + getter 计数） |
| broadcast.py | BroadcastQueue：subscribe 返回独立 Queue，publish 扇出 |
| signals.py | `install_sigint_handler`：Unix add_signal_handler，Windows fallback signal.signal，返回保证不抛异常的卸载函数 |
| slashcmd.py | 见 1.4 |

---

## 六大专题总结（zall 优先移植清单）

### A. 流式渲染防闪烁
1. Live(transient) 活跃区 + console.print 固化区双层结构。
2. markdown-it top-level block 边界增量提交。
3. `_live_render._shape=None` 重置测量。
4. refresh_per_second=10 足够（不必 30/60）。

### B. 终端宽度 / CJK
1. token 估算区分 ASCII(÷4)/非 ASCII(×1)。
2. shorten 词边界优先 + CJK 硬切 fallback。
3. tab 展开 offset map 的列感知算法。
4. ANSI-16 语法主题兼容一切终端。
5. ensure_new_line 光标列探测。

### C. 输入体验
1. 流式期间 prompt 保活 → 打字即 steer 入队。
2. Ctrl+C：清 buffer → 提示 → 上层中断；Ctrl+D 流式时"跑完即退"。
3. 粘贴折叠占位符；@ 文件补全；/ 命令 + 别名补全。
4. /btw 三方竞争 modal（不打断主 turn 的旁路提问）。

### D. 审批交互 UX
1. y/always/no + 自由文本 feedback；feedback 拒绝继续 turn、纯拒绝停 turn。
2. approve_for_session 批量解决同 action pending。
3. 子 agent 审批穿透到根 UI 并标注来源。
4. 来源生命周期结束批量 cancel_by_source。
5. diff/shell/todo 审批面板类型化渲染（approval_surface）。

### E. 错误恢复
1. 三层：provider client 重建 → OAuth 强刷 → tenacity 指数退避（仅 429/5xx/空响应）。
2. StepRetry wire 事件 → UI 显示重试进度。
3. 错误分类 → 用户可操作建议文案。
4. think-only 响应判空重试。
5. hooks/telemetry 一律 fail-open 且遥测在 try 外。
6. asyncio task 强引用集合防 GC。

### F. 会话持久化
1. 目录式会话（context.jsonl / wire.jsonl / state.json 分职）。
2. `_` 前缀特殊行；坏行跳过；首行协议版本。
3. atomic write + 读改写合并；文件旋转 + checkpoint 重放实现 undo/时间回溯。
4. wire.jsonl 回放重建 UI 历史 + 提取标题 + fork。
