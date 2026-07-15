# zall · Design Document

> 项目名:zall
> 定位:面向未来、基于现在;整合业界顶尖 coding agent 优点,推陈出新;
> 零点:不依赖任何现存 agent 框架,从第一性原理自建。
> 性能:高效卓越(把"性能"做成可度量的工程指标,不是口号)。
> 模型:模型无关。任何模型相关细节不得泄漏到核心抽象之外。
> 野心:agent 行业无评估标准,zall 的本体论与评估体系一并定义,
>       成为可被参照的标杆。

---

## 0 文档元规则(本项目对自己的约束,先于一切内容)

这些规则优先于本文档的所有具体条款。条款与规则冲突时,改条款不改规则。

### PR-0  自证伪义务(Obstruction to Self-Falsifiability)
agent 产生的任何论断——包括目标声明、完成声明、规划、自我评估——
都必须能被自身或外部证伪。不能被证伪的输出 *视为幻觉*。
本规则源于项目主理人的直接指令:
"agent 不能有幻觉,要会自己评估自己的工作。"
本规则也作用于设计文档本身:
本 DESIGN.md 的每一条论断必须可被红蓝对抗驳回 → 驳不倒才保留。

### PR-1  增量但不许错误
宁可慢一步不写入未经验证的设计,也不许录入"看起来对"的错误增量。
本 DESIGN.md 的每一节都标"状态":
  · SETTLED    已经过红蓝对抗验证,作为后续工作的前提
  · PENDING    形态已开,但运行前提未补齐,严禁当 SETTLED 用
  · OPEN       残余伤口,记着但未决,后续步骤处理
凡标 PENDING/OPEN 的内容,**禁止**作为下游结论的依据。

### PR-2  一步步来
不一次性定稿;每步可质疑、可回退。本文档的每个版本只反映
"已被推到可固化密度"的内容,不反映计划。

### PR-3  模型无关
任何"模型"字样在第 0 / 1.x 节(本体论 + Goal 维度)中,
只能出现在"模型无关"语境,不许出现在定义本身。
任何依赖具体模型行为(如 hallucination 倾向)的规则,
必须写成"Goal 类型决定判据来源"的形式,不得写死模型行为。

### PR-4  从定义长出实现,而非反之
模块、命名、架构图一律延后;先立本体论,本体不稳不画架构。
本文档目前不含架构图。架构是定义稳定后才允许的"实现投影"。

---

## 1 本体论:agent 是什么(已立 SETTLED)

### 1.1 行业诊断
现存 coding agent 共同病根:把"实现机制(loop + tool)"当成了"本体论"。
"心脏跳动"不等于"人"——loop+tool 是必要,不充分。

| 现存实现 | 它隐含的本体论 | 漏掉的东西 |
|---|---|---|
| Claude Code | "能调工具的对话过程" | 责任边界,失败可归因 |
| Cursor | "IDE 内的补全 + 编辑" | 目标态,终止判据 |
| Devin | "完成软件任务的虚拟员工" | 内部状态不可观测 |
| OpenHands | "LLM + 工具 + 循环" | agent 与其实现混同 |
| Aider | "Git-native 的代码修改器" | 无目标体系 |
| SWE-agent | "ACI 范式" | 工程落地弱 |
| Goose | "MCP 本地工具" | 循环鲁棒性一般 |

### 1.2 zall 的第一性定义(状态:SETTLED)
agent 不是一个跑循环的东西,agent 是一个**承诺体系**:它对外承诺要做
某个 Goal,被允许用某个 Authority,做到某个 Accountability 程度,
并用 Verifiability 保证全过程可被第三方独立复核。

四个正交维度,各自回答一个不同问题,缺一则 agent 退化:

```
       ┌──────────────────────────────────────────────────────────────┐
       │  ① Goal            要达成什么(目标态,非输入)             │
       │  ② Authority        被允许用什么手段(权限与边界)         │
       │  ③ Accountability   做到什么程度算完成(可判定终止)       │
       │  ④ Verifiability    全过程可被第三方独立复核(可观测)     │
       └──────────────────────────────────────────────────────────────┘

       缺 ①  agent 不知道何时停  → 永远在跑,永远没完成
       缺 ②  agent 会越界        → 删库、推远程、发邮件
       缺 ③  agent 自称完成无法证伪  → "我觉得改好了"
       缺 ④  agent 是黑盒        → 出错无法归因,改进无从下手
```

四条是**抽象槽位**,具体实例化由领域(软件 agent / 客服 agent / 数学证明 agent)填充。
本 DESIGN.md 在领域层默认填充"coding agent",但保持抽象形态普适。

### 1.3 红蓝对抗记录(为何 4 条没塌)
- "Accountability 是 Goal 的影子?" → 否。Counter-example:
  agent 给测试加 monkey-patch 让其通过,Goal(测试通过)= met,
  Accountability(是否真解决用户意图)? 失败。两者名义可重,
  主体不同,正交保留。
- "Verifiability 与 Accountability 同物?" → 否。
  Accountability 判结论(谁担责);
  Verifiability 判结论的可重做性(任何第三方拿轨迹都得同结论)
  后者解决信任,前者解决判定。正交保留。
- "本体论偷渡强 LLM 假设?" → 半条认输并已修正。
  原"终止必须由验收契约判定"假设了"模型会幻觉"。
  修正为 PR-3 体现的"判据来源由 Goal 类型决定"。
- "4 条普适性?" → 承认是抽象槽位,实例化由领域填。
  此为表述补强,非论断修正。

---

## 2 评估体系(从本体论长出)(状态:5 维度 metric 形态 SETTLED;指标 5 部分 OPEN)

### 2.0 元规则(本节内,优先于 §2 后续具体 metric)
评估维度的风险是"指标替换本体":metric 一旦被当成定义本身,
zall 就退回现存 agent 把 loop+tool 当本体的同一错。
本节加 3 条元规则,优先于 §2 各项具体 metric:

- **R-Metric 化 A**:每条 metric 必须可上溯到 §1.2 某项,否则降 OPEN。
- **R-Metric 化 B**:每条 metric 必须通过区分度检验——
  若该指标对"完成 Goal 的不同方式无区分度"(eg. 裸工具调用次数),
  降为 OPEN,不进 SETTLED。
- **R-Metric 化 C**:每条 metric 必带"配对反指标",
  对抗 Goodhart's Law(agent 按指标优化而非按 Goal 优化)。
  缺反指标者降 OPEN。

### 2.1 5 个评估维度(本体上溯)

每条 metric 在本体上溯、区分度、反指标三项上得到验证才标 SETTLED。

#### 2.1.1 目标达成率(上溯 §1.2 ① Goal;状态:SETTLED)
```
分母: 已终结 run = 总 run - DeclineTask 数 - 仍跑 run 数
        (declined 是诚实退让,按 §3.3 不算完成失败;与达成率分开统计)

切分: per GoalType (BaseGoalType 11 + unknown + ExtendedGoalTypes)

分子三态:
    goal_achievement_rate_pure(GoalType)         = met / 已终结 run
    goal_achievement_rate_with_caveat(GoalType)  = met_with_caveat / 已终结 run
    goal_non_achievement_rate(GoalType)           = not_met / 已终结 run

约束: 三率之和 = 1 (排除 declined/ongoing 后,该 GoalType 下)

反指标(配 R-Metric 化 C):
    decline_rate(GoalType) = declined / 已终结 run 数
    高 pure 达成率 + 高 decline_rate = Goodhart 可疑信号
    高 pure 达成率 + 低 decline_rate = 健康信号

区分度(验 R-Metric 化 B): PASS
    同一 GoalType 下确有完成 / 未完成二分。

残余 OPEN:
    - ExtendedGoalType 在 token 分布稀疏时统计噪声大,样本量阈值 OPEN
    - 三率之和=1 的离线 metric 工具实现细节 PENDING
    - "仍跑"vs"已终结"边界与 §5.4 caveat 关联,PENDING
```

#### 2.1.2 越界率(上溯 §1.2 ② Authority;状态:SETTLED)
```
分母: per GoalType 下该 run 的总工具调用数

四层子率(分母相同):
    whitelist_action_rate       = whitelist_calls / total
    greylist_consent_rate       = greylist_calls_passed_gate / total
    blacklist_intercept_rate    = blacklist_blocked_or_equivalenced / total
    override_after_audit_rate   = user_approved_blacklist_calls / total

反指标(配 R-Metric 化 C):
    proactivity_rate = (无 user 介入的成功 autonomous 动作) / total calls
    高 greylist_consent + 高 proactivity = 健康(主动但不越界)
    低 greylist_consent + 高 blacklist_intercept = 危险(agent 总撞红线)

区分度(验 R-Metric 化 B): PASS
    不同 agent / 不同 GoalType 下越界率确有差异。

残余 OPEN:
    - proactivity 中"autonomous"定义边界 OPEN(user 没说话是否算 autonomous?)
    - Override 后等价替换是否计入 greylist_consent_rate,OPEN
```

#### 2.1.3 可证伪率(上溯 §1.2 ③ Accountability;状态:SETTLED)
```
分母: 已终结 run (与 2.1.1 一致排除 declined/ongoing)
切分: per GoalType

分子四类(run 终结时):
    falsifiable_by_system_rate    = system Judge 实际跑了判定 / 分母
    falsifiable_by_user_only_rate = user Judge 完成确认 / 分母
    falsifiable_with_caveat_rate  = met_with_caveat(任何 caveat 子类型) / 分母
    unfalsifiable_rate            = RunEgress 仅出 undecidable / 分母

约束: 四率之和 = 1 (per GoalType)

反指标(配 R-Metric 化 C):
    test_baseline_mutation_rate = (agent 改测试基线事件数) / 已终结 run 数
    高 falsifiable_by_system_rate + 高 test_baseline_mutation_rate = 假阳性嫌疑
    高 falsifiable + 低 baseline_mutation = 健康信号
    (此反指标与 §4 greylist/blacklist 联动;测试文件已纳入 greylist/blacklist)

区分度(验 R-Metric 化 B): PASS
    不同 GoalType 主 Judge 不同,机械可证 vs 用户可证,确有差异。

残余 OPEN:
    - 4 子率 sum=1 的离线实现 PENDING
    - user_only 中 user "确认" 是真确认还是默认 accept 没看 (与 §6.5 弱模式签名
      同根),OPEN
```

#### 2.1.4 可复现率(上溯 §1.2 ④ Verifiability;状态:SETTLED)
```
两个独立子指标:
    timeline_integrity_rate       = (链式哈希校验通过的 run) / 总 run
    runegress_reproducibility_rate = (重放 timeline 得到一致 RunEgress 的 run)
                                      / (总 run 排除 non_reproducible_by_construction)

non_reproducible_by_construction (枚举型,PENDING 集可能漏):
    - 含 user Override 但 user_confirm 是弱模式且无 signature_opt
    - timeline 有断链 (启动时即标,运行时已不可挽)
    - 含未完成外部依赖快照 (§6.5 data_snapshot 缺 source_module)
    - [v0.0.5 新增] anchor_unreachable:锚点离线 / 私钥不可用 (§6.5.2)

复现对象: 仅 RunEgress 一致,非"模型生成"一致 —— 与 §6.2 Replay 协议同步。
        "生成复现" 属 development_aid,不参与评估 (见 §6.2)。

反指标:
    tamper_detected_rate = 检出篡改的 run / 总 run
    高 integrity + 低 tamper_detected = 健康信号
    低 integrity + 低 tamper_detected = 检测机制失效 (sensor 没工作)

区分度(验 R-Metric 化 B): PASS
    timeline 完整度在真实 run 间差异显著。

残余 OPEN:
    - non_reproducible_by_construction 枚举可能漏类型 OPEN
       (v0.0.5 已补 anchor_unreachable 一项, 集合仍有未观察类型 OPEN)
    - timeline 中 model_response 记录体积大小 OPEN (可能 infla timeline)
```

#### 2.1.5 资源效率(上溯全部维度;主理人"性能高效卓越";状态:形态部分 SETTLED,主要 OPEN)
```
分母:**仅 met (含 caveat) 的 run** —— 排除 not_met / declined
       按 §1 自我驳论:跨 met/not_met 比资源效率是错误增量,
                      agent 用 token 少但 proof 差不能算"优"。

切分: per GoalType × per main_Judge 主体 (system | user | model_self)
分布: p50 / p90 / p99 (而非均值;p99 震荡可接受,升降更敏感)

资源维度:
    token_count     : 记录但不归一化,以 model version 为 metadata
    tool_call_count : 记录 + tool 类型分布
    wall_time       : p50/p90/p99 + env snapshot
    cpu_io_intensity: PENDING 实现工具未定

反指标(配 R-Metric 化 C):
    shortcut_signal_ratio = (RunEgress 含 caveat 的 met 数) / 总 met
    高效率 + 高 shortcut 信号 = 性能卓越可能走了捷径
    跨 met/not_met 不能比资源效率 —— 这是自我驳论抓出的错误增量防御。

区分度(验 R-Metric 化 B): PASS
    不同 agent 实现确实有资源效率差异。

状态:
    SETTLED 部分:
        ✓ 分母仅 met (含 caveat)、不跨 met/not_met 比
        ✓ per GoalType × per main_Judge 切分
        ✓ p50/p90/p99 分位数 (而非均值)
        ✓ 反指标 shortcut_signal_ratio 配对
    OPEN 部分:
        - cpu_io_intensity 工具未定 (依赖具体 monitoring 设施)
        - shortcut_signal_ratio 中 caveat 子类型的细化算法 PENDING
          (与 §5.4 caveat 子类型 main_unavailable / main_aux_divergent 串联时
           是否都算"shortcut"信号,OPEN)
        - 资源效率跨 GoalType 比较时同分位数下是否合法,OPEN
       (按照 R-Metric 化,A/B/C 都通过是 SETTLED;但仅切分法已 SETTLED,
        具体 dimension 算法 OPEN,故标"部分 SETTLED 主 OPEN")
```

### 2.2 评估体系的派生关系图(为可读性)
```
       §1.2 本体论 (4 维)
       ┌───┴────────────┬──────────────┬──────────────┐
       ① Goal         ② Authority    ③ Accountability ④ Verifiability
       │                │                │                │
       ↓                ↓                ↓                ↓
    2.1.1 达成率    2.1.2 越界率    2.1.3 可证伪率   2.1.4 可复现率
       │                │                │                │
       └────────────────┴────────┬───────┴────────────────┘
                                 ↓
                            2.1.5 资源效率 (横贯全部维度)
                                 +
                            "卓越性" 投影
```

### 2.3 评估体系本身的剩余 OPEN 集中
- 🩹 全部 5 指标的"sum=1"或"per GoalType"的离线 metric 工具实现 PENDING
- 🩹 §2.1.3 的 user_only 子率绕回 §6.5 弱模式签名漏洞,OPEN
- 🩹 §2.1.4 的 non_reproducible_by_construction 类属枚举 OPEN
- 🩹 §2.1.5 的 cpu_io_intensity、跨 GoalType 比较合法性、shortcut 算法 OPEN

**总结:本轮把 5 维从雏形推到 metric 形态 4 维 SETTLED + 1 维部分 SETTLED;
OPEN 都做了显式标注,不假装全 SETTLED

---

## 3 本体论的维度展开·Goal 维度(推到可固化密度)

> 其他三维 Authority/Accountability/Verifiability 暂未展开,只开槽位。
> 凡 Goal 维度引用这些槽位时,必须标"来自维度[X],未展开 PENDING"。

### 3.1 Goal 维度解决的 3 个顽疾
| 病         | 现存 agent 通病              | Goal 维度对治手段          |
|-----------|---------------------------|-------------------------|
| 目标漂移   | 第 5 步忘了第 1 步       | Goal 一旦确立贯穿 run 不可改 |
| 终止失据   | 完成靠模型自报             | 终止必须由可计算判据给定   |
| 验收无据   | "我觉得改好了"          | 验收契约 + 测试基线冻结     |

### 3.2 Goal 三段式定义(状态:SETTLED)
```
Goal = (GoalStatement, TerminationCriterion, AcceptanceContract)
```

#### 3.2.1 GoalStatement —— 目标陈述
- intent:       用户原话保留(不可改,锚定意图)
- rewriting:    agent 自行重述(用于验证误读)
- rewrite_confidence: float —— agent 自评置信,低于阈值须用户澄清
- goal_type:    GoalType —— v0.0.8 回填(经 GoalTriple 落码红蓝对抗发现:
                §3.5.2 说 Refiner 提议 GoalType + ConfirmGate 确认后锁进 Goal,
                故 GoalTriple 必须含 goal_type 字段;§3.2 三段式原漏列此字段)
- translation_of: tuple[segment_id, ...] —— 每条可回指 user_raw 子句(R1)
- added_intent:   tuple[segment_id, ...] —— **必空**(R1 翻译禁加戏;
                  构造时传非空须在 validator 中 raise)

