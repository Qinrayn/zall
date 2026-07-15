# zall · Invariants ↔ DESIGN.md mapping

> 对应 IMPL.md IPR-0:每个 SETTLED 节(§1-§6)的代码化身必须有
> ≥1 个 invariant 测试,且**测试必须含反例**(明示何条件下失效,失效对应
> 文档哪条被偷渡)。

本文件维护 **测试文件 ↔ DESIGN.md 章节** 的映射。每加一个 primitive
对应加一行。

## 模板(每个 primitive 必填一行)

```
tests/test_<primitive>.py  ←→  DESIGN.md §<节号>
  反例:<一句话说明该测试在何条件下 fail, 对应文档哪条被破坏>
  上溯:<该 primitive 长出于 §1.2 的哪一项>
```

## 当前映射表

| 测试文件 | DESIGN.md 节 | 反例摘要 | 上溯 §1.2 |
|---|---|---|---|
| `scripts/check_ipr3.py` (CI 钩子,等价自检) | IMPL.md IPR-3 | 构造 `import openai` / `from anthropic` 的反例后 → 必报 forbidden; docstring 里出现 "import openai" 字样 → **不应**误判 | (元规则) |
| `tests/test_goal_invariants.py` | §3.2 / §3.5 | 6 条反例:added_intent 非空 raise / confidence 越界 raise / frozen 改写 raise / tuple 不可 append / system_judge+None exposed_set raise / Protocol 缺属性 isinstance fail | §1.2 ① Goal |
| `tests/test_action_invariants.py` | §4.2 输入侧 | 3 条反例:tool_id 空 raise / frozen 改写 raise / __no_tool_history__ 标记; 1 条已知 OPEN 记录(args dict 可变,不假装不可变) | §1.2 ② Authority |
| `tests/test_context_invariants.py` | §4.3 | 5 条反例:frozen 改写 raise / __no_tool_history__ 标记 / history 无 tool 字段 / artifacts tuple 不可 append / CwdMeta 缺属性 isinstance fail | §1.2 ② Authority |
| `tests/test_context_judge_invariants.py` | §4.2.1 | 8 条反例:tool_id 不匹配 / args 缺 key / context 属性不匹配 / Rule frozen / core_deny 含非 BLACKLIST raise / user_allow 不能覆盖 core_deny / 无匹配默认 greylist(不默认 whitelist) / Judgement frozen | §1.2 ② Authority |
| `tests/test_confirm_gate_invariants.py` | §4.5 / §6.4 | 10 条反例:whitelist 不等 user / greylist timeout 不当 reject / MODIFY 缺 action raise / blacklist 不直接 execute / OVERRIDE 缺 text raise / TERMINAL 不能再 process / 非 PENDING 无 response raise / OverrideEvent frozen / EquivalenceRequest frozen | §1.2 ② Authority |
| `tests/test_judge_invariants.py` | §5.2-5.4 | 9 条反例:CaveatType 只 2 种 / JudgeVerdict frozen / Evidence frozen / Evidence 无 tool 历史 / test_results tuple 不可 append / Judge Protocol 缺 __call__ isinstance fail / base_judge 全 GoalType 覆盖 / 主 undecidable 辅 met 不可越级 / 主 not_met 辅 met 不能救 | §1.2 ③ Accountability |
| `tests/test_tool_invariants.py` | §4.2 工具层 | 5 条反例:ToolResult frozen / ToolResult 无 tool 历史 / Tool Protocol 缺 execute isinstance fail / 重复 tool_id raise / ToolRegistry frozen | §1.2 ② Authority (工具手段) |
| `tests/test_verifiability_invariants.py` | §6.1 + §6.5.2 | 6 条反例:TimelineEvent frozen / 不同 payload 不同 hash / 篡改 event verify_chain False / TrustAnchor Protocol 缺 write_run_tail fail / TrustAnchorInit frozen / AckEvent frozen | §1.2 ④ Verifiability |
| `tests/test_model_invariants.py` | PR-3 + §0 PR-0 | 5 条反例:StopReason 只 3 种 / ToolChoice 只 3 种 / ToolCall frozen / Message frozen / role=tool 缺 call_id raise / ModelAdapter Protocol 缺 complete fail | §1.2 (模型无关, 横贯) |
| `tests/test_loop_invariants.py` | §0 + §3.2.2 + §4.2.1 + §4.5 + §6.1 | 4 条反例:TOOL_USE 无 tool_calls=幻觉报错 / 未注册 tool_id 报错 / RunEgress frozen / 跑完链完整; 4 条正向 hello-world:tool→stop / immediate stop / judge met / judge undecidable | §1.2 (全 4 维编排) |
| `tests/test_loop_observer_invariants.py` | §6.1 (呈现层投影) | 3 条反例:observer 抛异常不改 RunEgress / observer 抛异常不断链 / LoopEvent frozen 改写 raise; 2 条正向:有序事件 / step 单调 | §1.2 ④ Verifiability |
| `tests/test_loop_stream_invariants.py` | §6.1 (流式投影) | 3 条反例:stream=False 无 model_token / model_token 不进 RunRecorder / 无 complete_stream 降级; 2 条正向:流式≡阻塞 egress / 流式≡阻塞 timeline | §1.2 ④ Verifiability |
| `tests/test_loop_step_invariants.py` | §6.1 (对话接缝) | 3 条反例:run() 重构后幻觉仍捕获 / terminal 时 egress 非空 / 对话不调 judge; 4 条正向:run 行为不变 / step STOP→awaiting / step TOOL_USE→tool_used / 对话多轮 messages 增长 | §1.2 ④ Verifiability |
| `tests/test_timeline_completeness.py` | §6.1+§6.2 (timeline 完整性) | 3 条反例:加完整数据后摘要字段仍在 / tool_call_end 摘要仍在 / (缺失); 2 条正向:model_call 完整 content+tool_calls / tool_call_end 完整 output | §1.2 ④ Verifiability |
| `tests/test_replay_invariants.py` | §6.2 (replay) | 3 条反例:ReplayAdapter 不调 httpx / ReplayTool 不 open 文件 / step 不一致检测 DIVERGENT; 4 条正向:按序返回 / 用完 STOP / parse_timeline / 复现等价 | §1.2 ④ Verifiability |
| `tests/test_streaming_invariants.py` | PR-3 + §0 (adapter 流式) | 4 条反例:丢 arguments 分片可检测 / finish_reason 不硬编码 / 无 tool_calls / length 映射; 4 条正向:文本拼接 / 多 tool_calls / 混合 / 等价 | §1.2 (模型无关, 横贯) |
| `tests/test_grep_invariants.py` | §4.2 工具层 | 4 条反例:空 pattern raise / 路径不存在 raise / 无匹配不是 error / (截断); 3 条正向:匹配 / ignore_case / fixed | §1.2 ② Authority (工具手段) |
| `tests/test_glob_invariants.py` | §4.2 工具层 | 4 条反例:空 pattern raise / 路径不存在 raise / 文件非目录 raise / 无匹配不是 error; 2 条正向:递归匹配 / schema | §1.2 ② Authority (工具手段) |
| `tests/test_list_dir_invariants.py` | §4.2 工具层 | 4 条反例:空 path raise / 路径不存在 raise / 文件非目录 raise / 噪声目录须跳过; 3 条正向:树状 / depth 限 / schema | §1.2 ② Authority (工具手段) |
| `tests/test_read_image_invariants.py` | §4.2 工具扩展 | 10 条反例:空 path fail / 缺失 path fail / 文件不存在 / 路径是目录 / 不支持格式 / 非图片文件 / 超过 10MB 拒绝 / 构造后改 success raise / 失败时 output 非空; 9 条正向: PNG 读取 / JPG 读取 / 元数据完整 / MIME 正确 / 输出可读 | §1.2 ② Authority (工具手段) |
| `tests/test_bash_invariants.py` | §4.2 工具层 | 9 条反例:空命令 fail / 缺失命令 fail / 自保护阻断(BLOCKED) / kill 自进程阻断 / rm -rf/ 阻断 / frozen raise / 失败 output 非空 / 不存在命令 fail / truncated 标志; 6 条正向:echo 执行 / exit 0 / exit 1 / stdout 捕获 / artifacts exit_code / artifacts duration | §1.2 ② Authority (工具手段) |
| `tests/test_edit_file_invariants.py` | §4.2 工具层 | 9 条反例:空 path / 缺失 path / 文件不存在 / 空 old_string / old_string 不匹配 / 多处匹配列出位置 / frozen raise / 失败 output 非空 / 目录替换文件; 5 条正向:单处替换 / 文件实际更新 / artifacts path / artifacts old/new_lines / 多行替换 | §1.2 ② Authority (工具手段) |
| `tests/test_web_fetch_invariants.py` | §4.2 工具扩展 | 10 条反例:空 URL / 缺失 URL / ftp 拒绝 / file:// 拒绝 / 无效 URL / 无法连接 / frozen raise / 失败 output 非空 / max_chars 截断 / 不存在域名; 4 条正向:example.com 抓取 / artifacts url / artifacts title / artifacts chars | §1.2 ② Authority (工具手段) |
| `tests/test_checkpoint_invariants.py` | §4.2 工具扩展 | 8 条反例:无文件无标签返回 None / 恢复不存在 ID 返回 False / 删除不存在 ID 返回 False / 删除后恢复返回 False / clear_all 后恢复返回 False / clear_all 返回计数 / 恢复不影响其他文件; 10 条正向:保存返回 entry / 保存恢复文件一致 / 多文件恢复 / 链式 prev_id / chain_ids 排序 / get_latest / get_checkpoint / meta.json 写入 / list 排序 / label+tool_id+run_id 存入 | §1.2 ② Authority (工具手段) |
| `tests/test_refiner_invariants.py` | §3.3 (minimal) + §3.5 + §5.2 | 16 条:R1 added_intent 必空(两层) / R2 questions≤budget / confidence 范围 / 分类命中 BUGFIX·FEATURE·REFACTOR / UNKNOWN 分类 + 0.5 置信 / system 强制 BUGFIX+0.9 / §5.2 驱动 exposed(令→(),user→None) / 切分可回指 / fallback 不崩 / 不 Decline | §1.2 ① Goal |
| `tests/test_project_memory_invariants.py` | §9.4 (项目记忆注入) | 5 条:AGENTS.md 存在则读 / 缺失返回 None / 读取异常静默(反例) / system prompt 注入 PROJECT MEMORY / 缺失不注入仍正常 | §1.2 ② Authority (项目级) |

