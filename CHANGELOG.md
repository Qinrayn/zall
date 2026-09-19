# Changelog

## [Unreleased]

### 实测反馈轮: /provider 重做 + 干活排队打字 + 对比度/噪声 (2026-09-19)

用户实测反馈四条: 颜色对比差、provider 列表列不全也不知道咋换、干活时不能发信息、裸 `!` 变成任务还刷一屏 API 错误。

- **`/provider` 菜单重做**: 列表改读合并注册表 (内置 6 家 + `[[providers]]` 自定义, 此前自定义 provider 根本不出现); 每行显示 key 状态 (`✓ ready` / `· needs key`)、端点 host、custom 标记, 当前 provider `●` 高亮; 底部两行说明"怎么换" (输入编号或名称) 与"怎么接任意网关" (三件套语法); 选中未配 key 的 provider 时内联询问 key (回车跳过)。
- **干活期间打字排队** (`cli/typeahead.py` 新): 回合进行中键盘输入由采集线程接管 — 此前会被 `flush_stdin_typeahead()` 整个丢弃。打字逐字符回显在光标处 (流式输出期间也可见, 实测反馈"下一行输入根本看不到"), spinner 状态行同步显示 `▌ buffer` 尾部; Enter 提交进队列 (`· 1 queued`), 回合结束后逐条自动作为后续消息发出; 打到一半没提交的提示后丢弃。Ctrl-C 转主线程中断。Windows 走 msvcrt, POSIX 走 cbreak+select (ISIG 保持, 真 Ctrl-C 仍走 SIGINT)。
- **attic 主题对比度提升**: dim `#8c8c86→#9c9c95`、subtle `#5f5f5a→#74746d`、accent2/info/success/fail 同步提亮一档; `_shared_console` 注册语义样式名 (`[accent]`/`[dim]`/`[success]`… 跟随主题), 代码不再写 rich 内置色名。
- **API 错误不再刷三遍**: model_call 事件带 `api_error` 标记, 错误响应的 content 不再被当正文 (Markdown) 渲染 — `✗ error` 行是唯一出口; retry 期间只留一行 `· retry N/3 in Xs · ctrl-c to stop`, 不再重复错误全文; 用户中断后不再补"API still unavailable"。
- **裸 `!` 给用法提示**: 不再作为任务发给模型 (实测曾撞上限流刷屏)。
- **checkpoint 锚点防复读**: `[CHECKPOINT k]` 锚点加 internal 说明 — 实测裸标记会被模型复读进回答。

### 真人实测轮: 4 个实测 bug 修复 + Codex 视觉细节收尾 (2026-09-18)

ConPTY 真实终端驱动 (`scripts/pty_drive2.py`) 模拟真人键入, 对 sensenova (deepseek-v4-flash) 跑通多轮真实对话: 启动 banner、`/status` `/keys` `/mcp` `/stats` `/doctor`、`!shell` 直执行、`@file` 注入、补全菜单、恢复提示、`/quit` 干净退出、429 限流自动重试。

- **修复: 自定义 provider 合并不认 `CONFIG_DIR`** — `_merge_custom_providers` 硬编码 `Path.home()/.zall` 且优先于 cwd, 与 `safety.config` 的单源/层级口径脱节 (Windows 中文用户名下 `Path.home()` 还可能解析错位)。改读 `safety.config.CONFIG_DIR`, cwd 级保留为项目层 fallback。
- **修复: 404 "model is not found" 误导方向** — api_base 可达但模型 id 过期时 (实测 sensenova 下线 `deepseek-chat`), 旧文案让用户去查 api_base。`make_error_response` 识别 404+model-not-found 语义, 改为指向 `/model` 切换。
- **修复: 启动恢复提示吞首条任务** — 有残留 autosave 时启动弹 `restore? [y/N]`, 用户此刻已开始打的任务整行被 `ask()` 吃掉且静默丢弃, 会话空等 (实测卡 290s)。非 y/N 长答案现转交 REPL 作为 `_pending_first_input` 首输入, 并提示 "restore declined — your typed text runs as the first task"。
- **修复: UTF-8 BOM 静默击穿配置解析** — 编辑器/旧版写入留下的 BOM 使 tomllib 拒收, 静默回落宽松解析器, 而 `[[providers]]` 的字符串数组与数字全部失效 (`model_prefixes` 变单字符元组、`window_size=128000` 失效 → 状态栏显示 32k)。`load_toml_simple` 读时剥 BOM; 宽松解析器补齐 单层数组/int/float/bool 解析, 两条路径输出一致。
- **视觉: 工作态耗时紧凑格式** (Codex `fmt_elapsed_compact` 口径) — spinner/思考/工具/用量行的 elapsed ≥60s 转 `1m 05s` / `59m 59s` / `1h 00m 00s`, 亚分钟保留 0.1s 精度; 长任务一眼读出量级。

### Codex 吸纳轮: 提示缓存工程 + 控制台交互/视觉对齐 (2026-09-17)

学习源: OpenAI Codex CLI (Rust 源码按需研读) — 概念与工程口径吸收, 全原创实现, 不抄代码。zall 自有框架 (可证伪/可复现: 链哈希 timeline、Proof Gate、IPR-3 model-agnostic) 保持不变。

#### 提示缓存 (缓存命中) — 从"看不见"到"可观测 + 可优化"
- **`core/cache_stats.py` (新)**: `canonical_usage` 把各家 usage 归一为固定键集 (`prompt/cached/cache_write/completion/total`); `CacheStats` 逐次累加出命中率/缓存写入/前缀失效次数; `context_remaining_percent` 用 Codex 口径 (baseline 12k 归一) 算"上下文剩余 %"; `prefix_fingerprint` 给 system+tools 一个稳定摘要。
- **适配器记账**: openai-compat 收 `prompt_tokens_details.cached_tokens` / DeepSeek `prompt_cache_hit_tokens` / 顶层 `cached_tokens`; Anthropic 收 `cache_read_input_tokens` + `cache_creation_input_tokens` (并把 `prompt` 归一为 input+read+write, 修掉"上下文被低估"的口径问题); Gemini 收 `cached_content_token_count`。流式与非流式同源解析。
- **请求侧** (Codex `prompt_cache_key` 对标): 已知支持的 host 默认发会话稳定的缓存亲和键 (同项目同模型 → 同一缓存分片); Anthropic 默认打两个 `cache_control: ephemeral` 断点 (system 兼缓存工具 schema + 对话滚动断点), `ZALL_ANTHROPIC_CACHE=0` / config `anthropic_cache=false` 可关。
- **可观测性**: 缓存命中率进底部状态栏、`/status`、verbose 逐回合用量行; `MODEL_CALL` timeline payload 新增 `prefix_fp` (复现时可核对前缀一致性), 前缀变化计入统计 — 命中与否是**观测量**, 不是估计。
- **流式 usage 自愈**: 已知 host 默认开 `stream_options.include_usage` (缓存统计的前提); 若 provider 以 400 拒绝该字段, adapter 自动关掉并立即重试一次 — 统计降级但请求不失败。

#### 控制台交互 (Codex 对标)
- **底部状态栏重排**: 左侧快捷键提示, 右侧右对齐状态 — `ctx NN% left / 128k`(baseline 归一) 与 `cache NN%`; strict/plan 模式徽标随之显示。
- **工作态状态行**: `⠋ 活动标签 (12s · ctrl-c to interrupt) 1.2k tok` — 括号段固定位置 (Codex StatusIndicator 口径), 长任务一眼掌握"跑了多久/怎么打断"。
- **审批措辞与范围**: 选项改为"会发生什么"式 (`Yes, proceed` / `No, and tell zall what to do differently` / `Yes, and don't ask again`); **always-allow 收窄到命令前缀** (`git status` 放行不等于放行所有 `git`), 非 bash 工具仍按整工具持久化, 旧 `tool_ids` 文件继续兼容。
- **新命令/入口**: `/status`(会话配置+用量+缓存+链哈希头)、`/keys`(键位卡片, 裸 `?` 同效)、`/mcp`(配置的 server 与已注册工具)、`/new`(/clear 同义); `!<cmd>` 直接执行 shell (用户显式命令, 不经模型/确认门); verbose 下逐回合打印会话用量行。
- **压缩摘要** (Codex handoff 口径): 摘要头部改为接手口吻("另一个模型开始了这个任务…接着做而不是重做")并新增 `Objective (latest user request)` 段 — 压缩后模型知道要往哪走, 不只知道做过什么。

#### 视觉 (Codex exec/reasoning cell 口径)
- 工具调用行改用 Codex 的 `▌` 左侧命令条 + 类型色, 工具结果前缀 `└`, 元信息 (耗时/状态/折叠) 统一 dim 后缀; 与既有 `_C` 槽位/ASCII 回退契约一致 (非 TTY 输出不变)。

### 控制台交互对齐 Argus: 参数补全 + runall/last + banner/history (2026-09-14)
- **参数位 TAB 补全** (补齐与 Argus/cmd2 的最大交互差距): 首参之后也有候选 — `/science <TAB>` 全部子命令、`/science run|use|fav add <TAB>` 模块 id (名字前缀命中时给引号名, 与 shlex 解析兼容)、`/science set <TAB>` 选中模块的选项键、`/science profile <TAB>` 预设、`/science fav <TAB>` 子命令、`/science auto <TAB>` 旗标、`/help <TAB>` 全部命令、`/model <TAB>` 模型预设、`/mode <TAB>` 模式。非命令输入 (裸文本/任务) 不受影响 (反例冒烟锁定)。
- **`/science runall <section|tag:x>`** (Argus do_runall 对标): 整组批跑; 无参 = 全部目录。
- **`/science last`** (Argus do_last 对标): 一键重跑上一轮模块集。
- **`/banner`** (Argus do_banner 对标): 重印启动屏 (Ctrl-L 清屏后常用)。
- **`/history [n]`** (cmd2 history 对标): 列最近输入 (多行条目折叠为 `…+k`); Ctrl-R 仍是交互式反向搜索。

### 启动屏重设计 + 控制台排版/配色统一 (2026-09-14)
- **新启动屏 (简洁大气)**: 单层圆角细框 + 顶边中置 ◆ 徽记 + 底边嵌版本装备行 (`v0.5.2 · N commands · M research modules`); 框内为字距舒展的 zall、一句描述与运行态 (model · branch · plan)。无块字/无噪声。ASCII 字形回退时名字上边框 (免异体宽字符错位), 非 TTY (管道/CI) 降级单行文本 — 输出契约不变。
- **排版统一**: `section_header` 面板头与 `kv_table` 信息表水平居中 (Argus `_print_centered` 对标), 与启动屏构图一致。
- **交互配色统一**: prompt_toolkit 补全菜单/底部状态栏/占位符接入主题 Style (原先硬编码 ansiblue 与 attic 主题两张皮); 菜单底色/选中项/滚动条全部走 `_C` 槽位, 主题切换自动跟随。

### 默认界面翻转: console REPL 上位, TUI 转可选 (2026-09-14)
- `zall` 无参默认进入同步 REPL 控制台 (Argus 式主界面: 横幅装备行 + 闪讯 + 面板头 + 信息表 + 命令后状态行); `--tui` 为可选的 inline Textual UI (终端不支持自动回退 console); `--no-tui` 保留为兼容别名 (与默认等价, 旧脚本不报错)。
- 启动路径不再急切加载 textual (console 启动零 Textual 依赖, `import zall.cli.app` 后 textual 不在 sys.modules); 横幅提示行加入 `/science` 入口。
- README 安装说明同步 (tui extra 标注为可选); 既有行为测试同步更新 (默认→repl / --tui 可选进入 / rc=2 回退), 定向 152 测试全绿。

### Argus 吸纳轮: UI 控制台视觉词汇 + 科研工作台 + 自主研究循环 (2026-09-14)

学习源: 桌面 Argus (jasonxtn 的 rich+cmd2 侦察控制台) — 概念吸收, 全原创实现, 不抄代码。

#### UI: 控制台视觉词汇 (render.py, REPL/TUI 共享, 一次构建两处消费)
- 新 helper: flash 闪讯纪律 ([+]/[!]/[-]/[i] 前缀) / section_header 居中面板头 / kv_table 信息表 (已设=绿·未设=暗·caption 引导) / next_steps_panel (Recommended Next Steps) / batch_progress (spinner+n/m+已用时+ETA, transient)。TTY 走 rich 结构, 非 TTY 降级纯文本 (管道/CI 输出契约不变), 颜色全走 _C 槽位 (主题自动跟随)。
- REPL: banner 框内加装备行 `v0.5.2 · N commands · M research modules` (Argus logo 对标); 每条斜杠命令后回显状态行 (Argus _print_status_bar 纪律; render_status_bar 新增 force 参数)。
- TUI: 欢迎卡加 counts 行与 `?` 快捷键提示。

#### 科研工作台 (Argus 控制台架构 → /science, 数据驱动)
- `extensions/science/catalog.{json,py}`: 科研模块目录 (id/name/options/certifier|script/tags), env ZALL_SCIENCE_CATALOG 覆盖 + ~/.zall overlay 合并 + id 冲突自动重排; 内置 4 模块 (Erdős–Straus 密度 5/6 · Diophantine 四元组族 · 覆盖系统 · 完全平方多项式); 名字 token 级模糊 (coverng→Covering)。
- `runner.py`: in-process certifier (秒级) / script 子进程流式 双模; **tier 分级只来自验证器输出** (ProofCertificate.tier / 报告 tier 字段), 绝不从字符串猜 — Argus 用正则猜 severity, zall 把 Proof Gate 精神贯彻到运行器。
- `profiles.py` (quick/deep/exhaustive 预设, 手动 set 永远优先) / `report.py` (results/science/<topic>/ REPORT.md+json, 带 timeline 链尾锚点) / `state.py` (选中·选项·收藏·recent(10)·用5次建议收藏, 持久化 ~/.zall/science_state.json)。
- /science 新子命令: modules [-s|-d|-t] / use <id|name|tmp#> (多匹配给 Tmp# 表) / set·unset (k=v 弹性语法+did-you-mean) / run [ids] [--dry-run] [--timeout] (批跑进度条) / report / profile / fav (add·del·run·list·clear·tag:) / recent。

#### 自主研究循环 (混合自主: 规则执行零 token + LLM 只做目录内提议)
- `auto_loop.py` + `/science auto <topic> [--budget N] [--cycles M] [--module id]`: 假设(锁定,I-1)→实验(start/complete/fail 状态机)→证据(REFUTED 走 NegativeResult 一等公民, UNKNOWN 不记账)→tier 门控→经验蒸馏(record_certificate, CORROBORATED 带界永不升 PROVEN)→下一假设; PROVEN 自动推进假设 CONFIRMED。
- 规则引擎: UNKNOWN/CORROBORATED→扩界重跑 (residual_bound×2 / search_bound×4 / coef_max+2, 均有上限); REFUTED 或界用尽→LLM 在**目录内**提议下一模块+参数 — JSON 严格校验 (模块必须在目录、选项过白名单, 夹带即拒), 预算尽/无模型自动降级纯规则, 绝不阻塞。
- 全程可审计: ScienceStore 记账 + 链哈希 timeline 事件 (SYSTEM_INJECTION/science_auto) + 蒸馏进经验库。

### UI 轻便化: `?` 快捷键帮助层 (2026-09-03)