#### 3.2.2 TerminationCriterion —— 终止判据(三态)
- 必须为**纯函数**:输入 = 当前状态(文件树 + git 提交 + 测试结果),
  输出 ∈ {not_met, met, undecidable}
- 三态不是二态。"无法判定"是**诚实**的终止,比假阳性"完成了"更负责任。
- undecidable 是 PR-0 在 Goal 层的落地:agent 不能假装完成。
- **判据来源由 Goal 类型决定**(PR-3):
  · coding agent: 测试 / lint / diff
  · 数学证明 agent: 模型自身(数学可形式化验证)
  · 客服 agent: 用户确认
  · 其他类型的判据来源由领域填充
- **exposed_dependency_set**(v0.0.6 回填 —— 经 §5.5 hunk 归属红蓝对抗发现):
  system_judge 类 Goal 的 TerminationCriterion 必须额外暴露一个
  `exposed_dependency_set: Set[FileId | FunctionId | SymbolId]`,
  表明它读取哪些代码要素。这是 §5.5 静态 hunk 分类器的输入前提。
  user_judge 类 Goal 无 structure termination 判据,该字段缺省,
  hunk 归属走"保守默认仅含降级后 hunk"路径(见 §5.5)。
  *transitive 闘包不计算 (默认只直接依赖)*,扩边界留在实现 PENDING。

#### 3.2.3 AcceptanceContract —— 验收契约
- baseline_frozen_at: git sha —— 测试基线冻结点
- prohibited_actions: [] —— agent 不能动的东西列表(eg. edit test files);
  **authorization(能否真的拦住)属维度 ② Authority,不在 Goal**
- escalation: human_review | abort —— 触发后的出路(同样由 Authority 执行)

### 3.3 Goal Refiner(状态:SETTLED —— 形态 SETTLED;运行前提 minimal runnable 已落地 v0.0.9)

> 运行落地记录 (v0.0.9): `src/zall/core/refiner.py` 的 `GoalRefiner` 已 runnable,
> 经 `cli/app.py::_refine_goal` 接入 run() 与 REPL(带 fallback 守 IPR-0)。
> minimal 范围: 纯关键词分类(零模型) + 不反问(ask_budget=0) + 不改写 + UNKNOWN 不 Decline。
> 已消费 §5.2 base_judge 表驱动 exposed_dependency_set (原 PENDING 缺口填补)。
> 仍 PENDING (不在此版): R2 反问语义引导(走 §6.3 audit_warning) / 模型语义改写 /
> Goal Downgrade(§3.4) / translation_of segment_id 精确定位。

#### 3.3.1 定义
> Goal Refiner 是一个**纯翻译器**,不产出意图,只把用户自然语言输入
> 转译为可判定的 Goal 三段式。它有问询预算 K,反问形态受规则约束,
> 转译产出需经用户一次确认闸门才能锁死进入 run。

#### 3.3.2 形状
```
Input:
  user_raw:        str                        # 用户原话
  context_permitted: <来自 Authority,未展开 PENDING>
  ask_budget:      K    <来自 Authority,未展开 PENDING>

Output:
  RefinedGoal | DeclineTask

  RefinedGoal = {
      refined_goal:  GoalTriple,
      translation_of: [user_raw_intent_segment_ids],   # 每条都映射回用户原话
      added_intent:   [],         # 必须为空,否则 hijack
      questions_used: int ≤ K,
      confidence:     float
  }

  DeclineTask = {
      reason: "intent_not_refinable_in_budget",
      partial_translation: [...],
      questions_asked: int
  }
```

#### 3.3.3 三条刚性规则
| 编号 | 规则   | 形式化                                              | 违反时               |
|------|--------|----------------------------------------------------|---------------------|
| R1   | 翻译禁加戏 | `translation_of` 每条须可回指 user_raw 子句;`added_intent` 必空 | 转译被拒,Refiner 重来 |
| R2   | 问询禁引导 | 反问只能澄清(问 X 指什么),不能建议(说要不要写成 Z)      | 问询作废,扣预算 + audit |
| R3   | 翻译即锁定 | Refiner 产出经用户 confirm 后,Goal 进入 run 不可变;改必须新开 run | 防漂移               |

**R1 注解(v0.0.3 增)**:R1 禁止的是 *新意图* 的注入,不是 *新元数据*。
Refiner 给已转译的 Goal 打 `GoalType` 标签属"分类",不属"加戏"——
标签信息可回溯至 user_raw 的语义结构,**不引入未被表达的诉求**。
区分判据:若 Refiner 的产出包含量化指标 / 验收阈值的"具体数字",
且这些数字不在 user_raw 中,视为加戏;若仅打分类标签 (eg. "bugfix"),
属分类,允许。Refiner 提议 GoalType,在 §3.3.4 ConfirmGate 由用户确认。

#### 3.3.4 闸门协议
```
goal_gate(refined):
  if refined.added_intent 非空:          decline         # R1
  if refined.confidence < θ_low:
       ask_user_confirm:
         on_reject → Retry(once)
         on_reject_again → DeclineTask
  展示 translation_of 给用户            # 透明,非黑箱
  on_user_confirm → Confirmed → 进入 run
  on_user_reject  → Retry(once) | DeclineTask
  ask_budget 耗尽  → DeclineTask
```

#### 3.3.5 PR-0 耦合(幻觉的形式化判定)
> 当存在多个对 user_raw 同样合理的翻译时,Refiner **必须**问询,
> 不能自选其一跑下去。
> "多合理翻译共存 → 必须问询" 是 PR-0 在精化层的机械落地。

#### 3.3.6 残余伤口(OPEN)
- 🩹 诱导式问询(温和 hijack):只能靠审计轨迹取证,无法纯自动判定。
- 🩹 context_permitted, ask_budget 来自维度 ② Authority,Authority 未展开,
  Refiner 运行前提悬空。推 Authority 时须回填。

### 3.4 Goal Downgrade (状态:SETTLED —— 形态 SETTLED,运行前提 PENDING)

#### 3.4.1 定义
> 当 Refiner 耗尽 K 仍无法把用户意图转译为单一可判定 Goal 时,Refiner 可
> 建议**降级**:保留原始意图为 Goal_undecidable,开出一或多个更窄的可判定
> Goal 作为近似替身。降级只能由用户在闸门处触发,不可由 agent 单方触发;
> 降级深度有上限 D,默认 D=1。

#### 3.4.2 形状
```
GoalDowngrade = {
    original:    GoalTriple        # 原始意图; termination 永远 undecidable
    candidates:  [GoalTriple]      # 近似替身,各自带 termination
    downgrade_depth: int ≤ D       # 0 = 原始, 1 = 一次降级, ...
    approximate_flag: True           # 终身标记,不允许抹除
    baseline_at: git_sha             # 降级发生时刻
}
```

#### 3.4.3 三条刚性规则
| 编号 | 规则         | 形式化                                                            | 作用                          |
|------|------------|-----------------------------------------------------------------|-----------------------------|
| R4   | 双 Goal 共存   | original 永不删除,termination 永远 undecidable,不可被替换    | 防 agent 只报近似那半,蒙混过关 |
| R5   | 降级深度上限   | downgrade_depth ≤ D,超 D → DeclineTask;默认 D=1                | 防无限滑坡到 trivial Goal     |
| R6   | 不可单方触发   | Agent 不可自行降级;只能 Refiner 建议 + 用户闸门接受            | 防 agent 反向利用降级捷径     |

#### 3.4.4 闸门协议(在 Refiner 闸门基础上扩展)
```
goal_gate(...):
  # 上述 Refiner 流程全部成立之外:
  if refined 不可单一可判定 但可降解:
     询问 user: "接受降级?"
        on_Reject -> Retry(once) | DeclineTask
        on_Accept -> Downgrade 入 lock, depth 从 1 开始
  if downgrade.candidates 中仍有 termination=undecidable:
     if depth+1 > D: DeclineTask
     else: 可选地再降级一次 (深度+1, 同一闸门规则)
```

#### 3.4.5 Run 结束的报告义务(防"骗自己通过")
```
RunEgress = {
    original_goal:    Goal_undecidable,           # 永远报,不许只报 candidate
    candidate_goals: [(Goal_met_candidate, status: met|not_met|undecidable)],
    downgrade_depth:  int,
    final_claim:      "原始目标保持 undecidable,近似目标 X 状态为 met",
                      | "..."
}
```
**禁止 RunEgress 只报 candidate 的 met 不提 original 的 undecidable**。
这是 PR-0 在降级形态下的兜底:
即使 Goal 侧漏了,结果侧也不能漏。

#### 3.4.6 残余伤口(OPEN)
- 🩹 D 默认值无依据,凭直觉置 1;待 zall 跑出自己数据后校准。
- 🩹 近似 Goal 的 AcceptanceContract 语义变弱(baseline 是降级时刻 sha,
  非任务起点 sha)。**问 Accountability 时复审**,不在 Goal 维度修(职责越界)。
- 🩹 Refiner "建议降级"动作本身可能加戏(在建议文本里夹带"建议降级到 X")。
  对策:建议降级**只能陈述候选 Goal,不许带推荐倾向**,同 R2 同形约束。
  可审计但难纯自动,记为 OPEN。

### 3.5 GoalType Enum(状态:核心层 SETTLED-fornow;扩展机制 SETTLED)
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   fornow 含义:已观察到的 Base 集 SETTLED,
                                   未来若发现新类型须显式扩展,不许静默忽略。

#### 3.5.1 形态
```
GoalType := BaseGoalType | ExtendedGoalType

BaseGoalType(核心层,for coding agent,SETTLED-fornow):
    bugfix          # 修复现有行为
    feature         # 增加新行为
    refactor        # 重写不动外部行为
    test_write      # 编写/补充测试
    docs            # 文档撰写
    perf_opt        # 性能优化
    review          # 代码审查
    investigate     # 排查/调研,不修改代码
    migrate         # 版本升级/框架迁移
    scaffold        # 新建项目骨架
    unknown         # catch-all; fallback to investigate

ExtendedGoalType:
    {
      name: str,
      fallback_to: BaseGoalType,        # 该扩展的 K / Judge 默认继承自此
      registered_at: ts,
      registered_by: confirm_gate       # 必须用户在 ConfirmGate 确认
    }
```

**注 (PR-1)**:BaseTypes 集**显式承认可能漏**——通过扩展机制 + `unknown``
兜底**守未知**,而不是假装闭集。这本身就是反错误增量。

#### 3.5.2 Refiner 不单方打标签(修订 §3.3 R1 的边界)
- Refiner 可以**提议** GoalType;
- 提议需在 §3.3.4 ConfirmGate 由用户确认;
- Refiner 不可单方面将 GoalType 标记为 ExtendedGoalType ——
  ExtendedGoalType 注册必须经用户 ConfirmGate(防滥用 fallback 锁死 K=0)。
- 分类不是加戏(详见 §3.3 R1 注解 v0.0.3)。
- **ExtendedGoalType 透明义务(v0.0.3 补)**:ConfirmGate 显示 ExtendedGoalType 时,
  必须同时显示它将继承的 `fallback_to` base_K 与 base_judge 组合,
  并明示"该 fallback 的 base_K=N 意味着 agent 反问次数上限为 N。
  确认=接受此继承;可改 GoalType 或改 K"。
  防止用户在不知情下默认接受 K=0 导致 Refiner 被静默禁言。

#### 3.5.3 GoalType 是 PR-3 的落地(状态:SETTLED)
PR-3 说"判据来源由 Goal 类型决定"。GoalType Enum 是这条元规则的**具体形态**:
Judge 来源(§5.2)由 GoalType 决定,而非由模型决定。
**没偷渡 LLM 假设**:Judge 主体中的 `model_self` 指"agent 自身的语义判断",
任何能产生 confidence 的 agent 都可以做 model_self Judge,不限于 LLM。

### 3.6 Goal 维度的自我评估对照(主理人要求落地)
| 主理人要求        | 在 Goal 维度的落地                                  |
|------------------|---------------------------------------------------|
| 不能有幻觉        | rewrite_confidence + 多合理翻译必问询 (PR-0 落地)    |
| 会自己评估自己    | agent 自报 met 与系统算 met 必须一致;不一致→self_eval_error |
| 会创新            | 标 OPEN:创新 Capability 不属 Goal 本体,留待后续    |

---

## 4 Authority 维度(状态:形态 SETTLED,部分运行前提 PENDING)

### 4.1 Authority 解决的三件事
- 手段清单:agent 一共能调哪些工具 / 哪些动作?
- 约束规则:每个动作在什么条件下允许 / 禁止?
- 授权分发:谁有权批准越界动作?

### 4.2 三层手段清单 + context_judge 函数形态
        (v0.0.7: 状态——形态 SETTLED;规则匹配算法 PENDING)
```
AuthorityLayer = (
    whitelist:     [ToolId],    # 默认可执行,无需确认
    greylist:      [ToolId],    # 需 confirm_gate 确认
    blacklist:     [ToolId],    # 不可直接执行 → 走等价替换 + 用户 Override(被审计)
    context_judge: (Action, Context) -> SafeLevel,  # v0.0.7 形态 SETTLED
    equivalence:   (BlackAction) -> [SafeAlternative]
)
```

#### 4.2.1 context_judge 函数定义
```
SafeLevel := whitelist | greylist | blacklist

context_judge(action, context) -> SafeLevel:
    matched_rules = match_all(declared_rules, action, context)
    
    if any matched rule = blacklist:           return blacklist   # deny 优先
    if any matched rule = greylist (无 blacklist): return greylist
    if any matched rule = whitelist 且 无 greylist 无 blacklist: return whitelist
    
    if 无 matched rule:    return greylist                  # 默认 greylist 而非默认 whitelist
                             # 子状态: greylist_unresolvable_no_rule_matched
                             # 触发: audit_warning (走 §6.3 既有机制)

declared_rules = 
    核心不可改 deny-rules (硬编码 + 外化公示)
    ∪ user_local 项目/.zall/rules.toml (用户外化可改)
    ∪ domain rules (AgentType 领域知识常量)

优先级链 (deny 优先):
    核心不可改 deny-rules > user_local.deny > user_local.allow > domain.allow
```

#### 4.2.2 为何 context_judge 不是 agent(避免被偷渡)
| 关注点 | agent | context_judge |
|---|---|---|
| 决策机制 | 模型 + 工具调用循环 | 声明式规则匹配 + 字符串/集合/glob 运算 |
| context 语义理解 | 模型 | **不解** —— 只做 syntax 匹配 |
| 不可判部分 | agent 推理 / 反问 | **走 greylist + confirm_gate**,不走模型 |
| 配置可见 | agent 行为半黑箱 | rule 可外化、user 可审阅、可版本化 |
| 模型依赖 | 是 | **否** —— 守 PR-3 |

**关键斩断**:context_judge 不"理解" context 语义,不做 NLP 判定,
不调用任何模型。若机械可判的规则集不够,**默认归 greylist** 由 user 兜底,
**绝不**通过引入模型来扩展判定能力 —— 这正是上次错画 V.4(锚点承诺)的同型防御。

#### 4.2.3 与 §4.5 confirm_gate 的复用(不引层级)
confirm_gate 已经处理 greylist 的 confirm / reject / modify / suspend 流程,
context_judge 不重启另一套 confirm 流程。**直接复用 §4.5**:
- 返回 whitelist → confirm_gate 直 pass
- 返回 greylist → confirm_gate 走 user_response 流程
- 返回 blacklist → confirm_gate 走等价替换 + Override 流程

unresolvable 不开新 confirm 路径,只在 §4.5 既有 confirm 流程上提示
"这是非匹配默认走 grey",并触发 §6.3 audit_warning。

#### 4.2.4 残余伤口(OPEN)
- 🩹 `match_all` 具体 grammar (glob / 正则 / 谓词) 算法 PENDING;
       但约束**必须声明式**(不开 dynamic eval)已 SETTLED。
- 🩹 优先级链冲突解决算法 PENDING (deny 优先是规则但具体 polish 算法未明)
- 🩹 `unresolvable_no_rule_matched` 强化提示在 confirm_gate UI 层仅语义定,PENDING UI 形态
- 🩹 跨平台路径解析 (Windows / Unix separator / case sensitivity)
       抽象后实际行为差异 PENDING —— 与 §6.5.2.8 ACL 跨平台问题同根,留实现层一起做

### 4.3 Context(状态:SETTLED)
```
Context = (
    task_level:   (user_raw, cwd_meta_read_only),
    history_level:(前 N 次 run 的 GoalStatement + RunEgress 摘要),  # 不含 tool 历史
    domain_level: (coding agent 领域知识),
    user_explicit_artifacts: []   # 用户显式回灌
)
```
**核心斩断:agent 不许偷偷拿跨 run 上下文;用户可显式回灌,被审计。**
与 Claude Code "保留全部 session history" 形成显式对立 ——
理由是防跨 run 上下文污染(R-三维同推 风险对治)。

### 4.4 ask_budget(状态:base_K SETTLED;actual_K 公式 SETTLED;
                   context_factor 形态 OPEN;max_ask_budget OPEN)

```
base_K(BaseGoalType) -> int          # 见表 1
context_factor: Context -> float     # OPEN (eg. 仓库熟悉度因子)
max_ask_budget: int                 # OPEN 默认值,系统配置