## 元规则验定测试

| 测试文件 | DESIGN.md 节 | 反例摘要 |
|---|---|---|
| `tests/test_ipr3_lint.py` | IMPL.md IPR-3 | `core/` 出现 SDK import → fail (test 自身调 `scripts/check_ipr3.py`) |
| `tests/test_metrics_r_metric.py` | §2.0 R-Metric A/B/C | 任何 metric 无反指标配对 / 上溯不到 §1.2 → fail |

## 规范层 invariant 测试 (AAS, 对应 docs/spec/AGENT_ALIGNMENT_SPEC.md)

与上表不同:此子表测的是 **AAS 规范自身**, 不上溯 DESIGN.md 而上溯 AAS §B/§E。
故意独立: 任何第三方 agent 可声称遵守 AAS 而不采纳 zall 任何内部形态 (AAS §G),
故 `tests/spec/` 严禁 `import zall.core.*`。

| 测试文件 | AAS 节 | 反例摘要 | mutation 自检 |
|---|---|---|---|
| `tests/spec/test_aas_e_falsifiability.py::TestB12ClaimMetFalsifiable` | §B1.2 / §E.2 | 2 反例: claim=met 但 judge=undecidable / claim=met 但 judge=not_met → 必报 B1.2; 2 正向: claim/judge 一致 / claim=met_with_caveat 不误触发 B1.2 | ✅ 破坏 detector B1.2 条件 → 2 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB32AuxEscalationFalsifiable` | §B3.2 / §E.2 | 1 反例: main=undecidable 后 aux=met → 必报 B3.2; 3 正向边界: aux=not_met 不算越级 / aux 在 main 之前给 met 不算 / main 重判 met 关闭观察窗 | ✅ 破坏 `_aux_terminations_after_main_undecidable` append → 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB31NoMainJudgeFalsifiable` | §B3.1 / §E.2 | 2 反例: timeline 全无 judge_result / 仅 aux 无 main → 必报 B3.1 | ✅ 破坏 `if real_main is None` → 2 反例 fail |
| `tests/spec/test_aas_e_falsifiability.py::TestB41ChainBrokenFalsifiable` | §B4.1 / §E.2 | 2 反例: 首条 prev_hash≠GENESIS / 非首条 prev_hash 空 → 必报 B4.1; 1 正向: GENESIS+非空链不误报 | ✅ 让 `_broken_chain_event_ids` 早返回空 → 2 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB43NonReproducibleSilentFalsifiable` | §B4.3 / §E.2 | 1 反例: timeline 断链 + cause=None → 必报 B4.3 静默违规; 2 正向: 断链+cause 显式标注不报 B4.3 (但 B4.1 仍报) / 干净 run 不报 | ✅ 破坏 `if _has_chain_broken...` 条件 → 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB13GoalMutationFalsifiable` | §B1.3 / §E.2 | 1 反例: 首条 goal_statement intent=A, 后续 intent=B → 必报 B1.3; 3 正向: 单条 / 重复同 intent / 无 goal_statement 不报 (B1.3 是不可变性非存在性) | ✅ 让 `_goal_mutations` 早返回空 → 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB21DenyByDefaultFalsifiable` | §B2.1 / §E.2 | 1 反例: gate_decision(allow, unmatched_default) → 必报 B2.1; 3 正向: explicit_whitelist+allow / unmatched+greylist (正确默认) / deny 不触发 B2.1 | ✅ 让 `_deny_by_default_allows` 早返回空 → 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestB22BlacklistDirectExecFalsifiable` | §B2.2 / §E.2 | 1 反例: gate_decision(deny,A)→tool_call_end(A) 无 override → 必报 B2.2; 3 正向: deny→override→execute / deny→不同 action_id 的等价替换 / allow→execute 不触发 B2.2 | ✅ 让 `_blacklist_direct_executions` 早返回空 → 反例 fail, 正向不误报 |
| `tests/spec/test_aas_e_falsifiability.py::TestS93NoToolWithoutLockedGoal` | DESIGN.md §9.3 / §E.2 | 3 反例: tool_call 前无 goal / goal 提议未确认就调工具 / confirm 指向非 goal 事件 → 必报 S9.3; 3 正向: 确认后调工具 / 一次确认多次调工具 / 无 tool_call 不报 | ✅ 让 `_tool_calls_without_locked_goal` 早返回空 → 3 反例 fail, 正向不误报 |