#### Claude 式内联优化第一刀: 按 `?` 弹出快捷键帮助面板 (Codex CLI ?overlay 同款)
- 新增 `tui/widgets.py::HelpOverlay`: 输入区上方的键位帮助浮层, 空输入按 `?` 开/关, Esc 关闭 — **不会**把 Esc 误发成中断 (打开时 Esc 只关面板, 反例测试锁定)。
- 帮助数据来自**真实绑定** (TuiApp.BINDINGS 的 show=True 项 + 输入区固定功能 Enter/Shift+Enter/↑↓ 历史// 命令/@ 文件/Tab 补全/Esc 中断), Codex key-hint 思想: 显示的就是实际可用的一套, 换键自动跟随, 杜绝"帮助说一套".
- 非空输入中的 `?` 照常作为字符输入 (不抢键); 确认门 (select_mode) 中 `?` 不接管任何决策键。新增 I-HELP 不变量 22 测试 (test_help_overlay_invariants.py, 含以上全部反例孪生 + CSS 无 hex / ASCII 回退无 emoji)。


### kimi CLI 残值终扫 + 源码删除 (2026-07-26)

#### 删除前最后一次全面残值清扫 — 补捕 5 项真金 (上轮"榨干"宣告修正)
- **bash 防挂死**: 子进程 stdin 接 DEVNULL — 交互式提示 (git 密码/confirm) 立即收 EOF 而非挂到 timeout (kimi shell 关 stdin 技巧)。
- **read_file 负偏移尾读**: offset=-100 读最后 100 行 (deque 单趟流式 + 精确总行数), 日志场景刚需; 旧"负数修正为1"语义升级。
- **grep 超时部分结果**: rg 超时时返回已扫到的匹配 + 明确 PARTIAL 提示, 而非整体作废退 Python 重扫 (kimi grep_local 对标)。
- **compactor 分级压缩协议**: 规则折叠摘要新增"错误与解法必留"最高优先段 (压缩后模型仍知哪里失败过, 不重踩坑), 输出按 kimi compact.md 优先序 errors > decisions > files > 计数。
- **skill 多品牌目录复用**: 额外发现 ~/.zall・~/.claude・~/.codex・~/.agents 及项目级同名目录的 SKILL.md 技能 (frontmatter 解析 + 渐进披露: prompt 只指向文件, 正文用时才读) — 用户已装 Claude/Codex 技能直接可用; 自家 toml 同名优先。
- 新增 I-TAIL/I-SKILLDIR/I-SUMMARY-ERR/I-BASH-STDIN 不变量 11 测试 (test_kimi_final_sweep_invariants.py, 含反例孪生); 旧语义测试同步升级。
- 终扫其余判定无残值或已有等价物: web/search、broadcast/aioqueue、clipboard(Windows 路径即 Pillow 默认)、usage(专有配额 API)、write/replace、magic 嗅探(zall is_binary 够用)、EAGAIN 重试(Linux errno, zall run() 模型不适用)、git-bash 四级定位(zall 已有等价链)、会话导出/跳过版本提醒(低价值录待办不引入)。
- 清扫完成后按用户指令删除桌面 kimi-cli-main 源码 (知识已全部转化为 zall 原创实现 + 不变量测试)。


### kimi CLI 吸纳收口轮 (2026-07-26)

#### committed-boundary 流式提交边界 (kimi visualize/_blocks 对标)
- 新增 `cli/tui/commit_boundary.py`: 超长流式回复 (>6K chars) 把稳定前缀提前固化进历史, 活跃块只留尾部 — 重渲染成本从 O(全文) 降到 O(尾部), 长回复不再越流越卡。安全切点: 只在段落边界切 + 代码围栏成对 (markdown 不裂) + 最小前缀防碎片化。新增 I-COMMIT 不变量 8 测试 (含未闭合围栏不切/短回复不提交反例)。

#### PreCompact/PostCompact 扩展事件 (kimi hooks 事件面对标)
- ContextManager._auto_compact 压缩前后广播 `on_pre_compact`/`on_post_compact` 给扩展 (含 reason/压缩量/消息数; 失败静默 IPR-0)。新增 I-HOOK-COMPACT 2 测试 (含压缩量 0 无 post 反例)。

#### 吸纳终局审计 — 12 项盘点清单全部处置 (9 落地 + 3 论证不适用)
- **已落地 (均含不变量测试)**: context_rewind (D-Mail) / 双条件压缩 / 重复梯度惩罚+同步去重 / 动态注入 providers / 通知 claim-ack+子代理闭环 / MCP 三段式延迟加载 / ask_user 问卷 / btw 侧问 / 超限输出落盘分页 / committed-boundary / PreCompact-PostCompact。叠加早前轮次: 主题单色源、ANSI-16 语法主题、敏感文件防护、type-ahead 防护、粘贴折叠、steer has_steers→continue、ScriptedAdapter、diff 色、命令面板/@补全/底栏、工具输出截断纪律。
- **论证不适用 (非遗漏, 是设计差异)**:
  - ToolReturnValue 三通道 (output/message/display): zall 的 ToolResult(output→模型, artifacts→渲染) + ToolPanel 类型感知渲染已实现同等分离; display block 注册表是为 kimi wire 协议序列化设计, zall 无 wire。
  - 审批运行时 ContextVar/共享 future 并发原语: zall 子代理 auto-reject (无人监督不擅自执行) + 单 root 交互, 通过设计避免了 kimi 需要复杂并发原语解决的多代理并发审批问题。
  - acp/wire (IDE 集成协议)、kaos (SSH 远程 OS 抽象)、web/vis (Web UI)、telemetry: 产品方向差异 (zall = 本地可证伪 agent CLI), 非技术债; SubagentStart/Stop 可观测性已由通知闭环 + timeline 覆盖。


### kimi CLI 深度吸纳第五轮 (2026-07-26)

#### context_rewind — 模型驱动上下文回滚 (kimi D-Mail 机制对标, 全原创实现)
- 新增 `core/rewind.py` (单槽信箱+校验) / `core/loop_rewind.py` (落锚+施加, loop 协作者模式) / `tools/context_rewind.py` (工具本体, 倒置成功语义)。每步落 [CHECKPOINT k] 可见锚点 (仅当工具注册, 未注册零成本); 模型可把膨胀上下文 (大文件/大搜索/调试弯路) 折叠回锚点并携带"给过去自己的信"。compaction 之外的第二把上下文武器 — 与本轮的 executor 中心截断形成主动+被动双层防线。
- timeline 先记 CONTEXT_REWIND 后改消息 (§6.1 真相源不受损); 回滚后 doom-loop 追踪复位 (kimi begin_step 空列表复位对标)。新增 I-REWIND 不变量 6 测试 (含 e2e 与反例孪生)。

#### MCP 延迟后台加载 (kimi KimiToolset 三段式状态机对标)
- 新增 `mcp/deferred.py` DeferredMCPLoader: idle→loading→ready, 启动即回、连接在 daemon 线程、首个回合构建前才收敛 (届时通常已就绪, 等待为零)。REPL 与 TUI 双入口接线; 启动路径不再被 MCP 连接 (子进程+握手, 百 ms~秒级) 阻塞。加载日志进 status 缓冲 (不污染 TUI); 后台失败降级空工具集 (IPR-0)。退出路径收敛后统一关闭防连接泄漏。新增 I-MCPDEF 不变量 5 测试。

#### 工具重复梯度惩罚 (kimi toolset dedup/repeat 对标)
- 新增 `core/repeat_guard.py`: 单调用粒度连击追踪, 升级链 3→r1 轻提醒 / 5→r2 重提醒 / 8→r3 勒令停止 / 12→强制优雅止损 (awaiting_input 非 error)。提醒**直接追加在工具结果内** (kimi 实证该位置模型更听得进)。与现有 doom-loop (步级序列哈希) 互补共存; context_rewind/新回合复位连击。
- 同步内去重: 同一步内完全相同的调用只执行一次, 重复者得占位结果 (不过门不执行, tool_call 配对不破)。新增 I-REPEAT 不变量 8 测试。

#### 双条件自动压缩 (kimi should_auto_compact 对标)
- WatermarkMonitor 新增预留回复空间条件: tokens + reserved (16K, 小窗口取 window/4) >= window 时即使比率未达阈也强制压缩 — 防"比率未达但大回复直接撑爆窗口"。新增 I-WM-RESERVED 测试 (含反例孪生)。

#### 按步动态注入 providers (kimi DynamicInjectionProvider 对标)
- 新增 `core/dynamic_inject.py`: provider 协议 + **历史推断节流** (反向扫历史找上条提醒数 assistant 轮 — 计数器与历史永不漂移; 压缩摘掉旧提醒后自愈重注, 无需回调) + 稀疏/全文交替省 token。首个 provider: PlanModeReminderProvider (plan 只读纪律每 5 轮重申, 防长会话淡忘)。新增 I-DYNINJ 不变量 7 测试。

#### 通知中心 + 并行子代理闭环 (kimi background→notification→inject 对标)
- 新增 `core/notifications.py` NotificationCenter: pending→claimed→acked 状态机, handler 异常留 claimed + 超时 recover 归还重投 (**至少一次递送**), dedupe_key 幂等, 每步限流 4 条。
- 闭环接线: spawn_subagent 并行完成回调自动发布通知 → 主 loop 每步开始前注入上下文 (system 消息 + timeline) — **模型不再需要轮询 list_subagents**。root-only 语义: 仅主 loop 设通知中心, 子代理不误吞。新增 I-NOTIFY 不变量 7 测试。

#### 超限工具输出落盘 + 分页指引 (kimi Background 输出协议对标)
- executor 新增 `clip_with_spill`: 中心截断触发时全量落盘 `.zall/tool_outputs/step{N}_call{M}_{tool}.txt`, 截断提示附**精确路径 + read_file 分页读取指引** — bash 等不可重放输出也能事后补读而非永久丢失。落盘失败降级纯提示 (IPR-0)。新增 I-CTX-SPILL 测试 (含小输出不落盘反例)。

#### /btw 侧问 (kimi soul/btw.py 对标)
- 新增 `cli/commands/btw.py`: 长任务中途随口问一句, **不写入主对话历史** (agent 工作记忆不被打乱)。三大 kimi 巧思全部保留: 复用主对话消息前缀 + 相同工具 schema (**prompt cache 对齐省钱**, 只为增量付费); DenyAll 语义 (工具可见但一律拒绝); maxTurns=2 (首轮误调工具喂回拒绝结果给第二次机会)。新增 I-BTW 不变量 5 测试 (含主上下文纹丝不动/不无限循环反例)。

#### ask_user 结构化问卷工具 (kimi AskUserQuestion 对标)
- 新增 `tools/ask_user.py`: 模型在决策真正改变下一步时向用户提**结构化选择题** (1-3 题×2-4 选项, 含权衡说明与推荐标注), 系统自动补 "Other" 自由输入项, 答案以机器可解析 JSON 返回。复用现有交互基建: TUI → TuiUserResponder 选择菜单桥 / REPL → select_prompt 数字选择器 (含 type-ahead 冲刷)。
- kimi 关键语义保留: 无人在场 (非交互/未注入) → 自动 dismiss 返回"自行决策" (绝不阻塞); **root-only** — 子代理工具集强制排除 ask_user (后台线程不得弹面板抢用户)。新增 I-ASKUSER 不变量 8 测试。


### 真实使用反馈修复 (2026-07-26)

#### 上下文膨胀修复 (首轮对话占 20%/1M 上下文 → 响应超慢的根因)
- **executor 上下文入口中心截断** (`clip_tool_output_for_context`): 工具输出追加进消息历史此前**无上限** (各工具自身上限宽松: read_file 2000 行可达数百 KB), 多步首轮对话即可堆到数十万 token 且每次 model call 全量重发。现在单条工具消息封顶 32K 字符 (≈8K tokens), 保头保尾 + 可见截断提示 (模型可用 offset/limit 补读); timeline 仍记录全量。新增 I-CTX-CAP 不变量 3 测试 (含 executor 集成反例)。

#### TUI 回车修复 (“每次回车没用要手动点 steer”)
- 运行中按 Enter 从“进 pending 队列等回合结束”改为**立即 steer 注入当前回合** — 慢端点下回合可持续数分钟, 旧行为让回车看起来无反应。显示用户气泡 + “↳ injected into current turn”反馈。
- 回合末尾未消费的 steer (模型已 STOP) 不再丢失 → 自动降级为新回合。新增回归测试 (反例: 回归排队行为即失败)。

#### steer 链路加固 (第二轮反馈 “steer 无效” — 确定性探针定位后三处加固)
- 新增 `scripts/probe_steer_repro.py` (ScriptedAdapter+Pilot, 三场景): A 中途 Enter 回合内注入 / B STOP 后 steer 不丢 / C 真实键盘逐键路径 — 机制层实测三场景全 PASS, 排除输入层被挡。
- **worker 全局 try/finally 守护 `_agent_running`**: 此前构建段 (@展开/build_repl_loop/renderer) 抛异常会让标志永久卡 True → 此后每次回车都进无人消费的 steer 队列 (“回车无效”的真实可崩溃路径)。
- **kimi parity (kimisoul `has_steers → continue`)**: 模型 STOP 时若本步已攒 steer, 不结束回合 — 注入后强制再步, 用户消息在同一回合内立即得到回应 (此前降级新回合, 慢端点下体感延迟翻倍)。
- 降级路径双气泡修复: Enter 时已显气泡的 steer 降级运行不再重复 echo。

#### 主题精简 (“主题只用一个算了”)
- 移除 obsidian / ansi 主题, 只保留希腊美学 **attic** 单主题; 旧配置持久化的 obsidian/ansi 自动自愈回 attic (不崩)。`/theme` 描述同步; zall-ansi 语法主题解析器作为库能力保留。主题/diff/syntax 不变量测试改写为单主题语义 (含“移除主题不得残留/不可切换”反例)。

#### checkpoint 自杀 bug 修复 (B12 GC 与写入互打)
- `save_checkpoint` 在复制完文件后才调 `_ensure_loaded()`, 而它的孤儿 GC 回收一切 `_tmp_*` 目录 — 把自己正在写的快照删掉, meta 写入 FileNotFoundError → 静默返回 None, **文件快照全灭 / `/undo` 失效**。修复: `_ensure_loaded()` 提到临时目录创建之前。checkpoint 18 测试恢复全绿。

## [0.1.0] - 2026-07-26 · Collatz 研究+Bugfix 双段 e2e (2026-07-26)

### loop.py 瘦身 (第四轮, 工程化持续)
- 模型调用面 (`_call_model` + `_call_model_stream`, ~184 行内聚块) 抽取到 `core/loop_model_call.py` (无状态自由函数, 同 loop_perception/loop_checkpoint 协作者模式); loop.py 保留 2 行薄委托 (测试 patch 面不变)。纯搬运零逻辑变更, loop.py 1983→1869 行。
- 行为等价守护: test_loop_stream/stream_error/loop/loop_step/observer/retry 65 测试全绿 + IPR-3 门禁通过。

### 敏感文件防线闭合
- `code_understanding._read_file_content` 接入同源防线 (此前直接 open() 绕过 read_file 防护); 全工具层 open()/read_text 审计: grep/read_file/code_understanding 已防护, project_analysis 仅计行数不回显内容, git_protect/batch_edit 非内容回显面。

### 真实研究任务双段 e2e (中等难度, 全流程检验)
- 段一 (研究): one-shot `zall --yes -j` 跑 Collatz 停止时间研究 [1,100000) — 7 步/6 工具/44.8k tokens, 产出 collatz.py + RESULTS.md。
- **PR-0 独立核验抓到 agent 真错**: max(77031→350) 与 σ(27)=111 一致, 但均值 114.98 vs 独立参考 107.54 — agent 对整个 memo 字典求均 (含范围外中间键)。
- 段二 (bugfix): 只喂分歧现象不喂答案, zall 自主定位根因 (memo 含 ~59K 超限中间值)、修复、复跑得 107.5382 与独立参考一致, RESULTS.md 补 Correction 节 — 10 步/10 工具, EXIT=0。
- 结论: 多步研究与诊断任务链路 (写码/跑码/分析/报告/回归修复) 在真实 API 下端到端可用; 独立核验环节是抓错关键 (agent 自我宣称不可信, 与 PR-0 一致)。

### - 希腊美学转正 · 确认门 e2e 实证 · 工程化清理 (2026-07-26)

### 希腊美学 attic 主题转正为默认 (用户数学审美)
- `DEFAULT_THEME` obsidian → **attic** (月桂金 #c9a227 / 爱琴海蓝 / 大理石白 / 陶土红 / 橄榄绿); obsidian 保留可 `/theme` 切回。用户 `~/.zall/config.toml` [ui].theme 同步。
- 新增 I-THEME-6 不变量 (含反例): 默认主题必须是 attic, 退回即测试失败; 主题/diff/渲染相关 fixture 改为随 `DEFAULT_THEME` (不再硬编码 OBSIDIAN)。
- 实机验证: TUI SVG 截屏断言 attic 月桂金真实上屏且旧 amber (#e0a83b) 绝迹。

### TUI 确认门真人交互 e2e (真实 API + Textual Pilot, 四阶段后续)
- 新增 `scripts/e2e_tui_confirm.py`: 假 HOME 隔离 (不污染真实 ~/.zall) → 鼠标点击输入框 → 逐键敲入写文件任务 → greylist 确认菜单弹出 → 模拟真人 Enter 批准 (allow once) → 文件落盘 + `USER_RESPONSE accept` 入 timeline → attic 视觉断言, **10/10 断言通过**。
- 严谨判据升级: 不硬断言"菜单必弹" (模型可自由选 whitelist 工具), 而断言 **greylist 决策 ↔ 菜单弹出 严格对应** (有 greylist 无菜单 = 门被绕过 = 真 bug)。
- 新增 `scripts/probe_confirm_repro.py`: ScriptedAdapter 确定性确认门复现 (无 API, 秒级); 一次疑似"确认门旁路"的调查由它 + timeline 审计定性为**非产品 bug** (外部沙箱阻写致 always_allow 豁免未被摘除, 门对已豁免工具自动放行属设计行为)。
- 经验沉淀: Pilot 测试中 StatusBar 周期 timer 使消息泵永不静默, `pilot.press/pause` 需限时旁路 (按键已同步送达)。

### 工程化清理 (目录清晰)
- 删除根目录顽固 `nul` 保留名文件 (已验证方法: cmd `del \\.\<path>` 设备路径; 已回填 `scripts/del_nul.py` Method 4)。
- 移除误入仓库的 `Microsoft/` (PowerShell ModuleAnalysisCache) 与 `tmp/` 杂物; `tmp/phase1_acceptance.py` 迁入 `scripts/`; minecraft 日志解除 git 追踪。
- `.gitignore` 补 `tmp/`、`.e2e_playground/`、`minecraft_*.txt`。

### REPL 路径同源 type-ahead 防护 (续 TUI 宽限期)
- 新增 `responder.flush_stdin_typeahead()`: greylist 交互提问前冲刷 OS stdin 滞留按键 (Windows msvcrt.kbhit/getwch; POSIX termios.tcflush); 非 TTY/异常静默降级。接线不变量: 仅裸 input 才冲刷, 注入 ask_fn/input_fn (测试/prompt_toolkit) 绝不碰 OS 缓冲。
- `repl_ui._greylist_choose` 接线同款防护; TUI 子类天然跳过 (自有宽限期)。
- `test_select_typeahead_guard.py` 扩充至 9 测试 (+3: 非TTY跳过/TTY排空/接线反例)。

### config 加载器漂移修复 (I-CFG-PARITY)
- `config_layers._config_to_dict` 与 `safety.config` 对齐: [model] 内直写 provider/api_key、采样参数 (temperature/top_p/max_tokens/reasoning_effort)、window_size 现均参与层叠 (此前两套加载器对同一文件给出不同结果)。
- env 层对齐: 新读 ZALL_PROVIDER/ZALL_TEMPERATURE/ZALL_MAX_TOKENS/ZALL_TOP_P/ZALL_WINDOW_SIZE/ZALL_REASONING_EFFORT (非法值不崩不写)。DEFAULTS 键集与 safety.config 对齐 (新增对齐不变量: 任一方新增键另一方缺失即测试失败)。
- `test_config_layers.py` 新增 TestConfigLoaderParity (4 测试含反例)。

### 确认菜单 type-ahead 防护 (kimi 审批面板思想, 安全加固)
- 风险 (e2e 调查中识别): 模型运行时用户提前敲的 Enter/数字键滞留在消息泵, 确认菜单打开瞬间被重放 — 一个滞留回车即可误批写盘操作。
- 防护: `open_select` 记录打开时刻; 宽限期 `SELECT_GRACE_S=0.35s` 内决策键 (Enter/1-9) 被吞 (纯函数 `is_typeahead_decision`); 导航 (↑↓)/Esc 不受限 (误 Esc 是安全方向)。真人看到菜单再决策 ≥300ms, 无感知延迟。
- 新增 `tests/test_select_typeahead_guard.py` (6 测试含反例: 宽限期后必须可达/导航永不吞/接线不变量)。

### 敏感文件防护 (kimi utils/sensitive.py 对标, 密钥不进上下文)
- 新增 `safety/sensitive.py`: 高置信度敏感模式 (.env/.env.* / SSH 私钥 id_rsa等 / .aws·.gcp credentials / .netrc / **zall 自身 trust_anchor_key**), 模板豁免 (.env.example 等), 大小写不敏感 + Windows 反斜杠兼容。
- 接线: `read_file` 拒读 (BLOCKED 说明, 内容绝不外泄); `@` 引用跳过注入 (占位说明); `grep` 双引擎 (rg + python 退化) 敏感文件整体跳过 + 警示 (grep API_KEY 不得把 .env 密钥行吐进上下文)。逻辑: 凭证一旦读入即泄漏进会话存档/链式 timeline/API 请求; 用户确需时走 bash 显式命令 (信任边界清晰)。
- 新增 `tests/test_sensitive_file_invariants.py` (11 测试含反例孪生: 豁免文件必须可读 / 普通文件注入与 grep 命中不受影响 / 双引擎同源防线)。

### 测试
- 全量基线 2259 passed / 13 skipped (唯一失败为真实 API 流式端点闪断, 复跑即过); 主题切换后定向回归 206 passed; ruff 全绿。

### - kimi 对标 bugfix 轮 · GBK · 重试可见性 (2026-07-26)

### 背景
- 深读 kimi-cli 源码产出 `docs/research/kimi-cli-study.md` (10 模块研读) 与 `docs/research/kimi-zall-gap.md` (G1-G17 差距清单 + 不移植决策)。本轮落地 bugfix 部分 (G3/G4 复查 + 新发现 bug)。

### [HIGH] GBK subprocess 解码崩溃 (中文 Windows)
- **现象**: 全量测试暴露 `PytestUnhandledThreadExceptionWarning: UnicodeDecodeError ('gbk')` 于 subprocess reader 线程。
- **根因**: `subprocess.run(..., text=True)` 无 `encoding=` 时按控制台码页 (zh-CN 为 GBK) 解码, 而 git/pytest 输出 UTF-8。
- **修复**: 平衡括号扫描 codemod 精确修补 **43 处** (`cli/commands/*` · `core/judge` · `core/loop` · `perception` · `plugin` · `sandbox` · `tools` 等), 统一补 `encoding="utf-8", errors="replace"`; 已有 encoding 的调用 (bash.py/mcp) 不动。
- 新增 `tests/test_subprocess_encoding_invariants.py` (I-GBK 架构不变量: src/zall 内 `text=True` 必须同调用块内有 `encoding=`; 含扫描器反例, IPR-0)。

### [HIGH] Retry-After 预算泄漏 (无限重试)
- **根因**: `openai_compat.complete()` 中 429 带合法 `Retry-After` 头时 `delay = float(retry_after)` 跳过 `record_attempt` — api 预算不消耗, 服务器持续 429 时死循环。
- **修复**: `RetryBudget.record_attempt(category, delay_override=None)` — Retry-After 只覆盖延迟, 预算必消耗 (上限 max_delay)。

### 重试可见性 (G3, kimi 对标: 退避不再静默)
- 此前重试全程静默 sleep (最长 60s 零反馈, 像卡死); `RetryBudget.on_retry` 回调存在但从未接线。
- 接线链: `BaseAdapter.set_retry_callback` → `OpenAICompatAdapter._retry_budget(on_retry=_dispatch_retry)` + 流式连接重试 → `AgentLoop.__init__` duck-typed 注入 (core 不 import adapters, IPR-3) → `kind="retry"` LoopEvent。
- 渲染: REPL TTY 换 spinner 活动标签 `Retrying n/N · <原因> · Xs wait` (不插行零闪烁); 非 TTY 落行 `retrying (n/N) in Xs: <原因>`; TUI 加 dim 系统行。文案单一真相源 `adapters.base.RETRY_REASON`。
- `_ERROR_MAP` 补 402 (余额耗尽→充值/换 provider); `classify_http_status(402)=INVALID_REQUEST` 不重试。
- G4 复查: 空 STOP backoff (`is_empty_stop`+nudge) 已存在, 评级下调为 ◐, 不重复建设。
- 新增 `tests/test_retry_visibility_invariants.py` (9 测试: I-RETRY-BUDGET 含硬上限反例守卫 / I-RETRY-VISIBLE / I-402 / I-RETRY-EVENT 含无回调 adapter 反例)。

### 主题系统 (G6, kimi 对标: 单一色源 + 希腊美学主题)
- 新增 `cli/theme.py`: `Theme` frozen dataclass 为**唯一色源** (20 个 REPL 槽位 + 4 模式色 + code 主题 + 10 个 TUI hex); `apply()` 统一写入 `render._C`/`_ModeColor`/`_ANSI_MAP`/`CODE_THEME`/`CODE_BG`; 解析顺序 env `ZALL_THEME` > config `[ui].theme` > obsidian, 未知名自愈回退。
- 内置双主题: `obsidian` (现状精确复刻) + `attic` (希腊美学: 月桂金 #c9a227 / 爱琴海蓝 #4f93b8 / 大理石白 #d8d4c8 / 陶土红 #c96a5a / 橄榄绿 #7fa370, code=nord)。
- **修 4 处隐性色号错误**: 手工 `_ANSI_MAP` 与 rich 权威值不符 (spring_green3 35→41 / dark_orange 166→208 / steel_blue1 75→81 / grey37 240→59); 改为 `Color.parse().get_ansi_codes()` 自动派生, 消灭手工表。
- **修 widgets.py from-import 值拷贝 bug**: `from render import CODE_THEME` 绑定导入时值, 切主题不生效 → 6 处改模块属性访问 `_render_mod.CODE_THEME`。
- TUI: `_ZALL_THEME` 改 `_build_zall_theme()` 从 `theme.active()` 派生 (Textual 名固定 "zall", 兼容既有测试)。
- 命令面: 新增 `/theme [name]` (列出/切换/持久化到 `[ui].theme` + env); `/config set theme <name>` 同步即时换肤; help advanced 补条目。
- 新增 `tests/test_theme_invariants.py` (11 测试: I-THEME-1 obsidian 复刻等价 / I-THEME-2 ANSI 全槽位覆盖 + 4 旧错码不得回归 / I-THEME-3 未知名报错 + 垃圾 env 回退 / I-THEME-4 主题槽位对等 + TUI hex 校验 / I-THEME-5 attic↔obsidian 往返无残留)。

### Rich diff 三形态 (G1, kimi diff_render.py 对标: 最高价值移植)
- 新增 `cli/diff_render.py`: `build_hunks` (SequenceMatcher.get_grouped_opcodes 直接建 hunk, 免 unified 文本往返) + `parse_unified_hunks` (既有 artifacts["diff"] 路径, 支持 line_offset 平移到真实文件行号) 双来源; 词级内联高亮 (连续 -/+ 块按序配对, ratio<0.5 跳过); 三形态 = 完整 Panel (行号列+整行背景色+hunk 间 ⋮) / 紧凑 preview (只显改动行上限 6) / 大文件降级 summary (>5000 行, SequenceMatcher O(n²) 护栏)。
- **修审批盲区**: 此前 `_render_permission_panel` 读 `args["diff"]` — 但 edit_file args 根本没有 diff 键, 审批时永远看不到会改什么。现从 args 的 old/new_string 现算 preview (`_build_edit_preview`), 读文件定位真实行号; batch_edit 每 edit 一段 (最多 3 段)。
- edit_file artifacts 新增 `start_line` (真实起始行号); REPL `_render_edit_diff` / TUI `widgets._render_diff_panel` 优先走新渲染器, 解析失败回退旧文本着色 (legacy 保留)。
- Theme 新增 diff_add_bg/del_bg/add_hl/del_hl 四槽位 (kimi DiffColors 对标; attic 用橄榄绿/陶土红座标系); 新增 `theme.current()` (最后 apply 的主题, 与 env/config 解析的 active() 区分 — 热切换真相)。
- 新增 `tests/test_diff_render_invariants.py` (17 测试: I-DIFF-1 行号保真含 offset 反例 / I-DIFF-2 词级配对含 ratio 反例 / I-DIFF-3 双路等价+截断标记 / I-DIFF-4 preview 上限含反例 / I-DIFF-5 大文件降级含反例 / I-DIFF-6 审批预览含 write_file 反例 / I-DIFF-7 CJK+空输入健壮性)。

### ANSI-16 语法主题 (G7, kimi utils/rich/syntax.py 对标)
- 新增 `cli/syntax_theme.py`: `ZALL_ANSI_THEME` (ANSISyntaxTheme, kimi token 映射移植 — 关键字 magenta / 字符串 bright_blue / 函数 bright_cyan / 类 bright_yellow bold / 注释 bright_black italic); `resolve_code_theme("zall-ansi")` → 实例, 其余透传; `resolve_code_bg("")` → None (跟随终端背景)。痛点: one-dark/nord 是 truecolor 固定色板, 浅色终端下不可读; ANSI-16 主题自动跟随终端配色。
- 新增第三主题 `ansi` (纯 ANSI-16 色名槽位, 浅色终端自适应; code_theme="zall-ansi", code_bg=""), 注册进 THEMES。
- 消费点全接线: TUI `widgets.py` Syntax 1 处 + Markdown 4 处、REPL `render.py` Markdown (顺带修此前 REPL Markdown 从未传 code_theme 的不一致 — 之前 REPL 代码块永远是 rich 默认 monokai)。
- 新增 `tests/test_syntax_theme_invariants.py` (10 测试: I-ANSI16-1 resolve 大小写不敏感+透传反例 / I-ANSI16-2 code_bg 空串→None+透传反例 / I-ANSI16-3 ANSI 纯净性 — zall-ansi 渲染绝无 truecolor 38;2; 转义, one-dark 反例有 / I-ANSI16-4 主题注册完整+全槽位 ANSI 可派生+未知名反例)。

### 大段粘贴折叠 (G5, kimi placeholders.py 对标)
- 新增 `cli/paste_fold.py`: `PasteFolder` — 粘贴 >=1000 字符或 >=15 行 (env 可调) 折叠为 `[Pasted text #N +M lines]` 占位符, 提交时 `expand()` 展开为原文; 未知 id (跨会话历史召回) 原样保留; 入口即 `sanitize_surrogates` (Windows 剪贴板孤立 UTF-16 surrogate → U+FFFD, 免 json/历史文件 UnicodeEncodeError) + CRLF 归一化。
- REPL `prompt.py`: 新增 `Keys.BracketedPaste` eager 绑定 — 粘贴作为单一事件插入, **修复多行粘贴被 Enter 绑定逐行解释的问题**; 提交时 `folder.expand()`。
- TUI `widgets.py ChatTextArea`: 新增 `_on_paste` 拦截 (select_mode/read_only 吞掉; 否则折叠后 `_replace_via_keyboard` 插入); `expanded_text` property; Enter 提交与 Ctrl+S steer 均发展开后文本。
- 新增 `tests/test_paste_fold_invariants.py` (11 测试: I-PASTE-1 阈值含反例 / I-PASTE-2 往返保真+多占位符+未知 id 反例 / I-PASTE-3 CRLF+surrogate+CJK / I-PASTE-4 占位符格式 / I-PASTE-5 ChatTextArea 集成)。

### 测试自身 I-GBK 违规修复
- `tests/test_platform_compat.py::test_subprocess_output_encoding_consistency` 自身裸用 `text=True` 无 encoding — 子进程 GBK 输出碰 `-X utf8` 父进程时 reader 线程 UnicodeDecodeError、stdout=None 碰 `in` 报 TypeError。修复: 子进程 `-X utf8` + 父进程显式 `encoding="utf-8", errors="replace"`, 断言从“或者返回码为 0”弱断言强化为两条都必须成立。

### 审批 feedback 拒绝语义 (G2, kimi 审批 UX 对标)
- **痛点**: 此前拒绝时模型只知道 "user rejected" — 不知道为什么被拒/该怎么改, 常常换个写法重试同一件事。
- `core/gate.py`: `UserResponse` 新增 `feedback: str | None`; greylist/blacklist REJECT 分支带 feedback 时 rejection_reason 变为 "user rejected with feedback: {理由} — adjust the approach accordingly", 经 executor `_make_rejection_message` 流入 tool_result — 模型可见。
- `cli/responder.py`: greylist 新增 `f` 选项 (reject + why) — 选中后追问一行自由文本理由; 空理由退化为纯拒绝。`_GREYLIST_CHOICES` 同源 — TUI 选择菜单自动获得 f 项, feedback 输入经既有 `_tui_ask` 机制免接线。
- 新增 `tests/test_reject_feedback_invariants.py` (8 测试: I-FB-1 reason 含理由+纯拒绝反例 / I-FB-2 blacklist 同语义 / I-FB-3 f 收集+空输入退化反例+纯 n 不提问反例+菜单同源 / I-FB-4 端到端 tool_result 可见)。
- 同步 `tests/test_confirm_select.py` 选项契约测试: `_GREYLIST_CHOICES` 契约从 y/n/a/e 更新为 y/n/f/a/e (G2 引入 f 后全量曾短暂 1 failed, 已修)。

### cell-width 截断工具 (G11, kimi utils/string.py 对标 + 增强)
- `_util/string.py` 新增 `display_width` (East Asian Wide/Fullwidth 计 2 格) / `shorten` (空白归一化+词边界优先, CJK 无空格硬切不塌缩) / `truncate` (不归一化空白, 保留代码/日志行缩进) / `shorten_middle` (头尾保留, 反转串量尾部)。相对 kimi 版 (纯字符数) 升级为终端 cell 宽度感知 — CJK 双宽不再撑爆 rich 表格/面板。纯 stdlib (IPR-3)。
- 替换 5 处硬截断消费点: `responder._preview_args` (args 预览, 顺带多行归一防滚屏) / `widgets.ThinkWidget.render` (顺带消灭 `[:78]` 后永假的 `>78` 死分支) / `render.py` Goal intent / 工具输出预览行 (用 truncate 保缩进) / tool summary 首行。API key 脱敏 (`commands/config.py`) 非截断语义, 不动。
- **顺手修 TUI 崩溃隐患**: `ThinkWidget.render` 把原始 reasoning 直接塞 `Text.from_markup` — 思考文本含 `[` (如 `list[int]`) 会 MarkupError 崩 TUI; 展开/折叠两分支均补 `rich.markup.escape`。
- 新增 `tests/test_shorten_invariants.py` (17 测试: I-SHORT-1 词边界+CJK 硬切反例 / I-SHORT-2 cell 感知 ASCII 对照 / I-SHORT-3 永不超宽+短文本原样反例 / I-SHORT-4 middle 头尾保留 / I-SHORT-5 display_width / I-SHORT-6 truncate 保缩进与 shorten 对照反例)。

### 提示符行首保证 (G10, kimi utils/term.py 对标 + 修其 off-by-one)
- **痛点**: bash 等工具输出无尾换行时, REPL 提示符接在残留输出行尾。
- 新增 `_util/term.py`: `ensure_new_line()` 提示符前探测光标列, 不在行首才补 `\n`。Windows 走 `GetConsoleScreenBufferInfo` (ctypes 同步无竞态, 用户主环境); Unix 走 `ESC[6n` 查询 (cbreak+非阻塞读+200ms 超时, 不会卡死在不可中断 os.read)。纯 stdlib (IPR-3)。
- **修 kimi 原版 off-by-one**: kimi `_cursor_column_windows` 返回 1-indexed 却判 `not in (None, 0)` — 行首 (列 1) 会误插空行。zall 版两平台统一 1-indexed + 单一判据 `_needs_newline` (行首/探测失败都不写, 探测失败保守不乱插空行)。
- 接线 `repl_ui.py` 主循环: 每轮提示符前 `_ensure_new_line()`; 非 TTY (管道/测试) 零输出直接短路。
- 新增 `tests/test_term_invariants.py` (9 测试: I-TERM-1 判据含行首/None 反例 (off-by-one 回归守卫) / I-TERM-2 非 TTY 零输出反例 / I-TERM-3 恰写一个 \n+两反例 / I-TERM-4 错平台返 None+原生探测不抛)。

### 指数抖动退避 (G13, kimi wait_exponential_jitter 对标)
- **痛点**: 三处消费点 (core/loop · cli/repl_ui · cli/tui/app) 各自硬编码 `attempt * 2` (2s/4s/6s) — 线性退避僵硬, 固定值多客户端同时重试有同步惊群。
- 新增 `_util/backoff.py` `backoff_delay(attempt)`: base = min(2·2^(n-1), 8), 乘以中心对称均匀抖动 [1-j/2, 1+j/2) — 期望值恰为 base (好推理), rng 可注入 (测试确定性); jitter=0 退化为确定性指数 2/4/8。纯 stdlib (IPR-3)。
- 三消费点统一接线单一真相源; 显示格式化一位小数。
- 新增 `tests/test_backoff_invariants.py` (9 测试: I-BO-1 指数序列+非旧线性反例 / I-BO-2 封顶+放宽反例 / I-BO-3 抖动区间 rng 注入+200 样本带内 / I-BO-4 居中 / I-BO-5 attempt<1 防御 / I-BO-6 架构守卫三消费点无残留硬编码)。

### timeline 版本头 + 坏行容错 (G12, kimi wire.jsonl 首行 protocol_version 对标)
- 新增 `_util/jsonl.py`: `make_metadata`/`is_metadata`/`read_jsonl`/`read_metadata` — 首行版本头 `{"type":"metadata","version":1,...}`, 无头旧文件视为 legacy 行为完全一致 (向后兼容)。纯 stdlib (IPR-3)。
- **修一坏全弃**: 此前 `_load_timeline_events` 任一行 JSONDecodeError 整个返 None 丢掉全部可读事件; 现坏行 skip 保住其余记录。
- 接线: `_save_session` timeline 首行写版本头 (run_id+saved_at); `commands/system._load_timeline_events` 改走 `read_jsonl`; `core/eval.load_timeline` 显式跳版本头 (原 KeyError 兜底升级为显式语义)。
- 同步 `test_cli_app.py::test_timeline_chain_intact`: 链验证前过滤版本头并断言首行必为 metadata (端到端验证写入)。
- 新增 `tests/test_jsonl_header_invariants.py` (12 测试: I-JH-1 往返+事件非头反例 / I-JH-2 过滤+保留反例 / I-JH-3 坏行 skip+全坏反例 / I-JH-4 legacy 一致 / I-JH-5 read_metadata+legacy None 反例 / I-JH-6 两消费方端到端)。

### completion token 动态钳制 (G16, kimi compute_max_completion_tokens 对标)
- **痛点**: max_tokens 固定透传 — 长上下文时 input + requested 超出模型窗口, provider 直接 400 (kimi 用发前钳制根治)。
- 新增 `_util/tokens.py`: `estimate_text_tokens` (CJK 感知: ASCII (n+3)//4 + 非 ASCII 每字符 1, kimi 同款) / `estimate_body_tokens` (整个请求体 json.dumps 后估算 — 覆盖 kimi 分项估算全部内容且天然含结构开销) / `clamp_completion_tokens` (max(minimum, min(requested, window-input-margin)); window<=0 未知窗口不敢钳原样透传)。SAFETY_MARGIN=1024 (kimi 同值), MIN_COMPLETION=256 保底。纯 stdlib (IPR-3)。
- 接线: `openai_compat._build_body` max_tokens 注入处 + `anthropic._build_body` (tools/tool_choice 注入后估算 — 含 schema 开销; thinking 逻辑之前 — 显式启用的 thinking 抬高 budget+1024 语义保留)。窗口来源 `_util/model_registry.get_window_size`。
- 新增 `tests/test_token_clamp_invariants.py` (11 测试: I-TK-1 估算 ASCII/CJK/混合可加/body 嵌套 / I-TK-2 放得下原样反例+钳到余量+近满保底 / I-TK-3 未知窗口透传反例 / I-TK-4 adapter 集成 500k→<128k + 4096 原样反例 + anthropic 999999 被钳)。

### [HIGH] grep 退化路径 timeout 形同虚设 (G14 线程泄漏审查产出)
- **审查范围**: 全量 grep `Thread(|ThreadPoolExecutor|create_task` 10 处逐点核实 — spawn_subagent (close+__del__+幂等) / mcp/client (stop Event+join+daemon) / render spinner (持久线程+shutdown Event) / environment / anchor / update / coordinator 均健全。
- **唯一真缺陷**: `tools/grep.py::_grep_python` 用 `with ThreadPoolExecutor` 包 timeout — TimeoutError 后 `__exit__` 执行 `shutdown(wait=True)` **阻塞等待灾难性回溯的失控 regex 线程**, 5s 保护形同虚设; 且非 daemon 线程还阻止进程退出。
- **修复**: daemon 线程 + `join(timeout)` + `_cancel` 协作停止标志 (逐文件/逐行/walk 层检查) — 超时后主线程立即返回, 失控线程尽快自行退出; 异常不再静默 (result_box["error"] 回传)。
- 新增 `TestGrepTimeoutNonBlocking` (3 测试: I-GREP-TO-1 耗时 4s 的搜索在 0.3s timeout 下 <2s 返回 / 快搜索结果完整反例 / I-GREP-TO-2 架构守卫无 executor 回归)。

### 斜杠命令别名展示 (G17, kimi "/name (alias)" 对标)
- 展示层派生别名关系, `SlashCommand.description` 单一真相源不动: `get_command_meta` (REPL 补全) 别名条目标 `→ /规范名 · desc`, 规范名尾附 `(alias: /h)`; `get_palette_commands` (TUI 面板) desc 尾附别名。
- 副作用收益: 面板 fuzzy desc 命中现在可经别名召回 — 搜 "selfplay" 出 /lab, 搜 "quit" 出 /exit。
- 新增 `TestAliasAnnotation` (4 测试: 别名指向规范名 / 规范名列别名 / 无别名不污染反例 / 面板别名召回)。

### scripted/chaos provider (G15, kimi _scripted_echo/_chaos 对标, E2E 设施)
- 新增 `adapters/scripted.py` `ScriptedAdapter`: JSON 脚本回放 (确定性回归/无 key 冒烟) — 构造期全量解析条目 (坏脚本立即失败不留回放中途); tool_calls 省略 stop_reason 时推断 TOOL_USE; 耗尽返 STOP 收尾 (loop=True 循环); `complete_stream` 逐块 yield 且收尾帧与 complete() 等价 (Protocol 契约); `calls` 记录供测试断言。
- 新增 `adapters/chaos.py` `ChaosAdapter`: 包装任意真 adapter 按概率注入故障 — 形态与真实错误路径完全同构 (429/500/503 → raw={"status":N} 走 API 重试路径; transport → raise httpx.ConnectError 属 RETRYABLE_EXC); `max_consecutive` 前进性护栏 (概率 1.0 也不锁死会话); rng 可注入; `complete_stream` 经 `__getattr__` 按需派生 — inner 无流式时 hasattr 探测保持 False (loop 降级语义不被包装层破坏)。
- 接线 `cli/config._build_adapter`: `model=scripted:<path.json>` 或 env `ZALL_SCRIPT` → 回放 (优先于一切); env `ZALL_CHAOS=<0..1>` (+`ZALL_CHAOS_MODES`) → 构建后包装; 非法值警告降级不阻断。
- 全链路冒烟验证: `ZALL_SCRIPT=... zall --no-tui -y "say hi"` 完整跑通 loop→渲染→会话持久化。
- 新增 `tests/test_scripted_chaos_invariants.py` (15 测试: I-SC-1 顺序/耗尽+loop 反例 / I-SC-2 tool_calls 推断+STOP 反例 / I-SC-3 流式≡阻塞 (CJK 跨块) / I-SC-4 from_file+坏脚本反例 / I-CH-1 p=0 透传反例 / I-CH-2 前进性 9 次=3 组注注放 / I-CH-3 注入同构 is_retryable_status+RETRYABLE_EXC / I-CH-4 委托+hasattr 反例 / I-BLD-1 接线含无 env 不包装反例)。

### 测试与质量
- 全量基线: **2081 passed / 13 skipped / 0 failed** (修复前) → bugfix 后 2093 → G6 后 2104 → G7 后 2114 → G5 后 2142 → G2 后 2150 (含契约测试同步修复) → G11 后 2167 → G10 后 2176 → G13/G12/G16 后 2208 → G14/G17 后 2215 → G15 后 **2230 passed / 13 skipped** (全量确认) → E2E 轮后 **2260 passed / 13 skipped** (全量确认, +21 e2e 修复面 +5 流式降级 +4 TUI 死锁守卫); 本轮新增 12+11+17+10+11+8+17+9+9+12+11+3+4+15 测试, 受影响面定向回归 462 + 359 + 379 + 270 + 298 + 284 + 127 + 55 + 126 + 119 + 307 + 187 passed, ruff 全绿。
- **G8/G9 评估关闭 (2026-07-26)**: 代码核实后关闭两项低优先 gap — G8: REPL 流式实为 append-only 直写 (`_write_stream_text` 无重绘), `_streamed_step == step` 完成分支不重打全文, kimi 两段式固化无对应痛点; G9: 流式已有滚动预览, 非流式路径无增量数据物理不可行, 终态折叠已统一。gap 清单 G1–G17 全部闭环。
- **修复: 旧版 autosave 残留无限累积 (2026-07-26)**: 实测 `~/.zall/` 累积 37 个旧版 PID 命名 `.repl_autosave_<pid>.json` (E4 固定文件名改造后无代码回收它们)。新增 `_sweep_legacy_autosaves()` 接线 `_check_repl_autosave` 启动时顺手清扫 — 仅删 PID 已死的文件 (与 E4 软锁同一保护语义), 非数字后缀保守不碰。3 测试含反例 (固定名不删/存活 PID 不删/接线验证), test_interaction_debt_e4.py 17 passed。
- **修复: 脏工作区下 Q&A 会话误报 "modified: 167 file(s)" (2026-07-26, 真实 API 冒烟发现)**: `orchestrator.get_modified_files()` 用 `git diff HEAD` 拿的是工作区**全部**未提交改动, 非本次 run 产物 — 0 tools 的纯问答也报 167 文件。修复: 新增 `snapshot_modified_baseline()` 在 loop.run 前采基线集合, 结束后只报差集; baseline=None 保留旧语义兼容直接调用方。4 测试含反例。
- **修复: 一次性任务退出码语义 (2026-07-26, 真实 API 冒烟发现)**: 未开 judge (默认 --judge none) 时 UNDECIDABLE 是必然终态, Q&A 成功回答也 exit 2 — `zall -y "..." && next` 永远断链, 脚本化/CI 不可用。新语义: 未开 judge 且无 error → 0; 开了 judge 裁决不了或执行出错 → 仍 2 (PR-0 诚实不确定保留)。4 测试含反例, test_cli_app.py 52 passed; 真实 API 复验 exit=0。

### E2E 轮 (2026-07-26) — 真实 API (SenseNova deepseek-v4-flash) 全流程检验 + 五项真缺陷修复
- **E2E 通过**: ① 连通性冒烟 (1.5s 往返); ② 中等难度开发任务全自主闭环 (merge_intervals: 4 步/4 工具, 写模块→写测试→跑 pytest 7 passed, 产物落盘质量合格); ③ REPL 多轮模拟真人 (打错命令 did-you-mean、read→edit diff 面板→doctest 6 passed、优雅退出); ④ TUI Pilot 模拟真人输入+鼠标点击 (`scripts/e2e_tui_pilot.py`: 点击聚焦→逐键敲入→Enter→真实 API 回合→断言历史/无错误→/help, 9/9 断言通过)。
- **修复: 依赖混淆式自毁风险 (安全关键)**: PyPI 上存在**同名陌生包** `zall` (0.4.10) — 本地私有项目 (0.0.1) 启动时提示 "update available: 0.0.1 -> 0.4.10", `/update` 会 `pip install --upgrade zall` 把第三方包顶掉 import 名。新增 `_is_dev_install()` (源码运行/editable/file:// 任一命中即 dev, 未知保守归 dev) — dev 安装不提示更新、perform_update 直接拒绝不碰 pip。5 测试含反例 (正式 wheel 安装不误伤)。
- **修复: 脏仓库下每步误报 "anomaly detected"**: `loop.py __init__` 中 `_init_baseline_modified()` (271 行) 先于 `_project_root` 赋值 (341 行) 执行 — AttributeError 被 except 吞, 基线恒 0, 脏仓库 (167 存量改动 > 50) 下 coding_world_model 每步判 anomaly。修复: `_project_root` 提前到 `_init_baseline_modified()` 之前赋值; 源码顺序守卫测试 ×2。
- **修复: anomaly 警告刷屏**: render 层每个 perception_state 事件都重打同一条警告 (e2e 实测单任务 5 次) — 改为仅 False→True 翻转沿打印 (`_anomaly_active` 状态位), 恢复后再异常会再报。3 测试含反例。
- **体验: `/usage` 别名接入 `/stats`** (e2e 模拟真人直觉输入命中 unknown) — G17 alias 基建自动获得帮助/面板标注。
- **修复: 流式路径网络错误无重试 (chaos e2e 钓出)**: `_with_retry` 重试链只保护非流式 `complete()`, 各 adapter `complete_stream` 是裸流 — `ZALL_CHAOS=0.5` 实测 `ConnectError` 直接 step error 杀死任务 (真实网络闪断会杀死长任务)。分级恢复: **零产出**失败 → 降级非流式 `complete()` (自动获得完整重试链, UI 无重复输出) + 发 `retry(category=stream_fallback)` 可见性事件 (spinner 标签零闪烁); **已有部分产出** → 维持 A1 诚实传播 (降级重打会双重显示半截+完整内容)。`RETRY_REASON` 注册 `stream_fallback` 文案。chaos+真实 API 复验: 注入下任务 4 步收敛完成 (此前 step 2 即死)。5 测试含反例 (部分产出不降级 / timeline 携带降级响应 / 事件可见)。
- **修复: TUI 流式必死锁 (致命, TUI pilot e2e 钓出)**: `_tui_listener` (worker 线程) 在 `_buf_lock` **锁内**调 `call_from_thread` — 它阻塞等主线程回调完成, 而回调 `_flush_token/thinking_buffer` 首行就要拿同一把锁 → worker 持锁等主线程 / 主线程等锁, **流式第一个 token 即 100% 冻死整个 TUI** (faulthandler 全线程栈转储实锤)。此前所有 TUI 测试都主线程直调 `_handle_event`, 从未走过真实线程路径 (测试盲区)。修复: 锁内只置标志, `call_from_thread` 移到锁外。新增 `tests/test_tui_deadlock_guard.py` (4 测试: AST 守卫 I-TUI-LOCK 锁内禁 call_from_thread + 旧形态反例自证 + 修复形态放行 + flush 回调锁语义不可删)。
- 定向回归: update/loop/render/palette/e4/cli_app 面 171 passed + 新增不变量测试文件 test_update_and_anomaly_invariants.py 10 passed + stream/retry 面 101 passed + TUI 面 121 passed, ruff 全绿。

### - 深度审查 · Provider 一等化 · 真溯源 · 数学探索 (2026-07-23)

### Provider 一等化 (解决"硬编码"模型提供者)
- **根因**: `api_base` 本就是 config 驱动的 (`BaseAdapter` 读 `load_config()`), 已有 TOML `[[providers]]` 机制; 真正缺陷是 `model_registry.py` 多处绕过合并后的注册表, 自定义 provider 永远是"二等公民"。
- A1: `get_model_provider()` 接受可选 `registry` 参数, 用合并表推断; 新增**最长前缀优先**匹配 (修 `deepseek-v4-flash` 被内置 `deepseek-` prefix 错路由到 api.deepseek.com 的问题)。
- A2: `[[providers]]` TOML schema 新增 `window_size`/`price_in`/`price_out` 字段; `_merge_custom_providers()` 注入运行时覆盖表, `get_window_size()`/`get_price()` 先查覆盖表 (自定义模型不再一律拿默认 32000/$3+$15)。
- A3: `get_provider_tag()` 对自定义 provider 用首字母兜底 (不再返回 '?'); `list_providers()`/`get_provider_default_model()` 读合并表。
- A4: `/model` picker `_detect_configured_providers()`/`_build_dynamic_model_list()` 用合并表, 自定义 provider 用真实名 (不再硬编码 "openai")。
- 新增 `tests/test_custom_provider_invariants.py` (13 测试, 含反例: 自定义 prefix 路由/window/price/tag/list 均一等)。

### 安全配置 deepseek-v4-flash / SenseNova
- 用 `[[providers]]` 注册 `sensenova` provider (openai-compat), `deepseek-v4-flash` 正确路由到 `https://token.sensenova.cn/v1` (不再错路由到 DeepSeek)。
- **API key 仅经 env `ZALL_API_KEY` 注入, 不写入任何 git 管理文件**; `~/.zall/config.toml` (仓库外) `[auth].api_key` 留空占位。
- 端到端验证: provider=sensenova -> OpenAICompatAdapter -> SenseNova base -> 模型回复正常 (HTTP 200)。

### Bug 修复 (按严重度)
- **[HIGH] C1**: `loop_perception.py` 状态变化检测用错键名 (`git_modified`/`lsp_errors`), 传感器真实输出键是 `modified`/`errors` -> 检测从未触发 (死代码)。修正键名 + 用文件计数。旧测试曾固化 bug (用错键), 一并修正。
- **[HIGH] C2**: `coding_world_model.py` `anomaly()` 同样用错键名 -> git/LSP 异常检测失效。修正。新增 4 反例测试。
- **[MED] C3**: `loop.py` `_messages` 影子属性初始化不同步 (若 `chat_state` 预填消息, `_messages=[]` 与 `_chat_state` 不一致, MASTER.md §7.3.2)。修初始化从 ChatState 同步 + 移除冗余 `_sync_messages()` 调用。保留 `_messages` 为影子属性 (多处测试直接赋值, 避免 property 改造的回归风险)。新增 `test_no_dual_write_inconsistency`。
- **[MED] C4**: `perception/engine.py` `reset()` 销毁已配置 WorldModel (替换为 NullWorldModel) -> 数据丢失。改为只清状态, 保留 world model。新增反例测试。
- **[LOW] C5**: `chat_state.py` 7 处方法内 lazy `import Message` + `openai_compat.py` 2 处 `import time as _time` 提为模块级 (热路径重复 import)。

### 真溯源哈希 (E3 Science Kit 诚实化)
- 新增 `src/zall/_util/hash_utils.py`: `hash_file`/`hash_files` (顺序无关聚合)/`hash_dir`/`environment_hash` (stdlib-only, IPR-3)。
- `ScienceProvenance` 四个 hash 字段此前是硬编码占位符 (`sha256:cli-manual`/`sha256:agent`), 无任何代码对真实文件算哈希 -- "可复现"宣称是空话。现 CLI (`/science evidence --protocol/--data/--code`) 与 agent tool (`add_evidence` 新增 `protocol_path`/`data_path`/`code_path`) 均算真实 SHA-256; 无路径时降级为带标记占位符 `sha256:unspecified-<field>` (明确未溯源, 而非伪装)。
- 新增 `tests/test_hash_utils_invariants.py` (11 测试, 含反例)。

### Erdős–Straus 猜想真实探索 (Science Kit dogfood)
- **诚实前提**: 这是开放问题, 无 LLM agent 能"解决"它产出 Science 论文。价值是一次真实、可复现、可证伪的计算探索, 由 Science Kit 全程溯源。
- `experiments/erdos_straus/`: `solve.py` (有界剪枝枚举 + 整数算术验证), `run_search.py` (批量), `science_campaign.py` (假设驱动循环)。
- H1 (正假设): [2,50000) 内 4/n 均可分解 -> **CONFIRMED** (区间内, 非证明); H2 (反例假设) -> **FALSIFIED** (0 反例, 负结果 I-10)。
- 全程真实 provenance (protocol/data/code/env SHA-256, 已验证匹配实际文件) + 链式哈希 timeline 锚定; `REPORT.md` 明确标注局限。

### 测试与质量
- 全量: **1885 passed / 13 skipped** (基线 1854, +31 新测试, 0 退化)。ruff 全过; mypy 仅 1 预存无关错误。

### - v1.1 现实对齐修订 (2026-07-19/20)

### PARADIGM 落地轮 (2026-07-21) — 可证伪经验机变成能跑的命令 `/lab`

**从原语到成品: `/lab` 命令 (工程化落地)**
- 新增 `/lab` 命令 (别名 `/selfplay`): 把 Step 1-3 (经验流 / 沙盒证伪 / 开放式生成) 串成用户可直接跑的**可证伪自改进**入口。
  - `/lab <task>`: 真实模型提解 → **隔离沙盒执行证伪** (grounded reward, 非自评) → 熬过者蒸馏成技能写入经验库 (跨会话复利)。
  - `/lab` (无参): 开放式一轮, 从已验证技能派生新任务再各自提解+证伪 (空库诚实提示先 bootstrap)。
  - `/lab skills` / `/lab stats`: 只读查看已学技能与经验库统计。
- **最前沿交互**: `RedBlueLoop` 新增可选 `on_event` 观察者 (additive, 默认 None → 行为/确定性不变), `/lab` 用它把“猜想 → 提议 → 沙盒反驳 → 蒸馏”每一步 live 渲染 (● 风格)。`run_open_ended_round` 亦透传 `on_event` + 每任务发 `task` 事件。
- **模型无关**: blue_fn 由 CLI 层用当前 session 的 adapter 组装 (IPR-3 核心零模型依赖不变); adapter 解析 `loop.model_adapter → state → 现建`。
- 新增 `tests/test_lab_command_invariants.py` (8 测试, 真实沙盒 + 反例): on_event 投递/异常隔离/不改报告; verified→蒸馏、坏码→不蒸馏、只读子命令、无模型优雅退让、空库不调用模型。

### PARADIGM 路线推进轮 (2026-07-21) — Step 2/3 + Step 0 地基 (可证伪经验机脊柱)

**Step 2: 验证器制导反驳 (grounded refutation)**
- 新增 `core/sandbox_verifier.py`：`SandboxVerifier` 把候选解法**真的在隔离沙盒里跑** (ProcessSandbox), 用退出码/断言结果作 ground-truth reward (非模型自评 → 不自欺)。`make_sandbox_red_fn()` 提供可直接喂给 `RedBlueLoop` 的 red_fn。新增 9 测试 (真实执行, 含反例: 坏代码/错误实现被客观证伪)。

**Step 3: 开放式任务生成 + 永续自改进轮**
- 新增 `core/open_ended.py`：`OpenEndedGenerator` 从已验证技能模板变异生成**新颖且可学**新任务 (harden/generalize/vary/combine, 确定性、离线、模型无关)。`run_open_ended_round()` 闭环: 生成任务→红蓝提议→沙盒证伪→**仅 verified 解写回经验库为新技能** (Popperian Gate)。新增 8 测试 (含反例: 坏解不污染技能库; 只从 verified 技能派生)。至此 “生成→解→证伪→蒸馏→复利” 脊柱闭环。

**Step 0 地基: lean 工具集预设 (Bitter Lesson)**
- `core/toolset.py` 新增 `lean` 预设 (bash+read+write+edit+grep+glob+list_dir, 7 个少而宽工具, 对齐 Pi)。**opt-in, 不改默认** (零回归)。新增 `tests/test_toolset_presets.py` (填补之前缺失的预设不变量测试)。
- 诚实说明: Step 0 的“默认工具收窄 + 系统提示精简 + 热循环本体论解耦”是更大重构, 为不弄坏已验证的默认路径, 本轮只落地无风险的 lean opt-in, 余下作为单独一轮。

**Step 0 收尾 (续): 真实 token 计数 + lean 系统提示**
- 真实 token 水位计数 (Pi 教训): watermark 用上次响应真实 `usage.prompt` tokens 作 ground truth, 无则回退字符估算。`compactor`/`context_manager`/`loop` 加 `real_tokens` 可选参 (向后兼容), loop 追踪 `_last_usage`。新增 `tests/test_real_token_watermark.py` (4 测试含反例)。
- lean 系统提示: `build_system_prompt(lean=True)` 仅 base+env, 跳过 repo_map/记忆/skills/lsp/codegraph; `--toolset lean` 自动启用。新增 lean-prompt 测试。
- 热循环解耦 (完成): 感知块 (~95 行, 每步唯一大块) 抽取到 `core/loop_perception.py` (薄委托, 同 loop_checkpoint 模式; loop.py 净减 ~92 行, 行为等价, perception 测试守护)。六维仅测试用、链校验每 run 一次 → 本就不在每步热路径。至此 **Step 0 全部完成**。

### 可证伪经验机轮 (2026-07-21) — 北极星文档 + 持续学习第一块肌肉 (PARADIGM Step 1)

**北极星文档**
- 新增 `docs/PARADIGM.md`：**可证伪经验机 (Falsifiable Experience Machine)** — zall 的长期演进北极星。核心：“靠熬过证伪而成长”的持续学习通用智能体。基于前沿 (经验时代 Silver&Sutton / Bitter Lesson / 开放式演化 / Voyager 技能库 / “自改进仅在可验证处成立”) + 波普尔“猜想→反驳→蒸馏”。明确五器官、Popperian Gate、演进路线与诚实边界。

**Step 1: 持久经验流 + 技能复利**
- 新增 `core/experience_store.py`：`ExperienceStore` 跨会话持久 (`~/.zall/experience/experience.jsonl`)。**Popperian Gate**: 只有 verified (熞过证伪) 的经验才蒸馏为“技能”; `recall(task)` 按关键词相关性召回已验证技能 (模型无关/离线/确定性, 非向量检索, IPR-3)。
- **复利读路**: `PromptBuilder.add_experience_recall()` 在新任务开始时注入相关技能→同类任务第 2 次(新会话)能用上上次经验; prompt 缓存 key 加入 user_raw 防串。
- **写路**: `orchestrator.run()` 完成后写入经验 (final_state==MET→verified 技能; 否则仅历史)。
- 新增 `tests/test_experience_store_invariants.py` (13 测试含反例: 只召回 verified / 无关不注入噪声 / 跨会话复利 / 去重边界 / 排序确定性)。离线验证: 上一会话的 verified 技能确实注入新任务 prompt, 无关任务不注入。

### 红蓝对抗共进化轮 (2026-07-21) — 增量 A: Experience Bank + 吸取前沿 (非盲目 verify)

**版本号 → 0.0.1**
- `__init__.py` + `pyproject.toml` 统一为 `0.0.1` (完善前重置)。

**修复: inline 下上下文提示消失 (回归)**
- 默认改 Textual inline 后, 旧的 prompt_toolkit bottom_toolbar 的 ctx% 不再显示; 而 Textual StatusBar 的 `status_context_pct` 声明了却**从未被填充**。新增 `_update_usage()` 从 model_call usage 算 ctx% (prompt tokens / 模型 window) 并回填, 两个 usage 处理点共用。ctx% 重现于状态栏。

**增量 A: 红蓝对抗共进化循环 (借鉴 Hyra, 真正吸取前沿)**
- 新增 `core/red_blue.py`: `ExperienceBank`(存方案+评估, 兼 Context Agent 合成多样灵感) + `RedBlueLoop`(Blue 提议 → Red **主动攻击**打分 → EB 记录 → 逐轮抬门槛共进化)。三条前沿: 对抗压力(red-team/self-play)、多样性(quality-diversity)、评估器共进化(POET/双层循环)。**不再把“可验证”当卖点** — 可复现只是支撑审计/重放, 真正价值是对抗压力产出更鲁棒的解。纯编排+依赖注入 (blue_fn/red_fn), 无模型依赖 (IPR-3), 可离线单测。
- 新增 `tests/test_red_blue_invariants.py` (14 测试, 含反例: broken 高分不入选 / blue·red 异常隔离 / 共进化趋势可升可降 / 确定性 / 空方案判 broken / Red 门槛逐轮抬高)。
- **真实 API dogfood**: is_palindrome 任务, 2轮×2提议, 8 调用/61s, best=0.95; Red 真实找出 unicode 归一化边界案 (非橡皮图章)。

### 欢迎屏极简化轮 (2026-07-21) — 首次能看到真实渲染 (headless SVG 截图)

**用 Textual headless 截图真正看到 UI (不再盲改)**
- 通过 `App.save_screenshot` 导出 SVG + 浏览器渲染截图, 首次看到真实 TUI。发现旧欢迎屏是一个拥挤的带边框 Panel (header+分隔线+2 个网格), 多消息时被滚动截断→看似一个空框。
- 重设欢迎屏为极简风 (学 Claude/Pi): 无重边框, 高对比 wordmark (◆ zall vX · model) + 一行 tagline + 一行上手提示 (/help · @ · Shift+Tab · Ctrl+S); 完整命令/键位移到 /help。移除因此多余的 Panel/Group import。
- Footer: `Ctrl+O` 描述 "Edit in $EDITOR"→"Editor" (修底部键位条截断)。

### 视觉极简 + 持续自改进轮 (2026-07-21) — 学 Claude/Pi 渲染 + 借鉴 Hyra 递归自改进

**B 消息渲染极简化 (学 Claude Code / Pi 的无边框链式)**
- 工具调用渲染从带边框 Panel 改为极简 `● name(args)` + `└` 缩进 dim 输出 (Claude/Pi 手感); edit_file diff 仍红/绿/青着色但无边框; 用户消息改为 `❯ ` 提示符 + 明亮正文 (比整行 gold 更克制)。保留 assistant 代码块高亮 Panel。
- A: inline 配色/留白上轮已调; 本轮由消息渲染提升可见度。

**C 持续自改进循环 (借鉴腾讯 Hyra 递归自改进, verified-only 差异化)**
- 新增 `core/self_improve.py`: `SelfImprovementLoop` — propose→(去重)→**verify**→仅对通过者 apply。Hyra 的递归自改进过程不透明; zall 坚持 IPR-0: **只落地可验证的增益**, 未通过者记录+理由拒绝, 全程可复核。纯编排 + 依赖注入 (propose/verify/apply), 无模型依赖 (IPR-3)。`from_auto_learn()` 接既有 AutoLearnExtension (真实消费者)。
- 新增 `/evolve` (别名 `/improve`): **默认 dry-run** 预览 (验证候选但不落地), `/evolve apply` 才落地通过验证的; `-c` 调置信度阈。与 `/suggest` (逐条人工) 互补。
- 新增 `tests/test_self_improve_invariants.py` (14 测试, 含反例: 低置信度不 apply / dry-run 不落地 / 去重 / apply 异常隔离 / adjust_k 越界 / 不安全 skill 名拒绝)。既有自进化闭环 6 测试未破。

### 交互收敛轮 (2026-07-21) — inline 转正为默认 + 视觉美化 (学 Claude/Pi)

**交互界面收敛 (三路 → 一个清晰默认)**
- `main()` dispatch 重构: **默认即 Textual inline** (停靠输入框 + 模型运行时可输入, 对齐 Claude Code / Pi — 两者都是“同一 app + 两种渲染面”, inline 为主)。`--tui` 降为全屏 alt-screen opt-in; `--no-tui` 强制同步 REPL (dumb 终端 / SSH / 脚本); textual 缺失 / 终端不支持 → 自动回退同步 REPL。
- `_should_use_tui` 语义收窄为“是否全屏” (仅 `--tui` 且 textual 可用时 True); headless `run()` 一次性路径 **一行未动** (守住可复现/管道/replay)。
- 新增 4 dispatch 测试 (含反例): 默认 inline / `--tui` 全屏 / `--no-tui` 同步 REPL / 不支持时回退。

**inline 视觉美化 (学 Claude/Pi 的克制配色 + 留白)**
- CSS: 消息区增加垂直留白 (padding 0 1→1 1); 输入框水平留白 (0 1→0 2) + 配色更凝聚 (bg #232326 / 边框 #43434a); focus 边框改为温暖琴黄 (#b8860b→#e0a83b, 更雅); 状态栏更静音 (#202023 / #8a8a8a)。CSS 解析通过 (9 rules)。

### 内联 TUI 轮 (2026-07-21) — 停靠输入框 + 模型运行时可输入 (不入全屏)

**--inline: Textual inline 模式 (对齐 Claude Code / Pi 的"横框")**
- 新增 `zall --inline` / `-I`: 启动 Textual **inline 模式** — 停靠式输入框常驻底部, **模型运行时也能输入** (Enter 排队 / Ctrl+S steer, 复用已有机制), 但**不接管全屏** (不入 alt-screen, 保留终端滚回/复粘贴)。这正是用户要的"模型跑时也能输入的横框"。
- `run_tui(inline=True)` → `app.run(inline=True, inline_no_clear=True)`; 终端不支持时返回 2 安全回退到内联 REPL。Textual 8.2.8 原生支持 inline。
- 默认仍为内联 REPL (稳); `--tui` 全屏; `--inline` 为新的"停靠输入框"体验。新增解析测试。
  (本环境无真 TTY 无法交互测试 inline 渲染, 需真机 `zall --inline` 验收。)

### 底部状态栏轮 (2026-07-21) — 上下文占用提示 (学 Pi/Claude)

**内联 REPL 持久底部状态行 (prompt_toolkit bottom_toolbar)**
- 输入框下方常驻一行: `model · ctx N% / Wk · [plan] · / commands · @ files · Ctrl-D exit` — 对齐 Pi 的 `0.0%/131k` / Claude Code 的上下文指示。
- 上下文占用 = 最近一次模型调用的 input tokens / 模型 window (`get_window_size`); usage observer 实时刷新 `state["ctx_tokens"]`, toolbar 每次渲染读取。
- REPL 未传 `--model` 时从 config 解析真实模型名 (toolbar/提示符不再显 "zall")。抽出纯函数 `build_toolbar_text(state)` (可单测, 无 prompt_toolkit 依赖); 新增 3 测试 (含反例: 无 state 不显 / 无 ctx 不显 ctx)。

### @dir 支持 + 发布收尾轮 (2026-07-21) — 目录引用 / 全量回归 / release notes

**@dir 目录引用 (接着 @file)**
- `file_complete`: 新增 `list_workspace_dirs` + 目录参与补全 (`workspace_file_matches(..., include_dirs=True)`, 目录带末尾 /); `expand_at_references` 对 `@目录/` 注入**一层目录清单** `<dir>` (而非文件内容), 支持 file/dir 混合引用。新增 5 测试 (含反例: noise 目录不收录 / include_dirs=False / 不把目录当文件读)。

**发布收尾 (D)**
- 全量回归 (排除 real-API/subprocess/PTY 慢套件): **1746 passed / 4 skipped / 0 failed**; `ruff check src/` 全绿; IPR-3 架构不变量 (core/ 无模型 SDK import) 通过。
- 分发构建输入验证 (C): entry / 144 submodules / 4 adapters / 核心依赖均可解析 (pyinstaller 本环境未装, 单二进制需真机 `python scripts/build_binary.py`)。
- 新增 `RELEASE_NOTES.md` (面向用户的本版汇总 + 升级注意)。

### @file 自动注入轮 (2026-07-21) — 内联体验对齐 Claude Code 最后一步

**@file 引用 → 自动注入文件内容 (REPL + TUI)**
- 新增 `file_complete.expand_at_references(text)`: 提交时把解析到**真实文件**的 `@path` 展开为 `<file path="...">...</file>` 块注入消息 (不再只是补全路径字符串)。非文件 @token 原样保留; 去重; 单文件 64KB / 总 200KB 上限 (超限截断标注); 二进制/读失败标注跳过不崩。
- REPL: slash 处理后、发给 loop 前展开, 显示 `· injected N file(s)` 提示。TUI: `_run_agent_loop` 首回合 + 排队回合均展开 (用户气泡仍显原文 @path, 模型收到展开后)。一次性 `run()` 也已接入 (静默展开, 不污染 JSON/管道) — **三路径 (REPL/TUI/一次性) 全齐**。
- dogfood 验证: 一次性 `zall "@note.txt 的密码是?"` → 模型直接引用文件里的密码, **1 次模型调用 / 0 次工具调用** (无需 read_file 往返, 更快)。
- 新增 6 测试 (含反例: 非文件原样/无@快路径/二进制跳过/截断/去重)。至此内联 REPL 与 Claude Code 的 @ 体验对齐。

### 内联 REPL 打磨轮 (2026-07-21) — @ 文件补全进内联 + nul 崩溃修复

**内联 REPL 对齐全屏 (把好东西搬过来)**
- 新增共享 `cli/file_complete.py` (`file_query` / `list_workspace_files` / `workspace_file_matches`), REPL 与 TUI 共用**单一实现**。
- REPL 补全器 (`prompt.py` `_DescCompleter`) 新增 **@ 文件路径补全**: 输 `@src/lo` → 下拉候选工作区文件 (basename 前缀优先 + 路径短优先), 选中只替换末尾 @token; slash 命令补全不变。placeholder 提示同步为 `/ commands, @ files`。
- TUI `_file_query`/`_all_workspace_files`/`_workspace_file_matches` 改为薄委托共享模块 (消除重复)。

**顺带修真实 bug (dogfood 发现)**
- 工作区扫描遇 Windows 保留设备名文件 (如仓根的 `nul`) 时 `os.path.relpath` 抛 `ValueError` → 崩溃 (TUI 旧代码也潜在此 bug)。现 `list_workspace_files` 捕获并跳过异常路径。
- 新增 `tests/test_file_complete.py` (13 测试, 含反例: @ 前非空白/token 后空格/noise 目录跳过/缓存复用/不匹配过滤)。

### 内联化 + 提速 + 分发轮 (2026-07-21) — 方向定调: 不搞全屏, 对齐前沿

**A 默认内联 REPL (全屏降为 --tui)**
- `_should_use_tui`: 默认走内联 REPL (对齐 Claude Code / Codex / Aider / Pi — 前沿 coding agent 均为内联/滚动区, 非全屏 alt-screen); 全屏 TUI 仅 `--tui` 显式开启。内联保留原生滚回/复粘贴/SSH 友好, bug 面更小。新增测试锁定新默认。

**容错统一 (run/REPL/TUI 三路径)**
- `is_transient_error` / `TRANSIENT_KEYWORDS` 上提到 `core/loop.py` (单一真相源), REPL 从 core re-export; 新增 `AgentLoop._run_retry_transient` — 一次性 `run()` 也具备瞬态退避重试 (2s/4s/6s, retry_step 不漂移 step_count), 与 TUI/REPL 对齐。至此三个入口均不会因一次 429/5xx 而中途死。

**B-启动提速 (7x)**
- `cli/app.py`: 重型核心依赖 (Context/RunEgress/MCPTool) 降为 `TYPE_CHECKING` 仅注解, environment/TerminationState 懒加载 → `import zall.cli` 从 **487ms 降到 ~70ms** (core 链不再在入口处加载); `zall --version` 端到端 ~0.23s。REPL/任务路径用时才加载 core (无回归)。

**B-分发 (单二进制 “好装”)**
- 新增 `zall.spec` (PyInstaller, `collect_submodules('zall')` 处理动态 adapter/tool 加载, 默认精简不含 textual, `ZALL_BUNDLE_TUI=1` 可包全屏) + `scripts/build_binary.py` (构建+烟雾测试)。产出自包含单文件, 无需用户装 Python/venv/pip。

**C dogfood (真实 API 多步任务)**
- 用真实 API 跑“创建猜数字游戏 guess.py”: **24.4s 跑完** (2 model calls / 1 tool call), 文件正确生成 (含可运行的 `play()`, 导入校验通过)。证实修复后多步任务能建库/建项目且不再中途停。

### 容错修复轮 (2026-07-21) — “失败后中途就停” (dogfood 真实任务)

**P0 任务中途失败就终止 (多步任务不可用)**
- 根因: TUI 步循环在 `result.is_terminal` 时直接 `_show_error` + break — **无瞬态重试** (REPL 早就有)。在慢/抖的 reseller 端点上, 一次 429/5xx/timeout 就杀死整个多步任务 → 用户看到“失败后中途就停”。
- 修复: 提取共享 `is_transient_error` + `TRANSIENT_KEYWORDS` (repl_ui.py), REPL 与 TUI 共用; 新增 `TuiApp._retry_transient` — 瞬态错误退避重试 (2s/4s/6s, 最多3次, 用 `retry_step()` 不漂移 step_count), 成功恢复则继续任务, 非瞬态/耗尽才显错。重试期间状态栏 spinner 照常动 + 系统消息反馈。新增 `tests/test_transient_retry.py` (5 测试, 含反例: 恢复/耗尽限3次/非瞬态早停/中断中止)。

### 可用性/配置修复轮 (2026-07-21) — dogfood: model unset / config 损坏 / 感知延迟

**P0 model 显示为 "unset" (配置有 API 却显示未设置)**
- 根因: TUI `TuiApp.__init__` 用 `model=args.model or ""` — 未传 `--model` 时为空, 从不从 config 解析; 且 `state["model"]=""` 使 `build_repl_loop` 的 provider 检测退化为 openai。
- 修复: TUI 未传 `--model` 时从 `_config_status()` 解析真实 model (与 REPL 一致)。现显示 `agnes-2.0-flash` 而非 "unset", 且 provider 检测正确。

**P0 config 文件损坏 (重复 [auth]/[model] 段无限膨胀)**
- 根因: `_persist_model_to_config` 的 `_update_key_in_lines(lines, ...)` 收到**含段头**的 lines 并重新吐出段头, 而调用方又单独 append 了段头 → 每次 `/model -p` 都使 `[auth]`/`[model]` 翻倍 (model 因两次调用而三倍)。取决于 TOML 解析器, 严格解析器 (tomllib/tomli) 会拒绝重复表 → 将报错。
- 修复: `_persist_model_to_config` 改为规范化输出 (段头只写一次, 已知 key 从解析后 data 取, 额外 key 如 timeout 保留, 同名段去重); `save_api_key` 同款去重; `load_toml_simple` 对 tomllib/tomli 解析失败回退到宽松解析器 (last-wins), 不再崩溃。两者均**自愈**已损坏的 config。新增 `tests/test_config_selfheal.py` (3 测试, 含反例)。

**P0 响应慢 / “卡死”感 (dogfood 定位)**
- 真实 API 测试结论: 框架高效 (建文件任务仅 2 次模型调用, 最优); **慢的是端点** — agnes-ai 代理 ~9–12s/调用 (连“reply one word”也 12s), 与 input token 无关。多步任务 (坦克大战) × 弱模型 (flash) → 很多步 → 十分钟。
- 感知延迟优化: StatusBar 新增**动画 spinner + 已耗时秒数** (`◔ thinking 8s…`), 由控件自持 `set_interval(0.2)` 驱动 — 即使模型思考的 ~10s 无事件空窗也能看到“在动”, 不再显得卡死。
- `/doctor` 新增**延迟报告**: `model_api OK (8.9s, N tokens)` + 慢端点时提示 `SLOW endpoint (~9s/call) — try /provider or a faster model`。

### 外部评估修复轮 (2026-07-21) — 六维闭合 + Windows/GBK 健壮性

**P0 (本体论闭合 + 性能)**
- **I-0/I-7 兑现**: 新增 `AgentIdentity` (core/agent.py) 并接入 AgentLoop — 六维本体论 ① Identity 首次落到运行时; loop 暴露 identity/commitment/perception_engine/authority/accountability/verifiability 六维只读投影 + `agent_has_all_dimensions()` 完整性判据; 新增 `tests/test_ontology_invariants.py` (7 测试, 含反例), 补齐此前完全缺失的旗舰不变量。
- **世界模型性能**: CodingWorldModel 缓存 CodeGraph (只索引一次), 消除每次写工具调用前全项目重建索引的瓶颈 (§4.2.3)。

**P1 (确定性 bug)**
- **M1** `executor.py`: schema 校验失败事件改用唯一 event_id (原复用 `tool_call_start_{count}` 造成 timeline 重复 event_id, 破坏 replay/去重)。
- **M2** `system.py`: 移除重复注册的 `/verify` (旧实现停用注册, 保留从磁盘重建 recorder + anchor 状态的唯一实现)。
- **M3** `checkpoint.py`: 去重日志集合从模块级全局 set 改为实例级 (消除跨实例共享 + 只增不减泄漏)。
- **M4** `loop.py`: `_try_compact_for_continuation` 用 `ChatState.message_count` 判断压缩效果 (原用影子列表 `_messages` 可能漏判)。
- **M5** `/about` 补版本/作者/许可证 (author: qinrayn / Yuhan Zhang · MIT); 修正 `DESIGN.md`→`MASTER.md` 引用; pyproject/README 作者更新。

**P2 (健壮性)**
- checkpoint 安全网失败从静默 `pass` 改为 `warning` 日志 (可观测, 不再吞磁盘满/权限错误)。
- **GBK 修复 (中文 Windows)**: `detect_text_encoding` 预初始化 `raw`, 修复文件不存在时的 `UnboundLocalError`; `cmd_review` 的 git subprocess 显式 `encoding=utf-8/errors=replace` + `diff_text` None 防护 (修复 `/review` 在 UTF-8 diff 下崩溃)。

### 评估后续增强轮 (2026-07-21) — 架构拆分 / 多 agent / 文档 / TUI

**架构瘦身 (loop.py 拆分首步)**
- PR-0 幻觉扫描从 loop.py 抽取到 `core/hallucination.py` (纯函数 + 预编译正则); loop 保留薄委托 staticmethod 兼容内部调用与既有测试; 移除 loop.py 现已多余的 `import re`。

**多 agent 并行编排**
- 新增 `core/coordinator.py`: `Coordinator` 编排原语 (dispatch+aggregate 角色分离 · 隔离容错 · 有界并发 · 有序聚合), `from_subagent_tool()` 接既有 `SpawnSubagentTool` (真实消费者); 新增 `tests/test_coordinator_invariants.py` (8 测试, 含反例: 失败隔离 / 致命信号传播 / 空任务退让 / 并发上限)。

**文档同步**
- MASTER.md §12.1 新增 **Identity=REAL** 行 (AgentIdentity 接入 + I-0/I-7 测试); Perception 行改用方法名引用防行号漂移。
- README: Agent Architecture 新增 "Six-Dimension Ontology" 与 "Subagent & Coordinator"; 对比表新增 "Multi-agent orchestration" 行。

**TUI 全屏交互美化**
- 输入框边框 `tall`→`round` (圆角), 配色/内边距微调, 滚动条 hover/active 渐变 (#8a6d1f→#b8860b→#FFD700), 状态栏内边距。

### 交互/模型/思考增强轮 (2026-07-21) — UI 精简 + provider 切换 + Claude 思考可见

**TUI 精简 (学 Claude Code/Ink 无可见滑块)**
- 隐藏滚动条滑块 (`scrollbar-size-vertical: 0`, 保留滚轮/键盘滚动); MessageList / 输入框 / VerticalScroll 均去滑块。
- 命令菜单 CommandMenu 边框 `tall`→`round` (圆角), 与输入框统一。

**模型 provider 切换 + 去硬编码**
- `model_registry.py` 新增单一真相源辅助: `get_provider_display/get_provider_tag/list_providers/get_provider_default_model`。
- `model.py` 移除内联硬编码的 `_PROVIDER_TAG`/`_PROVIDER_LABEL` (改为从 registry 派生)。
- 新增 `/provider` 命令: 列出所有 provider (tag+显示名+配置状态+当前) / `/provider <name>` 切换 (自动选默认模型 + 缺 key 时提示 env 与获取链接) / `-p` 持久化。

**Claude 思考过程可见 (像 Claude Code)**
- Anthropic adapter 新增 opt-in 扩展思考: `_build_body` 在 `thinking_budget>=1024` 且模型支持 (claude-3-7/*-4) 时请求 thinking blocks; 自动保证 max_tokens>budget + 回落 tool_choice=auto。thinking 解析此前已就绪 (reasoning→model_thinking→CLI/TUI 展示)。
- 启用方式: `ZALL_THINKING=1` (默认预算 2048) 或 `ZALL_THINKING_BUDGET=<tokens>` 或 config `thinking_budget`。默认关 (不改成本)。
- 说明: DeepSeek-R1 / o1·o3 / Gemini 思考模型此前已通过 reasoning_content 自动展示。

### 稳健性/工程化轮 (2026-07-21) — bug 猎杀 + lint 清零 + /thinking + /provider picker

**Bug 修复 (bug 猎杀)**
- **严重**: gemini/ollama 流式适配器 `except GeneratorExit: pass` 后仍 yield → Ctrl+C 中断流式必崩 (RuntimeError); 改为 `return` (与 anthropic/openai_compat 一致)。
- **真实崩溃**: apply_patch 锤点未命中分支引用未定义的 `path` → NameError; 修为固定文案。
- apply_patch 死代码 `new_text`/`anchor_idx` 移除 (替换实际走 new_lines_list, 无功能损失)。
- checkpoint restore_checkpoint 兑现 bool 契约 (OSError → False, /revert 不崩)。
- checkpoint _ensure_loaded 空列表每次重扫盘 → 加 `_load_done` 标志。
- cmd_model `-p -g` 组合解析修正 (guide 检测用去 -p 后首 token)。
- coordinator from_subagent_tool: meta 先入、prompt/parallel 后设, 防 meta 覆盖必要字段。
- safety.py / process_anchor/protocol.py: 补 `from typing import Any` (注解未导入)。

**推荐项落地**
- `/thinking on|off|<budget>`: 运行时开关模型思考展示 (Anthropic Claude 扩展思考; 切换后重建 adapter 生效)。
- `/provider` 升级为交互式 picker (TTY 下数字选择), 切换时强制重建 adapter (key/base 变更生效)。

**工程化**
- ruff 从 84 error 清零: 修 3 个 F821 真实 bug + 移除 48 未用 import + 8 空 f-string + 6 死变量; 配置 `[tool.ruff.lint]` (ignore E702/E741, __init__ 免 F401) → `ruff check src/` 全绿。
- CI (.github/workflows/ci.yml): 新增 IPR-3 架构不变量门禁; test_file_not_found 已修复并移出跳过清单; 保留多 OS 矩阵 + PyPI 发布。

### 架构拆分/命令合并轮 (2026-07-21) — loop.py 瘦身 + /mode 合并 + did-you-mean 修复

**架构瘦身 (loop.py 拆分第二步)**
- file-based checkpoint 簇 (扫描/选文件/快照 + 追踪扩展名/敏感排除常量) 从 `loop.py` 抽取到新模块 `core/loop_checkpoint.py` (无状态自由函数 + 接收 loop, 与 executor.py/context_manager.py 协作者模式一致); loop 保留薄委托方法 (兼容 test_plugin_safety 的 MagicMock 替换); 移除 loop.py 因此多余的 `import os`/`import fnmatch`/`skip_noise_dirs`/`NOISE_DIRS` 导入。loop.py 净减 ~98 行。

**命令合并 (层次化)**
- 新增 `/mode [strict|fast]` 统一交互模式开关 (无参显示当前 strict/plan; 别名: safe/auto/normal); `/strict` `/fast` 保留为向后兼容快捷方式, 三者共享 `_apply_strict_mode` 单一真相源 (去重)。
- `/help` 核心区新增 `/mode`; `/advanced` 用单行 `/mode` 替代分散的 `/strict`+`/fast`, 并补上早已存在但未展示的 `/provider` `/thinking`; 新增 `/mode` 详细帮助条目。

**回归修复 (本轮变更引入并当场修复)**
- `/mode` 与 `/model` 名称相近导致 did-you-mean 回归 (`/modle` 误建议 `/mode`); 新增 OSA (Optimal String Alignment, 含相邻换位) 距离对 difflib 近似候选重排, 使转置类 typo `/modle → /model` (而非 /mode)。

### TUI 交互对齐轮 (2026-07-21) — 学 kimi-cli: Shift+Tab plan + Ctrl+O 编辑器

**新增交互快捷键 (对齐 kimi-cli / Claude Code)**
- **Shift+Tab**: 切换 plan 模式 (只读探索/规划) — 复用既有 plan_mode 管线 (loop 构建时读 state, 已有 loop 立即 set_plan_mode), 状态栏显示蓝色 `plan` 徽章 + 系统消息反馈。
- **Ctrl+O**: 用外部编辑器 ($VISUAL/$EDITOR, 缺省探测 code/vim/nano/notepad) 编辑当前输入, 适合多行 prompt; 经 `App.suspend()` 挂起 TUI, 编辑后回填, 全程失败降级 (无编辑器/异常 → 系统提示, 不崩)。
- 键位经 ChatTextArea→InputBar→App 消息管线拦截 (TextArea 聚焦时也生效), Footer 自动展示提示。
- 新增 `tests/test_tui_interaction_kimi.py` (12 测试, 含反例: plan 切换对合 / 无 loop 不崩 / 无编辑器返回 None / 未挂载安全)。

### TUI 交互对齐轮二 (2026-07-21) — steer / 队列 / @文件补全 / 主题色

**Ctrl+S steer + Enter 队列 (学 kimi-cli, 复用外部步进式 loop)**
- **Ctrl+S steer**: 流式生成中把输入注入当前回合 — worker 每步前从线程安全队列取出 steer 消息并 `add_user_message`, 模型下一步即可见 (真 mid-turn 注入, 非中断重启)。空输入 + 有排队 → 将最早排队消息提为 steer。
- **Enter 队列**: agent 忙时回车不丢失/不抢占 — 消息入 pending 队列, 当前回合结束后自动作为新回合依次运行 (外层循环排空); 中断/报错不自动排空。状态栏显示 `N queued`。
- 斜杠命令始终立即执行 (不入队列)。

**@ 文件路径补全 (学 kimi/opencode)**
- 输 `@` 弹工作区文件补全菜单 (basename 前缀优先 + 路径短优先); Tab/Enter 补全为 `@路径 ` 并继续编辑。文件扫描惰性缓存 (跳 noise 目录, 上限 20000, 逐键盘内存过滤)。
- `CommandMenu` 增 prefix 参数 (命令=`/`, 文件=`@`), 与 `/` 命令菜单共用一套上下选择/补全机制。

**主题色 (学 opencode 语义角色)**
- `_C` 新增 `QUEUE`/`STEER` 语义色 (复用既有 Obsidian 基色), 为新交互状态提供一致配色。
- 欢迎屏 + Footer 补齐 Shift+Tab/Ctrl+O/Ctrl+S/@ 提示。
- 新增 20 测试 (steer/队列/@补全, 含反例: 空队列返回 None / drain 后为空 / 空 steer 提升排队 / @ 前非空白不触发 / 不匹配过滤)。
- **附带修复 (验证时发现的陈旧测试)**: `test_non_tty_step_prefix` 仍断言 v0.4.10 旧行为 (非 TTY 加 step 前缀), 与 v1.5 有意设计 (render.py:928 非 TTY 纯净输出) 矛盾; 已更新为 `test_non_tty_no_step_prefix` 对齐当前行为 (非本轮回归, 色常量变更无关)。

### 思考渲染 + 主页美化 + bug 猎杀轮 (2026-07-21) — Claude Code 风思考 / dogfood

**思考过程: Claude Code 风格 (非流式)**
- 思考中只显静态 `✻ Thinking…` 指示 (不逐 token 闪烁), 完成时一次性定格为 dim 斜体块 (头 + 正文, 超 12 行折叠)。**顺带性能**: 思考渲染从每-token 全量重绘 (O(n²)) 降为每回合 2 次渲染 (O(1))。

**主页/欢迎屏美化**
- 重排为: 含版本号的 wordmark + tagline + 分隔线 + 对齐双列键位/命令网格 + 副标题 (MASTER.md → IMPL.md → code)。

**bug 猎杀 (子代理审查 + dogfood)**
- **思考卡死**: 中断/报错于思考阶段时不定格 → 永远停在 `✻ Thinking…`; 修: 每轮重置 `_thinking_active` + 在 `_show_interrupt`/`_show_error` 定格。
- **消息丢失竞态**: `_agent_running` 置位过晚 (build 期间为 False) → 快速第二次提交会被 exclusive worker 取消丢失首条; 修: 主线程提交时立即置位 + build 失败早返回复位。
- **光标无效**: 补全/历史导航用 `cursor_position`(Textual 8.x 无此属性, 静默空操作) → 改用 `cursor_location = document.end`。
- **fd 泄漏**: devnull sink 未关闭 → 持引用 + `on_unmount` 关闭。
- **argparse GBK**: `--help` 描述的 `—`/`→` 改 ASCII (健壮性; 实测→/— 本属 GBK 可编码, 仅 ©/✻ 不可 — 后者仅在 Textual 渲染, 不过 gbk stdout)。
- **Windows 可移植**: `test_execute_timeout`/`test_sandbox_timeout` 用 Unix `sleep` → Windows 无此命令即退而非超时; 改为平台分支 (`ping -n 11`), 首次在 Windows 上真正验证沙箱超时功能。

**全量测试 + dogfood**
- 全量套件 (除 real-api/子进程沙箱/pty 3 个慢套件): 1715 passed → 修两个不可移植测试后全绿; 新增 TUI 交互测试至 30 (含 bug 回归)。
- dogfood: 逐命令打钩 24/24 可用 (含 /verify 链 valid / /science / /sessions / /doctor); 入口 `--version`/`--help` 正常。

### 确认门/性能/交互修复轮 (2026-07-21) — 可用性关键修复

**P0 确认门挂死 (致命: agent 不可用)**
- TUI 之前复用 `CliUserResponder`, 其 `ask()` 调 `input()` 读 stdin — 而 Textual 占用 stdin → greylist 写文件需确认时 worker 线程永远阻塞 ("等待中")。
- 新增 `TuiUserResponder` (继承 CliUserResponder, 复用 greylist/blacklist/always-allow/y-n-a-e-s 全部决策逻辑), 但把权限面板投到 TUI 消息区, 用 threading.Event 阻塞 worker 直到用户在输入框回答; `build_repl_loop` 新增 `responder` 可注入参。中断时 cancel() 解除阻塞。新增 4 测试。

**P0 响应慢 (流式卡顿)**
- `MessageList._rerender_all` 每 80ms 节流都 `clear()`+重做整个会话的 markdown/语法高亮 (长会话 O(n×高亮)) → 新增 `to_rich_cached`: 定格消息缓存渲染, 只重算流式那一条。
- 内存: 测得启动+构建约 76MB (正常); RichLog 有 max_lines=10000 上限; 极高内存为环境相关 (大项目 CodeGraph 索引 / 已配 MCP 子进程)。

**交互 (右下键位无效)**
- App BINDINGS 全部加 `priority=True` — 修复输入框(TextArea)聚焦时 Ctrl+C/L/D/Q 等被 TextArea 吞掉导致 "按了无效"。

**思考 (改回 Claude 风: 流式 + 折叠)**
- 上轮改成静态占位不对 — Claude 是流式 + 可折叠。现恢复节流流式 (80ms, 非每-token → 非 O(n²)): 思考中流式显示末尾 ~8 行 dim 斜体, 完成后折叠 (前 12 行 + "+N more")。

**前沿调研 (评估是否落后)**
- 2026 SOTA 关键方法: 仓库向量索引/RAG、research-first、多 agent、context engineering。zall 在可证伪/可复现/安全门 **领先**; 多 agent(Coordinator)/压缩/plan **持平**; 语义向量检索(embeddings)、工程成熟度/并发/速度 **略落后** (向量检索与模型无关/离线是取舍)。

### v1.1 核心: MASTER.md §12 执行真相表 + E0-E9 路线 + 红蓝对抗

- **§12 能力真相表** - "宣称 vs 实现"双表对照, §1-§11 条目须在 §12 刷 REAL 才升回 SETTLED。
- **§12.3 E0-E9** - 近期硬约束路线 (替代旧 Phase 表)。
- **§12.4 守接口不落码** - Embodied 维度无硬件不写代码。
- **§1.3.1 红蓝对抗** - dogfood 6 条假设裁决 (2 OPEN 已修, 4 确认)。

### E0: 审计修复

- compactor 双向 tool_call/tool_result 配对保护 + 9 反例测试。
- gemini SAFETY/RECITATION 截断不再静默映射 STOP。
- GoalConfirm/help 测试适配 v0.6.0。

### E1: Perception 闭环 (DECORATIVE -> REAL)

- anomaly 熔断 (PERCEPTION_ANOMALY 事件 + system nudge)。
- 状态摘要注入 (显著变化才注, 反例无变化不注)。
- predict 进 timeline。
- OPEN 2 修正: anomaly 用 current-baseline, 脏工作区不误报。

### E2: Self-evolution 闭环 (DECORATIVE -> REAL)

- apply_suggestion 真落盘 (skills/ + learn_overrides.json)。
- get_config_overrides 读回持久化。

### E3: Science Kit (ABSENT -> REAL)

- core/hypothesis.py (H-1..H-4 不变量) + experiment.py + evidence.py (NegativeResult I-10) + provenance.py (接 RunRecorder anchor)。
- extensions/science/store.py (append-only JSONL) + cli/commands/science.py (/science 命令)。
- tools/science.py: agent 可调用的 science 工具 (E3.6)。
- **E3.6 dogfood 验证**: agent 自主用 science 工具跑通 假设->证据->证伪->修订 完整闭环 (GF-consistency 真实数据)。

### E4: 交互层还债

- autosave 去 PID + 原子写入 (tmp+os.replace) + 软锁。
- 中断丢弃半成品 (Ctrl+C 回滚 messages + USER_INTERRUPT 事件)。
- 权限跨会话持久化 (.zall/always_allow.json + /forget-permissions)。blacklist 永不放行。
- conftest autouse fixture 防 always_allow 测试污染。

### E5: Accountability 多 Judge (PARTIAL -> REAL)

- AgentConfig.judges dict + get_judge(type) 查 base_judge 表。
- _check_termination 接通 from_verdicts(main, aux) §5.4 编排。向后兼容。

### E6: Verifiability 运行时自检 + CLI

- run 结束 verify_chain() 自检 + CHAIN_BROKEN 事件 + RunEgress.chain_warning。
- /verify [run_id] 命令: 第三方独立复核 timeline。

### E7+E8+E9: Plugin 生态四支柱

- E7 版本化: get_tool_version/parse_semver/is_version_compatible + check_compatibility (SemVer)。
- E8 发现机制: plugin_loader.py 扫 entry-points + manifest + 内置优先 + 失败隔离。
- E9 schema 校验: validate_tool_args (stdlib 子集) + executor 执行前校验。
- E9 能力声明强制化: assert_capabilities_declared, 插件默认 Write。子进程隔离 OPEN。

### OPEN 修复

- OPEN 1: "undecidable - no judge" -> "no judge (Q&A mode)"。
- OPEN 2: perception anomaly 用 current-baseline。

### 测试

- 1242 passed / 5 failed -> **1461 passed / 0 failed / 11 skipped** (净增 219)。

### E10-E13: 第四轮深化 (2026-07-20)

- **E10 ScienceTool experiment 管理** - 新增 new_experiment/run_experiment/complete_experiment action, evidence 可关联 experiment_id 形成完整溯源链。25 测试。
- **E11 Plugin 子进程沙箱** - `core/sandbox.py` SubprocessSandbox (进程级隔离+超时+cwd), 插件工具 (namespace=PLUGIN) 默认 sandboxed。沙箱 PARTIAL->REAL (进程级); 内存/网络/容器级 OPEN。16 测试。
- **E12 交互层深度优化** - 多行输入 (反斜杠续行+粘贴检测), 会话恢复提示含最后消息摘要, 错误信息 422/401/429 具体提示。23 测试。
- **E13 bugfix 验证 system judge (dogfood)** - 真实 API 跑 bugfix 任务, 发现 P1: max_steps terminal 不调 judge。已修: max_steps 场景调 _check_termination 覆盖 final_state。P3: aux judge 类型解析 (多 Judge dict 模式才查类型, 单 judge 向后兼容)。3 不变量测试。
- **测试**: 1461 -> **1512 passed / 0 failed / 11 skipped** (净增 51)。

### E14-E16: 第五轮交互深化 (2026-07-20, 学习 Claude Code/Grok Build/Kimi Code)

- **E14 MAX_STEPS 软处理** - 到上限不直接终止, 先压缩上下文+重置步数继续 (最多重试 2 次)。回答用户"步数限制不合理"的痛点: 限制是安全阀, 但到上限就 UNDECIDABLE 太粗暴, 改为压缩后继续。3 不变量测试。
- **E15 视觉风格优化 (借鉴三源码)** - spinner stall 检测 (超 10s 渐变 WARN, 超 30s 变 FAIL+taking long); 新增模式色 (_ModeColor: normal/plan/accept/strict); thinking 符号 (◬); 多帧 spinner 已有 (盲文, 同 Grok Build)。无 emoji, 纯 Unicode 几何符号 (三源码共识)。
- **E15 权限请求 Panel UI (借鉴 Claude Code)** - greylist 工具调用时用 rich Panel 显示工具名+关键参数+风险色, 而非纯文本 "Allow? [y/N]"。降级安全 (Panel 失败回退纯文本)。
- **E16 P2 CLIXML 解码 (Windows bash)** - bash 工具检测 PowerShell CLIXML 输出 (#< CLIXML) 并解码为纯文本。dogfood 发现 agent 在 Windows 上无法读 pytest 输出。5 测试。
- **三源码学习** - Claude Code (Ink/React, 双键 Esc, 权限 Panel, 主题系统), Grok Build (Rust, doom_loop 独立预算, Unicode 符号 fallback, TurnCapture 零拷贝), Kimi Code (pi-tui, 流式 50ms 合并刷新, thinking 双模式, 步骤自动折叠)。报告存档供后续迭代。
- **P4 SystemJudge 假阴性修复 (dogfood 发现)** - judge 之前跑全量 1500+ 测试超时 -> "pytest unavailable" 假阴性。修: 基于 git diff 只跑受影响的快测试 (排除 integration/interaction/cli_app 等慢测试), 加 `-x` 首失败即停, timeout 30s。验证: SystemJudge 现在正确返回 MET + "all 9 test(s) passed"。4 不变量测试。
- **测试**: 1512 -> **1533 passed / 0 failed / 11 skipped** (净增 21)。

### E17-E20: 第六轮交互跨代升级 (2026-07-20, 全屏 TUI + 流式 + 重试 + 自优化)

- **E17 流式渲染重构 (借鉴 Kimi Code)** - `_StreamBuffer` 50ms 定时合并刷新 (替代字符级节流); partial Markdown fence 临时补全防闪烁; 工具调用实时预览 (部分 JSON 提取 path/command); 中断保留部分输出 + [Interrupted] 标记。断句点 (空格/换行) 立即 flush 给用户即时反馈。21 测试。
- **E18 API 超时/重试优化 (借鉴 Claude Code + Grok Build)** - 错误分类 (RATE_LIMIT/SERVER_ERROR/TIMEOUT/AUTH_FAILED/INVALID_REQUEST/CONTENT_FILTER); `classify_http_status` + `is_retryable_status`; 401/403/400/422 不重试, 429/5xx 重试; `_notify_retry` 用户可见重试回调; 修复 `is_retryable_http` 变量名 bug (status->status_code)。22 测试。
- **E19 全屏 TUI 双模式 (借鉴 Claude Code/Kimi Code, 用户坚持不放弃)** - `src/zall/cli/tui/` (textual 8.x): TuiApp + ChatMessage/MessageList/InputBar/StatusBar/ToolPanel/ThinkingPanel widget。`--tui`/`--no-tui`/auto-detect (TTY+textual 可用->TUI, 否则行式)。全事件类型处理 (17 种 LoopEvent); 流式 token reactive 更新; Obsidian 配色 + Unicode 几何符号 (无 emoji)。textual 为可选依赖 (`pip install zall[tui]`)。50 测试。
- **E20 AI 自优化方案 §13** - MASTER.md 新增 §13 (六步循环 Observe->Diagnose->Propose->Execute->Verify->Reflect, 8 条安全阀, 防退化机制, before/after 基线对比); `docs/AI_SELF_OPTIMIZE_PROMPT.md` 可复制提示词模板 (用户可用 Claude Code/zall 自跑自优化)。
- **bug 修复** - `is_retryable_http` 变量名 bug (status->status_code); Ctrl+C 中断提示恢复 "interrupted" 文本; 流式 buffer 断句点 flush。
- **测试**: 1533 -> **1576 passed / 0 failed / 13 skipped** (净增 43, 含 TUI 50 + 流式 21 + 重试 22)。

---

### — 2026-07-19

### UX Overhaul

- **`confirm_goal` 默认不交互** — 不再弹 `[y/N]` 确认。仅在 `--strict` 或 `--judge system` 模式才交互。goal card 仍然渲染（信息性）。
- **`--strict` / `-S` 标志** — 新 CLI 参数，启用 full confirm/downgrade gates。`zall "task" -S` 或 `zall -S` 进入严格模式。
- **`_init_downgrade` 默认非交互** — 降级候选仅记录到 timeline，不弹出交互确认。严格模式才交互。
- **移除 `_is_trivial_task` hack** — 不再需要（降级默认非交互，不会打断 hello world 类任务）。
- **auto-compact 用户可见提示** — 上下文压缩时在终端输出一行 `compact: N msg (reason)`，替代原先的静默压缩。
- **`AgentConfig.strict` 属性** — 新增 `strict` 配置字段，控制 confirm/downgrade 是否交互。`AgentBuilder` 新增 `.with_strict(bool)`。

### Architecture: ToolCapabilities 权限体系

- **`ToolCapabilities` 能力声明** — 每个工具声明 `is_read_only` 和 `tool_scope (Read/Write)`。22 个内置工具全部声明完毕。参考 Grok Build 的 `xai-tool-protocol/src/capabilities.rs`。
- **`context_judge` 决策链重构** — 无规则匹配时不再默认 greylist，而是根据工具能力决定：只读工具 → WHITELIST（默认放行），写工具 → GREYLIST（默认询问）。大幅减少只读操作的不必要弹窗。
- **`get_tool_capabilities(tool)` 辅助函数** — 获取工具的能力声明，未声明时返回默认值（Write/非只读）。
- **`ToolExecutor` 移除 `_is_tool_write_by_kind`** — 替换为 `ToolCapabilities.is_read_only` 检查，plan mode 强制执行使用统一能力声明。

### Architecture: PlanModeTracker 状态机

- **`core/plan_mode.py` 新增** — `PlanModeTracker` 状态机（`Inactive↔Active`）。参考 Grok Build 的 `xai-grok-shell/src/session/plan_mode.rs`。
- **Plan mode 可序列化** — `PlanModeSnapshot` 支持跨进程重启持久化。
- **Plan mode 只写 plan.md** — 在 plan mode 下，只有 `write_file`/`edit_file`/`batch_edit` 对 `plan.md` 的写入被允许，其他写操作全部拦截。
- **`AgentConfig.planner` 属性** — 注入 PlanModeTracker 实例。`AgentBuilder` 新增 `.with_planner()`。
- **`AgentLoop.planner` 属性** — 公开访问 PlanModeTracker，替换纯 `_plan_mode` bool。

### New: ApplyPatchTool (Codex 式语义补丁)

- **`tools/apply_patch.py` 新增** — 语义锚点补丁工具。使用 `@@ function_name` 定位编辑目标，比 `edit_file` 的精确字符串匹配更鲁棒。参考 OpenAI Codex CLI 的 apply_patch 自定义补丁语言。
- **补丁格式**: `@@ <function/class signature>` 锚点 + `-` 删除行 + `+` 新增行。
- **语义定位**: 在文件中搜索函数/类签名找到锚点，然后在锚点范围内查找旧文本。
- **注册到工具集** — 加入 `_get_native_tools()` 默认工具列表和 `toolset.py` 的 `_TOOL_MODULES` 映射。
- **`ApplyPatchTool` 集成** — 通过 `orchestrator.py` 注入工具循环，模型可直接调用。

### New: Second-Agent /review

- **`/review` 命令新增第二 agent 评审** — 独立上下文模型调用评审代码变更，不污染主对话。评审结果直接输出到终端。
- **评审维度**: 正确性/安全/代码质量/测试覆盖/改进建议。输出格式: `PASS / MINOR_ISSUES / MAJOR_ISSUES / CRITICAL`。

### DESIGN.md 更新

- **§4.2.1**: 更新 context_judge 函数定义，文档化工具能力决策链。
- **§4.5**: 新增 tiered-rigor (分层严谨) 文档，说明默认/严格/judge 三种模式的交互差异。
- **§9.2.1**: 更新 Goal 确认为 tiered confirm，反映 v0.5.1 默认不交互的改动。
- **§9.2.5**: 更新计划模式为 PlanModeTracker 状态机 + ToolCapabilities 强制执行。

### Phase 1: 修裂缝 (v0.5.x → v0.6)

- **Refiner 接入 run** — `core/verifiability.py` 新增 `GOAL_STATEMENT`、`USER_CONFIRM` EventType；`core/loop.py` `AgentLoop.run()` 在第一条 `tool_call_start` 之前记录 `goal_statement` + `user_confirm` 事件到 timeline。不变量：timeline 中第一条 `tool_call_start` 之前必须有 `goal_statement` + `user_confirm`。
- **外部锚点真正外部化** — `core/process_anchor/` 新包（`protocol.py` IPC 协议、`server.py` 独立 anchor 进程、`client.py` `ProcessTrustAnchor`）。`ProcessTrustAnchor` 实现 TrustAnchor Protocol，走 IPC 签名，不可达时返回 `None`（诚实退让）。不变量：`ProcessTrustAnchor` 不在 agent 进程内持有私钥。
- **TerminationCriterion 默认实现** — `core/judge/` 新包（`SystemJudge`、`UserJudge`、`ModelSelfJudge`、`default_judges_for_goal_type`）；`cli/judge.py` 委托到 `core/judge`。不变量：无自定义 Judge 时 run 不崩溃。
- **评估体系落地** — `core/eval/` 新包（`load_timeline`、`compute_goal_achievement_rate`、`compute_timeline_integrity_rate`、`evaluate_from_timeline`）；`cli/commands/eval.py`（`/eval` 命令从 timeline 计算 metrics）。不变量：`/eval` 从 timeline 计算 metrics，无 timeline 时诚实退让。
- **测试文件** — `tests/test_phase1_invariants.py`（15 个不变量测试，每个含 counterexample）。

## [0.5.2] — 2026-07-18

### Architecture
- **移除 `__setattr__` 拦截器** — 不再拦截 `_messages` 赋值。所有消息操作统一通过 `_chat_state.messages`，代码路径更清晰，消除潜在递归风险。
- **统一消息访问路径** — 所有内部代码（`_call_model`、`_call_model_stream`、`_run_step_body`、Extension hooks）统一使用 `self._chat_state.messages` 或 `self.messages` property，不再直接访问 `self._messages`。
- **`TemplateRenderer` 提示词模板系统** — 新建 `core/prompt_template.py`，从 Grok Build 的 `PromptContext` + `TemplateRenderer` 模式获得启发。支持 `${{variable}}` 插值、命名模板、模板组合。提取了 `mid_turn_interjection`、`doom_loop_nudge`、`empty_stop_nudge`、`session_resume_note` 等模板。
- **`_EMPTY_STOP_NUDGE` 移至模板系统** — 从 `loop_events.py` 移入 `prompt_template.py`，消除硬编码常量的循环导入问题。

### Fixed
- **AutoLearn `_error_patterns` 无限增长** — 添加 `_MAX_ERROR_PATTERNS=100` 上限，`on_turn_done` 中自动修剪。跨会话加载时也进行修剪。
- **AutoLearn `_persist` 线程安全** — 移除后台线程（改用同步写入），添加重试机制（最多 3 次），写操作在锁保护内完整执行。
- **`_is_trivial_task` 误判 "help"** — 从 greetings 集合移除 `"help"`，防止 `/help` 命令被跳过目标降级。
- **CLI `session.py` 后备路径绕开 ChatState** — 移除 `if hasattr(loop, "set_messages"): ... else: loop._messages = ...` 死代码路径，统一使用 `loop.set_messages()`。
- **`ContextManager._auto_compact` 直接读取 `_loop._messages`** — 改为 `self._loop.messages` property，确保读取 ChatState 真相来源。
- **AutoLearn 跨会话去重** — 加载时按 `(kind, target)` 去重建议，按置信度排序。

### Improved
- **AutoLearn 跨会话模式聚类** — `_load_persisted` 中添加 `_cluster_similar_chains()`，相同前缀的工具链只保留最长代表。
- **AutoLearn 配置层注入** — `get_config_overrides()` 添加 `judge_mode` 覆盖输出，高置信度建议（≥0.8）的 `adjust_judge` 类型自动注入配置层。
- **AutoLearn 持久化可靠性** — 添加重试机制（指数退避 100ms/200ms/300ms）和最大文件大小限制（500KB）。

## [0.5.1] — 2026-07-18

### Architecture
- **ChatState 单真相来源** — `AgentLoop._messages` 改为 property 委托到 `_chat_state.messages`。`_append_message`、`set_messages`、`remove_messages_by_predicate`、`_auto_compact` 全部通过 ChatState 操作，消除双写不一致。`_chat_state` 不再为 None（始终初始化）。
- **`_auto_compact` 通过 `set_messages()` 同步** — 不再直接写 `_loop._messages` 私有属性，通过公开 API 同步 ChatState。

### Improved
- **错误处理** — `except Exception: pass` 一律改为 `logging.warning(...)`（loop.py auto_learn、events.py EventBus、checkpoint.py rollback），保留 IPR-0 安全性的同时让故障可观测。
- **`AnthropicAdapter.close()` 防护** — 当 `_client` 为 None 时不再崩溃。
- **`_SubagentCwdMeta` 继承 git 信息** — 从父 context 继承 `git_branch` / `git_remote`，使 git-protection 规则对子 agent 生效。
- **`RetryBudget` 集成 OpenAI 适配器** — `_call()` 方法使用 `RetryBudget` 区分 transport/api 错误预算，替代旧 `with_retry`。
- **工具 schema 性能优化** — 移除 `copy.deepcopy`（ToolRegistry frozen 保证不可变），直接缓存引用。
- **死代码清理** — 移除 `_COMPACT_PROMPT`（v0.1.4 后改用规则折叠）、`ChatState.messages.setter`（零外部调用）。

### Fixed
- **ChatState sync 路径统一** — `context_manager.py _auto_compact` 通过 `set_messages()` 同步，消除直接写私有属性的 Bug。

## [0.5.0] — 2026-07-18

### Fixed
- **B1: `_append_message` 事务安全** — 先写 `_messages` 再写 ChatState，ChatState 失败时回滚，保证两状态一致。
- **B2: Anthropic + Gemini 工具 schema 查错层级** — zall schema 是 OpenAI 格式 `{"type": "function", "function": {"name": ..., "parameters": {...}}}`，适配器在顶层查 `tool_id`/`name`/`input_schema` 全部返回 None。修复后从 `function` 嵌套 dict 正确提取。影响所有 Anthropic/Gemini 用户的工具路由。
- **B3: Compaction 在 timeline 记录前应用** — 先记录 CONTEXT_COMPACTION 事件到 timeline，再替换 `_messages`，保证 "timeline 是真相来源" 不变量。
- **B4: `_auto_apply_suggestions` 绕过 ExtensionRegistry 公开 API** — 添加 `iter_extensions()` 方法，不再直接访问私有 `_extensions` 属性。
- **C1: Gate SUSPENDED 无超时** — 增加 300 秒整体超时，防止用户按 "s" 后永不回应导致永久挂起。
- **C2: Anthropic stream 吞掉 GeneratorExit** — 改为 `raise` 传播，确保 Ctrl-C 可正确中断流式请求。
- **C5: Subagent Future 泄漏** — `_on_done` 回调添加 `except BaseException` 兜底清理，防止 `SystemExit`/`KeyboardInterrupt` 导致线程泄漏。
- **C6: Gate Decision timeline 顺序错误** — 先询问用户，再记录 GATE_DECISION 事件，保证 timeline 顺序正确反映实际交互时序。
- **C8: 全局可变状态测试隔离** — 添加 `reset_tool_classes_cache()`、`reset_env_cache()`、`reset_sessions_cache()` 等测试辅助函数；`bash.py` 的 `_SELF_PID` 改为惰性计算避免 fork 后 stale PID。

### Added
- **Doom-loop 检测** — 检测模型重复相同 tool call 序列（`_DOOM_LOOP_WINDOW_SIZE=5`），超过 3 次警告、5 次注入 nudge 打断循环。借鉴 Grok Build 的 doom-loop 恢复机制。
- **RetryBudget 重试预算体系** — 区分 transport / api / semantic 三类错误预算，各自独立退避策略。借鉴 Grok Build 的 `RequestBudget` 设计。
- **中间打断缓冲区** — `ContextManager.push_interjection()` / `drain_interjections()` 支持用户在 agent 工作时发消息，下个 step 自动注入为 system message。
- **ToolStreamItem 流式协议** — 定义 `ToolStreamItem` 类型（Progress / Terminal），为工具流式输出打下基础。借鉴 Grok Build 的 `ToolStreamItem` 协议。
- **跨会话元学习增强** — `AutoLearnExtension.get_config_overrides()` 新增基于跨会话成功率的 K 值优化：高成功率工具保持低 K，持续失败工具建议高 K。

### Changed
- **`AgentLoop._append_message` 顺序** — 先写 `_messages` 主存储，再同步 ChatState 副存储。
- **`ContextManager._auto_compact` 顺序** — 先记录 timeline 事件，再应用压缩结果。
- **`_init_downgrade` 决策顺序** — 先询问用户，再记录 GATE_DECISION 事件。
- **`bash.py` 自保护 PID 检测** — `_SELF_PID` 改为惰性计算，每次调用时检查当前进程 PID，避免 fork 后使用父进程 PID。

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

## [0.1.0-pre] (legacy) — 2025-06-?? (Pre-release)

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