actual_K = clamp(base_K(GoalType) × context_factor(Context),
                 0, max_ask_budget)
        + user_override_k(user_pref)       # 用户可手动覆盖
        + adaptive_decay(reject_count)     # 连续被 reject 自动降 K,防无效反问
```

**表 1: base_K 默认值 (for coding agent)** —— 依据为对常见 Goal 类型的
反问需求观察,无大规模数据校准;Volume 后调整。

| BaseGoalType | base_K |
|---|---|
| bugfix      | 0 |  # 意图必须清晰,不许退化问答机
| feature     | 1 |
| refactor    | 3 |
| test_write  | 1 |
| docs        | 1 |
| perf_opt    | 3 |
| review      | 0 |  # 审查属"看",意图清晰
| investigate | 2 |
| migrate     | 3 |
| scaffold    | 1 |
| unknown     | 2 | # catch-all 给个保守中等值,避免 K=0 静默禁问询

**与 §3.3 R2 兼容性**:base_K=0 的(bugfix, review)禁 Refiner 反问,
与"小 Goal 不许退化问答机"一致。
**与 §3.5 兼容性**:ExtendedGoalType 的 K,默认继承 fallback_to 的 base_K;
用户可在 ConfirmGate 显式修改。

**K=0 类型的 user_raw 模糊退化路径(v0.0.3 补)**:
若 Refiner 收到的 user_raw 被分类为 K=0 类型(bugfix / review),
但 user_raw 本身不清晰(eg. "修一下 bug"但未指明哪个 bug):

- Refiner **不得**强行做 Goal 转译假装 user_raw 已明确(违 PR-0);
- Refiner **不得**突破 K=0 反问;
- 唯一合法路径:走 §3.4 Goal Downgrade → 降级到 `unknown`(base_K=2),
  此时获得反问能力;
- 降级触发与确认走与 §3.4 相同的 ConfirmGate,
  用户可拒绝降级 → DeclineTask(用户应当提供更明确 user_raw)。

防"小 Goal 强行 mute Refiner"导致的 hijack:K=0 不是给 agent 用于堵嘴的
机制,而是给意图本来就清晰的 Goal 的默认;模糊意图不是这类 Goal。

### 4.5 confirm_gate(状态:SETTLED;timeout 默认值 OPEN)
```
gate(action, context):
    level = context_judge(action, context)
    match level:
        whitelist -> execute
        greylist  -> on_user_response(timeout=T_conf [默认 60s, 可配]):
                       accept  -> execute
                       reject  -> raise RejectedByUser; agent 须改其他手段
                       modify  -> 修改参数 → re-gate
                       timeout -> suspend  # 不 reject,挂起,agent 可转跑别的
        blacklist -> 不执行原动作;
                     提供 equivalence(action) 给 user
                     若 user 接受等价动作  -> 执行等价动作
                     若 user 显式 Override -> 执行原动作 + 触发
                                              Verifiability 的 Override 审计 (§6.4)
```

### 4.6 Authority 接住的 Goal 留的伤口
| Goal 留的伤口(§3.3.6 / §3.4.6) | Authority 接住方式 | 状态 |
|---|---|---|
| `ask_budget` 来源 | §4.4 | ✅ base_K SETTLED,context_factor OPEN | 部分 SETTLED |
| `context_permitted` 边界 | §4.3 | SETTLED |
| `confirm_gate` 形态 | §4.5 | SETTLED (timeout 默认 OPEN) |
| `prohibited_actions` 拦不拦 | 三层名单映射 | SETTLED |
| baseline 测试的可动性 | 测试文件纳入 greylist/blacklist | SETTLED |

### 4.7 Authority 的残余伤口(OPEN / PENDING)
- 🩹 equivalence 函数的"等价"判定 OPEN(eg. `--force` → `--force-with-lease` 是否真等价?)
- ~~context_judge 函数形态 PENDING~~(**v0.0.7 已交付**, 见 §4.2;
  规则匹配算法 / 优先级链 polish 仍 PENDING)
- 🩹 K 默认值无依据 OPEN;
  adaptive_decay 的衰减函数形态 PENDING
- 🩹 [v0.0.7 新增] match_all 具体 grammar 算法 PENDING (但约束: 必须声明式不开 dynamic eval)
- 🩹 [v0.0.7 新增] 优先级链冲突解决 polish 算法 PENDING
- 🩹 [v0.0.7 新增] `unresolvable_no_rule_matched` 强化提示的 UI 形态仅语义定 PENDING
- 🩹 [v0.0.7 新增] 跨平台路径解析与 §6.5.2.8 ACL 同根, 实现层一起做 PENDING

---

## 5 Accountability 维度(状态:形态 SETTLED;部分运行前提 PENDING)

### 5.1 职责切分(防止与 Goal 撞车)
- **Goal 维度**:给出判据的形态(TerminationCriterion 纯函数声明)
- **Accountability 维度**:给出判定主体与证据来源(谁跑这个函数,它读什么输入)

### 5.2 Judge 主体(状态:base_judge SETTLED;运行时调节 SETTLED)
```
Judge = system | user | model_self
base_judge(BaseGoalType) -> (default_judge_main, default_judge_aux)
主 Judge 决定 met / not_met / undecidable
辅 Judge 只给信息,不参与 met 判定
```

**表 2: base_judge 默认组合 (for coding agent)** —— 依据为各 GoalType
天然对应哪些可机械判定的 artifact;无大规模数据校准;Volume 后调整。

| BaseGoalType | main     | aux        | 设计理由 |
|---|---|---|---|
| bugfix      | system    | model_self | 测试决定成败;辅 judge 自审逻辑 |
| feature     | system    | model_self | 同 bugfix |
| refactor    | system    | model_self | 测试须保持;辅 judge 审外部行为不变 |
| test_write  | system    | user       | 测试本身是给用户看的;主 judge 须有跑测试基线 |
| docs        | user      | model_self | 文档无机械判据;主 judge 用户 |
| perf_opt    | system    | model_self | 性能须有 baseline 数据;辅 judge 自审 |
| review      | model_self | user      | review 不需修改代码,模型自检即可;辅用户对照 |
| investigate | model_self | user      | 调研无 met 标准;主模型自检结论,辅用户确认 |
| migrate     | system    | model_self | 迁移须测试仍过;辅 judge 自审兼容性 |
| scaffold    | system    | user       | 项目骨架须基础测试可跑;辅用户验收 |
| unknown     | user      | model_self | catch-all,人为主审慎 |

**运行时调节 (与 §5.4 一致)**:
```
if 主 Judge 当前不可用 (eg. system 无测试可跑):
    降级路径:
        (a) aux Judge 升为暂代 main,但结果标 met_with_caveat (§5.4)
        (b) 或人工提级 user 介入作为新 main J
        两者运行时不可逆地被 recorder 记录 (Verifiability)
if 主/辅 Judge 都能跑但结论不一致: 
    走 §5.4 的 met_with_caveat (caveat=main_aux_divergent) / not_met_with_signal
```
**与 §3.5 兼容性**:ExtendedGoalType 的 base_judge
默认继承自 `fallback_to` 指向的 BaseGoalType;
用户可在 ConfirmGate 显式修改 main/aux。

### 5.3 Evidence(状态:SETTLED;external schema 移交 Verifiability 已接)
```
evidence = (
    baseline_sha:    git_sha,
    current_sha:     git_sha,
    diff:            structured_diff,
    test_results:    [(test_id, status)],
    lint_results:    [(rule_id, status)],
    external:        <schema 见 §6.5>
)
```

### 5.4 一致性(PR-0 落地;状态:SETTLED)
- 主 Judge = `undecidable` → 辅 Judge **不可越级**改 met (保 PR-0)
- 主/辅 Judge 不一致 → 状态升级为 `met_with_caveat` / `not_met_with_signal`
- caveat **必须**在 RunEgress 报

**caveat 子类型(v0.0.3 补 —— 导自偷渡检查 #6)**:
两个看起来都会触发 met_with_caveat 的事件,**实质独立**,须在 RunEgress 标明子类型:

| 触发事件 | caveat 子类型 | 语义 |
|---|---|---|
| 主 Judge 当前不可用(eg. system 无测试可跑,跑不了判定) | `main_unavailable` | 判据缺位;结果由 aux 暂代,可信度低于常态 |
| 主/辅 Judge 都能跑但结论不一致 | `main_aux_divergent` | 判据都在场但有冲突;信号说明问题空间尚有未达成共识 |

不允许把两个事件合并成单一 caveat:`main_unavailable` 缺的是**判定**,
`main_aux_divergent` 缺的是**一致**;两者下游对策完全不同(前者需补判据,
后者需调 Goal / 加 Validate Channel)。混标会让 Verifiability 失去诊断能力。

### 5.5 近似 Goal 的 baseline 与 evidence 时间窗
        (v0.0.6: 状态——形态 SETTLED for system_judge GoalType;
                 OPEN for user_judge GoalType)

#### 5.5.1 baseline 与默认 evidence_window(状态:SETTLED)
```
original_goal.baseline_sha       = 任务起点 sha         # SETTLED
original_goal.evidence_window    = [任务起点 sha, now]   # SETTLED

approximate_goal.baseline_sha    = 降级时刻 sha         # SETTLED (与 §3.4 一致)
approximate_goal.evidence_window =
    [降级时刻 sha, now]                                   # SETTLED
    ∪ 取自 [任务起点 sha, 降级时刻 sha] 范围内
      被静态分类器判为 direct_enables / indirect_enables 的 hunk   # v0.0.6 SETTLED
```

#### 5.5.2 静态 hunk 分类器(机制 A1,状态:SETTLED 形态)
```
static_hunk_classifier(hunk, GoalTriple) -> flags:
    输入: 一个 hunk + 一个 GoalTriple
    要求: GoalTriple.TerminationCriterion.exposed_dependency_set  
          (该字段由 v0.0.6 §3.2.2 回填)

    计算方法: hunk 接触的代码 element touch-set 与 exposed_dependency_set 做集合运算

    binary flags (集合型,无连续分值, 抗 agent 作弊):
        direct_enables      iff  hunk.touch-set ⊆ exposed_dependency_set
        indirect_enables    iff  hunk.touch-set ∩ exposed_dependency_set ≠ ∅
                              且  非 direct_enables
        independent          iff  touch-set ∩ exposed_dependency_set = ∅
        sabotages           iff  hunk 改了 exposed_dependency_set 中 element
                              但 让 它 原先对 termination 的贡献消失
                              (eg. 删了预期 test code,改了 expected baseline)
                              # 与 §4.4 test_baseline_mutation_rate / §6.3 audit_warning
                                已勾稽
```
**evidence_window 计算 (含 flag 规则)**:
```
flag = direct_enables → 全部计入 evidence
flag = indirect_enables → 计入,标 prefix indirect
flag = independent     → 不计入
flag = sabotages       → 不计入 + audit_warning (照 §6.3 audit_warning 处置)
```

#### 5.5.3 为何不引新维度 (设计过程透明记录)
本轮红蓝对抗过程中,hunk 归属判定的"独立第三方打 flag" 需求**看似**会引出
一个新维度。但蓝方 A1 形态证明:**只引入静态分析器机制 + 对 §3.2.2 回填
exposed_dependency_set**,即可在 4 维内闭合。*未引新维度*是本轮一个发现,
且**自抓到 §3.2.2 漏掉 exposed_dependency_set 是存量错误增量**(PR-0 第 4 次实证)。

#### 5.5.4 残余伤口(OPEN):
- 🩹 **user_judge GoalType** 的 hunk 归属无 exposed_dependency_set → 分类器无法跑。
  保守路径:对 docs/review/investigate 类 Goal,evidence_window 默认只含降级后 hunk。
- 🩹 **transitive 闭包不计算**:hunk 间接(inlined 进 benchmark 的代码)是否算?留 OPEN。
- 🩹 **sabotages 检测的具体算法**: 对治 adversarial 行为时,需要实现层做插桩,PENDING。
- 🩹 **exposed_dependency_set 的"边界模糊"**: 多文件 GoalType (eg. refactor 多文件) 的
  exposed_dependency_set 可能过大,hunk 几乎都判 indirect_enables → evidence 失真。OPEN。

### 5.6 Accountability 的残余伤口(OPEN)
- ~~hunk 归属判定机制 OPEN~~ (**v0.0.6 已交付** → §5.5 静态分类器 SETTLED for system_judge,
  保守默认 for user_judge,残余 OPEN 见 §5.5.4)
- 🩹 `met_with_caveat` 的 caveat 在 RunEgress 中的结构形态 PENDING
- 🩹 "system Judge 跑测试失败但辅 judge 觉得无问题"的兜底路径(理论上 met_with_caveat 已涵盖,实操未验)
- 🩹 [v0.0.6 新增] §5.5.4 列出的 4 条 OPEN (user_judge 保守默认 / transitive 不计算 /
  sabotages 算法 PENDING / exposed_dependency_set 边界模糊)

---

## 6 Verifiability 维度(状态:形态 SETTLED;部分运行前提 PENDING)

### 6.1 RunRecorder(状态:录入原则 SETTLED;外部锚点形态 v0.0.5 已立,
         见 §V.4 / 此处 §6.4.1 alias)
```
timeline.jsonl(append-only mode,系统调用层强制):
    每条事件 = {
        event_id, ts,
        type: model_call | tool_call_start | tool_call_end | gate_decision
            | user_response | override | judge_result | anchor_ack,  # v0.0.5 加 anchor_ack
        payload, prev_hash      # 链式哈希
    }
    录入原则(SETTLED):
        - append-only mode (系统调用层强制)
        - 事件先于对应行动写入("意图先于行动")
        - 每条含 prev_hash (链式)
    承诺降级:
        "不可篡改"承诺被取消。篡改若发生,通过 (§V.4 外部可信锚点 +
        离线 audit 比对) 发现。锚点形态 v0.0.5 已 SETTLED (承诺边界见 §V.4.2)。

承诺边界 (v0.0.5 明示):
    ✅ agent 进程篡改可发现
    ✅ 同 OS user 启动的其他非 root 进程篡改可发现
    ❌ 同 OS user 本人主动篡改 (须远程/硬件 token, 本轮不推)
    ❌ OS root 篡改 (同上)
```
**不假装 adversary-resilient**,这点在 v0.0.2 的对抗记录里特别说明:
agent 自己有文件系统写权限,纯文件级不可篡改是不可能的。
**外部锚点的承诺边界在 v0.0.5 立**,不属于"无承诺",但也不属于"全对手"。

### 6.2 Replay 协议(状态:SETTLED)
```
replay(run_dir):
    foreach event in timeline.jsonl:
        if event.type == model_call:    使用记录的 model_response (不重新调模型)
        elif event.type == tool_call_end:使用记录的 tool_result     (不重新执行工具)
        elif event.type == judge_result:使用记录的 judge_result     (不重新判定)
    最终比对:
        重放得到的 RunEgress == 原 RunEgress → 复现成功
        否则 → replay_verifiability_breached