**注 1**:`mutation 自检`列记录已对该 invariant 跑过 mutation test
(破坏 detector → 反例测试必须 fail, 正向不误报)。九条均已跑 (v0.1-draft-impl)。

**注 2**:§E.3 / §E.4 / §E.1 / §E.5 故意 *不* 在 `tests/spec/` 占位 —
这四条在规范层不可机械检测 (见 `test_aas_e_falsifiability.py` 末尾注释)。
按 IPR-0 "占位不构成 invariant" 的纪律, 空白才是合规姿态。

**注 3**:机械 *不可* 测的断裂 (prev_hash 与前一 event 实际哈希不一致)
故意不在 detector 内 — 那需规范规定哈希函数, AAS v0.1 不规定 (§F OPEN)。
若假装能测即违反 §E.2 "不可证伪即自塌"。

## 反例库(IPR-0 的实质内容)

每个 primitive 的测试必须含至少一个**反例** —— 不是 happy path,
而是构造一种**违规实现让该测试理应失败**的场景:
- 例:`test_goal_triple_immutability` 必须断言
  "Refiner 试图把新 intent 加进 `translation_of`"时,
  GoalTriple 不允许这一操作 → 触发 R1 (§3.3 翻译禁加戏)。
- 如果一个测试只能 happy path,**它不构成 invariant**,违反 IPR-0。

### 已落证据(实施层 PR-0 第 1 次实证, v0.0.8-impl)
`scripts/check_ipr3.py` 最早版用字符串子串匹配,但 docstring 中出现 "import openai"
字样会被误判为违禁 —— 在 PR-0 不豁免占位脚本的方针下,
v0.0.8-impl 已自纠为 AST 解。这条自纠被记录为
**实施层 PR-0 第 1 次实证占位脚本也要红蓝对抗**。