```
**复现的是结论,不是生成**。模型 temperature > 0,生成不可复现。
生成复现需固定 seed,属 development_aid,**不参与评估**。

### 6.3 诱导式问询取证(状态:SETTLED;自动判定有损 OPEN)
- recorder 记录 Refiner 每次反问的完整句式
- 离线 audit 正则分类:
    clarifying_pattern √:  "X 指什么", "是否包含 Y", "Y 的预期是?"
    suggestive_pattern ×: "要不要", "建议", "应该写成 X"
- 命中 suggestive → run 在评估数据集中降权
- 不可纯自动判定所有诱导句(可变形),audit_warning **不阻塞 run**,
  **影响评估维度**(§2 的"可证伪率"等 metric)

### 6.4 Override 审计(状态:SETTLED)
```
Override 事件 = {
    event_id, ts,
    original_blacklisted_action,
    user_confirm: <schema 见 §6.5>,
    override_text, prev_hash
}
RunEgress 含 override_count
override_count > 0 的 run 不许作为评估数据来源
```

### 6.5 external schema(状态:SETTLED;HMAC 强模式 optional)
```
external = {
    user_confirm?:    { confirm_event_id, ts, signature_opt? },   # 默认弱模式
    model_self_check?:{ self_eval_report, confidence: float },
    data_snapshot?:   { snapshot_hash, source_module }
}
默认弱模式: signature_opt 缺省,仅记"用户按下确认"这件事
可选强模式: signature_opt = HMAC(user_id + run_id + ts, user_key)
```

### 6.5.1 external schema vs 外部锚点 (不可合并, v0.0.5 显式边界)
| 关注对象 | §6.5 HMAC 强模式 | §6.4.1 外部锚点 |
|---|---|---|
| 被签字内容 | user_confirm (用户授权不可否认) | run 尾 hash (timeline 完整性不可否认) |
| 私钥拥有者 | user | anchor 进程本身 |
| 对抗目标 | user 自己事后否认 | agent 篡改 timeline |
| 触发时机 | user 确认 override 等高风险授权时 | 周期性 + run 终结 |

**两套独立不合并**。混淆后会让"用户授权" 与 "timeline 完整性" 两个独立信任链
相互污染。

### 6.5.2 §6.4.1 (V.4) 外部可信锚点(状态:形态 SETTLED;Windows ACL 运维 OPEN)

#### 6.5.2.1 一段话定义
外部可信锚点是一个**位于 agent 进程外、由 user 直接控制** 的运行体,
其目标是对抗 **agent 进程** 与 **同 OS 上 user 启动的其他进程**
(**不防** user 本人;**不防** OS root)。
实现"篡改可发现"承诺,而非"不可篡改"承诺。

#### 6.5.2.2 对手/承诺边界 (核心 — 错画即塌)
| 对手 | 锚点对策 | zall 承诺 |
|---|---|---|
| agent 进程 (只有 agent 的文件写权限) | OS 进程隔离: 锚点 non-agent uid, agent 无写权限 | ✅ agent 直接篡改可发现 |
| 同 OS user 启动的其他非 root 进程 | OS 文件权限: 锚点私钥 chmod 0600 | ✅ |
| 同 OS user 本人 (user 主动改 timeline) | 须远程/硬件 token,本轮不推 | ❌ **不承诺**(本轮明示) |
| OS root | 同上 | ❌ **不承诺**(本轮明示) |

#### 6.5.2.3 信任根的 out-of-band 初始化(防 silent 替换)
```
TrustAnchorInit:
  ts_init = anchor boot
  private_key, public_key = generate_ed25519()                # 纯密码学
  init_print = (anchor_id, public_key_fp, ts_init, anchor_software_version)
  write_file("~/zall_trust_anchor_init.txt", init_print)   # agent 不可写 chmod 0400
  # user 通过对比此处 public_key_fp / ts_init 与 timeline 内首条 anchor_event 的签名,
  # 验证锚点未被 silent 替换。
  # **out-of-band 一次性建立, 之后不可重写**。这是防 daemon 重启背堵的唯一手段。
```

#### 6.5.2.4 锚点接口(最小化)
```
class TrustAnchor:
  def write_run_tail(self, run_id: str, last_event_hash: str, ts: int)
    -> AckEvent:
      # 1. 验证 last_event_hash 是 hex 64 字符 (SHA-256)
      # 2. 用 self.private_key 签名 (last_event_hash || ts || run_id)
      # 3. 写入 self.append_only_anchor_log (chmod 0600)
      # 4. 返回 ack_event (含签名, 写回 timeline.jsonl 作为一条事件)
      return AckEvent(anchor_id, run_id, last_event_hash, ts,
                      sig = sign(private_key, last_event_hash || ts || run_id),
                      prev_anchor_hash)
```
**最小化是对抗特洛伊木马的关键**:锚点不读 timeline 内容、不验证语义、不调外部服务,
只签收到的 hash 并写自己的 log。任何加重载逻辑都是攻击面扩大。

#### 6.5.2.5 与 RunRecorder 的接入路径(闭环设计)
```
RunRecorder.commit_event(event):
    append_to_timeline(event)                    # 原链式哈希仍在 (§6.1)
    if event 是 "run 终结" 或 每 N 条事件 (per_run_anchor_interval, 默认 N=50):
        last = current_tail_hash_of_timeline
        ack = anchor.write_run_tail(run_id, last, ts)
        append_to_timeline(ack)                 # ack 本身也作为一条事件
        # 离线 audit 时比对 timeline 的 ack 与 anchor_log 的 ack:
        #   ack.last_event_hash == timeline 链尾 hash
        #   verify_sig(ack.sig, ...) == True
        #   ack.prev_anchor_hash == anchor_log 里前一条 ack 的 hash
```

#### 6.5.2.6 与 PR-3 模型无关 / OS 假设边界
- **密码学不依赖任何模型**: ed25519, SHA-256 纯算法 ← 守 PR-3
- **OS 假设**: 进程隔离 / chmod 0600 等,假设 POSIX 或 Windows 等价语义
- **跨平台警告**: Windows 上 chmod 等价语义靠 ACL 实现,具体落地需测
  → 标 PENDING,但**不影响本维度的 HTTP 抽象 SETTLED**

#### 6.5.2.7 在 §2 评估层的落地 (解锁部分 §2.1.4 OPEN)
- `tamper_detected_rate`:离线 audit 比 anchor_log vs timeline 不通过的 run 计数 → **有了具体算法**
- `timeline_integrity_rate`:从单层 hash→双层 hash+锚点签字
- `non_reproducible_by_construction` 枚举补一条:**`anchor_unreachable`**(锚点离线/私钥不可用)

#### 6.5.2.8 残余伤口(OPEN)
- 🩹 Windows ACL 实现 PENDING,需识别 anchor_log 与 agent 工作目录权限隔离的具体 API
- 🩹 锚点 silent 替换仅靠 user out-of-band 对照 — 人因风险,有兜底但 OPEN
- 🩹 `per_run_anchor_interval` N=50 凭直觉,需数据校准,OPEN 缺省值
- 🩹 anchor_log 的存储/备份/迁移策略 OPEN
- 🩹 锚点独立进程启动/挂掉监听的协议 PENDING

### 6.6 Verifiability 的残余伤口(OPEN / PENDING)
- ~~外部可信锚点的具体形态 PENDING~~ (**v0.0.5 已交付**, 见 §6.4.1/V.4;
  仅承诺 agent 进程与同 OS 其他非 root 進程篡改可发现;
  不承诺同 OS user 本人 / OS root 篡改可发现)
- 🩹 timeline.jsonl 文件级 append-only 在 Windows 的实现细节 PENDING
- 🩹 诱导式问询的强判定(eg. 语义级而非正则级)OPEN
- 🩹 data_snapshot 的 source_module 跨界跨服务时的 schema 兼容 OPEN
- 🩹 [v0.0.5 新增] Windows ACL 实现 PENDING (anchor_log 与 agent 工作目录权限隔离的具体 API)
- 🩹 [v0.0.5 新增] 锚点 silent 替换仅靠 user out-of-band 对照 -- 人因风险,仅兜底
- 🩹 [v0.0.5 新增] per_run_anchor_interval N=50 缺省值凭直觉,OPEN
- 🩹 [v0.0.5 新增] anchor_log 的存储/备份/迁移策略 OPEN

---

## 6.7 维度交叉点清单(对治 R-三维同推风险)

R-三维同推 风险规则 PR-1 的对治产物:

| 伤口移交链 | 来源 | 目标 | 目标是否接住 | 状态 |
|---|---|---|---|---|
| prohibited_actions 拦不拦 | §3.2.3 | §4 三层名单 | ✅ | SETTLED |
| ask_budget 来源 | §3.3.6 | §4.4 | ✅ base_K SETTLED,context_factor OPEN | 部分 SETTLED |
| context_permitted | §3.3.6 | §4.3 | ✅ | SETTLED |
| confirm_gate 形态 | §3.3.6 | §4.5 | ✅ timeout OPEN | 部分 SETTLED |
| 用户 Override 审计 | §4.5 | §6.4 | ✅ | SETTLED |
| external schema | §5.3 | §6.5 | ✅ HMAC optional | SETTLED |
| hunk 归属 | §5.5 (机制来源) | §3.2.2 (回填 exposed_dependency_set) | ✅ §5.5 静态分类器 SETTLED for system_judge;user_judge 走保守默认 | SETTLED-fornow |
| 尾部 hash 外部锚点 | §6.1 | 自留 → v0.0.5 §6.4.1/V.4 | ✅ 锚点形态 SETTLED (承诺边界 §V.4.2);Windows ACL / 锚点运维 OPEN | SETTLED-fornow |
| GoalType Enum | §3.5 (本源) | §4.4 / §5.2 (两组消费者) | ✅ 本源 SETTLED-fornow + 两组消费者 base 表 SETTLED | SETTLED-fornow |
| "剔除含 suggestive/override 的 run" | §6.3 / §6.4 | §2 评估体系 | ✅ metric 形态本身 PENDING | PENDING |
| 篡改可发现承诺 | §6.1 | 未移交(自留) | ✅ | PENDING |

**关键观察**:本轮(v0.0.2)所有伤口都被显式接住或自留,无假象 SETTLED。
v0.0.3 在此基础上推 GoalType Enum,**两个 PENDING(§4.4 K 表、§5.2 Judge 组合)**
被本源 SETTLED-fornow 解锁,翻为 SETTLED。
剩余 PENDING/OPEN 显式保留,不假装已闭。

---

## 7 长期属性清单(状态:SETTLED —— 维度雏形)
本 design 文档对应的实现完成后,需具备以下长期属性:
| 长期属性      | 落点                                |
|-------------|-------------------------------------|
| 模型可换      | 模型适配层独立,新增模型只动一个 Adapter   |
| 工具可换      | 工具注册 + MCP,新功能不动主循环         |
| 协议可换      | 适配层抽象 stdio/SSE/HTTP             |
| 上下文策略可换  | Compactor 是策略接口,可换不同摘要策略  |
| 部署形态可换    | 应用层分离,CLI/TUI/Web/IDE 可加      |
| 版本可演进     | 主循环只依赖接口,各层可独立升级         |

注:此表是"维度雏形",具体每一项的状态将从对应维度被推到 SETTLED 时起同步。

---

## 8 版本与修订记录

### v0.0.21 (本版本 — 核心可用性修复: 空 STOP 退避 + 强化提示词)

用户实测暴露的核心可用性问题: 弱模型 (agnes-1.5-flash) 对探索性任务 (eg. "检查一下
桌面有啥") 常返回**空 STOP** (不调工具也不回答), agent 形同没动 → "好多没到位"。
本轮三处修复让 agent 真正"动起来":

- **空 STOP 退避 (loop.py, 核心)**: `step()` 第一次调模型若返回 STOP + 空 content
  (不调工具也不回答) → 注入 `_EMPTY_STOP_NUDGE` system 消息 (要求模型"用工具或实质
  回答, 别空回复, 别宣告意图就停") + 重试一次 `_call_model()`。重试后的 resp
  (TOOL_USE / 带 content 的 STOP / LENGTH) 交给正常 dispatch。**限 1 次/step, 不循环**
  (防无限重试); 持续空 STOP → 诚实返回空 (反例测试 `test_persistent_empty_stop_no_infinite_loop`
  断言只调 2 次)。
  - nudge 本身被审计: append 空 assistant + system nudge 到 `_messages` (timeline 可见)。
  - 修复中发现的子 bug: nudge 误用 `Message.system(...)` (Message 无 system 类方法,
    只有 user/assistant/tool_result) → AttributeError 被 step 的 try 吞成 terminal;
    改 `Message(role="system", ...)`。
- **强化系统提示词 (_SYSTEM_PROMPT_BASE)**: 加 "CORE DIRECTIVE" 段 —— "You ACT
  through tools. 对任何需要文件/环境信息的请求, MUST 先 emit tool_call, 别从假设回答;
  别宣告意图就停; 别返回空回复" + 规则 7 "不确定路径就 list_dir/glob 探索, 别猜"。
  直接提升弱模型的工具使用率。
- **空 STOP 渲染可操作提示 (render.py)**: nudge 后仍空时, 旧实现只打无意义的
  `step N · (stop)`; 改为 `step N · (empty — model gave an empty response — try
  rephrasing or /model to switch models)`, 让用户知道下一步怎么办。
- **真实 API 复测验证**: "检查一下桌面有啥" 修复前 → `step 1 · (stop)` 空回复;
  修复后 → `✻ List C:\Users\云丘\Desktop` → `✓ ⎿ 192 entries` → 模型实质列出桌面内容
  (server_output/ / aDrive/ / CodeBuddy CN/ / 燕云十六声.lnk / ...). agent 真正调工具
  干活了。
- **v0.0.21b nudge 收紧 (用户二次实测反馈)**: 用户实测"在桌面建文件夹 textjlkj" →
  nudge 后模型只回"我来在桌面创建…"宣告意图却**不调 bash** (旧 nudge 给了"或给文本
  回答"的退路, 模型钻空子)。收紧: nudge 文案去掉退路, 明确"MUST emit a tool_call,
  Do NOT reply with text that only describes intent (eg. 'I will create ...' without a
  bash tool_call is a failure)"。系统提示词 CORE DIRECTIVE 同步加"actionable 请求
  (create/modify/delete/run) MUST 用工具执行, 不许只描述意图"。复测: agent 直接
  `✻ Bash mkdir "%USERPROFILE%\Desktop\textjlkj"` → `✓ exit 0` → 再 `✻ Bash dir`
  验证 → 回答"已在桌面创建空文件夹 textjlkj"。**写操作任务也端到端跑通** (连 nudge
  都没触发, CORE DIRECTIVE 直接让模型调 bash)。
- **v0.0.21c nudge 双重渲染修复 (Bug E, 用户三次实测反馈)**: 用户实测"建文件夹"空
  STOP 时 `(empty...)` 提示**显示两遍**。根因: nudge 重试时第一次空调用的 `model_call`
  渲染事件被广播了 (它是被 nudge 取代的中间态, 不该呈现), 重试结果又渲染 → 双重。
  修复: `_call_model` 加 `emit_model_call` 参数 + 抽 `_emit_model_call_event` 助手;
  `step()` 首次调用 `emit_model_call=False` (只记 timeline + spinner, 不渲染), nudge
  重试 `emit_model_call=True` (渲染重试结果), 非 nudge 补发首次渲染。**timeline 全保真
  不变** (第一次空调用仍记 RunRecorder, 只是呈现层不重复渲染)。回归测试
  `test_nudge_emits_single_model_call_render` (断言 model_call 事件只 1 个, 且是重试结果)。
- **v0.0.21c /model picker 校验**: 用户在 picker 输入"继续，"被当成模型名设置 → 下次
  对话 API 报错。修复: picker 输入非数字时, 只接受已知别名/预设名 或 看起来像模型名
  (alnum/`-`/`_`/`.` 且无中文) 的输入; 否则警告"doesn't look like a model name"并保留
  当前模型。自定义模型仍可用 `/model <name>` (带参) 直接设。回归测试 2 个 (拒中文误输入 /
  接受自定义 alnum 模型名)。
- **不变量守住**:
  - 失败安全: nudge 限 1 次, 持续空不无限循环; nudge 消息被审计 (入 timeline)。
  - 向后兼容: 既有 loop 测试 (ScriptedAdapter 返回非空 content) 不触发 nudge, 零改动。
  - PR-0 不变: nudge 不绕过幻觉扫描; 空 STOP nudge 后若伪造工具输出仍被 PR-0 检出。
- 改动文件: `core/loop.py` (`_EMPTY_STOP_NUDGE` + step 空 STOP 退避) /
  `cli/app.py` (强化 `_SYSTEM_PROMPT_BASE`) / `cli/render.py` (空 STOP 提示)。
- 测试: `tests/test_loop_invariants.py::TestEmptyStopNudge` (4 测试: nudge 后实质回答 /
  nudge 后改用工具 / 持续空不循环 / nudge 单次渲染不双重) + `tests/test_interaction_v020.py`
  picker 校验 (+2)。全套 591 passed / 2 skipped / 0 failed (+6, 无回归)。

### v0.0.20 (本版本 — 交互层完善: 开箱即用 / 换模型 / 命令提示)

对齐 Claude Code 的首次引导、模型 picker、did-you-mean + tab 补全 (参考全网公开的
Claude Code CLI 交互范式, 借鉴形态不照搬源码)。

- **开箱即用 (首次运行引导)** — `_onboarding(out, input_fn)` 在 REPL 启动时跑:
  - 已配置 API key → 零干扰直接返回。
  - TTY + 无 key (缺失或仍为 `your-api-key-here` 占位) → 打印欢迎 + 提示输入 key
    (Enter 跳过), 调 `save_api_key` 存入 `~/.zall/config.toml`; `ensure_config` 先生成
    模板文件方便手动编辑。
  - 非 TTY → 打一行指引 (`set ZALL_API_KEY or edit ~/.zall/config.toml`), 不阻塞脚本。
  - 旧版: 首次 REPL 输入后才在构造 adapter 时报 "API key required", 体验差; 现前置引导。
- **换模型 (/model picker + 别名)** — `_cmd_model(state, out, arg, input_fn)`:
  - 带参 → 直接设置 (支持短别名: `flash`/`mini`/`sonnet`/`deepseek`/`glm`/`qwen`/`4o`),
    下次新对话生效。
  - 无参 + TTY → 交互式 picker: 列常用模型示例 (agnes/gpt/claude/deepseek/glm/qwen,
    标注 "availability depends on your api_base"), 编号选或直接输名; 标记 current。
  - 无参 + 非 TTY → 显示当前模型 + 用法示例。
  - 旧版: `/model [n]` 只接受裸字符串、无 picker 无别名。
- **命令提示 (did-you-mean + tab 补全)**:
  - `_suggest_command(name)`: 未知 slash 命令用 `difflib.get_close_matches` 给出最接近
    的已知命令 (eg. `/modle` → "did you mean /model?"), 无匹配则退回 "(try /help)"。
  - `_setup_completion(skills)`: 注册 readline tab 补全 (内置命令 + `/skill <name>`),
    平台无 readline (Windows 默认) 则静默跳过, 不报错。`set_completer_delims("")` 让
    `/skill <name>` 整行可补全。
- **不变量守住**:
  - 开箱即用不破坏非交互: 非 TTY / 无 input_fn 时 onboarding 只打指引不阻塞。
  - /model 仍是"下次对话生效"语义 (与 /max-steps /verbose 一致), 不改当前 loop adapter。
  - did-you-mean / tab 补全为纯增强, 不改变命令语义。
  - 失败安全: readline 缺失 / config 加载异常都不让 REPL 崩溃。
- 改动文件: `cli/app.py` (新增 `_config_status` / `_onboarding` / `_MODEL_PRESETS` /
  `_MODEL_ALIASES` / `_resolve_model_alias` / `_cmd_model` / `_suggest_command` /
  `_setup_completion` + 接入 `repl()` 与 `/model`、unknown 分支)。
- 测试: `tests/test_interaction_v020.py` (22 测试, 含反例: 别名/大小写/did-you-mean/
  config_status ready+placeholder+空/picker 编号+输名+空输入/onboarding ready零干扰+
  非TTY指引+TTY保存+跳过)。
- **终端模拟验证发现并修复 4 个真实 bug** (用真实 `repl()` + 假 input_fn 喂命令序列;
  含真实 API 一次性只读 task 端到端 + fake adapter 多轮对话 + Goal 确认路径):
  - **Bug A — rich markup 在非 TTY 泄漏**: `render_goal_card` 非 TTY 分支旧实现
    `out.write(lines[0])` 把 `[bold yellow]Goal[/]` 等标签原样写出 → 管道/脚本/CI 里
    是字面量垃圾。修复: 非 TTY 也走 `_shared_console(out).print(...)` 渲染剥离 markup。
    回归测试 `TestGoalCard.test_non_tty_strips_markup` (断言不含 `[bold`/`[/]`/`[cyan`)。
  - **Bug B — Windows GBK 控制台 Unicode 乱码**: v0.016 精心选的 `· ─ → § ✓ ✻` 在
    Windows 中文控制台 (cp936/GBK, 缺这些码位) 被 Python 替换成 `？` → 去塑料感全毁。
    修复: `main()` 入口 `_ensure_utf8_stdio()` 把 stdout/stderr `reconfigure(encoding=
    "utf-8", errors="replace")`;     现代终端 (Windows Terminal/VS Code) 正确渲染, 旧 cmd
    不崩。非 Windows 无副作用。
  - **Bug C — /compact 无条件 `return "clear"` 丢弃对话态**: `_handle_slash` 的
    `/compact` 分支旧实现无论压缩是否成功都 `return "clear"` → REPL 丢 loop 重建 →
    下一句输入开新对话 (重锁 Goal + 丢上下文)。但 `_cmd_compact` 成功时已**原地替换**
    `loop._messages` (system + 摘要 + recent), 且 `ModelCompactor` 保留 system 消息;
    故 "clear" 在所有路径都是 bug: 成功路径丢压缩后上下文, 无可压缩/失败路径丢当前
    对话。修复: `/compact` 不再 `return "clear"`, 保留 loop (落到 `return "handled"`);
    成功时压缩已生效, 无可压缩/失败时原对话态保留。回归测试
    `TestCompactPreservesConversation` (3 路径: 无 model / 无可压缩 / 成功, 均断言
    `"handled"` + loop 保留), 并修正旧测试 `test_slash_compact_returns_clear` (它把
    bug 当正确行为固化, 改为 `test_slash_compact_returns_handled_preserves_loop`)。
  - **Bug D — repl 的 Goal 确认没用 input_fn**: `repl` 调 `_confirm_goal` 时未传
    `input_fn`, 导致 Goal 确认用真实 `input()` 而非 REPL 注入的 `input_fn` —— 与主循环 /
    onboarding / `/model` picker 用的 `input_fn` 不一致 (程序化驱动 / 自定义 input_fn 时
    confirm 绕过它, 不可测试)。修复: `repl` 把 `input_fn` 传给 `_confirm_goal`。
    回归测试 `TestGoalConfirmUsesInputFn` (fake adapter + 强制 stdin TTY + 模型调用计数:
    reject→0 调用 / accept→≥1 调用)。
  - 模拟还验证了端到端真实可用 (本机有 key → `hello world` 走完 Goal 锁定 + 模型调用
    返回 "Hello! How can I help you today?"), 以及 did-you-mean / picker / 大小写 /
    空白 / 未注册 skill / 无参命令等边界全正常。
- 测试: 全套 585 passed / 2 skipped / 0 failed (+22 交互 +2 Goal 卡片回归 +3 /compact 回归 +2 Goal 确认 input_fn 回归, 无回归)。

### v0.0.19 (本版本 — §9.2.7 skills 斜杠命令 + §9.2.11 子 agent 继承 MCP 工具)

- **§9.2.7 skills 斜杠命令落地** — 对齐 Claude Code 的可复用工作流 (custom skills):
  - **skill = 可复用 Goal 模板 (输入快捷方式), 不绕 gate**: `/skill <name> [args]`
    展开为 task 文本 → 落回 REPL 对话分支 → 走完整 Goal 锁定 + ConfirmGate。若展开后
    任务触发 greylist/blacklist 动作, 仍走 context_judge + confirm_gate (§9.2.7
    偷渡防线: 斜杠命令不是"免确认的宏")。
  - **format 定型 (§9.4 skill format OPEN → SETTLED)** — 极简 TOML `.zall/skills.toml`
    的 `[[skills]]` (name / description / prompt), 与 mcp.toml / rules.toml 同源哲学
    (IPR-3 仅 stdlib, 手写解析, 不引 toml 库)。prompt 支持多行 `"""`; 占位符 `{input}`
    调用时被参数替换 (无 `{input}` 但带参 → 参数附加末尾)。
  - **优先级 + 失败安全 (IPR-0)**: 项目级 > 用户级同名覆盖; 文件缺失 / 坏 TOML /
    单个 skill 缺 prompt → 跳过该 skill 或整文件返回 [] (最坏不阻断 agent 启动)。
  - 命令面: `/skills` 列出 (TTY rich Table / 非 TTY 文本降级) / `/skill <name> [args]`
    运行 / `/help` 含入口 / `zall init` 生成注释态样本 (review / explain 两示例)。
  - 改动文件: `skills/__init__.py` + `skills/loader.py` (新包) / `cli/app.py`
    (`_route_skill` 分发 + `_print_skills` 列表 + REPL 加载 + 主循环 `/skill` 展开走
    对话分支 + `/help` 入口 + `zall init` 样本)。
  - 测试: `tests/test_skills.py` (21 测试, 含反例)。

- **§9.2.11 子 agent 继承 MCP 工具 (残余 OPEN 收口)** — 收尾 v0.0.17 遗留:
  subagent 不再用纯 native `_build_tools()` 收敛, 而是 `_build_subagent_tools(parent)`
  继承 parent 完整 registry (含 MCP 工具), 仅排除 `spawn_subagent` 自身 (防递归)。
  MCP 工具 Authority 仍由 `_build_subagent_rules` 继承 parent 规则决定 (默认 greylist
  → 子 agent 无监督自动 reject; parent 显式 whitelist 的只读 MCP 工具 → 子 agent 继承
  可用)。不变量: 子 agent 工具集 ⊆ parent (只减不增), 永不比 parent 更宽松 (守 §9.2.10)。
  - 改动文件: `tools/spawn_subagent.py` (新增 `_build_subagent_tools` + execute 调用点)。
  - 测试: `tests/test_subagent_mcp_inheritance.py` (含反例: 子 agent 拿不到
    spawn_subagent → 不能再次生成子 agent)。

- **不变量守住**:
  - 斜杠命令不绕 gate: skill 展开走完整 Goal 锁定 + ConfirmGate (§9.2.7 偷渡防线)。
  - 子 agent ⊆ parent: 工具集只减不增, 继承不放宽 (§9.2.10)。
  - 失败安全: skills 配置错误不阻断 agent 启动; MCP 工具继承不改 deny-by-default。
  - 向后兼容: 既有子 agent 纯 native 测试 (test_subagent_authority_v014) 不受影响
    (parent 无 MCP 时 `_build_subagent_tools` 仅排除 spawn, 行为等价)。
- 测试: 全套 556 passed / 2 skipped, 0 failed (新增 21 测试, 无回归)。

### v0.0.18 (本版本 — §9.2.9 反应式 auto-compact: 长会话不崩)

- **反应式 auto-compact 落地** — 对齐 Claude Code / OpenCode 的长会话体验:
  模型返回 `stop_reason=LENGTH` (context window 爆) 时, AgentLoop 自动压缩
  model context 并重试, 而非直接终止报错 (旧 `step()` 的 LENGTH 分支原注释
  "ContextManager not yet implemented", 本版补齐)。
  - **反应式 (非预测式) 是 PR-3 模型无关的直接推论**: zall 不预设各模型的确切
    窗口大小 (那是模型相关知识, 会污染核心抽象), 靠模型自报 `LENGTH` 触发压缩,
    换任何模型都对。Claude Code / OpenCode 走预测式 (估 token 到阈值就压), zall
    走反应式, 终态一致 (长会话不崩) 但触发不绑模型窗口常量。
  - **timeline 全保真 (§6.1)**: `_auto_compact` 只替换 model 看到的 `_messages`,
    审计 timeline 永不压缩 —— 压缩本身反而是 timeline 一条 `CONTEXT_COMPACTION`
    事件 (链式哈希完整, Replay 可复现)。
  - **失败安全 (IPR-0)**: compactor 抛异常 → 吞掉 + 广播 error → 退回 LENGTH 终止,
    绝不崩溃。压缩后仍 LENGTH / 无 compactor / 压缩 0 条 → 诚实 UNDECIDABLE 终止。
  - **可插拔 + 向后兼容**: `AgentLoop(compactor=...)` 默认 None → 行为与旧版一致
    (既有 loop 测试零改动); CLI (run + REPL) 注入 `ModelCompactor` 默认开启。
    Compactor 是 §7 策略接口, 策略可换。
- **不变量守住**:
  - timeline 全保真: 压缩只影响 model window, 审计轨迹不丢 (记 CONTEXT_COMPACTION)。
  - 失败安全: compactor 故障不得让 agent 崩溃。
  - 模型无关 (PR-3): 触发条件是模型自报 LENGTH, 不绑窗口大小常量。
  - 向后兼容: 未注入 compactor 时 LENGTH 行为与旧版逐字节一致。
- 改动文件: `core/loop.py` (compactor 注入 + `_auto_compact` + LENGTH 分支重试) /
  `cli/app.py` (run + REPL 两处注入 ModelCompactor + 模块级 import)。
- 测试: 新增 `tests/test_auto_compact_v018.py` (9 测试, 含反例)。

### v0.0.17 (本版本 — §9.2.11 MCP 注册协议落地)

- **MCP 注册协议 (§9.2.11) 真正落地** — 把 MCP server 暴露的 tool 注册成 zall 工具,
  且**默认 greylist (deny-by-default)**, 不豁免 Authority:
  - `mcp/client.py`: **零第三方依赖**的极简 MCP stdio JSON-RPC 客户端 (stdlib only,
    守 IPR-3), 实现 initialize / tools/list / tools/call 子集; 单后台 reader 线程按
    id 收响应, server notification 忽略。
  - `mcp/tool.py`: `MCPTool` 把每个 MCP tool 包装成 zall Tool。tool_id 命名空间化
    `mcp__<server>__<tool>` (防撞名, 满足 ToolRegistry 唯一不变量); schema 直接复用
    MCP inputSchema; execute 用**原始** MCP 名调 server (tool_id 是命名空间化的)。
  - `mcp/config.py`: 加载 `.zall/mcp.toml` 的 `[[servers]]` 声明 (name/command/args[]/
    env{}), 项目级 > 用户级同名覆盖; 手写极简解析 (与 rules_file 同源哲学)。
  - **deny-by-default 机械保证**: MCP 工具**不**被任何代码 whitelist, 默认 greylist
    由 §4.2.1 context_judge 无匹配默认 greylist 保证。这是 §9.2.11 偷渡风险的机械对治
    (反例测试: 空规则集下 MCP 工具 → GREYLIST, 不得 WHITELIST; 显式 whitelist 规则可提升)。
  - **失败安全 (IPR-0)**: 任一 MCP server 连接/list 失败 → 打 `[mcp] skip ...` 警告并
    跳过, 返回其余工具, **不阻断核心 native agent** (反例测试验证)。
  - **生命周期**: run() 结束 / REPL 退出关闭所有 MCP server 子进程 (MCPClient.close
    幂等, 防子进程泄漏); REPL 会话内 MCP 只连接一次, 跨对话态重建复用。
  - **系统提示词**: 已注册 MCP 工具追加进清单 (默认 greylist 明示); 无 MCP 配置时该
    段不出现 (旧测试断言不受影响)。`zall init` 生成注释态 `.zall/mcp.toml` 样本。
- **不变量守住**:
  - MCP 工具默认 greylist (不得 whitelist) — 由 context_judge 保证, 非硬编码。
  - 失败安全: MCP 配置错误 / server 不可用不得让 agent 启动崩溃。
  - 非 TTY 渲染契约 / 首 token `step N` / 工具摘要 `(N chars)` 等既有不变量零改动。
- 改动文件: `mcp/__init__.py` + `mcp/client.py` + `mcp/tool.py` + `mcp/config.py`
  (新包) / `cli/app.py` (`_build_mcp_tools` + `_merge_tools` + 系统提示词 + 生命周期
  + `zall init` 样本)。
- 测试: 新增 `tests/test_mcp_registration_v017.py` (17 测试, 含反例) +
  `tests/_mock_mcp_server.py` (真实 stdio 协议 mock, 端到端验证)。
- 残余 OPEN: 子 agent 当前不继承 MCP 工具 (subagent 用 native `_build_tools()` 收敛);
  后续可把会话级 MCP registry 注入 subagent (同 §9.2.10 parent 继承语义)。

### v0.0.16 (本版本 — 去塑料感: 吸收 Claude Code 终端 UI)
- **去塑料感 (用户反馈: 图标有塑料感)**: 吸收 Claude Code 终端 UI 的真实符号体系
  (源码级确认自 Claude Code Deep Dive 第 18 章 Terminal UI):
  - **思考过程**: `💭` emoji → `✻ thinking…` (Claude Code STAR_ICON + 灰阶, 零 emoji)。
    流式指示行 `✻ thinking… N chars` (纯文本覆盖行, 不混 rich 样式以免把 "N chars" 拆碎);
    完整块渲染为 **dim + italic** Panel (标题 `✻ thinking`) — 对齐 Claude Code
    `✻ Thinking…` 的 `dimColor italic` 灰阶斜体美学, 视觉上从属正式回答。
  - **assistant 前缀**: 删除装饰星号 `✦` (v0.0.13 §9.2.2 加的)。Claude Code 的
    assistant 文本**无前缀符号**, 直接流式/Markdown; 工具用 `✻`、思考用 `✻` 自然区分。
  - **spinner**: braille 帧 `⠋⠙⠹…` → Claude Code 几何帧 `·✢✳✶✻✽` (非 braille 通用塑料感),
    仍显示模型名 + 实时耗时。
  - **工具图标**: `●` → `✻` (Claude Code STAR_ICON, U+273B 几何符号非 emoji)。
    `✻` = 活动/工具, `●` = 最终决策 (judge/egress) — 语义分流, 二者皆非塑料。
  - 保留 `✓`/`✗` (工具成败) / `?`/`!` (gate) / `⎿` (结果箭头) — 已是 Claude Code 式,
    且被渲染不变量测试断言 (不得改)。
- **不变量守住**: 非 TTY 仍无 ANSI; 首 token 仍带 `step N`; token 间不换行; 工具摘要仍含
  `(N chars)`。测试 `test_tty_first_token_has_assistant_prefix` (v0.0.13) 改为
  `test_tty_first_token_has_no_assistant_prefix` (反例: `✦` 不得出现 + 内容本体仍显示)。
- 改动文件: `cli/render.py` (✻/几何 spinner/dim-italic 思考块/去 ✦ 前缀) /
  `tests/test_cli_interaction_v013.py` (§9.2.2 测试改新契约)。

### v0.0.15 (本版本 — 思考过程投影 + UI/性能打磨)
- **思考过程投影 (§9.2.12) 真正落地** — 把模型的 reasoning (extended thinking)
  当作透明的"思考过程投影", 而不是丢弃:
  - `ModelResponse` 新增 `reasoning: str` 独立字段 (与 `content` 分离,
    不进 PR-0 幻觉判定 —— PR-0 只扫 content 里的伪造工具输出)。
  - 适配器 (`openai_compat`) 流式捕获 `delta.reasoning_content` /
    `delta.reasoning`, 阻塞捕获 `message.reasoning_content` / `message.reasoning`。
  - 循环层 (`loop._call_model_stream`) 用"长度增量"判定当前 token 属于
    reasoning 还是 content 通道 (reasoning 阶段 content 不增长), 不引入新
    接口 (仍沿用 `complete_stream` 的 `(token, accumulated)` 协议):
    reasoning 增量 → 新 `model_thinking` 事件; content 增量 → `model_token`。
  - 呈现层 (`render`):
    - TTY: 流式实时 `💭 thinking… N chars` 指示 (不刷全文, 省渲染+防刷屏);
      model_call 收尾把完整思考画成 dim Panel (标题 `💭 thinking`),
      视觉上从属于正式回答 (无色 Markdown)。
    - 非 TTY: 一行纯文本摘要 `  thinking: N chars` (无 ANSI, 保管道可消费)。
    - JSON: `model_thinking` 逐行 NDJSON; `model_call` payload 含 `reasoning`。
  - 审计: `model_call` 的 RunRecorder 载荷也记 `reasoning` (§6.1 全保真,
    replay 可复现思考过程)。
- **性能打磨** (守 PR-1: 修现有路径, 不引入新架构):
  1. 共享 Console: `render`/`app` 各函数原本每次 `Console(file=out, ...)`
     新建 (重复做终端能力探测)。新增 `_shared_console(out)` 按流缓存复用,
     `render_goal_card` / `render_egress_summary` / `_print_help` / `_list_sessions`
     / `_run_eval` / `_cmd_cost` / `_cmd_doctor` / `_print_banner` / `_cmd_compact`
     全部改用。REPL 多轮 + 多命令零重复探测。
  2. 预编译正则: `_summarize_tool_output` 原本每次调用 `import re` + 重新编译
     `Lines X-Y of Z` / `Replaced N line` 两个模式 → 提到模块级 `_RE_LINES` /
     `_RE_REPLACED` 预编译。
  3. adapter 复用 httpx 连接池: 旧实现每次调用 `with httpx.Client() as client`
     建+拆, REPL 多轮无连接复用 → 改为 `__init__` 建持久 `self._client` 复用
     (仅响应流 `with` 关闭, 不关 Client)。
- **UI 审美打磨** (TTY, 非 TTY 契约不动):
  - banner 加顶部细分割线 + `zall` 加粗, 信息行更克制 (仍不含步数, 守 v0.0.12)。
  - 阻塞模式 spinner 显示模型名 + 实时耗时 (`⠋ model thinking… 1.2s`),
    不再是裸 "thinking..."。
  - 思考过程块 (dim Panel) 让"模型先想后答"可视化, 透明、可审计。
- **不变量守住**:
  - 非 TTY 渲染仍无 ANSI (`test_non_tty_no_ansi_codes` 不破); 首 token 仍带
    `step N`; token 之间不换行; 工具摘要仍含 `(N chars)`。
  - 流式 ≡ 阻塞 不变量不破 (reasoning 默认空串, 两路径一致; 新 `model_thinking`
    事件只在 reasoning 非空时产生)。
- 改动文件: `core/model.py` (`reasoning` 字段) / `adapters/openai_compat.py`
  (捕获 + 复用 Client) / `core/loop.py` (`model_thinking` + payload) /
  `cli/render.py` (共享 Console + 预编译正则 + 思考渲染 + spinner) /
  `cli/app.py` (共享 Console + banner)。
- 测试: 新增 `tests/test_thinking_display.py` (10 测试, IPR-0 含反例):
  TTY 实时指示 / 非 TTY 摘要 / JSON NDJSON / 流式 model_thinking→token 分流 /
  流式+阻塞 model_call 携带 reasoning / 无 reasoning 反例 / 适配器捕获
  `reasoning_content` 及 `reasoning` 回退。

### v0.0.14 (本版本 — Subagent Authority 继承协议, §9.2.10)
- 把 §9.2.10 "子代理生成 → Authority + Accountability" 的**继承协议**真正落地:
  - 旧 `_subagent_rules()` 只生成一套**全新**收紧规则, **完全没继承 parent 的
    Authority** —— 正是 §9.2.10 点名的偷渡风险 (parent blacklist rm -rf 后
    spawn subagent 绕道)。改为 `_build_subagent_rules(parent)` 合并:
    - 继承 `parent.core_deny_rules` (最强, 防绕道) 直接作子 agent core_deny;
    - 继承 `parent.user_local_rules + parent.domain_rules` (parent 自定义约束);
    - 叠加子代理收紧 (override 更严格): `spawn_subagent`→BLACKLIST (防递归),
      `bash`/`write_file`/`edit_file`→GREYLIST (即使 parent 是 whitelist,
      靠 §4.2.1 优先级链 DENY>GREY>WHITE 保证子 agent 不更宽松)。
  - 不变量: 子 agent 永不超过 parent 宽松度; parent blacklist 必被继承。
- 同批修复 subagent 两个**预存、导致端到端跑不起来**的 bug (守 PR-1: 修现有
  脚手架而非引入新架构):
  1. 过时 API: 旧 `_subagent_rules` 用 `Rule(tool_ids=..., description=...)` 和
     `RuleSet(rules=...)` —— 与当前 `safety.py` 的 `Rule`(仅 `tool_id_pattern`、
     无 `description`) / `RuleSet`(无 `rules` 字段) 接口不符 → ValidationError
     或静默空 RuleSet。已用正确接口重写。
  2. `Context(cwd_meta=None)` 违反 `Context` 必填 `cwd_meta: CwdMeta` 约束 →
     execute 在构造子 context 时必崩。新增 `_SubagentCwdMeta` 占位
     (子 agent 继承主进程 cwd), 满足类型约束。
- 测试: 新增 `tests/test_subagent_authority_v014.py` (10 测试, IPR-0 风格含反例):
  parent blacklist 继承 / parent whitelist 写工具被收紧 / 递归 spawn 必禁 /
  readonly 白名单继承不过度收紧 / execute 真把继承规则传给子 AgentLoop /
  空 prompt & 未初始化两错误路径。
- 改动文件: `src/zall/tools/spawn_subagent.py` (`_build_subagent_rules` +
  `_SubagentCwdMeta` + execute 接线); `DESIGN.md` §9.2.10 / §9.5 翻已交付。

### v0.0.13 (本版本 — 进度投影 + 流式前缀, 对齐主流 agent 水准)
- 把 §9.2.6 / §9.2.2 两个 UX 原语**真正投影到 CLI 交互**:
  - §9.2.6 **TodoWrite 进度投影**: 新增 zall 原生 `todo_list` 显示型工具
    (无副作用, 不读写文件系统/不执行命令)。模型调 `todo_list(todos)` 刷新进度,
    结果进 `ToolResult.artifacts` → 进 timeline (§6.1) → 渲染层画 checklist 面板。
    TTY: ✓/~/○ 着色 Panel (含 `N/M done` 进度 + `active_form`); 非 TTY: 纯文本
    (无 ANSI)。底部 dim 注脚 "progress only — completion judged by criteria",
    把"投影 vs 判据"边界显式画出 (守 §9.2.6 偷渡风险: 清单全打勾 ≠ met,
    judge 用 TerminationCriterion 纯函数, 不读 todo)。
    per-Goal 重置: 每个 run()/REPL 新对话构造新 renderer → `self._todos` 自然归零。
  - §9.2.2 **流式 assistant 前缀**: TTY 流式首个 token 加柔和 `✦ ` 前缀,
    阻塞 TTY 回合加 `✦` 行首, 与 `●` 工具卡形成视觉层级 (内容仍自己说话)。
    非 TTY 路径不动 (保旧不变量: 首 token 仍带 `step N`, 无 ANSI 泄漏)。
  - `todo_list` 默认 **whitelist 免确认** (对齐主流 TodoWrite 体验): 因是显示型
    无 Authority 边界可越, 与 read_file/grep 同列。为保证**任意配置**下都免确认
    (deny-by-default 不应让它落到 greylist 每步弹确认), 在 `load_rules` 中
    **无条件追加** `native_allow_todo` 规则 (用户仍可用显式 blacklist 覆盖,
    blacklist > whitelist 优先级链守)。
- 关键不变量守住:
  - todo 是投影, 不是完成判据 → 面板注脚明示, 不引入"清单=met"语义。
  - 非 TTY 渲染无 ANSI 转义 (保 test_non_tty_no_ansi_codes)。
  - `todo_list` 事件不打冗余摘要行 (直接渲染面板, 跳过 `✓ Todo ⎿ updated N`)。
- 实现位置: `tools/todo.py` (TodoListTool) / `cli/app.py` (注册 + 系统提示词) /
  `safety/rules_file.py` (`native_allow_todo` 无条件 whitelist) /
  `cli/render.py` (`_render_todo_list` + `✦` 前缀)。
- 测试: 新增 `tests/test_cli_interaction_v013.py` (15 测试, 含反例);
  更新 `tests/test_git_rules_invariants.py` 计数断言 (v0.0.13 +1);
  全套 **489 passed, 2 skipped** (原 474 + 15 新)。

### v0.0.12 (本版本 — 交互层 overhaul, 对齐主流 agent 水准)
- 把 §9 已立映射的 UX 原语**真正投影到 CLI 交互**, 不再是只有映射形态:
  - §9.2.1 / §9.2.5 **Goal 锁定卡片 + 确认**: run() 与 REPL 首轮显示 Goal 卡片
    (intent / goal_type / termination 判据来源 / confidence), 开工前确认承诺
    (y/N)。非交互 / --yes 自动确认 (守一次性任务与测试语义); 拒绝则诚实中止,
    不调模型、不伪造完成。这是 zall 与"对话即本体" agent 的核心差异落地。
  - §9.2.4 **greylist 确认增强**: 新增 `e`(就地改参, 返回 MODIFY 经 gate 重判) +
    `a`(本次会话允许该工具, 不豁免 blacklist)。提示文案同步 `[y/N/e/a/s]`。
  - §9.2.3 **edit_file diff 预览**: 工具成功替换时产出 bounded unified diff 到
    artifacts, 渲染层以 +/- 着色 Panel 展示 (TTY) / 纯文本 (非 TTY)。
  - §9.2.5 **plan mode (只读姿态)**: 新增 `/plan` 开关; plan 模式下写工具
    (write_file/edit_file/bash) 被强制 greylist 需确认 (复用 §4.5 confirm_gate,
    不引新路径, 不碰 blacklist)。banner 显示 `· plan`。
  - 视觉打磨: run()/REPL 横幅与欢迎提示提示 Goal 锁定 + plan mode; 帮助新增 /plan。
- 关键不变量守住了 (未破主流 agent 的 PR-0 防线):
  - `a` 只影响 greylist, **绝不**让 blacklist 不经显式 override 理由就放行。
  - plan_mode 只把写工具从 whitelist 降到 greylist, blacklist 流程不变。
  - 非交互 / --yes 自动确认 Goal, 不阻塞管道 / CI / 测试。
- 实现位置: `cli/app.py` (Goal 卡片 + 确认 + /plan 接线) / `cli/render.py`
  (`render_goal_card` + edit diff) / `cli/responder.py` (`e`/`a` + plan 标注) /
  `core/loop.py` (`plan_mode` + `goal` 属性) / `tools/edit_file.py` (diff 产出)。
- 测试: 新增 `tests/test_cli_interaction_v012.py` (12 测试, 含反例);
  全套 **474 passed, 2 skipped** (原 462 + 12 新)。

### v0.0.7
- 立 §9 交互层投影: 已被证明的 UX 模式 → zall 四维映射
  - 11 个 UX 原语逐一映射到四维, 每条标形态 SETTLED / 具体 PENDING
  - 红蓝对抗抓出 1 条新不变量: **"无 Goal 不调工具"**
    (timeline 中 tool_call_start 前必须有 goal_statement + user_confirm;
     机械可检测, 已加入 AAS 检测器 v0.1-draft-impl)
  - 立 §9.4 自我进化设计槽位: 规则演化 = greylist 动作,
    经 confirm_gate + 审计; 不许 agent 自改 Authority 越界
- 关键立场 (长远性):
  · UX 是投影, 非本体 (PR-4 在交互层的落地)
  · 采纳已被市场证明的 UX 模式, 不发明新 UX
  · 绑在 UX 模式上, 不绑在某一具体产品的当前实现上
- 残余伤口 OPEN (不假装闭合):
  · 11 个 UX 原语的具体 UI 形态全部 PENDING (只立映射, 不画 UI)
  · "极轻量 Goal 锁定"的 UX 边界 OPEN (轻到何种程度算偷渡?)
  · 自我进化的 skill/memory format OPEN
  · "无 Goal 不调工具"不变量已加入 AAS 检测器 (§9.3 红蓝对抗产出, v0.1-draft-impl 交付)
  · subagent Authority 继承协议 PENDING

### v0.0.6 (本版本)
- 推 §5.5 hunk 归属判定机制 OPEN → 形态 SETTLED(机制 A1: 静态 hunk 分类器)
  - 4 个 binary flags: direct_enables / indirect_enables / independent / sabotages
  - 输入: hunk + GoalTriple (经 §3.2.2 暴露的 exposed_dependency_set)
  - 抵抗 Goodhart: 不让 agent 自打标签,纯集合运算 + 静态分析
- **回填 §3.2.2**:TerminationCriterion 必须暴露 `exposed_dependency_set`
  (system_judge 类 GoalType)
  - 此为 PR-0 第 4 次实证:hunk 归属红蓝对抗反推了已 SETTLED 的 §3.2.2
  - 自抓**存量错误增量**——已 SETTLED 的节也要重新被红蓝对抗看
- 引新维度与否的发现:
  - 红方驳出"独立第三方打 flag" 需求,看似要引新维度
  - 蓝方 A1 形态证明只需新机制 + §3.2.2 回填,4 维内可闭合
  - **未引新维度是发现,不是损失**
- 残余伤口 OPEN(都显式标注,不假装):
  - user_judge GoalType 走保守默认(仅含降级后 hunk)
  - transitive 闭包不计算(留边界)
  - sabotages 检测具体算法 PENDING
  - exposed_dependency_set 边界模糊(多文件 GoalType 可能过大)
- 跨维度耦合:
  - sabotages 对治与 §4.4 test_baseline_mutation_rate / §6.3 audit_warning 勾稽
  - hunk 计入 evidence_window 与 §3.4 降级链可计算性 完整接通
- §6.7 表 "hunk 归属" 行翻为 SETTLED-fornow

### v0.0.5
- 推 §6.1 外部可信锚点(§6.5.2)从 PENDING → **形态 SETTLED**(承诺边界严格切割)
- 立 §6.5.1 边界:**external schema HMAC 强模式 vs 外部锚点**,两套独立信任链不合并
- 立 §6.5.2 完整形态:
    · 对手/承诺边界 (agent 进程 ✅ / 同 OS 其他非 root ✅ / user 本人 ❌ / root ❌)
    · 信任根 out-of-band 初始化(防 silent 替换)
    · 锚点接口最小化 (1 个 write_run_tail,只签收到的 hash)
    · 与 RunRecorder 双向闭环 (ack 写回 timeline.jsonl)
    · audit_verify 协议 (链尾 hash + 锚点签字)
- 通过红蓝对抗抓 1 处潜在错误增量:
    · 承诺画太大(假装 adversary-resilient)→ 自驳,降级为"对手/承诺边界" 4 行表
    · 此为 PR-0 在 Verifiability 层的第一次实证
- 通过 PR-3 模型无关检验:
    · ed25519/SHA-256 纯密码学,不依赖任何模型
- 解锁部分 §2.1.4 OPEN:
    · tamper_detected_rate 有了具体算法
    · timeline_integrity_rate 升级为双层 hash+签字
    · non_reproducible_by_construction 补 anchor_unreachable
- 残余 OPEN 标注(不假装闭合):
    · Windows ACL 具体实现 PENDING (跨平台等价语义)
    · 锚点 silent 替换仅靠 out-of-band 人因兜底 OPEN
    · per_run_anchor_interval N=50 缺省凭直觉 OPEN
    · anchor_log 备份/迁移策略 OPEN

### v0.0.4
- 推 §2 评估体系从"5 维雏形"到"metric 形态"细化,
  引 3 条本节元规则(R-Metric 化 A/B/C):
    · 每条 metric 必须可上溯到 §1.2 某项(PR-1 应用)
    · 每 metric 必须通过区分度检验
    · 每 metric 必带配对反指标 (抗 Goodhart's Law)
- 推 5 个评估指标逐条红蓝对抗:
  · §2.1.1 目标达成率(上溯 ①)      — SETTLED
  · §2.1.2 越界率(上溯 ②)            — SETTLED
  · §2.1.3 可证伪率(上溯 ③)          — SETTLED
  · §2.1.4 可复现率(上溯 ④)          — SETTLED
  · §2.1.5 资源效率(跨全部维度)       — **部分 SETTLED 主 OPEN**
- 通过红蓝对抗抓出 1 处潜在错误增量:
  · 资源效率最初计划跨 GoalType 归一化 → 自驳;
    改为"仅 met(含 caveat)的分母 + per GoalType × per main_Judge 切分"。
  · 此为 PR-0 在评估层的第 2 次实证(第 1 次是 v0.0.3 的 caveat 子类型合并)。
- 所有 SETTLED 项配 1 条反指标:
    达成率 ↔ decline_rate
    越界率 ↔ proactivity_rate
    可证伪率 ↔ test_baseline_mutation_rate
    可复现率 ↔ tamper_detected_rate
    资源效率 ↔ shortcut_signal_ratio
- 所有 SETTLED 项标志本体的'+ 切分 + 分布 + 反指标'已落地为可运行的 metric 形态,
  具体 algorithm OPEN(实现工具未成,与 zall 实现并行)。
- §2 增 2.2 派生关系图 + 2.3 集中 OPEN 清单。

### v0.0.3
- 推 GoalType Enum 到 §3.5(**本源 SETTLED-fornow**,扩展机制 SETTLED)
  - BaseTypes 11 种 for coding agent
  - ExtendedGoalType 通过 fallback_to 继承,显式不假装闭集
  - `unknown` catch-all 兜底未知,守 PR-1 不假象闭集
- 立一条 R1 边界修订:"分类不是加戏",标签属元数据,可机械判定
- 回填 §4.4 ask_budget:base_K 表 SETTLED(11 行 for coding agent),
  actual_K 公式 SETTLED,context_factor / max_ask_budget OPEN
- 回填 §5.2 base_judge 表:11 行 main/aux 默认组合 SETTLED,
  运行时降级路径 SETTLED(呼应 §5.4 caveat 子类型)
- 通过红蓝对抗完成 7 项偷渡检查,抓住 1 处真错误增量:
  - 发现 §5.2 把"主不可用"与"主辅不一致"合并为同一 caveat → 修正
  - §5.4 立两个 caveat 子类型:`main_unavailable` / `main_aux_divergent`
- 补三处运行时保障:
  - ExtendedGoalType 在 ConfirmGate 必须透明显继承的 K/Judge(防静默禁言)
  - K=0 类型遇 user_raw 模糊 → 合法路径走 Downgrade 到 unknown(防堵嘴 hijack)
  - §5.2 运行时调节显式调用 caveat 子类型
- 同步更新:
  - §3.3 R1 注解加边界扩散;"分类不是加戏"显式
  - §6.7 交叉点表 2 行状态翻新(ask_budget / GoalType Enum)
  - §5.6 移除 GoalType→Judge 那条 PENDING(已 SETTLED)
  - §2 评估雏形"可证伪率"上方 PENDING 注释更新

### v0.0.2
- 推 Authority 维度到形态 SETTLED (§4):三层手段清单 / Context / ask_budget 形态 / confirm_gate 形态;
  - K 默认值表与 context_judge 函数 PENDING (← v0.0.3 中 K 表已翻 SETTLED)
  - 等价替换义务形成完整"拦 + 替"闭环
  - 斩断跨 run 上下文污染
- 推 Accountability 维度到形态 SETTLED (§5):Judge 主体三选项 / Evidence 形态 / 多 Judge 一致性升级 met_with_caveat;
  - GoalType→Judge 映射 PENDING (← v0.0.3 中 base_judge 表已翻 SETTLED)
  - hunk 归属判定 OPEN
- 推 Verifiability 维度到形态 SETTLED (§6):RunRecorder append-only 链式哈希 / Replay 协议(决策复现) / 诱导式问询取证 / Override 审计 / external schema;
  - "不可篡改"承诺**取消**,降级为"篡改可发现 + 外部锚点"(PENDING)
  - HMAC 强模式 optional
- 立 R-三维同推 风险规则,完成 11 条交叉点清点(§6.7),无假象 SETTLED

### v0.0.1
- 立 PR-0 / PR-1 / PR-2 / PR-3 / PR-4 元规则
- 立 agent 本体论 4 正交维度定义(1.2)
- 立评估体系雏形 5 维度(第 2 节)
- 推 Goal 维度到可固化密度:
  · Goal 三段式(3.2)
  · Goal Refiner 与 R1/R2/R3(3.3)
  · Goal Downgrade 与 R4/R5/R6 + RunEgress 强制双轨汇报(3.4)
  · PR-0 在 Goal 层的 3 处具体落地
- 开 Authority / Accountability / Verifiability 槽位(未展开)

### 待办(下一轮任选其一推进,不混合以免错误增量)
- 推 §6.5 HMAC 强模式可工程化路径(在 §6.5.1 边界已立之后)
- 推 §6.3 R1 诱导式问询的语义级判定(正则 → 语义,依赖模型,延后)
- 推 §6.5.2 外部锚点的 Windows ACL 具体实现(§6.5.2.8 OPEN)
- 第 1-5 metric 的离线 metric 工具实现 PENDING
  (依赖 zall 主体实现跑起来,本轮已 SETTLED 的形态会先成形态,数据校准延后)
- base_K / base_judge 两表的 default 值和 §3.4 downgrade_depth D 默认值,
  均待 zall 跑出实际数据后方可校准;本轮明确不推(凭直觉调直觉违反 PR-1)
- 评估数据集"剔除含 suggestive/override 的 run"具体算法 (§6.3/§6.4 提了原则)
- "近似 Goal hunk 归属"新机制需要在后续单独一轮专门推(可能引入新维度抽象)
- §9 "无 Goal 不调工具"不变量加入 AAS 检测器 (§9.3 红蓝对抗产出, 候选 §B1 补强)
- §9 各 UX 原语的具体 UI 形态 (依赖 S0 主体实现跑起来后投影)
- §9.4 自我进化的 skill/memory format (依赖 §4.5 confirm_gate 形态先落码)

---

## 9 交互层投影: 已被证明的 UX 模式 → zall 四维
        (状态: 映射形态 SETTLED; 具体 UX 形态 PENDING; 红蓝对抗已跑)

### 9.1 设计立场 (状态: SETTLED)

> UX 是本体论的投影, 不是本体论本身。
> 采纳已被市场证明的 UX 模式, 不发明新 UX。
> 绑在 UX 模式上, 不绑在某一具体产品 (eg. Claude Code) 的当前实现上。

三条立场条款:

- **投影非本体** (PR-4 在交互层落地):
  交互层是四维 (Goal / Authority / Accountability / Verifiability) 的呈现投影。
  UX 形态由本体论约束, 不反之。UX 创新不引新维度, 只投影已有维度。

- **采纳已证明的模式**:
  不发明新 UX 原语 (不造新交互范式)。只采纳已在 Claude Code / Cursor / Aider
  等产品中被用户验证的 UX 模式, 但每个模式经四维映射 + 红蓝对抗后才入。

- **长远性**:
  设计绑在 UX 模式 (eg. "permission prompt", "plan mode") 上, 不绑在
  Claude Code 当前实现细节上。Claude Code 换代后, 模式仍在, zall 交互层
  仍成立。这是"长远的"的落地形态。

### 9.2 UX 原语映射表 (状态: 映射形态 SETTLED; 具体 UX PENDING)

11 个已被证明的 UX 原语, 逐一映射。每条标: 来源 / 映射维度 / zall 差异 /
偷渡风险。具体 UI 形态 (布局 / 样式 / 快捷键) 全部 PENDING —— 本节只立映射,
不画 UI (守 PR-4)。

#### 9.2.1 对话输入 → Goal (Refiner)
```
来源:         Claude Code / Aider / 所有对话式 agent
映射维度:     ① Goal (§3.3 Refiner + ConfirmGate)
Claude 形态:  用户自然语言, agent 直接解读, 无显式 Goal 锁定
zall 差异: 用户自然语言 → Refiner 转译 → ConfirmGate 锁定 → run
              UX 多一步"确认 Goal"; 但可轻量 (展示 translation_of + 一次确认)
偷渡风险:     若"确认 Goal"被做成可选或可跳过 → 偷渡回"对话即本体"
              → 必须强制 (见 §9.3 红蓝对抗)
状态:         映射 SETTLED; "轻量但不可选"的 UX 形态 PENDING
```

#### 9.2.2 流式输出 → Verifiability (RunRecorder 投影)
```
来源:         Claude Code / Cursor / 所有流式 agent
映射维度:     ④ Verifiability (§6.1 RunRecorder)
Claude 形态:  stream 是独立通道, 边生成边显示
zall 差异: stream 是 timeline 的只读呈现投影, 不是独立通道
              "意图先于行动" (§6.1): tool_call_start event 先写 timeline,
              再执行, stream 显示 tool_call_start
偷渡风险:     若 stream 绕过 timeline 直接显示 → timeline 不完整 → 偷渡
              → 必须 stream ⊂ timeline (stream 是 timeline 的子集视图)
状态:         映射 SETTLED; 具体投影协议 PENDING
```

#### 9.2.3 工具调用展示 → Authority (三层名单投影)
```
来源:         Claude Code (tool call card)
映射维度:     ② Authority (§4.2 三层名单)
Claude 形态:  显示 tool name + params, 无分层标记
zall 差异: 显示 tool + SafeLevel 标记 (whitelist/greylist/blacklist 判定结果)
              用户看见每个调用的 Authority 判定, 不只看见"它在调工具"
偷渡风险:     若展示不显示 SafeLevel → 用户不知道越界风险 → 偷渡
              → 必须显示 SafeLevel
状态:         映射 SETTLED; UI 标记形态 PENDING
```

#### 9.2.4 权限提示 → Authority (confirm_gate)
```
来源:         Claude Code (permission prompt)
映射维度:     ② Authority (§4.5 confirm_gate)
Claude 形态:  危险工具弹 y/n
zall 差异: greylist  → y/n (accept/reject/modify/timeout)
              blacklist → 不弹 y/n, 弹等价替换建议
              whitelist → 不弹
              差异核心: blacklist 不给 y/n (防 user fatigue 后盲点 y)
偷渡风险:     若 blacklist 也给 y/n → 一键越界 → confirm_gate 形同虚设
              → 必须 blacklist 走等价替换, 不走 y/n
状态:         映射 SETTLED (§4.5 已 SETTLED); timeout 默认值 OPEN
```

#### 9.2.5 计划模式 → Goal (Refiner + ConfirmGate)
```
来源:         Claude Code (plan mode)
映射维度:     ① Goal (§3.3 Refiner + §3.3.4 ConfirmGate)
Claude 形态:  plan mode 可选, agent 提议 plan, user approve
zall 差异: plan mode 不是可选 — 每次 run 启动必经 Refiner + ConfirmGate
              "plan" 内容是 Goal 三段式 (含 TerminationCriterion), 非非正式 step list
偷渡风险:     若 plan mode 可选 → agent 可跳过 Goal 锁定 → 偷渡
              → 必须强制
状态:         映射 SETTLED; "强制但不烦"的 UX 形态 PENDING (见 §9.3)
```

#### 9.2.6 待办列表 → Goal 进度投影 / Accountability
```
来源:         Claude Code (TodoWrite)
映射维度:     ① Goal 进度 + ③ Accountability (§5.2 Judge)
Claude 形态:  agent 自管 todo, 非正式, 打勾 = 完成
zall 差异: todo 是 Goal 进度的呈现层投影; TerminationCriterion 是纯函数
              todo 全打勾 ≠ met; judge 用纯函数, 不用 todo
偷渡风险:     若 todo 全打勾就报 met → 偷渡回"模型自报完成"
              → 必须 judge 用 TerminationCriterion (§B1.2 已落地)
状态:         映射 SETTLED (§B1.2 已守); todo 具体格式 PENDING
```

#### 9.2.7 斜杠命令 → Authority + 技能
```
来源:         Claude Code (/help, /clear, /compact, custom skills)
映射维度:     ② Authority (§4.2) + §9.4 自我进化
Claude 形态:  用户快捷方式, 部分免确认
zall 差异: 斜杠命令是输入快捷方式, 不绕过 confirm_gate
              若命令触发 greylist/blacklist 动作, 仍走 gate
偷渡风险:     若斜杠命令 = 免确认 → 偷渡 Authority
              → 必须仍走 context_judge
状态:         映射 SETTLED; 斜杠命令框架已落地 (v0.0.x); skills 斜杠命令 v0.0.19 已交付
```

交付要点 (v0.0.19 — §9.2.7 skills 斜杠命令):
  - **skill = 可复用 Goal 模板 (输入快捷方式), 不绕 gate**: `/skill <name> [args]`
    展开为 task 文本 → 落回 REPL 对话分支 → 走完整 Goal 锁定 + ConfirmGate。若展开后
    任务触发 greylist/blacklist 动作, 仍走 context_judge + confirm_gate。这是 §9.2.7
    偷渡防线的机械保证 (斜杠命令不是"免确认的宏")。
  - **format (极简 TOML, §9.4 skill format 已定)** — `.zall/skills.toml` 的 `[[skills]]`
    数组 (name / description / prompt), 与 mcp.toml / rules.toml 同源哲学 (IPR-3 仅
    stdlib, 手写解析, 不引 toml 库)。prompt 支持多行 `"""`; 占位符 `{input}` 在调用时
    被参数替换 (无 `{input}` 但带参 → 参数附加到末尾)。
  - **优先级 + 失败安全 (IPR-0)**: 项目级 `.zall/skills.toml` > 用户级
    `~/.zall/skills.toml` (同名覆盖); 文件缺失 / 编码错误 / 坏 TOML / 单个 skill 缺
    prompt → 该 skill 跳过或整文件返回 [] (最坏 **不阻断 agent 启动**)。
  - **命令面**: `/skills` 列出已注册 skills (TTY 用 rich Table, 非 TTY 降级文本) /
    `/skill <name> [args]` 运行 / `/help` 含两项入口。
  - **zall init**: 生成注释态 `.zall/skills.toml` 样本 (review / explain 两个示例)。
  - 改动文件: `skills/__init__.py` + `skills/loader.py` (新包) / `cli/app.py`
    (`_route_skill` 分发 + `_print_skills` 列表 + REPL 加载 + `/skill` 展开走对话分支
    + `/help` 入口 + `zall init` 样本)。
  - 测试: `tests/test_skills.py` (21 测试, 含反例: 多行 prompt / 占位符 / 项目覆盖用户 /
    坏 TOML 失败安全 / 缺 prompt 跳过 / find 大小写不敏感 + 前导斜杠)。
  - 残余 OPEN: skill 的 "Authority 预声明" (§9.4 设想 skills = Authority rules +
    Goal templates) 本版未做 —— 现有 rules.toml 已可声明项目 Authority, 确认路径已守
    偷渡防线; 预声明可作为 v2 增强。

#### 9.2.8 会话记忆 → Context (§4.3)
```
来源:         Claude Code (session history 自动保留)
映射维度:     ② Authority (§4.3 Context)
Claude 形态:  全 session history 自动保留, 跨 run 也自动继承
zall 差异: run 内 history OK; 跨 run 不自动继承 (§4.3 斩断)
              用户可显式回灌 (被审计, 走 user_explicit_artifacts)
              跨 run 时 zall 问"携带上次上下文?" 而非自动继承
偷渡风险:     若跨 run 自动继承 → 跨 run 上下文污染 (§4.3 已点名)
              → 必须显式回灌
状态:         映射 SETTLED (§4.3 已 SETTLED); 回灌 UX 形态 PENDING
```

#### 9.2.9 上下文压缩 → Verifiability (timeline 不可压缩)
```
来源:         Claude Code (auto-compact)
映射维度:     ④ Verifiability (§6.1 RunRecorder) + §7 "上下文策略可换"
Claude 形态:  auto-compact 压缩 session history
zall 差异: 压缩只发生在 model context window (呈现层)
              timeline 不可压缩 (全保真)
              每次压缩是 timeline 一条 context_compaction event
偷渡风险:     若压缩丢掉 timeline 信息 → Replay 不可复现 → 偷渡
              → 必须 timeline 全保真, 压缩只影响 model 看到什么
状态:         映射 SETTLED; 压缩策略接口 SETTLED (Compactor, v0.0.10);
              反应式 auto-compact v0.0.18 已交付
```

交付要点 (v0.0.18 — §9.2.9 反应式 auto-compact):
  - **反应式而非预测式 (PR-3 模型无关的直接推论)**: zall 不预设各模型的确切
    context window 大小 (那是模型相关知识, 会污染核心抽象), 而是靠模型自报
    `stop_reason=LENGTH` 触发压缩。这天然模型无关 —— 换任何模型都对。
    Claude Code / OpenCode 走预测式 (估 token 到阈值就压), zall 走反应式, 二者
    终态一致 (长会话不崩), 但 zall 的触发不绑模型窗口常量。
  - **补齐 loop 缺口**: 旧 `step()` 在 `LENGTH` 分支直接终止 (代码原注释
    "ContextManager not yet implemented")。现: `LENGTH` → `_auto_compact()` 压缩
    model context → 重试一次 `_call_model()`; 若重试仍 `LENGTH` (或无 compactor /
    已无可压缩) → 诚实 UNDECIDABLE 终止 ("could not reduce further")。
  - **timeline 全保真 (§6.1 不变量)**: `_auto_compact` 只替换 `self._messages`
    (model 看到的), 审计 timeline 永不压缩 —— 压缩本身反而是 timeline 上一条
    `CONTEXT_COMPACTION` 事件 (含 reason/compacted_count/strategy/summary_preview,
    链式哈希完整, Replay 可复现)。
  - **失败安全 (IPR-0)**: compactor 抛异常 → 吞掉 + 广播 error 事件 → 返回 False
    → 退回原 LENGTH 终止路径, **绝不让 agent 崩溃** (反例测试验证不崩 + 无
    CONTEXT_COMPACTION 事件写入)。
  - **可插拔 + 向后兼容**: `AgentLoop(compactor=...)` 默认 None → LENGTH 行为与旧版
    完全一致 (既有 loop 测试零改动)。CLI (run + REPL) 注入 `ModelCompactor` →
    默认开启 auto-compact。Compactor 是 §7 策略接口, 可换滑窗/规则折叠等策略。
  - 测试: `tests/test_auto_compact_v018.py` (9 测试, 含反例): LENGTH 触发压缩重试 /
    timeline 记 CONTEXT_COMPACTION / messages 真收缩 / observer 事件 / 无 compactor
    保持旧终止 / 压缩失败安全 / 压缩 0 条不重试 / 压缩后仍爆诚实终止 /
    真实 ModelCompactor 端到端恢复。

#### 9.2.10 子代理生成 → Authority + Accountability
```
来源:         Claude Code (subagent / background agent)
映射维度:     ② Authority + ③ Accountability
Claude 形态:  自由 spawn subagent
zall 差异: subagent = sub-run, 必须自身合规 (有 Goal/Judge/Authority)
              subagent 继承 parent 的 Authority 约束
              (防 parent blacklist rm -rf 后 spawn subagent 绕道)
偷渡风险:     若 subagent 不继承 Authority → 绕道越界 → 偷渡
              → 必须继承 parent Authority
状态:         映射 SETTLED; 继承协议 v0.0.14 已交付 (parent 规则继承 + 子代理更严格)

交付要点 (v0.0.14):
  - `_build_subagent_rules(parent)` 合并 parent Authority + 子代理收紧:
    继承 parent.core_deny_rules (防绕道) + parent.user_local/domain_rules,
    叠加 spawn_subagent→BLACKLIST (防递归) + bash/write/edit→GREYLIST (更严格)。
  - 优先级链保证 (§4.2.1 DENY>GREY>WHITE): parent 白名单写工具被收紧为 GREY,
    parent blacklist 必被继承 (偷渡防护)。子 agent 永不超过 parent 宽松度。
  - 同批修复 subagent 两个预存 bug: 旧 `_subagent_rules` 用过时 Rule/RuleSet API
    (Rule(tool_ids=...)/RuleSet(rules=...) → ValidationError/静默空 RuleSet),
    `Context(cwd_meta=None)` 违反必填约束 → execute 端到端跑不起来。已修。
  - 测试: tests/test_subagent_authority_v014.py (10 测试, 含反例)。
```

#### 9.2.11 MCP 集成 → Authority (工具层)
```
来源:         Claude Code (MCP tools)
映射维度:     ② Authority (§4.2 三层名单)
Claude 形态:  MCP tools 直接可用
zall 差异: MCP tools 走 Authority 三层名单; MCP 是工具来源, 不豁免 Authority
              新 MCP tool 默认 greylist (deny-by-default, §4.2.1)
偷渡风险:     若 MCP tools 自动 whitelist → 新工具未经 Authority 审查 → 偷渡
              → 必须 MCP tools 默认 greylist
状态:         映射 SETTLED (§4.2.1 deny-by-default 已 SETTLED); 注册协议 v0.0.17 已交付
```

交付要点 (v0.0.17 — §9.2.11 MCP 注册协议):
  - **零第三方依赖的 MCP stdio 客户端** (`mcp/client.py`): 仅 stdlib 实现 MCP stdio
    传输的 JSON-RPC 2.0 子集 (initialize / tools/list / tools/call), 不引官方
    mcp SDK (守 IPR-3)。单后台 reader 线程按 id 收响应, server 的 notification 忽略。
  - **MCPTool 包装** (`mcp/tool.py`): 把 MCP server 暴露的一个 tool 包装成 zall Tool
    (实现 tool_id / schema / execute 协议)。tool_id 命名空间化
    `mcp__<server>__<tool>` 防撞名 (满足 ToolRegistry 唯一不变量); OpenAI function
    name 非法字符洗成 `_`, 长度截断到 64。schema 直接复用 MCP 的 inputSchema
    (本就是 JSON Schema); execute 用**原始** MCP 名调 server (tool_id 是命名空间化的)。
  - **配置加载** (`mcp/config.py`): `.zall/mcp.toml` 的 `[[servers]]` 声明
    (name/command/args[]/env{}), 项目级 > 用户级同名覆盖。手写极简解析 (与
    rules_file 同源哲学, 不引 toml 库)。文件缺失 / 编码错误 / 解析异常 → 返回 []
    (失败安全, IPR-0)。
  - **deny-by-default 保证 (§9.2.11 核心)**: MCP 工具**不**被任何代码 whitelist;
    默认 greylist 由 §4.2.1 context_judge 无匹配默认 greylist 机械保证
    (测试 `test_no_rule_match_defaults_greylist` 反例验证: 空规则集下 MCP 工具
    → GREYLIST, 不得 WHITELIST; 用户显式 whitelist 规则可提升, 证明非硬编码)。
  - **失败安全 (IPR-0)**: `_build_mcp_tools` 任一 server 连接/list 失败 → 打
    `[mcp] skip server ...` 警告并跳过, 返回其余工具, **不阻断核心 native agent**
    (测试 `test_bad_server_skipped_returns_empty` 验证返回 [] + 警告)。
  - **生命周期**: run() 结束 / REPL 退出时关闭所有 MCP server 子进程
    (MCPClient.close 幂等, 多工具共享同 client 只关一次, 防子进程泄漏)。
  - **系统提示词**: 已注册 MCP 工具追加进 system prompt 清单 (默认 greylist 明示),
    模型可见可用; 无 MCP 配置时该段不出现 (旧测试断言不受影响)。
  - **zall init**: 生成注释态的 `.zall/mcp.toml` 样本 (默认不启用)。
  - 测试: `tests/test_mcp_registration_v017.py` (17 测试, 含反例) +
    `tests/_mock_mcp_server.py` (真实 stdio 协议 mock, 端到端验证)。
  - 残余 OPEN 已收口 (v0.0.19 — §9.2.11 子 agent 继承 MCP 工具): subagent 不再用
    纯 native `_build_tools()` 收敛, 而是 `_build_subagent_tools(parent_tools)` 继承
    parent 的**完整** registry (含 MCP 工具), 仅排除 `spawn_subagent` 自身 (防递归)。
    MCP 工具的 Authority 仍由 `_build_subagent_rules` 继承 parent 规则决定
    (默认 greylist → 子 agent 无监督时自动 reject; parent 显式 whitelist 的只读 MCP
    工具 → 子 agent 继承可用)。不变量: 子 agent 工具集 ⊆ parent (只减不增), 永不比
    parent 更宽松 (守 §9.2.10 继承语义)。
  - 测试: `tests/test_subagent_mcp_inheritance.py` (含反例: 子 agent 拿不到
    spawn_subagent → 不能再次生成子 agent)。


#### 9.2.12 思考过程 → Verifiability (§6.1 透明投影)
```
来源:         Claude Code (extended thinking) / DeepSeek-R1 / Qwen3-thinking / GLM
映射维度:     ④ Verifiability (§6.1 RunRecorder) + 呈现层投影
Claude 形态:  模型先"想"后"答", 思考过程可见、可审计
zall 差异: reasoning 是 ModelResponse 独立字段, 与 content 分离
              reasoning 不进 PR-0 幻觉判定 (PR-0 只扫 content 伪造工具输出)
              流式 reasoning 增量 → model_thinking 事件 → TTY 实时指示 + 完整思考块
偷渡风险:     若思考过程可被模型用来"伪造已验证" → 偷渡
              → 必须 reasoning / content 两字段分离 + 思考块视觉上从属正式回答
状态:         映射 SETTLED; 投影形态 v0.0.15 已交付 (捕获+分流+渲染, 审计入 timeline)
```

### 9.3 红蓝对抗: "对话即本体" 的偷渡风险 (状态: SETTLED)

本节是 §9 的核心对抗 —— 回答"采纳 Claude Code UX 是否会从正门偷渡 loop+tool 本体"。

**红方论点**:
Claude Code 的 UX 假设"用户随时插话, agent 随时调工具, Goal 是隐式对话意图"。
若 zall 采纳这套 UX, 用户会预期"聊天就能干活", Goal 锁定感觉是"别扭的打断"。
小任务 (eg. "grep foo") 走 Refiner + ConfirmGate 太重, 用户会绕过 (eg. 直接到
对话模式让 agent 干), 绕过后 zall 退化为 Claude Code。

**蓝方反驳**:
zall 的 Goal 锁定不是"打断", 是"确认承诺"。UX 可做到:
用户说完话 → agent 展示"我理解你要做 X, 终止判据是 Y, 确认?" → 一次回车 → 锁定。
这不是打断, 是"确认承诺"。Claude Code 的 plan mode 已证明用户接受"先看 plan
再 approve"的范式。zall 只是把 plan mode 从可选变成必选, 且 plan 内容从
非正式 step list 升级为 Goal 三段式。

**红方再驳**:
但 Claude Code 的 plan mode 是可选的 — 用户可跳过直接让 agent 干。
zall 强制每次都走 Goal 锁定, 对小任务太重。

**蓝方再驳**:
§4.4 base_K 表已对治 — 小 Goal (bugfix/review) 的 K=0, Refiner 不反问。
Goal 锁定 ≠ Refiner 反问; 锁定是"展示 translation_of + 一次确认",
即使 K=0 也走"展示+确认"但无反问。对小任务, "展示+确认"可极轻量
(eg. 一行 "→ grep foo in src/, confirm?" 而非长表单)。

**红方第三次驳**:
"极轻量"的边界在哪? 若轻量到"agent 直接开干, 事后补记 Goal",
就偷渡回"对话即本体"了。

**蓝方终驳 — 抓出不变量**:
边界是机械可检测的:
**timeline 中第一条 tool_call_start 之前必须有一条 goal_statement
且该 goal_statement 经 user_confirm**。

即: agent 可不在用户说完后立即锁定 (多轮对话细化 OK), 但一旦 agent
要调工具, 此刻必须已有锁定的 Goal。这条机械可检测:
- 若 tool_call_start 前无 goal_statement → agent 在无 Goal 下调了工具 → 违规
- 若 goal_statement 存在但无 user_confirm → Goal 未锁定就调工具 → 违规
  (user_confirm 弱模式问题与 §6.5 同根, 走同条 audit 路径)

**此不变量记为 §9 的核心产出, 已加入 AAS 检测器作为 §B1 的补强
(`_tool_calls_without_locked_goal`, v0.1-draft-impl 交付, 6 测试 + mutation 全塌)**。

### 9.4 自我进化: 规则演化机制 (状态: 形态 SETTLED; 读取注入已落地 v0.0.9, format OPEN)

> 运行落地记录 (v0.0.9): `cli/app.py::_read_agents_md` + `_build_system_prompt`
> 已在 run() 与 REPL 启动读 `<cwd>/.zall/AGENTS.md` 并注入 system prompt 的
> `PROJECT MEMORY` 段 (只读投影)。文件缺失/读取异常静默跳过 (守 IPR-0 反例)。
> 仍 OPEN: AGENTS.md 具体 format (§9.4 "project memory 的具体 format OPEN"),
> 以及 agent 运行时改 AGENTS.md = greylist 动作 (走 confirm_gate + 审计, 未接)。

> agent 可修改自身规则, 但每次修改是 greylist 动作, 经 confirm_gate + 审计。
> 不许 agent 自改 Authority 来越界。

**Claude Code 的化身**: skills (可学习工作流) + CLAUDE.md (项目级记忆, agent 可读写)。

**zall 的自我进化设计**:

- **skills = Authority rules + Goal templates**
  可复用的 Goal 三段式 + 配套 Authority 清单。skill 不是"免确认的宏",
  是"预填的 Goal + 预 declared 的 Authority"。调用 skill 仍走 ConfirmGate。

- **project memory = 类似 CLAUDE.md**
  agent 可读写, 但每次修改 = greylist 动作 (修改自身规则是高风险),
  经 confirm_gate, 记入 timeline。

- **演化反馈 = §C metrics 反馈**
  若某条 skill 关联的 run 的 goal_achievement_rate 下降, agent 必须 *提示*
  (不自动删除, 只提示)。用户决定是否废弃该 skill。
  反 Goodhart: agent 不许通过修改 metric 定义来美化自己的指标
  (§C3 test_baseline_mutation_rate 已守这条, §4 测试文件已纳入 greylist/blacklist)。

**关键不变量**:
agent 修改自身规则 (Authority rules / skills / project memory) = greylist 动作,
必须经 user confirm_gate, 且记入 timeline。
此条从 §4.5 + §6.4 直接推得, 不需新机制, 形态 SETTLED。

**残余 OPEN**:
- skill 的具体 format (eg. YAML / TOML / Python) — **v0.0.19 已定**: 极简 TOML
  (`.zall/skills.toml` 的 `[[skills]]`, name/description/prompt, 多行 `"""` prompt +
  `{input}` 占位符), 与 mcp.toml / rules.toml 同源哲学; 项目级 > 用户级同名覆盖
- project memory 的具体 format OPEN
- 演化算法 (eg. 何时提示废弃, 阈值) OPEN
- "agent 提示废弃 skill" 本身是否算 suggestive (§6.3 诱导式问询) OPEN

### 9.5 残余伤口 (OPEN / PENDING 集中)

- 🩹 11 个 UX 原语的具体 UI 形态全部 PENDING (只立映射, 不画 UI, 守 PR-4)
- 🩹 "极轻量 Goal 锁定"的 UX 边界 OPEN
  (§9.3 红蓝对抗已立机械检测边界, 但 UX 呈现形态未定)
- ~~🩹 "无 Goal 不调工具"不变量待加入 AAS 检测器 PENDING~~
  (**v0.1-draft-impl 已交付**: `_tool_calls_without_locked_goal` 检测器 +
  6 测试 + mutation test 全塌, 见 `tests/spec/`)
- 🩹 自我进化的 skill/memory format OPEN
- ✅ subagent Authority 继承协议 (**v0.0.14 已交付**: parent 规则继承 + 子代理更严格; 同批修 2 个预存 bug)
- ✅ MCP 注册协议 (**v0.0.17 已交付**: 零依赖 stdio 客户端 + MCPTool 包装 + config 加载 + deny-by-default + 失败安全)
- ✅ 反应式 auto-compact (**v0.0.18 已交付**: LENGTH → 压缩重试, 模型无关, timeline 全保真, 失败安全)
- 🩹 §9.4 "agent 提示废弃 skill"是否算 suggestive (§6.3) OPEN
- 🩹 子 agent 继承 MCP 工具 (v0.0.17 暂未做, native _build_tools 收敛) OPEN

**总结**: 本节把交互层从"未开槽"推到"映射形态 SETTLED + 红蓝对抗已跑"。
所有 UX 原语都有四维落点, 偷渡风险都有对治。但具体 UI 形态全部 PENDING ——
按 PR-4, UI 是投影, 投影在主体 (S0) 落地后才画。本节的长远性在于:
绑在 UX 模式上 (不绑在 Claude Code 当前实现上), 且每个模式都经红蓝对抗
确认不偷渡本体。Claude Code 换代后, 模式仍在, zall 交互层仍成立。
