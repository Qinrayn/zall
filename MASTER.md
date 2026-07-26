# zall · Master Design Document

> 项目名: zall
> 定位: 面向未来、基于现在;整合 coding agent / 具身智能 / 科研探索 / 多 agent 协作;
>      从第一性原理构建可演进的 agent 框架。
> 零点: 不依赖任何现存 agent 框架，从第一性原理自建。
> 性能: 把"性能"做成可度量的工程指标，不是口号。
> 模型: 模型无关。任何模型相关细节不得泄漏到核心抽象之外。
> 野心: agent 行业无评估标准,zall 的本体论与评估体系一并定义,
>       成为可被参照的标杆。
> 演进: 本框架设计为"可演进"——当前设计不是终点,而是可以被未来范式
>       吸收和重新解释的起点。

---

## 文档组织

本文件是 zall 项目的**单一真相来源**。所有设计决策、架构定义、演进路线、
生态策略在此统一。其他文档（DESIGN.md、FUTURE_ARCH.md、IMPL.md）是本文档的
历史投影或子集，以本文档为准。

### 文档版本

| 版本 | 日期 | 内容 | 状态 |
|---|---|---|---|
| v1.0 | 2026-07-19 | 统一本体论 + 架构 + 演进 + 生态 + 迭代 | PENDING (待红蓝对抗) |
| v1.1 | 2026-07-19 | 现实对齐修订: 外部代码审计结果回填 (§12), 装饰性抽象降级, 执行路线 E0-E3 | ACTIVE |

> **v1.1 修订原则**: 本次修订不改动本体论 (§1) 和理论根基 (§2) 的任何论断——
> 它们在概念层面经受住了外部审计。修订的对象是**宣称与实现的落差**:
> 凡代码审计证实"接口存在但闭环断裂"的能力,其对应文档条目一律降级,
> 并在 §12 建立唯一的执行真相表。这是 PR-0 (自证伪) 对本文档自身的执行。

### 文档状态规则

- **SETTLED**: 已经过红蓝对抗验证,作为后续工作的前提
- **PENDING**: 形态已开,但运行前提未补齐,严禁当 SETTLED 用
- **OPEN**: 残余伤口,记着但未决,后续步骤处理

凡标 PENDING/OPEN 的内容,**禁止**作为下游结论的依据。

---

## 目录

- [0 文档元规则](#0-文档元规则)
- [1 本体论: agent 是什么](#1-本体论agent-是什么)
- [2 理论根基: 从 FEP 到可验证推理系统](#2-理论根基)
- [3 评估体系](#3-评估体系)
- [4 架构设计](#4-架构设计)
- [5 统一循环](#5-统一循环)
- [6 领域扩展](#6-领域扩展)
- [7 演进路线图](#7-演进路线图)
- [8 生态建设](#8-生态建设)
- [9 迭代策略](#9-迭代策略)
- [10 不变量](#10-不变量)
- [11 与现存框架对比](#11-与现存框架对比)
- [12 执行真相表与路线 (v1.1)](#12-执行真相表与路线-v11)
- [13 自优化循环: AI 驱动的持续自我改进](#13-自优化循环-ai-驱动的持续自我改进-ai-self-optimization)
- [附录 A: 术语表](#附录a-术语表)
- [附录 B: 与代码的映射](#附录b-与代码的映射)

---

## 0 文档元规则

### PR-0 自证伪义务 (Self-Falsifiability)
agent 产生的任何论断——包括目标声明、完成声明、规划、自我评估——
都必须能被自身或外部证伪。不能被证伪的输出视为幻觉。
本规则作用于设计文档本身: 本 MASTER.md 的每一条论断必须可被红蓝对抗驳回,
驳不倒才保留。

### PR-1 增量但不许错误
宁可慢一步不写入未经验证的设计,也不许录入"看起来对"的错误增量。
本 MASTER.md 的每节都标状态 (SETTLED/PENDING/OPEN)。
凡标 PENDING/OPEN 的内容,禁止作为下游结论的依据。

### PR-2 一步步来
不一次性定稿;每步可质疑、可回退。本文档的每个版本只反映
"已被推到可固化密度"的内容,不反映计划。

### PR-3 模型无关
任何"模型"字样在第 0/1 节 (本体论 + Commitment 维度) 中,
只能出现在"模型无关"语境,不许出现在定义本身。
任何依赖模型行为 (如 hallucination 倾向) 的规则,
必须写成"Goal 类型决定判据来源"的形式,不得写死模型行为。

### PR-4 从定义长出实现,而非反之
模块、命名、架构图一律延后;先立本体论,本体不稳不画架构。
本文档先立本体论 (Part 1),再出架构投影 (Part 4)。

### FR-0 统一性原则 (Unification)
任何新引入的抽象必须同时适用于具身和非具身场景。
如果一条规则只适用于 coding agent 或只适用于机器人,它是领域特化,
不是本体论。领域特化放在扩展层 (Part 6),不得进入核心 (Part 4 §1)。

### FR-1 物理不可逆性原则 (Physical Irreversibility)
在物理世界中,动作不可逆 (文件可 git stash,人撞了不能撤销)。
核心层的所有安全机制必须按最坏场景 (物理伤害) 设计,而非最佳场景 (代码可回滚)。
这条决定了 Authority 和 Accountability 的默认严格度。

### FR-2 感知不确定性原则 (Perceptual Uncertainty)
感知是有噪声的、不确定的、可被欺骗的。
核心层不得假设"观察=事实"。所有从感知到状态的映射必须携带置信度。
这条引入了 World Model 维度的必要性。

### FR-3 科研友好原则 (Science-Friendliness)
框架必须原生支持"假设驱动的探索循环"。
负结果和正结果同等珍视。假设可证伪、可版本化、可追溯。
这条决定了 Goal 和 Accountability 的扩展方向。

### FR-4 分层严谨性原则 (Tiered Rigor)
内部严谨 ≠ 外部复杂。
核心层保持最简抽象 (≤6 维),领域扩展和 UI 层按需暴露复杂度。
日常任务零摩擦,关键任务全验证。

### IPR-0 自证伪义务的代码形态 (对应 PR-0)
每个 SETTLED 节具化为 ≥1 个 invariant 测试。测试必须包含反例——
断言"该不变量在何输入下不成立",并对应其失败到文档哪条被偷渡。
CI 检测: 任何修改实现让 invariant 失效的 PR,测试必须 fail。

### IPR-1 增量但不许错误的代码形态 (对应 PR-1)
每段实现代码必须对应文档中某一条 SETTLED/PENDING 条目的回填。
来自 SETTLED 条目 → 直接落码;来自 PENDING 条目 → 落码前先在文档把它转 SETTLED 或 OPEN。
不许凭直觉写无文档对应的代码。CI 检测: 每段 PR 描述里必须显式引用文档章节号。

### IPR-2 一步步来的代码形态 (对应 PR-2)
每轮提交不跨步: 单次 implement step 仅落 1 个 primitive + 它的 invariant 测试。
禁止"顺手多写一点"。

### IPR-3 模型无关的代码形态 (对应 PR-3)
`src/zall/core/` 下不得 import 任何模型 SDK。
仅 4 个例外: cryptography (ed25519/HMAC)、pydantic (schema 验证)、
pytest (测试)、stdlib。CI lint: 任何 core 模块文件出现禁用 import → fail。

### IPR-4 不画架构图先立本体 (对应 PR-4)
在 primitive 全部 SETTLED 前,不许写综合编排 (agent.py 主 Loop)。
"Primitive SETTLED" 的标志: Interface 已编译通过 + invariant 测试 fail 在反例存在时通过。

---

## 1 本体论: agent 是什么

### 1.1 行业诊断: 所有现存框架的共同盲区

| 框架家族 | 它们把 agent 当作 | 盲区 |
|---|---|---|
| Coding agent (Claude Code/OpenHands) | 对话 + 工具循环 | 无承诺、无目标体系、无可证伪 |
| 具身 agent (RT-2/Figure/Tesla) | 策略网络/感知-控制流水线 | 无记忆、无安全、无目标 |
| 科研 agent (Elicit/ChemCrow/AI Scientist) | 文献综合/工具调用/论文生成 | 无假设管理、无实验闭环 |
| 认知架构 (BDI/SOAR/ACT-R) | 理性主体/状态搜索 | 无工具使用、不可扩展、符号落地问题 |
| LLM agent (ReAct/AutoGen) | 推理轨迹 + 工具调用 | 无承诺、不可靠、无验证 |
| 可微分编程 (JAX/Neural Fields) | 可微分计算图 | 无身份、无承诺、无审计、不可验证 |
| 集体智能 (Swarm/MARL) | 群体涌现 | 无个体承诺、不可编程、不可控 |

**共同错误**: 它们把"agent 如何工作" (机制) 当成了"agent 是什么" (本体)。

### 1.2 第一性定义: agent 是一个有承诺的感知-行动系统

> **Agent = (Identity, Commitment, Perception, Authority, Accountability, Verifiability)**
> 六个正交槽位,各自回答一个不同问题,缺一不可:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ① Identity        它是谁 (身份 + 具身形态)                              │
│  ② Commitment     它承诺做什么 (目标 + 终止 + 验收)                      │
│  ③ Perception     它如何理解世界 (感知 + 世界模型 + 置信度)               │
│  ④ Authority      它被允许用什么手段 (权限 + 边界)                       │
│  ⑤ Accountability 做到什么程度算完成 (判定 + 责任 + 证据)                │
│  ⑥ Verifiability 全过程可被第三方独立复核 (审计 + 可复现 + 溯源)         │
└──────────────────────────────────────────────────────────────────────────┘

缺 ①  agent 无身份 → 无法归因、无法追责、多 agent 时身份混淆
缺 ②  agent 无承诺 → 退化"对话即本体",不知道何时停
缺 ③  agent 盲目行动 → 在不确定世界中不可靠 (具身/科研致命)
缺 ④  agent 会越界 → 物理世界: 伤人; 数字世界: 删库
缺 ⑤  agent 自称完成无法证伪 → "我觉得改好了"
缺 ⑥  agent 是黑盒 → 出错无法归因,改进无从下手
```

六条是**抽象槽位**,具体实例化由领域 (软件 agent / 具身 agent / 数学证明 agent) 填充。
本文档在领域层默认填充"coding agent",但保持抽象形态普适。

### 1.3 红蓝对抗记录 (为何 6 条没塌)

- **"Identity 是 Authority 的子集?"** → 否。Counter-example: 同一身份 (如"厨房机器人")
  可以在不同权限模式下运行 (清洁模式/烹饪模式)。身份是"我是谁",权限是"我被允许做什么"。
  正交保留。

- **"Perception 是 Commitment 的输入?"** → 半条认输并已修正。原"感知只是状态输入"
  假设了"感知是可靠的"。修正为 FR-2: 感知必须携带不确定性,World Model 负责从噪声观测
  估计状态。Perception 是独立槽位。

- **"Verifiability 是 Accountability 的影子?"** → 否。
  Accountability 判结论 (谁担责); Verifiability 判结论的可重做性 (任何第三方拿轨迹都得同结论)。
  前者解决信任,前者解决判定。正交保留。

- **"Learning 为何不是第七维?"** → 学习是增强,不是定义。一个不学习的 agent 仍然是 agent
  (如传统规划器)。学习是跨维度的能力增强机制,放入扩展层。

- **"FEP (自由能原理) 是否让 Commitment 维度多余?"** → 半条认输并已修正。
  FEP 的"偏好先验"确实可以重新定义 Commitment,但"偏好"是内禀的统计属性,
  而"承诺"是社会契约——前者无法被第三方验证,后者可以。在工程场景中 (agent 需要
  对用户负责),承诺不能简化为偏好。保留 Commitment 维度,但增加与 FEP 的映射 (见 §2)。

- **"6 条普适性?"** → 承认是抽象槽位,实例化由领域填充。coding agent 的 Identity = 工具集 + API 身份;
  具身 agent 的 Identity = 身体 schema + 传感器。

#### 1.3.1 红蓝对抗记录 (v1.1 真实交互 dogfood, 2026-07-19)

v1.1 落码后用真实 API + GF-consistency 真实科研数据做了首次端到端 dogfood。
以下是真实使用驳倒/确认的设计假设 (E3.7):

- **"Accountability 默认 empty 是合理的"** -> **半条认输**。真实运行显示
  `undecidable · no judge` 是默认状态。多 Judge 编排虽接通 (E5), 但 AgentConfig
  默认 `judge=None`, 大多数一次性任务无判定。这不是 bug--一次性问答确实不需要
  system judge--但 UI 显示 "undecidable" 对用户有误导性 (像失败)。
  **修正**: UI 应区分 "no judge configured (OK for Q&A)" vs "judge ran, undecidable"。
  已修正 (v1.1): _render_judge() 检查 reason 字段区分 "no judge" 与真正 undecidable;
  render_egress_summary() 新增 judge_ran 参数区分两种场景。渲染层显示中性标记
  "· no judge (Q&A mode)" 替代 "○ undecidable"。

- **"Science Kit 假设管理在真实科研中可用"** -> **确认**。用 GF-consistency 的
  H1 (Spectral G-F=0.163 > baseline 0.128, confirmed) / H2 (n=11 P=0.056, falsified)
  / H3 (n=25 P=1.58e-5, revised from H2) 跑通完整闭环, 含 NegativeResult (I-10)。
  revise() 的版本链 (v1->v2, revised_from) 真实反映了"证伪后修订"的科研实践。

- **"Hypothesis.lock() 后不可改太硬"** -> **未驳倒**。dogfood 中未触发 lock,
  因为 CLI 创建的假设都是 PROPOSED 状态。lock 的语义在"提交假设进入正式验证"时
  才有意义, 当前 CLI 还没暴露 lock 命令。降为 OPEN, 待 lock 命令落地后再判。

- **"Evidence.supports 是 bool 够用"** -> **确认 (暂时)**。GF-consistency 的证据
  都是二分 (显著/不显著, 支持/反驳)。但更细粒度的科研 (如 "部分支持") 可能需要
  float。当前 bool 不阻碍使用, 保留。

- **"Verifiability timeline 可第三方独立复核"** -> **确认**。加载真实 session 的
  13 事件 timeline (goal_downgrade -> ... -> anchor_ack), verify_chain() 返回 True。
  这是 zall 对外宣称的杀手场景, 在真实运行中验证有效。

- **"perception anomaly 检测在真实运行中工作"** -> **确认但有噪声**。真实运行中
  perception 把 "72 modified files" (git 工作区预存状态) 识别为 anomaly 触发了
  熔断提示。这不是 bug (确实有大量文件改动), 但对"继承自脏工作区"的场景过于敏感。
  **修正**: anomaly 的 "modified_files > 50" 阈值应在 run 启动时记录基线, 只检测
  run 期间新增的改动。
  已修正 (v1.1): CodingWorldModel.anomaly() 使用 self._baseline_modified 基线;
  AgentLoop.__init__ 调用 _init_baseline_modified() 记录启动时 git modified 文件数并
  设置到世界模型。仅当 current - baseline > 50 时触发 anomaly; baseline > 50 且无新
  增时不触发。

### 1.4 六维的领域实例化

| 维度 | Coding Agent 实例 | 具身 Agent 实例 | 科研 Agent 实例 | 集体智能 (群体化) |
|---|---|---|---|---|
| **Identity** | 工具集 + API key + 用户身份 | 身体 schema + 传感器 + 执行器 | 研究者身份 + 实验室权限 + 仪器 | 群体身份 + 角色 + lineage |
| **Commitment** | 修复 bug X | 把杯子从 A 移到 B | 验证假设 A: X 抑制 Y | 共享目标 + 委托承诺 |
| **Perception** | 文件内容 + git 状态 + 测试结果 | 摄像头 + 力矩 + 位置 + 语音 | 实验数据 + 文献 + 仪器读数 | 分布式感知 + 共享世界模型 |
| **Authority** | 文件读写 + bash + 网络 | 关节速度 + 力矩 + 移动范围 | 试剂 + 仪器 + 数据库 + 计算资源 | 委托权限 + 角色审批 |
| **Accountability** | 测试通过 + 用户确认 | 传感器确认位置 B + 无碰撞 | 统计显著性 + 可复现 + 同行评审 | 交叉验证 + 声誉系统 |
| **Verifiability** | 链式哈希 timeline + git | 传感器数据哈希 + 动作日志 | 实验 protocol + 数据溯源 + 代码 | 跨 agent timeline 关联 + 共识审计 |

### 1.5 Commitment 的特殊地位: 从 Goal 到 Hypothesis

在 coding agent 中, Commitment 是 GoalTriple (GoalStatement + TerminationCriterion + AcceptanceContract)。
在科研 agent 中, Commitment 扩展为 HypothesisGoal:

```
HypothesisGoal = {
    hypothesis: Hypothesis,        # 假设 (claim + confidence + version + status)
    prediction: Prediction,        # 预测 (如果假设成立,应观察到什么)
    experiment: ExperimentGoal,    # 验证实验
    status: proposed | testing | confirmed | falsified | revised,
}
```

Hypothesis 是 Commitment 的一等公民——它有版本、状态、证据、衍生关系。
假设可以被证伪、修订、合并、分支——像代码的 git 分支管理。

**关键设计**: Hypothesis 不是"第五维",而是 Commitment 维度的领域特化。
这保持了六维的简洁性,同时允许领域扩展。

---

## 2 理论根基: 从 FEP 到可验证推理系统

### 2.1 定位声明

本 Part 不定义 agent 的本体论 (那是 Part 1 的工作),而是回答:
**"为什么这个本体论是对的? 它的理论基础是什么? 它如何与更根本的智能理论对接?"**

这是 zall 与其他 agent 框架的关键差异——我们不只说"agent 应该这样设计",
我们解释"为什么 agent 必须这样设计"。

### 2.2 自由能原理 (FEP): agent 的物理学

Karl Friston 的自由能原理 (Free Energy Principle, FEP) 提供了一个比"目标导向感知-行动系统"
更根本的智能定义:

> **智能 = 最小化预期不确定性的自组织系统。**

FEP 的数学框架 (变分贝叶斯推断 + 马尔可夫毯) 解释了为什么 agent 的六维本体论
不是任意的工程选择,而是智能系统的**必要结构**。

#### 2.2.1 FEP 与六维的映射

| zall 六维 | FEP 对应 | 重新定义 |
|---|---|---|
| **Identity** | 马尔可夫毯 (Markov blanket) | Identity 不再是"身份声明",而是"系统的统计边界"——系统是什么,由它的马尔可夫毯定义。 |
| **Commitment** | 偏好先验 (preference prior) | 承诺不是"对外宣布的目标",而是"系统生成模型中隐含的偏好分布"。目标从外部输入变成了系统内禀属性。 |
| **Perception** | 预测编码 (predictive coding) | 感知不再是"传感器管道",而是"生成模型的预测-误差修正循环"。感知是主动预测,不是被动接收。 |
| **Authority** | 马尔可夫毯约束 | 权威不再是"权限列表",而是"系统的物理边界——行动只能通过马尔可夫毯影响外部世界"。 |
| **Accountability** | 自由能最小化的收敛性 | 责任不再是"是否完成目标",而是"系统是否维持了自身组织——自由能是否持续降低"。 |
| **Verifiability** | 自由能轨迹的可重放性 | 可验证性不再是"链式哈希时间线",而是"系统行为是否一致地最小化自由能"。 |

#### 2.2.2 FEP 对 agent 范式的三个根本贡献

1. **目标从外部输入变成了系统内禀属性**。在 agent 范式中,目标是用户给的,
   系统本身没有"自己的目标"。在 FEP 中,偏好是系统生成模型的一部分,
   是系统"存在"的体现。这解决了 agent 范式中"目标从哪里来"的归因问题。

2. **感知和行动统一为一个过程**。在 agent 范式中,感知和行动是分离的管道
   (感知→规划→行动)。在 FEP 中,感知和行动是同一个自由能最小化过程的两个方面
   (感知调整模型,行动调整世界)。这提供了更优雅的统一理论。

3. **探索的内在动机被解释**。在 agent 范式中,探索需要外部奖励函数或人工设计的
   探索策略。在 FEP 中,探索 (信息获取) 本身就是目的,因为减少不确定性直接对应于
   自由能最小化。这自然解释了为什么智能系统会探索、好奇、实验。

#### 2.2.3 FEP 的工程局限 (诚实声明)

FEP 是"agent 的物理学",不是"agent 的工程学"。它有三大工程局限:

1. **计算不可行**: 预期自由能的计算在连续、高维状态空间中是 NP-hard 级别的。
   即使使用近似方法,计算量也远超真实世界应用的要求。

2. **与 LLM 的结合未解决**: LLM 是自回归的 (预测下一个 token),
   不是 FEP 意义上的生成模型 (预测感官输入)。如何将 LLM 与 FEP 结合,
   是当前最活跃但最困难的前沿方向。

3. **偏好先验的来源未解决**: FEP 说"偏好先验是系统生存状态的涌现",
   但没有说明这个涌现过程如何工程化。在 agent 范式中,目标由用户指定——简单但有效。
   在 FEP 中,偏好先验从哪来?如果我们要用 FEP 构建一个 real 系统,
   偏好先验需要人工设计,这又回到了 agent 范式的老问题。

**结论**: FEP 是 zall 的**理论基础**,不是实现方案。zall 的六维本体论是 FEP 在
工程层面的投影——我们接受 FEP 的理论洞察 (身份=边界、目标=偏好、感知=预测),
但工程实现上保持 agent 范式的可操作性。

### 2.3 可验证推理系统: agent 的未来形态

综合七个前沿方向 (世界模型+搜索、神经符号、测试时计算、FEP、可微分编程、
集体智能、程序合成+自我改进),我们认为 agent 范式的未来形态是:

> **可验证推理系统 (Verifiable Reasoning System) = 神经直觉 × 符号验证 × 世界模型模拟 × 测试时搜索**

#### 2.3.1 四支柱

| 支柱 | 功能 | 对应 zall 维度 | 来源范式 |
|---|---|---|---|
| **神经直觉** | 生成候选解、理解模糊性、处理开放性 | Perception (World Model) | LLM / World Models |
| **符号验证** | 保证正确性、维护一致性、提供可解释性 | Accountability (Judge) | 神经符号 / 程序合成 |
| **世界模型模拟** | 预测行动后果、想象未来、减少真实交互 | Perception (World Model) | 世界模型+搜索 |
| **测试时搜索** | 在推理空间中搜索更优路径,用计算换质量 | Commitment (规划) | 测试时计算扩展 |

#### 2.3.2 这个范式如何吸收 agent

Agent 不会被这个范式替代,而是被**编译**——用户仍然说"帮我做 X",
但 agent 内部的推理过程不再是"LLM 单次生成+工具调用",而是被编译成:

1. 神经直觉生成候选方案
2. 符号验证器检查正确性
3. 世界模型模拟预测后果
4. 测试时搜索找到最优路径
5. 符号验证器最终确认

**Agent 从"推理者"变成了"编译器"**——它把用户的自然语言意图"编译"成可验证的推理过程。

#### 2.3.3 Agent 范式 vs 可验证推理系统

| 维度 | Agent 范式 (当前) | 可验证推理系统 (未来) |
|---|---|---|
| 推理方式 | 单次前馈 (CoT 是线性的) | 神经×符号循环 (生成→验证→精炼→再验证) |
| 正确性保证 | 概率性 ("我觉得对") | 形式化 ("验证器证明了对") |
| 规划方式 | 语言推理 (在 token 空间中走) | 潜空间规划 (在想象中搜索最优路径) |
| 学习方式 | 试错 (真实环境交互) | 想象训练 (在模拟中优化) |
| 探索动机 | 外部奖励驱动 | 内禀+外禀 (信息获取本身有价值) |
| 目标来源 | 外部给定 | 偏好先验 + 形式规格 |
| 错误处理 | 重试/回滚 | 验证器定位错误步骤,定向修正 |

#### 2.3.4 六维本体论在新范式下的命运

| 维度 | 当前定义 | 新范式中的重新定义 | 状态 |
|---|---|---|---|
| **Identity** | 身份+具身形态 | 马尔可夫毯边界 + 能力签名 | 保留+增强 |
| **Commitment** | Goal 三段式 | 形式化规格 + 偏好先验 | 保留+形式化 |
| **Perception** | 传感器+世界模型 | 层级预测编码 + 主动感知 | 保留+深化 |
| **Authority** | 三层名单+门 | 符号约束 + 行动空间裁剪 | 保留+符号化 |
| **Accountability** | 三态 Judge | 形式化验证结果 + 因果归因 | 保留+严格化 |
| **Verifiability** | 链式哈希+重放 | 证明轨迹+因果溯源+时间线 | 保留+增强 |

**关键洞察**: 六个维度**全部保留,无一被淘汰**。但每个维度的含义从"工程抽象"
升级为"可验证推理"。这是 zall 框架最大的资产——六维本体论无意中捕捉到了
agent 的本质结构,使得它天然兼容从 Agent 1.0 到 Agent 3.0 的演进。

---

## 3 评估体系

### 3.0 元规则 (本节内,优先于 §3 后续具体 metric)

评估维度的风险是"指标替换本体": metric 一旦被当成定义本身,
zall 就退回现存 agent 把 loop+tool 当本体的同一错。
本节加 3 条元规则,优先于 §3 各项具体 metric:

- **R-Metric 化 A**: 每条 metric 必须可上溯到 §1.2 某项,否则降 OPEN。
- **R-Metric 化 B**: 每条 metric 必须通过区分度检验——
  若该指标对"完成 Goal 的不同方式无区分度" (eg. 裸工具调用次数),
  降为 OPEN,不进 SETTLED。
- **R-Metric 化 C**: 每条 metric 必带"配对反指标",
  对抗 Goodhart's Law (agent 按指标优化而非按 Goal 优化)。
  缺反指标者降 OPEN。

### 3.1 5 个评估维度 (本体上溯)

每条 metric 在本体上溯、区分度、反指标三项上得到验证才标 SETTLED。

#### 3.1.1 目标达成率 (上溯 §1.2 ② Commitment; 状态: PENDING)

```
分母: 已终结 run = 总 run - DeclineTask 数 - 仍跑 run 数
      (declined 是诚实退让,按 §3.3 不算完成失败;与达成率分开统计)

切分: per GoalType (BaseGoalType 11 + unknown + ExtendedGoalTypes)

分子三态:
    goal_achievement_rate_pure(GoalType)         = met / 已终结 run
    goal_achievement_rate_with_caveat(GoalType)  = met_with_caveat / 已终结 run
    goal_non_achievement_rate(GoalType)           = not_met / 已终结 run

约束: 三率之和 = 1 (排除 declined/ongoing 后,该 GoalType 下)

反指标 (配 R-Metric 化 C):
    decline_rate(GoalType) = declined / 已终结 run 数
    high pure 达成率 + high decline_rate = Goodhart 可疑信号
    high pure 达成率 + low decline_rate = 健康信号

区分度 (验 R-Metric 化 B): PASS
    同一 GoalType 下确有完成 / 未完成二分。

残余 OPEN:
    - ExtendedGoalType 在 token 分布稀疏时统计噪声大,样本量阈值 OPEN
    - 三率之和=1 的离线 metric 工具实现细节 PENDING
    - "仍跑"vs"已终结"边界与 caveat 关联,PENDING
```

#### 3.1.2 越界率 (上溯 §1.2 ④ Authority; 状态: PENDING)

```
分母: per GoalType 下该 run 的总工具调用数

四层子率 (分母相同):
    whitelist_action_rate       = whitelist_calls / total
    greylist_consent_rate       = greylist_calls_passed_gate / total
    blacklist_intercept_rate    = blacklist_blocked_or_equivalenced / total
    override_after_audit_rate   = user_approved_blacklist_calls / total

反指标 (配 R-Metric 化 C):
    proactivity_rate = (无 user 介入的成功 autonomous 动作) / total calls
    high greylist_consent + high proactivity = 健康 (主动但不越界)
    low greylist_consent + high blacklist_intercept = 危险 (agent 总撞红线)

区分度 (验 R-Metric 化 B): PASS
    不同 agent / 不同 GoalType 下越界率确有差异。

残余 OPEN:
    - proactivity 中"autonomous"定义边界 OPEN (user 没说话是否算 autonomous?)
    - Override 后等价替换是否计入 greylist_consent_rate,OPEN
```

#### 3.1.3 可证伪率 (上溯 §1.2 ⑤ Accountability; 状态: PENDING)

```
分母: 已终结 run (与 3.1.1 一致排除 declined/ongoing)
切分: per GoalType

分子四类 (run 终结时):
    falsifiable_by_system_rate     = system Judge 实际跑了判定 / 分母
    falsifiable_by_user_only_rate  = user Judge 完成确认 / 分母
    falsifiable_with_caveat_rate   = met_with_caveat (任何 caveat 子类型) / 分母
    unfalsifiable_rate             = RunEgress 仅出 undecidable / 分母

约束: 四率之和 = 1 (per GoalType)

反指标 (配 R-Metric 化 C):
    test_baseline_mutation_rate = (agent 改测试基线事件数) / 已终结 run 数
    high falsifiable_by_system_rate + high test_baseline_mutation_rate = 假阳性嫌疑
    high falsifiable + low baseline_mutation = 健康信号
    (此反指标与 §4 greylist/blacklist 联动;测试文件已纳入 greylist/blacklist)

区分度 (验 R-Metric 化 B): PASS
    不同 GoalType 主 Judge 不同,机械可判 vs 用户可证,确有差异。

残余 OPEN:
    - 4 子率 sum=1 的离线实现 PENDING
    - user_only 中 user "确认" 是真确认还是默认 accept 没看 (与弱模式签名同根),OPEN
```

#### 3.1.4 可复现率 (上溯 §1.2 ⑥ Verifiability; 状态: PENDING)

```
两个独立子指标:
    timeline_integrity_rate       = (链式哈希校验通过的 run) / 总 run
    runegress_reproducibility_rate = (重放 timeline 得到一致 RunEgress 的 run)
                                      / (总 run 排除 non_reproducible_by_construction)

non_reproducible_by_construction (枚举型,PENDING 可能漏):
    - 含 user Override 但 user_confirm 是弱模式且无 signature_opt
    - timeline 有断链 (启动时即标,运行时已不可挽)
    - 含未完成外部依赖快照 (data_snapshot 缺 source_module)
    - anchor_unreachable: 锚点离线 / 私钥不可用

复现对象: 仅 RunEgress 一致,非"模型生成"一致 —— 与 Replay 协议同步。
        "生成复现" 属 development_aid,不参与评估。

反指标:
    tamper_detected_rate = 检出篡改的 run / 总 run
    high integrity + low tamper_detected = 健康信号
    low integrity + low tamper_detected = 检测机制失效 (sensor 没工作)

区分度 (验 R-Metric 化 B): PASS
    timeline 完整度在真实 run 间差异显著。

残余 OPEN:
    - non_reproducible_by_construction 枚举可能漏类型 OPEN
    - timeline 中 model_response 记录体积大小 OPEN (可能 inflate timeline)
```

#### 3.1.5 资源效率 (上溯全部维度;主理人"性能高效卓越";状态: PENDING)

```
分母: 仅 met (含 caveat) 的 run —— 排除 not_met / declined
      按 §1 自我驳论: 跨 met/not_met 比资源效率是错误增量,
                     agent 用 token 少但 proof 差不能算"优"。

切分: per GoalType × per main_Judge 主体 (system | user | model_self)
分布: p50 / p90 / p99 (而非均值;p99 震荡可接受,升降更敏感)

资源维度:
    token_count     : 记录但不归一化,以 model version 为 metadata
    tool_call_count : 记录 + tool 类型分布
    wall_time       : p50/p90/p99 + env snapshot
    cpu_io_intensity: PENDING 实现工具未定

反指标 (配 R-Metric 化 C):
    shortcut_signal_ratio = (RunEgress 含 caveat 的 met 数) / 总 met
    高效率 + high shortcut 信号 = 性能卓越可能走了捷径
    跨 met/not_met 不能比资源效率 —— 这是自我驳论抓出的错误增量防御。

区分度 (验 R-Metric 化 B): PASS
    不同 agent 实现确实有资源效率差异。

状态:
    PENDING 部分:
        ✓ 分母仅 met (含 caveat)、不跨 met/not_met 比
        ✓ per GoalType × per main_Judge 切分
        ✓ p50/p90/p99 分位数 (而非均值)
        ✓ 反指标 shortcut_signal_ratio 配对
    OPEN 部分:
        - cpu_io_intensity 工具未定 (依赖具体 monitoring 设施)
        - shortcut_signal_ratio 中 caveat 子类型的细化算法 OPEN
        - 资源效率跨 GoalType 比较时同分位数下是否合法,OPEN
```

### 3.2 评估体系的派生关系图

```
       §1.2 本体论 (4 维原始 → 6 维扩展)
       ├───┴────────────┬──────────────┬──────────────┬──────────────┐
       ① Identity     ② Commitment   ③ Perception  ④ Authority   ⑤ Accountability ⑥ Verifiability
       │              │               │              │              │                │
       ↓              ↓               ↓              ↓              ↓                ↓
     (身份完整度)  3.1.1 达成率   (感知准确度)  3.1.2 越界率  3.1.3 可证伪率  3.1.4 可复现率
       │              │               │              │              │                │
       └──────────────┴──────────────┴──────────────┴──────┬───────┴────────────────┘
                                                           ↓
                                                      3.1.5 资源效率 (横贯全部维度)
                                                           +
                                                      "卓越性" 投影
```

### 3.3 评估体系的派生: 科研维度特有 metric

科研 agent 需要额外的评估维度,这些维度从六维本体论派生:

| 科研 metric | 上溯 | 定义 |
|---|---|---|
| hypothesis_falsification_rate | §1.2 ② Commitment | 被证伪的假设比例 (不是失败率,是科学价值) |
| negative_result_capture_rate | §1.2 ⑤ Accountability | 被记录的负结果比例 (vs 被丢弃的) |
| experiment_reproducibility_rate | §1.2 ⑥ Verifiability | 可复现的实验比例 |
| evidence_quality_score | §1.2 ⑤ Accountability | 证据质量综合评估 (样本量/效应量/偏差) |
| knowledge_graph_growth | §1.2 ① Identity | 知识图谱新增实体/关系数 |
| cross_session_transfer_rate | §1.2 ③ Perception | 跨实验的知识转移率 |

### 3.4 评估体系的派生: 具身维度特有 metric

| 具身 metric | 上溯 | 定义 |
|---|---|---|
| physical_safety_violation_rate | §1.2 ④ Authority | 物理安全违规次数 / 总动作 |
| sim2real_gap | §1.2 ③ Perception | 仿真性能 vs 真实性能的差距 |
| real_time_compliance_rate | §1.2 ③ Perception | 控制周期内完成动作的比例 |
| sensor_cross_validation_rate | §1.2 ⑤ Accountability | 多传感器交叉验证一致的比例 |
| action_reversibility_rate | §1.2 ⑤ Accountability | 可逆动作比例 (不可逆动作需更高安全等级) |

---

## 4 架构设计

### 4.1 总体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Layer 3: 应用层 (Application)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Coding IDE  │  │  具身控制台  │  │  科研工作台  │  │  多 Agent 编排  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  │
│            CLI / TUI / Web / API / ROS2 Bridge / MCP Gateway                │
├─────────────────────────────────────────────────────────────────────────────┤
│                       Layer 2: 领域扩展层 (Domain Extensions)                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Coding Kit  │  │  Embodied Kit│  │  Science Kit │  │  Social Kit    │  │
│  │ - GoalType:  │  │ - BodySchema │  │ - Hypothesis │  │ - Multi-agent  │  │
│  │  bugfix/feat │  │ - MotorProg  │  │ - Experiment │  │   negotiation  │  │
│  │ - Tools:     │  │ - Perception │  │ - Evidence   │  │ - Reputation   │  │
│  │  LSP/CodeGit │  │  Pipeline    │  │ - NegResult  │  │ - Delegation   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  │
│           领域扩展通过插件系统加载,不修改核心                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                     Layer 1: 核心层 (Core) ← 永远严谨                        │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │
│  │ Identity│ │Commitment│ │Perception│ │Authority │ │Accountabi│ │Verifi-││
│  │ Manager │ │  Engine  │ │  Engine  │ │  Gate    │ │lity Judge│ │ability││
│  │         │ │ -GoalRef │ │ -Sensor  │ │ -3-layer │ │ -Judge   │ │ -Run  ││
│  │ -AgentID│ │ -TermCrit│ │  Abstra  │ │ -RuleSet │ │ -3-state │ │ Recodr││
│  │ -BodySc│ │ -AcceptC │ │ -World   │ │ -Gate    │ │ -Evidence│ │ -Anchor││
│  │ -Caps   │ │ -Downgra │ │  Model   │ │ -PlanMode│ │ -Caveat  │ │ -Replay││
│  └─────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────┘ │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                   Action Execution Engine                            │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────────┐  │  │
│  │  │ Discrete │ │ Continuous│ │ Hybrid   │ │  Real-time Scheduler   │  │  │
│  │  │ ToolCall │ │ MotorProg │ │  Mix     │ │  (具身实时约束)        │  │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Memory & Learning (跨维度增强)                      │  │
│  │  - Episodic Memory  (情景: 过去 N 步的 observation-action)            │  │
│  │  - Semantic Memory  (语义: 长期知识图谱)                              │  │
│  │  - Procedural Memory (程序: 技能库/motor primitives)                  │  │
│  │  - Cross-session Learn (跨会话模式识别 + 建议)                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│                    Layer 0: 适配层 (Adapters) ← 模型无关                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │  LLM     │  │  Robot   │  │  Sensor  │  │  Tool    │  │  Protocol     │  │
│  │ Adapter  │  │ OS Adapter│ │ Adapter  │ │ Registry │ │  MCP/SSE/StdIO│  │
│  │ OpenAI/  │  │ ROS2/    │ │ Camera/  │ │ Native   │ │              │  │
│  │ Anthropic│  │ Isaac/   │ │ Force/   │ │ + MCP    │ │              │  │
│  │ /Local   │  │ Tesla    │ │ Mic/...  │ │          │ │              │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 核心层六维详解

#### 4.2.1 Identity Manager

```
Identity = {
    agent_id: UUID,              # 全局唯一身份
    body_schema: BodySchema,     # 具身形态 (非具身 agent = 工具集 schema)
    capabilities: Capabilities,  # 能力声明 (参考 Grok Build ToolCapabilities 扩展)
    credentials: Auth,           # 认证凭据 (API key / 证书)
    version: str                 # agent 版本 (用于归因)
}

BodySchema = {
    type: "humanoid" | "arm" | "mobile" | "virtual" | "custom",
    sensors: [SensorSpec],       # 传感器列表 (类型、采样率、精度)
    actuators: [ActuatorSpec],   # 执行器列表 (类型、范围、精度)
    kinematics: Kinematics,      # 运动学模型 (非具身为 null)
    constraints: PhysicalLimits  # 物理约束 (关节角度、速度、力矩上限)
}

Capabilities = {
    read_only_tools: [ToolId],   # 只读工具
    write_tools: [ToolId],       # 写工具
    special_abilities: [str],    # 特殊能力 (如 "spawn_subagent", "mcp")
    constraints: [str]           # 约束 (如 "no_internet", "no_bash")
}
```

**关键设计**: Identity 不是"配置",是**本体论承诺**。agent 的所有行为必须与其
Identity 一致——一个"只有机械臂"的 agent 不应该被分配"移动整个桌子"的任务。

**与 FEP 的映射**: Identity = 马尔可夫毯边界。Agent 的能力声明定义了
它可以通过马尔可夫毯与外部世界交互的通道 (传感器=输入通道,执行器=输出通道)。

#### 4.2.2 Commitment Engine

```
Commitment = {
    goal: GoalTriple,            # 三段式 Goal
    downgrade: GoalDowngrade?,   # 降级追踪
    timeline: Timeline,          # 承诺的生命周期 (创建→锁定→执行→终结/撤销)
    deadline: TemporalConstraint?, # 时间约束 (具身/科研关键)
    priority: Priority,           # 优先级 (多任务调度)
}

GoalTriple = {
    statement: GoalStatement,    # 目标陈述
    termination: TerminationCriterion,  # 终止判据 (三态)
    acceptance: AcceptanceContract,     # 验收契约
}

GoalStatement = {
    intent: str,                 # 用户原话 (immutable)
    rewriting: str,              # agent 重述 (用于验证误读)
    rewrite_confidence: float,   # 自评置信 (低于阈值须用户澄清)
    goal_type: GoalType,         # 目标类型 (11 种 Base + Extended)
    translation_of: [segment_id], # 每条可回指 user_raw 子句
    added_intent: [],            # 必空 (R1 翻译禁加戏)
}

TerminationCriterion = {
    exposed_dependency_set: [str] | None,  # system_judge 必填
    # 纯函数: 输入 = 当前状态, 输出 ∈ {not_met, met, undecidable}
}

AcceptanceContract = {
    baseline_frozen_at: git_sha,  # 测试基线冻结点
    prohibited_actions: [],       # agent 不能动的东西列表
    escalation: human_review | abort,  # 触发后的出路
}
```

**科研扩展**: HypothesisGoal (见 §1.5)

**关键设计**: Commitment 是**不可撤销的承诺**——一旦锁定,agent 必须执行或
显式放弃 (记录放弃原因)。这与 BDI 的"意图即承诺"一致,但以工程可验证的方式实现。

**与 FEP 的映射**: Commitment = 偏好先验 + 自由能阈值。偏好先验定义了
agent"倾向于观察到的状态" (即目标),自由能阈值定义了"何时放弃" (当预期自由能
超过阈值,agent 理性地放弃当前承诺)。

**与可验证推理系统的映射**: Commitment 的形式化规格 (formal specification)
是符号验证器的输入——验证器检查"推理过程是否符合规格"。

#### 4.2.3 Perception Engine (新增)

```
PerceptionEngine = {
    sensors: [Sensor],           # 传感器通道
    observ: Observation → Percept,     # 原始观测 → 结构化感知
    estimate: Percept → StateEstimate, # 感知 → 状态估计 (带置信度)
    world_model: WorldModel,     # 世界模型 (预测行动后果)
}

Observation = {                  # 原始传感器数据
    sensor_id: str,
    timestamp: int,
    data: bytes,                 # 原始数据 (图像帧、力矩读数、文件内容...)
    metadata: dict,              # 采样率、精度、噪声模型
}

Percept = {                      # 结构化感知 (解释后的)
    state: dict,                 # 键值对状态 (如 "object_A_position": [x,y,z])
    confidence: float,           # 置信度 [0,1]
    uncertainty: Uncertainty,    # 不确定性模型 (高斯/分布/区间)
    timestamp: int,
}

StateEstimate = {                # 状态估计 (融合多传感器)
    state: dict,
    confidence: float,
    covariance: Matrix,          # 协方差矩阵 (多变量不确定性)
    source: [sensor_id],         # 来源传感器
    timestamp: int,
}

WorldModel = {                   # 世界模型
    predict: (State, Action) → (StateEstimate, Confidence),  # 预测后果
    update: (Observation) → WorldModel,                       # 从观测更新
    anomaly: (State) → bool,                                  # 异常检测
}
```

**World Model 的实现策略**:

| 级别 | 适用场景 | 实现 | 精度 | 可验证性 |
|---|---|---|---|---|
| **轻量级** | coding agent | 代码图 + 历史经验预测 | 中 | 高 (符号化) |
| **中量级** | 科研 agent | 知识图谱 + 因果推理 | 中高 | 高 (可追溯) |
| **重量级** | 具身 agent | 神经网络 latent dynamics + MPC | 中 | 低 (黑盒) |

**关键设计**: Perception Engine 是**具身和非具身的统一抽象**。对 coding agent,
`Observation` = 文件内容 (确定性高,置信度 ≈1),`World Model` = "如果改这行代码,
测试大概率通过" (粗略但够用)。对具身 agent,`Observation` = 摄像头图像
(噪声大,置信度 <1),`World Model` = "如果向左转 90 度,大概率不会撞墙"
(需要精确)。

**与 FEP 的映射**: Perception = 预测编码层级。`Observation` = 感官输入,
`Percept` = 预测误差修正后的状态估计,`World Model` = 生成模型。
感知不是"从外部世界提取信息",而是"内部模型主动预测 + 预测误差修正"。

**与可微分编程的关系**: World Model 的重量级实现可以是可微分的神经网络
(如 Dreamer 的 RSSM),但 Perception Engine 的接口 (predict/update/anomaly)
是模型无关的——可微分实现只是其中一种策略。

#### 4.2.4 Authority Gate

```
AuthorityGate = {
    layers: [SafetyLayer],       # 三层名单 (whitelist/greylist/blacklist)
    rules: RuleSet,              # 规则集 (声明式)
    context_judge: (Action, Context, StateEstimate) → SafeLevel,  # 扩展: 加入状态估计
    gate: ConfirmGate,           # 确认闸门 (状态机)
    plan_mode: PlanModeTracker,  # 只读模式
    budget: ResourceBudget,      # 资源预算 (token/时间/能量/试剂)
}

SafeLevel := WHITELIST | GREYLIST | BLACKLIST

context_judge(action, context, state_estimate, rules, tool_registry=None) -> SafeLevel:
    matched_rules = match_all(declared_rules, action, context, state_estimate)
    
    if any matched rule = blacklist:           return blacklist   # deny 优先
    if any matched rule = greylist (无 blacklist): return greylist
    if any matched rule = whitelist 且 无 greylist 无 blacklist: return whitelist
    
    # 无规则匹配时,根据工具能力决定默认权限
    if tool_registry 可获取工具能力:
        if tool.is_read_only:  return whitelist    # 只读工具默认放行
        else:                  return greylist     # 写工具默认询问
    
    if 无 tool_registry / 找不到工具: return greylist  # 保守 fallback

declared_rules = 
    核心不可改 deny-rules (硬编码 + 外化公示)
    ∪ user_local 项目/.zall/rules.toml (用户外化可改)
    ∪ domain rules (AgentType 领域知识常量)

优先级链 (deny 优先):
    核心不可改 deny-rules > user_local.deny > user_local.allow > domain.allow
    > 工具能力声明 > greylist_deny > 默认 greylist
```

**具身扩展**: PhysicalSafetyLayer

```
PhysicalSafetyLayer = {
    hard_limits: PhysicalLimits,   # 硬约束 (关节角度/速度/力矩上限,不可违反)
    soft_constraints: [Rule],      # 软约束 (尽量遵守,违反需记录)
    collision_checker: CollisionChecker,  # 碰撞预测 (基于 World Model)
    emergency_stop: EmergencyStop,         # 急停 (硬件级)
}
```

**科研扩展**: ExperimentAuthority

```
ExperimentAuthority = {
   试剂权限: [ChemicalPermission],
   仪器权限: [InstrumentPermission],
   数据权限: [DataPermission],
   计算资源: [ComputeBudget],
}
```

**关键设计**: Authority Gate 在具身场景中**必须考虑 World Model 的预测**。
例如,一个"移动手臂"的动作在执行前,Authority Gate 必须用 World Model 预测
"这个动作是否会导致碰撞",而不仅仅是检查"这个动作是否在黑名单"。

**与 FEP 的映射**: Authority = 马尔可夫毯约束。行动只能通过马尔可夫毯
(即 Authority Gate 允许的通道) 影响外部世界。硬约束 (hard_limits) 定义了
马尔可夫毯的"不可穿越边界"——任何试图穿越的行动被物理拦截 (L1 反射层)。

#### 4.2.5 Accountability Judge

```
AccountabilityJudge = {
    main: Judge,                 # 主判定主体 (system/user/model_self)
    aux: Judge,                  # 辅判定主体
    evidence: Evidence,          # 证据收集
    consistency: ConsistencyCheck, # 一致性检查 (主辅判定交叉验证)
    caveat: Caveat?,             # 降级标注 (main_unavailable / main_aux_divergent)
}

Judge = system | user | model_self

Evidence = {
    baseline_sha: git_sha,
    current_sha: git_sha,
    diff: structured_diff,
    test_results: [(test_id, status)],
    lint_results: [(rule_id, status)],
    external: <schema 见 Verifiability>,
}

# 科研扩展: ScientificJudge
ScientificJudge = {
    statistical: StatisticalTest,   # 统计检验 (p-value / effect size / power)
    reproducibility: ReproducibilityCheck, # 可复现性检查
    peer_review: PeerReview,        # 同行评审 (多 Judge 交叉验证)
    evidence_quality: EvidenceQuality, # 证据质量评估 (样本量/偏差/效应量)
}

# 具身扩展: PhysicalJudge
PhysicalJudge = {
    sensor_verification: [SensorCheck],  # 传感器交叉验证 (视觉+力矩+位置)
    physical_state: PhysicalStateCheck,  # 物理状态确认 (物体是否在 B 位置)
    safety_audit: SafetyAudit,           # 安全审计 (动作全程无碰撞)
}
```

**关键设计**: Accountability 的核心是**多主体交叉验证**。在科研中,一个假设的
验证需要统计显著性 + 可复现 + 同行评审三重验证。在具身中,一个动作的完成需要
视觉 + 力矩 + 位置三重确认。

**与 FEP 的映射**: Accountability = 自由能最小化的收敛性。当主 Judge 判定
"met"时,意味着系统的自由能已降低到阈值以下 (内部模型与外部世界一致)。
当主 Judge 判定 "undecidable"时,意味着自由能不能进一步降低 (系统需要更多信息)。

**与可验证推理系统的映射**: Accountability Judge = 符号验证器。
在三态判定 (met/not_met/undecidable) 的基础上,增加形式化验证
(Lean/Isabelle/Coq 证明助手、类型系统、模型检测)。

#### 4.2.6 Verifiability Recorder

```
VerifiabilityRecorder = {
    timeline: Timeline,            # 事件时间线 (链式哈希)
    recorder: RunRecorder,         # 记录器
    anchor: TrustAnchor,           # 外部锚点 (ed25519 签名)
    replay: ReplayProtocol,        # 重放协议
}

TimelineEvent = {
    event_id: UUID,
    ts: int,                       # unix timestamp (毫秒)
    event_type: EventType,         # model_call/tool_call_start/tool_call_end/...
    payload: dict,
    prev_hash: str,                # 链式哈希 (SHA-256)
}

# 科研扩展: ScienceProvenance
ScienceProvenance = {
    hypothesis_version: str,       # 假设版本
    experiment_protocol_hash: str, # 实验 protocol 哈希
    data_hash: str,                # 数据哈希
    analysis_code_hash: str,       # 分析代码哈希
    environment_hash: str,         # 环境配置哈希
    lineage: DAG,                  # 数据血缘 (实验→数据→分析→结论)
}

# 具身扩展: PhysicalProvenance
PhysicalProvenance = {
    sensor_data_log: [SensorLog],  # 传感器数据日志 (高频率)
    action_log: [ActionLog],       # 动作日志 (每个控制周期)
    state_estimation_log: [StateLog], # 状态估计日志
    world_model_version: str,      # 世界模型版本
    sim2real_gap: GapMetric,       # 仿真到现实的 gap 度量 (如适用)
}
```

**关键设计**: 物理世界的 Verifiability 有一个**根本性限制**——无法保证因果复现
(物理扰动)。但框架可以做到**数据级复现** (相同的传感器数据 + 相同的模型 → 相同的动作)。
这是物理世界 Verifiability 的上限,必须诚实声明。

**与 FEP 的映射**: Verifiability = 自由能轨迹的可重放性。如果系统的行为
是一致地最小化自由能,那么它的行为轨迹应该是可重放的 (相同的初始状态 + 相同的
行动 → 相同的自由能轨迹)。

**与可验证推理系统的映射**: Verifiability = 证明轨迹 + 因果溯源。
在代码场景中,Verifiability 的链式哈希时间线记录了"每一步推理的证明步骤";
在科研场景中,ScienceProvenance 记录了"从假设到结论的完整因果链"。

### 4.3 执行引擎: 统一离散/连续动作

```
ActionExecutionEngine = {
    # 离散动作 (coding agent)
    discrete_executor: ToolExecutor,       # 执行工具调用
    
    # 连续动作 (具身 agent)
    continuous_executor: MotorExecutor,    # 执行动作基元
    real_time_scheduler: RealTimeScheduler, # 实时调度 (具身控制周期 <10ms)
    
    # 混合动作 (科研 agent + 复杂任务)
    hybrid_executor: HybridExecutor,       # 混合调度 (离散决策 + 连续执行)
}

RealTimeScheduler = {
    cycle_time: Duration,          # 控制周期 (如 1kHz = 1ms)
    priority_queue: PriorityQueue, # 优先级队列 (紧急动作优先)
    deadline_monitor: DeadlineMonitor, # 截止时间监控
    fallback: FallbackStrategy,    # 超时降级策略 (如"停止所有动作")
}
```

**关键设计**: coding agent 的"think-then-act"模式 (慢思考、快执行) 在具身世界中
必须改为"think-while-act" (边想边做)。RealTimeScheduler 确保关键动作 (如避障)
在固定周期内执行,不受 LLM 推理延迟影响。

### 4.4 记忆与学习: 跨维度增强

```
MemoryAndLearning = {
    # 三类记忆 (参考认知架构)
    episodic: EpisodicMemory,     # 情景记忆 (过去 N 步的 observation-action-result)
    semantic: SemanticMemory,     # 语义记忆 (长期知识图谱)
    procedural: ProceduralMemory, # 程序记忆 (技能库/motor primitives)
    
    # 跨会话学习
    cross_session: CrossSessionLearn, # 跨会话模式识别 + 建议
    
    # 学习策略 (可插拔)
    learning_strategy: LearningStrategy, # in-context / fine-tune / RL / none
    forgetting_protection: ForgettingProtection, # 灾难性遗忘防护
}

EpisodicMemory = {
    buffer: [Episode],            # 环形缓冲区 (保留最近 N 步)
    max_size: int,                # 容量上限
    recall: (query) → [Episode],  # 按查询检索
    compress: () → Episode,       # 压缩 (摘要化,腾空间)
}

SemanticMemory = {
    graph: KnowledgeGraph,        # 知识图谱 (实体 + 关系)
    entities: {id: Entity},       # 实体 (假设/实验/证据/结论/代码/文件...)
    relations: {from, to, type},  # 关系 (支持/反对/派生/修改/引用...)
    query: (pattern) → [Result],  # 图查询
    update: (entity, relation) → (), # 更新
}

ProceduralMemory = {
    skills: {id: Skill},          # 技能 (编码的 motor primitive + tool sequence)
    learn: (episode_sequence) → Skill, # 从经验序列中学习技能
    execute: (skill_id, params) → Result, # 执行技能
}
```

**关键设计**: 记忆不是"把对话存起来",而是**结构化的知识管理**。情景记忆支持
快速检索过去类似场景,Semantic Memory 支持长期知识积累和推理,
Procedural Memory 支持"学会的技能自动可用"。

---

## 5 统一循环

### 5.1 统一循环 (Unified Loop)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Unified Agent Loop                              │
│                                                                     │
│  ① Perceive: 传感器 → Observation → Percept → StateEstimate         │
│       ↓ (带置信度的状态估计)                                         │
│  ② Evaluate: StateEstimate vs Goal → 是否需要行动?                  │
│       ↓ (Commitment Engine 检查当前承诺是否仍有效)                    │
│  ③ Reason: World Model 预测行动后果 → 规划/策略选择                 │
│       ↓ (生成候选行动序列)                                           │
│  ④ Authorize: Authority Gate 检查每个行动的安全性                   │
│       ↓ (whitelist→执行 / greylist→确认 / blacklist→拒绝)           │
│  ⑤ Execute: 执行行动 (离散工具 or 连续动作基元)                     │
│       ↓ (记录到 Verifiability timeline)                             │
│  ⑥ Verify: 感知新状态 → Accountability Judge 判定进展               │
│       ↓ (met / not_met / undecidable)                               │
│  ⑦ Commit: 如 met → 终结承诺; 如 not_met → 回到①                   │
│       ↓                                                             │
│  ⑧ Learn: 经验写入 Memory (情景+语义+程序)                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 具身 vs 非具身的循环差异

| 步骤 | Coding Agent (非具身) | 具身 Agent | 差异本质 |
|---|---|---|---|
| ① Perceive | 文件内容 (确定性高) | 传感器数据 (噪声大) | 感知不确定性 |
| ② Evaluate | git diff + 测试结果 | 状态估计 + 置信度 | 判定标准 |
| ③ Reason | 代码图 + 历史经验 | World Model + MPC | 推理复杂度 |
| ④ Authorize | 规则匹配 + 确认门 | 规则 + 碰撞预测 + 物理约束 | 安全实时性 |
| ⑤ Execute | 工具调用 (原子) | 动作基元 (连续) | 动作粒度 |
| ⑥ Verify | 测试 + git | 传感器交叉验证 | 验证方式 |
| ⑦ Commit | 目标达成 | 状态达成 + 安全审计 | 完成判据 |
| ⑧ Learn | 模式识别 + 建议 | 策略更新 + motor skill 学习 | 学习形式 |

**关键洞察**: 循环结构完全一致,只是每一步的**实现复杂度不同**。
这证明了六维本体论的统一性——具身和非具身共享同一个抽象框架。

### 5.3 实时约束的处理

具身 agent 的实时性要求 (<10ms 控制周期) 与 LLM 推理延迟 (100ms-1s) 存在根本矛盾。
解决方案是**分层决策**:

```
┌─────────────────────────────────────────────────┐
│              L3: 策略层 (慢, 1-10s)              │
│  LLM 推理: 目标分解、规划、假设生成、长期决策     │
│  输出: 高层意图 (如"把杯子从桌上移到厨房")         │
├─────────────────────────────────────────────────┤
│              L2: 规划层 (中, 10-100ms)            │
│  World Model + MPC: 动作序列规划、碰撞预测        │
│  输出: 动作基元参数 (如"grasp(cup_id, force=0.5)")│
├─────────────────────────────────────────────────┤
│              L1: 反射层 (快, <1ms)                │
│  硬编码控制器: 避障、急停、力矩限制、关节保护     │
│  输出: 电机力矩指令 (不可被上层覆盖)              │
└─────────────────────────────────────────────────┘
```

**不变量**: L1 (反射层)**永远优先**于 L2 和 L3。即使 LLM 规划了一个危险动作,
L1 的急停和碰撞保护会拦截。这是 FR-1 (物理不可逆性) 的工程体现。

---

## 6 领域扩展

### 6.1 Coding Kit

```
CodingKit = {
    goal_types: [bugfix, feature, refactor, test_write, docs, perf_opt, review, investigate, migrate, scaffold, unknown],
    tools: [read_file, write_file, edit_file, bash, grep, glob, list_dir, search, web_fetch, lsp, codegraph, spawn_subagent],
    judge: system_judge (测试/lint) + model_self_judge (代码审查),
    provenance: git_sha + diff_hash,
}
```

**与 zall 现有架构的映射**: 基本不变,只需将现有模块嵌入到六维框架中。

### 6.2 Embodied Kit

```
EmbodiedKit = {
    body_schemas: [humanoid, arm, mobile, drone, custom],
    motor_primitives: [grasp, lift, place, push, pull, navigate, avoid_obstacle],
    perception_pipeline: [camera_preprocessing, force_calibration, sensor_fusion],
    world_model: latent_dynamics | mpc | hybrid,
    safety: [collision_checker, torque_limiter, speed_limiter, emergency_stop],
    judge: physical_judge (传感器交叉验证 + 状态确认),
    provenance: sensor_data_log + action_log + state_estimation_log,
}
```

**关键抽象——Motor Primitive (动作基元)**:
具身 agent 的连续动作空间需要"动作基元"的抽象层。一个 Motor Primitive 是一个
**参数化的动作序列**,如 `grasp(object_id, force, approach_angle)`。它封装了底层
控制细节,向 Commitment Engine 暴露"声明式接口" ( "我要抓取这个物体"而非
"我要让关节1转30度、关节2转15度..." )。

这与 zall 的 `Tool` 抽象一致——`write_file(path, content)` 不关心文件系统的底层实现。

### 6.3 Science Kit

```
ScienceKit = {
    hypothesis_manager: HypothesisManager,   # 假设管理 (生成/比较/证伪/修正/版本化)
    experiment_designer: ExperimentDesigner, # 实验设计 (变量控制/随机化/对照/样本量)
    evidence_synth: EvidenceSynthesizer,     # 证据合成 (元分析/证据等级/不一致检测)
    neg_result_mgr: NegativeResultManager,   # 负结果管理 (记录/综合/发布)
    knowledge_graph: KnowledgeGraph,         # 长期知识图谱 (假设-实验-证据-结论的关系网)
    goal_types: [hypothesis_test, experiment_design, data_analysis, literature_review, discovery, replication],
    judge: statistical_judge + reproducibility_judge + peer_review_judge,
    provenance: experiment_protocol_hash + data_hash + analysis_code_hash + environment_hash + lineage,
}
```

**Hypothesis 数据结构**:
```
Hypothesis = {
    id: UUID,
    claim: str,                    # "化合物 X 抑制蛋白 Y"
    prediction: str,              # "如果 X 存在,Y 的活性应降低 >30%"
    confidence: float,            # 当前置信度 [0,1]
    evidence_for: [EvidenceId],   # 支持证据
    evidence_against: [EvidenceId], # 反面证据
    status: proposed | testing | confirmed | falsified | revised,
    version: int,                 # 版本号 (证伪后可修订为新版本)
    lineage: [HypothesisId],      # 衍生关系 (从哪个假设派生)
    created_by: AgentID,          # 哪个 agent 提出
    timestamp: int,
}
```

**负结果管理**:
```
NegativeResult = {
    experiment_id: str,
    what_failed: str,             # 什么没起作用
    conditions: dict,             # 在什么条件下没起作用
    diagnosis: str,               # 诊断 (假设错误? 实验设计问题? 执行误差?)
    value: str,                   # 这个负结果的价值 (排除了什么可能性)
    published: bool,              # 是否已发布 (科学界最缺的)
}
```

### 6.4 Social Kit (多 Agent 协作)

```
SocialKit = {
    negotiation: Protocol,        # Agent 间协商协议 (任务委托/资源交换)
    reputation: System,           # 声誉系统 (记录其他 agent 的可靠度)
    delegation: Mechanism,        # 任务委托 (父 agent 委托子 agent,权限继承)
    coordination: Protocol,       # 协调协议 (冲突解决/资源共享)
}
```

**关键设计**: 多 agent 不是"多个 agent 一起聊天",而是**有协议的协作**。
委托时,子 agent 继承父 agent 的权限 (但不超过父 agent),子 agent 的结果计入
父 agent 的 Accountability。

---

## 7 演进路线图

### 7.1 Phase 0: 奠基 (本轮) — 统一本体论 + 架构设计

**目标**: 把六维本体论立为 zall 的新设计基准,现有代码对标定位。

**产出**:
- 本文档 (MASTER.md)
- DESIGN.md 标记为历史投影,以本文档为准
- 代码模块与六维的映射表 (见附录 B)

**状态**: PENDING (待红蓝对抗)

### 7.2 Phase 1: 对齐 (1-2 轮) — 补全裂缝

**目标**: 让设计文档真正在运行时生效。

**具体改动**:

1. **Refiner 接入 run()**
   - 文件: `cli/app.py` + `core/loop.py` + `core/refiner.py`
   - 改动: `run()` 入口调用 `GoalRefiner.refine()` → `RefinedGoal` 或 `DeclineTask` → `ConfirmGate` → 锁定 GoalTriple → 进入主循环
   - 不变量: timeline 中第一条 `tool_call_start` 之前必须有 `goal_statement` + `user_confirm`
   - 测试: `test_refiner_integrates_with_run()` (反例: Refiner 未调用 → run 直接跳过)

2. **外部锚点真正外部化**
   - 文件: `core/verifiability.py` (新增 `ProcessTrustAnchor`)
   - 改动: 锚点从 `.zall/` 文件移出,改为独立进程 (Unix domain socket 通信)。私钥由锚点进程独占。
   - Windows: 命名管道 + ACL
   - 测试: `test_anchor_is_external_to_agent_process()` (反例: 锚点在同一进程 → 测试 fail)

3. **TerminationCriterion 默认实现**
   - 文件: `core/judge/` (新增包: system_judge.py, user_judge.py, model_self_judge.py)
   - 改动: 提供 `SystemJudge` (跑测试/lint)、`UserJudge` (等待用户确认)、`ModelSelfJudge` (模型自评) 的默认实现
   - `base_judge` 表在代码里注册 (不是只在文档里)
   - 测试: `test_default_judges_work_without_user_implementation()` (反例: 无自定义 Judge → run 崩溃)

4. **评估体系落地**
   - 文件: `cli/commands/eval.py` + `core/eval/` (新增)
   - 改动: `/eval` 命令读取 `timeline.jsonl`,计算 `goal_achievement_rate` + `timeline_integrity_rate`
   - 测试: `test_eval_command_produces_metrics_from_timeline()` (反例: 无 timeline → eval 输出空)

### 7.3 Phase 2: 降摩擦 (2-3 轮) — 日常顺滑

**目标**: 让日常任务零摩擦,关键任务全验证。

**具体改动**:

1. **快速路径 + 严格路径**
   - 文件: `core/loop.py` + `cli/app.py`
   - 改动: 根据 GoalType + 仓库复杂度自动选择路径
   - 快速路径: Goal 后台静默确认 + 只读工具免确认 + 写工具一次确认
   - 严格路径: Refiner 交互 + 每步确认 + 降级提示
   - 不变量: 快速路径可跳过确认,不可跳过 timeline 记录

2. **统一消息存储**
   - 文件: `core/loop.py` + `core/chat_state.py`
   - 改动: 彻底移除 `_messages` 属性,所有代码统一用 `_chat_state.messages`
   - 消除双写,消除 `_sync_messages()`
   - 测试: `test_no_dual_write_inconsistency()` (反例: _messages 与 _chat_state 不同步)

3. **概念暴露面减半**
   - CLI 只暴露 5-6 个命令: `zall run` / `zall --strict` / `zall --judge` / `zall replay` / `zall eval` / `zall /skill`
   - 内部抽象保留 30+,但不暴露给 CLI 用户

### 7.4 Phase 3: 科研维度 (3-4 轮) — 假设驱动的探索

**目标**: 让 zall 成为首个原生支持科研探索的 agent 框架。

**具体改动**:

1. **新增 Hypothesis 模块**
   - 文件: `core/hypothesis.py` (新增)
   - 数据结构: `Hypothesis` (claim/confidence/evidence_for/evidence_against/status/version)
   - `HypothesisManager`: 生成/比较/证伪/修正/版本化
   - `GoalType` 扩展: `hypothesis_test` / `experiment_design` / `discovery`

2. **新增 Experiment 模块**
   - 文件: `core/experiment.py` (新增)
   - 数据结构: `Experiment` (design/parameters/controls/protocol/observations/status)
   - `ExperimentDesigner`: 变量控制/随机化/对照/样本量
   - `ScienceProvenance`: 实验 protocol 哈希 + 数据哈希 + 代码哈希 + 环境哈希 + 血缘 DAG

3. **新增 Evidence 模块**
   - 文件: `core/evidence.py` (新增)
   - 数据结构: `Evidence` (data/analysis/interpretation/confidence/provenance)
   - `EvidenceSynthesizer`: 元分析/证据等级/不一致检测
   - `ScientificJudge`: 统计检验 + 可复现性 + 同行评审

4. **新增 Negative Result 模块**
   - 文件: `core/negative_result.py` (新增)
   - `NegativeResultManager`: 记录/综合/发布负结果
   - 负结果与正结果同等存储在 Knowledge Graph

5. **Knowledge Graph 长期记忆**
   - 文件: `core/knowledge_graph.py` (新增)
   - 实体: 假设/实验/证据/结论/代码/文件
   - 关系: 支持/反对/派生/修改/引用
   - 查询: 图查询 + 自动推理 ("从已有知识推导新假设")

### 7.5 Phase 4: 具身维度 (4-5 轮) — 物理世界 agent

**目标**: 让 zall 框架能同时驾驭数字世界和物理世界。

**具体改动**:

1. **新增 Perception Engine**
   - 文件: `core/perception/` (新增包: sensor.py, world_model.py, state_estimate.py)
   - `Sensor` 抽象: 摄像头/力矩/位置/文件内容/API 响应统一为 Sensor
   - `Observation → Percept → StateEstimate` 管道
   - `WorldModel` 接口: predict/update/anomaly
   - 轻量级 World Model (coding agent): 基于代码图的静态分析

2. **新增 Motor Primitive 抽象**
   - 文件: `core/motor/` (新增包)
   - `MotorPrimitive`: 参数化的动作序列 (grasp/lift/place/navigate)
   - `MotorExecutor`: 执行动作基元
   - 与 `Tool` 抽象统一 (Tool 是离散的 Motor Primitive)

3. **新增 Real-time Scheduler**
   - 文件: `core/realtime/` (新增包)
   - `RealTimeScheduler`: 控制周期 <10ms
   - 优先级队列 + 截止时间监控 + 超时降级
   - L1 反射层 (急停/碰撞保护) 永远优先

4. **新增 Physical Safety**
   - 文件: `core/safety/physical.py` (新增)
   - `PhysicalSafetyLayer`: 硬约束 (关节角度/速度/力矩) + 软约束 + 碰撞预测 + 急停
   - `Authority Gate` 扩展: 加入 World Model 预测 (动作是否导致碰撞)

5. **新增 Physical Provenance**
   - 文件: `core/provenance/physical.py` (新增)
   - `PhysicalProvenance`: 传感器数据日志 + 动作日志 + 状态估计日志 + 世界模型版本 + sim2real gap

6. **具身 Kit 插件**
   - 文件: `extensions/embodied/` (新增)
   - ROS2 Adapter: 连接 ROS2 话题/服务/动作
   - Isaac Adapter: 连接 NVIDIA Isaac Sim
   - 通用 Robot OS Adapter

### 7.6 Phase 5: 社会维度 (5-6 轮) — 多 agent 协作

**目标**: 让多个 agent 能像团队一样协作。

**具体改动**:

1. **多 Agent 协议**
   - 文件: `core/social/` (新增包: negotiation.py, reputation.py, delegation.py)
   - 协商协议: 任务委托/资源交换
   - 声誉系统: 记录其他 agent 的可靠度
   - 委托机制: 子 agent 继承权限 (但不超过父 agent)

2. **多 Agent Verifiability**
   - 扩展 RunRecorder: 支持跨 agent 的 timeline 关联
   - 委托的任务结果计入父 agent 的 Accountability

### 7.7 Phase 6: 可验证推理 (6-8 轮) — 神经×符号融合

**目标**: 让 zall 从 Agent 1.0 演进到 Agent 2.0 (可验证推理系统)。

**具体改动**:

1. **规格形式化模块**
   - 文件: `core/specification/` (新增包)
   - 将自然语言目标转换为可验证的形式规格 (PDDL / 类型系统 / Lean 证明目标)
   - 这是 Commitment Engine 的形式化层

2. **符号验证器接入**
   - 文件: `core/verification/` (新增包)
   - 接入 Lean/Isabelle/Coq 证明助手、类型检查器、模型检测器
   - Accountability Judge 的符号验证层

3. **推理-验证循环**
   - 文件: `core/loop.py` (修改)
   - 每一步推理 (或每 N 步) 必须通过符号验证器才能继续
   - 验证失败 → 定向修正 (而非重试)

4. **World Model 重量级实现**
   - 文件: `core/perception/world_model.py` (扩展)
   - 可微分神经网络 World Model (latent dynamics + MPC)
   - 与轻量级 World Model (代码图) 共存,按场景选择

### 7.8 时间线总览

| 版本 | 时间 | 里程碑 | 对应 Phase | 风险 |
|---|---|---|---|---|
| v0.5.x | 当前 | Coding agent 成熟化 (修裂缝、降摩擦) | Phase 1-2 | 低 |
| v0.6 | +1 月 | 六维本体论立为设计基准,DESIGN.md 标记为历史 | Phase 0 | 低 |
| v0.7 | +2 月 | Refiner 接入 run + 外部锚点 + 默认 Judge + 评估落地 | Phase 1 | 低 |
| v0.8 | +3 月 | 快速路径/严格路径 + 统一消息存储 + CLI 精简 | Phase 2 | 中 |
| v0.9 | +5 月 | 科研维度 (Hypothesis + Experiment + Evidence) | Phase 3 | 中 |
| v1.0 | +7 月 | Perception Engine + World Model (轻量级) | Phase 4 部分 | 中 |
| v1.1 | +9 月 | 具身维度 (Motor Primitive + Real-time + Physical Safety) | Phase 4 | 高 |
| v1.2 | +11 月 | 多 Agent 社会维度 | Phase 5 | 中 |
| v2.0 | +14 月 | 可验证推理 (规格形式化 + 符号验证 + 推理-验证循环) | Phase 6 | 高 |
| v2.5 | +18 月 | World Model 重量级 (可微分神经网络) | Phase 6 扩展 | 高 |
| v3.0 | +24 月 | 完整统一框架 (coding + 科研 + 具身 + 多 agent + 可验证) | 全部 | 极高 |

---

## 8 生态建设

### 8.1 为什么生态决定生死

zall 与 Claude Code/Grok Build 的根本差距不是技术,是**生态**。
Claude Code 有 Anthropic 的模型生态和 Claude Code SDK 的开发者网络;
Grok Build 有 xAI 的算力生态和 Rust 社区。zall 必须建立自己的生态,
否则再好的架构也只是学术玩具。

### 8.2 生态的三层结构

```
┌─────────────────────────────────────────────────────┐
│              Layer 3: 应用生态 (Applications)        │
│  zall IDE 插件 / zall CI / zall 科研平台 / zall 机器人  │
│  ← 用户直接使用的产品                                                  │
├─────────────────────────────────────────────────────┤
│              Layer 2: 扩展生态 (Extensions)          │
│  Skills / Tools / MCP Servers / Judges / World Models │
│  ← 开发者扩展 zall 能力的插件                                            │
├─────────────────────────────────────────────────────┤
│              Layer 1: 核心生态 (Core)                 │
│  zall 核心框架 / 协议标准 (MCP/zall-native) / 评估基准 │
│  ← 所有扩展依赖的稳定基础                                                │
└─────────────────────────────────────────────────────┘
```

### 8.3 Layer 1: 核心生态 (稳固基础)

**目标**: 让所有扩展有一个稳定、文档良好、向后兼容的基础。

**策略**:

1. **API 稳定性承诺**: 核心层 (src/zall/core/) 的公共 API 在 v1.x 期间保持向后兼容。
   破坏性变更只在 v2.0+ 引入,且提供迁移指南。

2. **协议标准化**:
   - **MCP**: 原生支持 MCP 协议 (已实现),让 zall 可以调用任何 MCP server。
   - **zall-native Tool Protocol**: 定义 zall 原生工具的标准化接口 (Tool schema / execute / stream)。
   - **Judge Protocol**: 定义 Judge 的标准化接口 (Evidence → Verdict + Report)。
   - **WorldModel Protocol**: 定义 World Model 的标准化接口 (predict / update / anomaly)。

3. **评估基准**: 建立 zall 自己的评估基准,不只依赖 SWE-bench。包括:
   - **zall-Correctness**: 代码正确性 (测试通过率 + 代码审查评分)
   - **zall-Safety**: 安全合规 (越界率 + 安全违规次数)
   - **zall-Verifiability**: 可验证性 (timeline 完整度 + 复现成功率)
   - **zall-Efficiency**: 资源效率 (token 用量 + 工具调用次数 + wall time)
   - **zall-Science**: 科研能力 (假设验证成功率 + 负结果捕获率 + 知识图谱增长)

4. **开放治理**: 建立技术委员会 (TC),负责:
   - 审核核心层的破坏性变更
   - 制定协议标准
   - 管理评估基准
   - 裁决生态争议

### 8.4 Layer 2: 扩展生态 (激发创新)

**目标**: 让开发者可以低成本扩展 zall 的能力,形成正向飞轮。

**扩展类型**:

| 类型 | 描述 | 示例 | 难度 | 影响力 |
|---|---|---|---|---|
| **Skill** | 可复用 Goal 模板 (输入快捷方式) | review / explain / deploy | 低 | 中 |
| **Tool** | 新工具 (实现 Tool Protocol) | read_image / call_api / query_sql | 中 | 高 |
| **MCP Server** | MCP 协议暴露的工具/资源 | docker / github / slack | 低 | 高 |
| **Judge** | 新判定主体 (实现 Judge Protocol) | 统计检验 / 安全审计 / 性能基准 | 中 | 高 |
| **World Model** | 世界模型实现 (实现 WorldModel Protocol) | 代码图 / 知识图谱 / 神经网络 | 高 | 极高 |
| **Agent Definition** | 预定义 agent 身份 + 工具集 + 权限 | 安全审计 agent / 代码审查 agent | 低 | 中 |
| **Theme / UI** | 终端 UI 主题 | dark / monokai / 自定义 | 极低 | 低 |

**扩展机制**:

1. **插件系统 (Plugin System)**:
   - 插件通过 `pyproject.toml` 的 entry points 注册
   - 插件可以注册: Skills / Tools / Judges / World Models / Agent Definitions
   - 插件安装: `zall plugin install <git-url>` 或 `pip install zall-plugin-<name>`
   - 插件隔离: 每个插件在独立进程中运行 (通过 IPC 通信),防止插件崩溃影响核心

2. **Skill 格式**:
   - 极简 TOML (`.zall/skills.toml` 或 `~/.zall/skills.toml`)
   - `[[skills]]` 数组: name / description / prompt
   - prompt 支持多行 `"""`; 占位符 `{input}` 在调用时被参数替换
   - 项目级 > 用户级同名覆盖
   - 文件缺失 / 坏 TOML / 单个 skill 缺 prompt → 跳过该 skill 或整文件返回 [] (失败安全)

3. **Tool 注册**:
   - 工具通过 `ToolRegistry` 注册
   - 每个工具声明 `ToolCapabilities` (is_read_only / tool_scope)
   - 工具自动纳入 Authority Gate (根据 is_read_only 默认 whitelist 或 greylist)

4. **MCP Server 配置**:
   - `.zall/mcp.toml` 声明 `[[servers]]` (name/command/args[]/env{})
   - 项目级 > 用户级同名覆盖
   - MCP 工具默认 greylist (deny-by-default)

**激励**:

1. **插件市场**: 建立 zall 插件市场 (plugins.zall.dev),开发者可以发布插件,
   用户按使用量付费 (或免费)。市场提供:
   - 插件评分和评论
   - 安全审计 (自动扫描恶意代码)
   - 兼容性测试 (自动测试与最新 zall 版本的兼容性)

2. **贡献者计划**:
   - 核心贡献者获得 TC 席位 (参与治理)
   - 插件开发者获得推广资源 (首页推荐、博客采访)
   - 评估基准贡献者获得署名权

3. **黑客马拉松**: 定期举办 zall 黑客马拉松,奖励:
   - 最佳新 Skill
   - 最佳新 Tool
   - 最佳 Judge
   - 最佳 World Model
   - 最佳生态整合

### 8.5 Layer 3: 应用生态 (用户触达)

**目标**: 让终端用户通过熟悉的产品形态使用 zall,降低 adopt 门槛。

**应用类型**:

1. **zall IDE 插件**: VS Code / JetBrains 插件,在 IDE 内直接调用 zall。
   - 类似 Cursor 的体验,但开源、模型无关、可验证
   - 与 LSP 集成 (代码诊断 + agent 修复)

2. **zall CI**: CI/CD 集成,zall 自动修复 CI 失败。
   - GitHub Actions / GitLab CI 插件
   - CI 失败 → zall 分析 → 自动修复 → 提交 PR

3. **zall 科研平台**: 科研实验室专用平台。
   - 与 Benchling / LabArchives 集成 (ELN → zall 分析)
   - 假设管理 + 实验设计 + 结果分析 + 知识图谱

4. **zall 机器人控制台**: 具身 agent 的控制中心。
   - ROS2 集成
   - 机器人任务编排 + 监控 + 审计

**策略**:

1. **先做 IDE 插件**: 这是开发者最熟悉的形态,adopt 门槛最低。
   IDE 插件的用户基数最大,可以反哺插件生态 (开发者用 zall 写插件)。

2. **再做 CI 集成**: CI 是 zall 的"杀手级场景"——CI 失败是明确的 Goal,
   修复结果是可验证的 (CI 通过),完美契合 zall 的 Verifiability 维度。

3. **科研平台和机器人控制台长期做**: 这两个场景需要领域深耕,
   但 ROI 最高 (科研的 ROI 最高,机器人的壁垒最高)。

### 8.6 社区建设

**目标**: 建立一个活跃的、自生长的 zall 社区。

**策略**:

1. **文档第一**: 所有设计决策、架构定义、API 文档必须在合并代码之前完成。
   文档是社区的"宪法"——没有文档,社区无法自治。

2. **透明治理**: 所有 TC 决策公开讨论 (GitHub Discussions),
   重大变更需要经过 RFC (Request for Comments) 流程。

3. **新手友好**:
   - `good first issue` 标签 (适合新贡献者的问题)
   - 贡献指南 (CONTRIBUTING.md) 详细到"第一步该做什么"
   - 导师计划 (新贡献者配对资深贡献者)

4. **内容生态**:
   - 博客: 每月发布技术深度文章 (架构解读、案例分析、生态更新)
   - 教程: 从"5 分钟上手"到"构建你自己的 Judge"的完整教程体系
   - 案例: 真实用户案例 (如何用 zall 修复 bug / 做科研 / 控制机器人)

5. **多语言**: 当前文档以中文为主,但核心文档 (MASTER.md、API 参考) 必须有英文版。
   国际社区是生态的关键——没有英文,zall 永远只是中国项目。

---

## 9 迭代策略

### 9.1 为什么迭代策略重要

zall 的架构是雄心勃勃的——六维本体论 + 四层架构 + 五个领域扩展 + 六个演进 Phase。
如果没有清晰的迭代策略,团队会陷入"什么都想做的陷阱",
最后每个功能都做不好。

**迭代策略的核心原则**: **每次只做一件事,但做的那件事必须为未来铺路。**

### 9.2 增量开发规则

#### 规则 1: 单 Primitive 原则 (对应 IPR-2)

每次 implement step 仅落 **1 个 primitive** + 它的 invariant 测试。
Primitive 是代码投影 (Interface 已编译通过 + invariant 测试 fail 在反例存在时通过)。

**反例**: "顺便把 Refiner 和 Judge 一起接了" → 违反 IPR-2,回退。

#### 规则 2: 不变量优先原则

在写新功能之前,先写不变量测试。不变量测试定义了"什么绝对不能被破坏"。
只有不变量测试通过,新功能才能合并。

**反例**: "先上线功能,测试后补" → 违反 IPR-0,回退。

#### 规则 3: 向后兼容原则

核心层 (src/zall/core/) 的公共 API 在 v1.x 期间保持向后兼容。
破坏性变更必须:
1. 提前 2 个版本发 deprecation warning
2. 提供迁移指南
3. 在 CHANGELOG 中醒目注明

#### 规则 4: 文档先行原则

任何新功能的 PR 必须包含:
1. 对应文档章节的更新 (引用 MASTER.md 的节号)
2. API 文档 (docstring + 示例)
3. 变更日志 (CHANGELOG 条目)

缺少任何一项,PR 不被 review。

#### 规则 5: 渐进式暴露原则

新功能先在实验中暴露 (alpha),稳定后进入 beta,
充分测试后进入 stable。每个阶段有明确的准入门槛:

| 阶段 | 门槛 | 用户 |
|---|---|---|
| alpha | 不变量测试通过 + 基本功能可用 | 核心开发者 |
| beta | alpha + 边缘case测试 + 文档完整 | 早期 adopter |
| stable | beta + 社区反馈 + 性能优化 | 所有用户 |

### 9.3 Phase 内的迭代节奏

每个 Phase 内部采用 **2 周 sprint** 节奏:

| 周 | 活动 | 产出 |
|---|---|---|
| Week 1 | Primitive 实现 + invariant 测试 | 可编译的 primitive + fail 的反例测试 |
| Week 2 | 集成测试 + 文档 + 边缘 case | 可合并的 PR |

**关键**: 每个 sprint 的产出必须是**可合并的**——不是"快做完了",
而是"可以合入主干"。如果 sprint 结束时不能合并,说明 scope 太大,
下个 sprint 必须缩小。

### 9.4 跨 Phase 的依赖管理

Phase 之间有严格的依赖关系:

```
Phase 0 (本体论) → Phase 1 (对齐) → Phase 2 (降摩擦)
                                          ↓
Phase 3 (科研) ← Phase 2 (降摩擦) → Phase 4 (具身)
                                          ↓
                              Phase 5 (社会) → Phase 6 (可验证)
```

**关键依赖**:
- Phase 1 依赖 Phase 0 (本体论必须先立)
- Phase 2 依赖 Phase 1 (裂缝必须先补)
- Phase 3 和 Phase 4 可以并行 (科研和具身是独立领域扩展)
- Phase 5 依赖 Phase 3/4 (多 agent 需要单体 agent 先成熟)
- Phase 6 依赖 Phase 5 (可验证推理需要多 agent 验证交叉)

**例外**: Phase 3 的 Science Provenance (数据溯源) 可以提前到 Phase 1 实现,
因为它是 Verifiability 的自然扩展,不依赖科研维度的其他部分。

### 9.5 回退策略

如果某个 Phase 遇到不可逾越的障碍:

1. **记录教训**: 在文档中记录"为什么此路不通",作为后续工作的参考。
2. **缩小 scope**: 把当前 Phase 拆成更小的 step,看是否有子 step 可以完成。
3. **切换平行 Phase**: 如果 Phase N 卡住,切换到 Phase N+1 (如果不依赖 N) 或 Phase N-1 (如果 N-1 还有未完成)。
4. **重新评估**: 如果连续 3 个 sprint 没有可合并产出,重新评估该 Phase 的设计是否合理。

### 9.6 质量门禁

每个 PR 合并前必须通过:

```
CI Pipeline:
├── 代码质量
│   ├── ruff check (lint)
│   ├── mypy (类型检查)
│   ├── pytest (单元测试 + 不变量测试)
│   └── pytest --cov (覆盖率 ≥ 90%)
├── 不变量验证
│   ├── test_loop_invariants.py
│   ├── test_goal_invariants.py
│   ├── test_confirm_gate_invariants.py
│   ├── test_verifiability_invariants.py
│   └── ... (所有 invariant 测试)
├── 集成测试
│   ├── test_integration.py
│   ├── test_integration_v040.py
│   └── test_real_api_v030.py
├── 文档检查
│   ├── CHANGELOG 条目存在
│   ├── docstring 完整
│   └── 设计文档引用 (MASTER.md 节号)
└── 安全扫描
    ├── 敏感信息泄露检测 (API key / token)
    └── 依赖漏洞扫描
```

任何一项 fail,PR 不被合并。

---

## 10 不变量

以下不变量在所有 Phase 中必须守住。任何修改实现让不变量失效的 PR,
对应的 invariant 测试必须 fail——这是 IPR-0 的要求。

### I-0 六维完整性 (Ontological Completeness)
任何 agent 实例必须实现全部六维 (Identity/Commitment/Perception/Authority/Accountability/Verifiability)。
缺一维的"agent"不是 agent,是工具。

**不变量测试**: `test_agent_has_all_dimensions()` — 构造一个 AgentLoop,
断言它必须有 Identity / Commitment / Perception / Authority / Accountability / Verifiability 六个组件。

### I-1 承诺不可撤销 (Commitment Irrevocability)
Goal 一旦锁定,不可隐式修改。修改必须显式 (新 Goal + 原因记录)。
放弃必须记录原因。

**不变量测试**: `test_goal_cannot_be_modified_after_lock()` — 锁定 Goal 后,
尝试修改 Goal 的 statement,断言修改失败或产生新的 GoalTriple。

### I-2 无 Goal 不调工具 (No Tool Without Goal)
timeline 中第一条 `tool_call_start` 之前必须有一条 `goal_statement` 且该 `goal_statement` 经 `user_confirm`。

**不变量测试**: `test_no_tool_calls_without_locked_goal()` — 构造一个没有 Goal 的 AgentLoop,
尝试执行工具调用,断言工具调用被拒绝。

### I-3 物理不可逆性 (Physical Irreversibility)
在具身场景中,所有安全机制按最坏场景 (物理伤害) 设计。
反射层 (L1) 永远优先于规划层 (L2) 和策略层 (L3)。

**不变量测试**: `test_emergency_stop_overrides_planning()` — 触发急停,
断言即使 L2/L3 规划了动作,L1 反射层仍然拦截。

### I-4 感知不确定性 (Perceptual Uncertainty)
任何从感知到状态的映射必须携带置信度。框架不得假设"观察=事实"。

**不变量测试**: `test_percept_has_confidence()` — 构造一个 Percept,
断言它必须有 confidence 字段 ∈ [0,1]。

### I-5 Verifiability 不打折 (Verifiability Non-negotiable)
快速路径可以跳过用户确认,但**不能跳过 timeline 记录**。
所有动作 (无论快速/严格) 必须完整记录到 Verifiability timeline。

**不变量测试**: `test_fast_path_still_records_timeline()` — 在快速路径模式下执行动作,
断言 timeline 中有对应的 tool_call_start / tool_call_end 事件。

### I-6 模型无关性 (Model Irrelevance)
核心层零模型 SDK import。所有模型相关细节在适配层。

**不变量测试**: `test_core_has_no_model_sdk_import()` — 扫描 `src/zall/core/` 下的所有 import,
断言没有 openai / anthropic / google.generativeai 等模型 SDK。

### I-7 分层严谨 (Tiered Rigor)
内部严谨 ≠ 外部复杂。核心层保持最简抽象 (≤6 维),领域扩展和 UI 层按需暴露复杂度。

**不变量测试**: `test_core_has_six_dimensions()` — 扫描 `src/zall/core/` 的公共 API,
断言核心层暴露的抽象不超过 6 个维度 (Identity / Commitment / Perception / Authority / Accountability / Verifiability)。

### I-8 Authority Gate 不可跳过 (Authority Gate Non-bypassable)
任何工具调用必须经过 Authority Gate 的判定。不允许绕过 Gate 直接执行工具。

**不变量测试**: `test_all_tools_pass_gate()` — 构造一个 AgentLoop,执行工具调用,
断言 timeline 中有对应的 GATE_DECISION 事件。

### I-9 Timeline 链式完整性 (Timeline Chain Integrity)
timeline 中每条事件的 prev_hash 必须等于前一条的 compute_hash()。
首条的 prev_hash 必须是 genesis hash。

**不变量测试**: `test_timeline_chain_is_valid()` — 构造一个 run 的 timeline,
断言 verify_chain() 返回 True。

### I-10 负结果平等 (Negative Result Equality)
负结果与正结果同等存储、同等索引、同等珍视。"假设被证伪"不是"失败",是"有价值的信息"。

**不变量测试**: `test_negative_results_equally_indexed()` — 在 Knowledge Graph 中
插入正结果和负结果,断言两者在查询中的权重相同。

---

## 11 与现存框架对比

### 11.1 定位对比表

| 维度 | Claude Code | OpenHands | SWE-agent | 具身框架 (RT-2等) | 科研框架 (Elicit等) | zall (当前) | zall (未来 v3.0) |
|---|---|---|---|---|---|---|---|
| **Identity** | 无 | 无 | 无 | 无 (身体隐式) | 无 | 部分 (AgentDefinition) | ✅ 完整 |
| **Commitment** | 无 (隐式) | 无 | 无 | 无 | 无 | ✅ GoalTriple | ✅ + Hypothesis |
| **Perception** | 无 (文件隐式) | 无 | 无 | 传感器管道 | 文献搜索 | Context | ✅ World Model |
| **Authority** | 三层 (闭源) | Docker 沙箱 | 无 | 无 | 无 | ✅ 三层 + Gate | ✅ + 物理安全 |
| **Accountability** | 用户判定 | 无 | SWE-bench | 无 | 无 | ✅ 三态 Judge | ✅ + 科学判定 |
| **Verifiability** | Transcript | 无 | 无 | 无 | 无 | ✅ 链式哈希 | ✅ + 物理溯源 |
| **Learning** | 会话记忆 | 无 | 无 | Fine-tune | 无 | AutoLearn | ✅ 知识图谱 |
| **具身支持** | 无 | 无 | 无 | ✅ 框架 | 无 | 无 | ✅ 统一 |
| **科研支持** | 无 | 无 | 无 | 无 | ❌ 窄化 | 部分 (可扩展) | ✅ 原生 |
| **多 Agent** | Subagent | 无 | 无 | 无 | 无 | Subagent | ✅ 社会协议 |
| **模型无关** | Claude only | 多模型 | 多模型 | 神经网络 | LLM | ✅ 严格 | ✅ 严格 |
| **可验证推理** | 无 | 无 | 无 | 无 | 无 | 无 | ✅ 神经×符号 |
| **开源** | ❌ | ✅ | ✅ | 部分 | 部分 | ✅ | ✅ |
| **生态** | 大 (Anthropic) | 中 (社区) | 小 (研究) | 中 (NVIDIA) | 小 | 小 | 目标: 中-大 |

### 11.2 差异化定位

**zall (未来) 是唯一同时覆盖以下维度的框架**:

1. **承诺体系** (Goal/Commitment) — 从 BDI 的"意图即承诺"工程化
2. **权限控制** (Authority) — 三层名单 + 确认门 + 物理安全
3. **可验证性** (Verifiability) — 链式哈希 + 外部锚点 + 因果溯源
4. **感知理解** (Perception/World Model) — 从传感器到状态估计 + 世界模型
5. **科研探索** (Hypothesis/Experiment) — 假设管理 + 实验闭环 + 负结果
6. **具身行动** (Embodied Action) — 动作基元 + 实时调度 + 物理约束
7. **模型无关** (Model Agnostic) — 核心零模型 SDK import
8. **可验证推理** (Verifiable Reasoning) — 神经直觉 × 符号验证 × 世界模型 × 测试时搜索

**这 8 个维度的交集是空白的**——没有任何现存框架同时覆盖超过 4 个。
zall 的目标是填补这个空白。

### 11.3 与 Claude Code 的关系

**不是竞争对手,是互补定位**:

- Claude Code 是"对话式 agent"——最优场景是开发者与 AI 的交互式编程。
- zall 是"可验证推理系统"——最优场景是需要可靠性、可审计性、可复现性的任务。

**zall 可以集成 Claude Code 作为模型后端** (通过 OpenAI 兼容适配器),
但 zall 的架构不依赖 Claude Code——zall 的核心是六维本体论,不是模型。

### 11.4 与 Grok Build 的关系

**Grok Build 是 zall 的灵感来源之一**,但 zall 在以下方面超越了 Grok Build:

1. **目标体系**: Grok Build 无显式目标体系,zall 有 GoalTriple + HypothesisGoal。
2. **可验证性**: Grok Build 无链式哈希/外部锚点,zall 有 RunRecorder + TrustAnchor。
3. **科研维度**: Grok Build 无科研抽象,zall 有 Hypothesis/Experiment/Evidence。
4. **理论根基**: Grok Build 无明确的理论基础,zall 有 FEP + 可验证推理系统。

---

## 附录 A: 术语表

| 术语 | 定义 |
|---|---|
| **Commitment** | agent 对目标做出的不可撤销承诺 (比"Goal"更强,含执行义务) |
| **BodySchema** | agent 的具身形态定义 (传感器 + 执行器 + 运动学 + 约束) |
| **WorldModel** | agent 对世界的内部表征,用于预测行动后果 |
| **MotorPrimitive** | 参数化的动作序列,封装底层控制细节 |
| **Hypothesis** | 可证伪的科学假设 (有版本、状态、证据、衍生关系) |
| **NegativeResult** | 负结果——假设被证伪或实验失败,但包含有价值信息 |
| **ScienceProvenance** | 科研溯源——实验 protocol + 数据 + 代码 + 环境的完整血缘 |
| **PhysicalProvenance** | 物理溯源——传感器数据 + 动作 + 状态估计的完整日志 |
| **TieredRigor** | 分层严谨——内部严谨,外部顺滑,按需暴露复杂度 |
| **RealTimeScheduler** | 实时调度器——保证具身控制周期内的动作执行 |
| **MarkovBlanket** | (FEP) 系统内部状态与外部状态之间的统计边界,定义了"个体"的身份 |
| **GenerativeModel** | (FEP) 系统对世界如何产生感官数据的内部模型 |
| **ExpectedFreeEnergy** | (FEP) 行动策略的预期自由能,包含信息获取价值 (epistemic) 和偏好实现价值 (pragmatic) |
| **VerifiableReasoningSystem** | 可验证推理系统——神经直觉 × 符号验证 × 世界模型模拟 × 测试时搜索 |

---

## 附录 B: 与代码的映射

### B.1 六维与现有代码的映射

| 六维 | zall 现有模块 | 需要改动 | 新增模块 |
|---|---|---|---|
| Identity | `core/agent.py` (AgentDefinition) | 提升到本体论级别,加 BodySchema | `core/identity.py` |
| Commitment | `core/goal.py` (GoalTriple) | 重命名 Goal→Commitment,加 HypothesisGoal | `core/hypothesis.py` |
| Perception | `core/context.py` (Context) | 扩展为 Perception Engine + World Model | `core/perception/` |
| Authority | `core/safety.py` + `core/gate.py` | 加 PhysicalSafety + ExperimentAuthority | `core/safety/physical.py` |
| Accountability | `core/accountability.py` | 加 ScientificJudge + PhysicalJudge | `core/judge/scientific.py` |
| Verifiability | `core/verifiability.py` | 加 ScienceProvenance + PhysicalProvenance | `core/provenance/` |
| Memory | `core/chat_state.py` + AutoLearn | 扩展为 Semantic/Episodic/Procedural Memory | `core/memory/` |
| Execution | `core/executor.py` | 加 MotorExecutor + RealTimeScheduler | `core/motor/` + `core/realtime/` |

### B.2 领域扩展与现有代码的映射

| 领域扩展 | 现有模块 | 需要改动 | 新增模块 |
|---|---|---|---|
| Coding Kit | `tools/` + `cli/commands/` | 无大改 (基本兼容) | 无 |
| Embodied Kit | 无 | 全部新增 | `extensions/embodied/` + `core/motor/` + `core/perception/` |
| Science Kit | 无 | 全部新增 | `extensions/science/` + `core/hypothesis.py` + `core/experiment.py` + `core/evidence.py` |
| Social Kit | `spawn_subagent.py` | 扩展为多 agent 协议 | `core/social/` |

### B.3 评估体系与现有代码的映射

| 评估维度 | 现有模块 | 需要改动 | 新增模块 |
|---|---|---|---|
| 达成率 | 无 | 全部新增 | `core/eval/achievement.py` |
| 越界率 | 无 | 全部新增 | `core/eval/authority.py` |
| 可证伪率 | 无 | 全部新增 | `core/eval/falsifiability.py` |
| 可复现率 | `test_replay_invariants.py` | 扩展到离线 metric | `core/eval/reproducibility.py` |
| 资源效率 | 无 | 全部新增 | `core/eval/efficiency.py` |

### B.4 不变量测试与文档的映射

| 不变量 | 对应文档节 | 测试文件 |
|---|---|---|
| I-0 六维完整性 | §1.2 + §10 I-0 | `test_agent_dimensions.py` (新增) |
| I-1 承诺不可撤销 | §10 I-1 | `test_commitment_irrevocability.py` (新增) |
| I-2 无 Goal 不调工具 | §9.3 (DESIGN.md) + §10 I-2 | `test_tool_calls_without_locked_goal.py` (已有, spec/aas) |
| I-3 物理不可逆性 | §10 I-3 | `test_emergency_stop_priority.py` (新增) |
| I-4 感知不确定性 | §10 I-4 | `test_percept_confidence.py` (新增) |
| I-5 Verifiability 不打折 | §10 I-5 | `test_fast_path_timeline.py` (新增) |
| I-6 模型无关性 | IPR-3 + §10 I-6 | `test_core_no_model_sdk.py` (已有) |
| I-7 分层严谨 | §10 I-7 | `test_core_six_dimensions.py` (新增) |
| I-8 Authority Gate 不可跳过 | §10 I-8 | `test_all_tools_pass_gate.py` (新增) |
| I-9 Timeline 链式完整性 | §6.1 (DESIGN.md) + §10 I-9 | `test_verifiability_invariants.py` (已有) |
| I-10 负结果平等 | §10 I-10 | `test_negative_results_equality.py` (新增) |

---

## 文档维护规则

1. **本文档是单一真相来源**: 其他文档 (DESIGN.md、FUTURE_ARCH.md、IMPL.md) 是历史投影,
   以本文档为准。冲突时以本文档为准。

2. **版本化**: 每次重大更新创建新版本 (v1.0 → v1.1),旧版本保留在 Git 历史中。
   不原地修改——重大变更必须经过 RFC 流程。

3. **状态管理**: 每个节的状态 (SETTLED/PENDING/OPEN) 必须与实际进展一致。
   状态的变更必须经过红蓝对抗 (至少 1 人挑战,1 人辩护,结论记录在 PR 讨论中)。

4. **代码-文档双向引用**: 每段实现代码必须引用文档节号 (如 `# Corresponds to MASTER.md §4.2.2`),
   每节文档必须引用对应代码文件 (如 `实现: src/zall/core/goal.py`)。
   CI 检查: PR 描述必须包含文档节号引用。

5. **社区贡献**: 任何人可以在 GitHub Issues 中提出对文档的质疑 (红蓝对抗),
   或在 PR 中提出修改建议。TC 审核修改,通过后合并。

---

*本文档是 zall 项目的宪法——所有后续工作以此为据。*
*所有论断可被红蓝对抗驳回——驳不倒才保留 (PR-0)。*
*本文档本身也在演进——今天的 SETTLED 可能被明天的红蓝对抗推翻。*
*这是框架的自反性 (reflexivity): 它对自己的规则也适用。*

---

## 12 执行真相表与路线 (v1.1)

> **本节是 v1.1 修订的核心**。它在 §1-§11 的"宣称"之外,
> 建立第二张表: **代码审计证实的实现深度**。两表并存是有意的--
> §1-§11 是本体论层面的"应该如此", §12 是工程层面的"现在如此"。
> 任何下游代码、对外宣传、生态合作,以 §12 为准, 不以 §1-§11 为准。
> §1-§11 的条目要升回 SETTLED, 必须先在 §12 把对应行刷成 REAL。

### 12.0 评级定义 (与外部代码审计对齐)

| 评级 | 含义 | 准入: 进入此级的判据 |
|---|---|---|
| **REAL** | 接口存在, 运行时被真实调用, 形成闭环 (有生产者和消费者) | 三者齐备: 定义 + 接线 + 不变量测试 (含反例) |
| **PARTIAL** | 接口存在且被部分调用, 但闭环断裂或覆盖不全 | 定义 + 接线存在; 缺消费者或缺测试 |
| **DECORATIVE** | 接口存在, 但运行时无消费者, 或消费者是 no-op | 定义存在; 无真实接线, 或接线指向空操作 |
| **ABSENT** | 接口不存在, 仅文档提及 | 无代码 |

### 12.1 能力真相表 (v1.1 基线, 2026-07-19 外部审计)

| 能力 (上溯 §1) | 宣称状态 | 实际评级 | 证据 file:line | 缺口 (升 REAL 需补) |
|---|---|---|---|---|
| Verifiability: 链式哈希 timeline | SETTLED | **REAL** (v1.1 增强) | `verifiability.py:75-114`, `loop.py` run 结束自检, `cli/commands/system.py` /verify | 运行时 verify_chain 自检 + CHAIN_BROKEN 事件 + /verify CLI 已就绪 |
| Verifiability: ed25519 TrustAnchor | SETTLED | **REAL** | `verifiability.py` FileTrustAnchor | CLI 暴露 `zall verify <run>` |
| Authority: 三层名单 + ConfirmGate | SETTLED | **REAL** | `gate.py:169-337`, `safety.py:256-346`, `executor.py:84-87` | (可选) 跨会话持久化 always_allow |
| Accountability: 三态编排 | SETTLED | **REAL** (v1.1 接通) | `accountability.py:252-318` base_judge 表 + from_verdicts, `loop.py:1727-1786` 多 Judge 编排, `loop_config.py` judges dict | - |
| Compactor: tool_call 配对保护 | (隐含 §4.5) | **REAL** (v1.1 修后) | `compactor.py:361-389` (双向) | system 状态丢失风险 OPEN |
| Checkpoint: 文件快照链 | (隐含 §4.5) | **REAL** | `checkpoint.py:64-288` | - |
| **Identity: AgentIdentity 接入 AgentLoop** | 部分 (§11) | **REAL** (评估修复轮) | `core/agent.py` AgentIdentity + `loop.py` identity/commitment/perception_engine/authority/accountability/verifiability 六维属性 + `agent_has_all_dimensions()`, `tests/test_ontology_invariants.py` (7 tests 含反例) | I-0/I-7 不变量已补齐 |
| Commitment: GoalTriple + Termination | SETTLED | **REAL** | `goal.py`, `loop.py` _check_termination | (可选) HypothesisGoal 见 §6.3 |
| **Perception: StateEstimate 进决策** | SETTLED | **REAL** (v1.1 修后) | `loop.py` _run_step_body (anomaly 熔断 + 状态摘要注入) | - |
| **Perception: World Model predict/anomaly** | SETTLED | **REAL** (v1.1 修后) | `loop.py` _handle_tool_use (predict 进 timeline), `perception/engine.py` | - |
| **Self-evolution: 建议闭环** | SETTLED | **REAL** | `auto_learn.py:321-373` (apply_suggestion 写盘到 skills/ + learn_overrides.json), `repl_ui.py:241-248` (auto-apply + config 层), `test_self_evolution_closed_loop.py` (6 tests) | 见 E2 |
| Plugin: 发现机制 | (§8 宣称) | **REAL** (v1.1) | `core/plugin_loader.py` discover_tools/discover_extensions (entry-points) + load_plugin_manifest | - |
| Plugin: 版本化 | (§8 宣称) | **PARTIAL** (v1.1) | `tool.py` get_tool_version/parse_semver/is_version_compatible, ToolRegistry.check_compatibility | 发现机制仍 ABSENT |
| Plugin: 沙箱 | (§8 宣称) | **REAL** (v1.1 进程级) | `core/sandbox.py` SubprocessSandbox (子进程隔离+超时), `plugin_loader.py` _PluginToolWrapper sandboxed (PLUGIN 默认 True) | 进程级隔离 DONE; 内存/网络/容器级隔离 OPEN (需 OS 支持) |
| Plugin: schema 强制校验 | (§8 宣称) | **REAL** (v1.1) | `tool.py` validate_tool_args (stdlib 子集), `executor.py` 执行前校验 | - |
| Embodied: MotorPrimitive 等 | §4 架构图有 | **ABSENT** | - | 不在路线内, 守接口不落码 (见 §12.4) |
| Science: Hypothesis/Experiment | §1.5 + §6.3 | **REAL** (v1.1 落码 + dogfood 验证) | `core/hypothesis.py`, `core/experiment.py`, `core/evidence.py`, `core/provenance.py`, `extensions/science/store.py`, `cli/commands/science.py`, `tools/science.py` (agent 工具) | E3.6 交互式 dogfood 已通过 (agent 自主用 science 工具创建假设+记录证据) |

### 12.2 文档条目降级 (v1.1 生效)

下列 §1-§11 的条目, 因 §12.1 证实为 PARTIAL/DECORATIVE/ABSENT,
其状态标记**正式降级**。在对应行升 REAL 前, 禁止作为下游依据:

- §4.2.3 Perception Engine 中的 "感知状态摘要注入到 context (供 safety judge 和 goal 判定使用)" -- 该具体论断降为 **OPEN** (loop.py:840 注释与实际行为不符)。
- §4.2.3 World Model "predict 预测行动后果" 在运行时被调用 -- 降为 **OPEN**。
- §6 自演化章节中 "apply_suggestion 改变系统行为" -- 降为 **OPEN**。
- §8 生态建设章节中 "插件系统支撑第三方生态" -- 整节降为 **PENDING** (缺四支柱)。
- §1.5 HypothesisGoal 作为 Commitment 扩展 -- 保留为概念定义, 但标注 "代码形态 ABSENT, 待 E3"。

### 12.3 执行路线 E0-E3 (替代旧 §7 Phase 表的近期段)

> 旧 §7 Phase 表保留为远期愿景。**E0-E3 是未来 4-8 周的硬约束路线**,
> 优先级严格递减, 前一阶段未 REAL 不开后一阶段 (PR-2 一步步来的工程投影)。
> 每个 E 项的"完成"判据是: §12.1 对应行刷成 REAL, 即定义+接线+不变量测试齐备。

#### E0 - 审计修复回填 (本次已完成, 记录在案)
- [x] GoalConfirm / help 测试适配 v0.6.0 语义
- [x] compactor 双向 tool_call/tool_result 配对保护 + 9 个反例测试
- [x] gemini SAFETY/RECITATION 截断不再静默映射 STOP
- 测试基线: 1247 passed, 0 failed

#### E1 - Perception 接通闭环 (目标: §12.1 两行 DECORATIVE -> REAL)
**完成判据**: StateEstimate 至少有 2 个真实消费点, 写入不变量测试。
- [x] E1.1 anomaly 熔断: `perception_engine.anomaly()==True` 时, timeline 记录事件 + 消息流注入 system 提示 (复用现有 system nudge 注入机制, 不新造通道)
- [x] E1.2 状态摘要注入: 关键状态相对上一步显著变化时 (git_modified 文件数变化 / lsp_errors 出现 / 置信度骤降), 注一行紧凑 system 摘要; 无变化不注 (反例)
- [x] E1.3 不变量测试: `test_perception_consumed_by_loop` -- 断言 anomaly=True 时消息流与 timeline 都有痕迹; 反例断言无变化时不注入
- [x] E1.4 修正 `loop.py:840` 注释使其与真实行为一致
- [x] E1.5 (可选) `perception_engine.predict()` 在写工具调用前被调用一次, 结果进 timeline (不强制进 model context, 避免上下文膨胀)

#### E2 - Self-evolution 接通闭环 (目标: §12.1 一行 DECORATIVE -> REAL)
**完成判据**: `apply_suggestion` 真正改变运行时行为, 可用测试证伪。
- [x] E2.1 `create_skill` 写真实文件到 `.zall/skills/` 并被 ToolRegistry 下次加载识别
- [x] E2.2 `adjust_judge` 修改 AgentConfig 的 judge 配置 (运行中只记 pending, 下次 run 生效; 不热改以避免一致性陷阱)
- [x] E2.3 `get_config_overrides()` 返回值在 `repl_ui.py` 中真实写回 config 层 (目前被丢弃)
- [x] E2.4 不变量测试: 应用建议后, 新 run 的行为可观测改变 (反例: 未应用时行为不变)
- [x] E2.5 文档: §6 自演化章节状态从 OPEN 升回 SETTLED

#### E3 - Science Kit 首个闭环 (目标: §12.1 一行 ABSENT -> REAL)
**完成判据**: 用 ~/GF-consistency-framework 项目跑通一次真实 hypothesis 闭环。
**这是 zall 与所有 coding agent 拉开差异的关键**, 也是六维本体论首次接受外部现实审判。
- [x] E3.1 `core/hypothesis.py`: Hypothesis (claim + confidence + version + status) + 衍生关系 (revised_from, falsified_by)
- [x] E3.2 `core/experiment.py`: ExperimentGoal (protocol + data_snapshot + result)
- [x] E3.3 `core/evidence.py`: Evidence + NegativeResult (同等索引, 对应 I-10)
- [x] E3.4 ScienceProvenance: protocol + data + code + env 的完整血缘, 接入 Verifiability timeline
- [x] E3.5 CLI: `/science new|list|show|evidence|falsify|revise` (REPL 内)
- [x] E3.6 dogfood: GF-consistency 的 H1(positive)/H2(falsified)/H3(revised) 三假设闭环在 test_science_cli_invariants.py::TestDogfoodScenario 中跑通, 含 NegativeResult (I-10)
- [x] E3.7 红蓝对抗记录: v1.1 dogfood 结果已记入 §1.3.1 (6 条假设, 2 条认输降 OPEN, 4 条确认)

#### E4 - 交互层还债 (与 E1-E3 并行, 独立分支)
**完成判据**: 三项交互差距关闭, 各有测试。
- [x] E4.1 autosave 去 PID 化 + 原子写入 (临时文件 + rename), 崩溃恢复可工作
- [x] E4.2 中断后"丢弃本次回复"语义: Ctrl+C 在流式生成中, 半成品 model message 不入 loop.messages
- [x] E4.3 权限决策跨会话持久化: `.zall/always_allow.json`
- [ ] E4.4 (可选) 多行输入: `\` 续行 或 alt+enter

### 12.4 守接口不落码原则 (Embodied 维度的明确策略)

具身维度 (MotorPrimitive / RealTimeScheduler / BodySchema 完整实例) 在 E0-E3 期间**不写实现代码**。
理由 (诚实声明):
1. 无机器人/仿真环境/实时测试手段, 此刻写代码只会制造 DECORATIVE 抽象, 违反 PR-1。
2. 六维本体论的价值在于"具身来时不用推翻核心"--守 §4.2.1 的 BodySchema 接口形态 + FR-1/FR-2 原则即可。
3. 资源集中于 Science Kit (E3), 因为它是六维中唯一能立刻真实闭环的领域扩展。

当且仅当以下条件齐备时, 启动 Embodied 落码 (届时开 E5+):
- 有具身硬件或仿真器 (Isaac/ROS2) 可对接
- 有实时约束的可度量测试设施
- Science Kit 已 REAL (E3 完成), 证明非具身领域扩展的抽象可行

### 12.5 修订的闭环规则

本节是 PR-0 (自证伪) 对文档自身的执行机制:

1. **任何 §1-§11 条目要升回 SETTLED, 必须先在 §12.1 把对应行刷成 REAL**。两表不一致时, 以 §12 为准。
2. **新增任何宣称, 必须同时在 §12.1 开一行并标 ABSENT**, 直到代码补齐。禁止"先写 §1 后补 §12"。
3. **E0-E3 每完成一项, 对应 §12.1 行的评级更新 + §12.3 checkbox 勾选**, 在同一次 commit 内完成。
4. **外部审计常态化**: 每 3 个月或每个 E 阶段完成时, 跑一次独立代码审计, 结果回填 §12.1。

---

*v1.1 修订到此为止。本体论未动, 落差已显。下一步是 E1。*

---

## 13 自优化循环: AI 驱动的持续自我改进 (AI Self-Optimization)

> 状态: **PENDING** (首次使用后转 SETTLED)
> 对应 §12: 本节本身的执行真相表在 §13.8, 自优化循环对自身适用 PR-0 (自证伪)。
> 设计起源: 像 Claude Code 那样用 AI 自己持续优化 zall 框架。

### 13.0 元规则

1. **自优化循环是 §1-§12 的执行引擎**, 不是替代。所有优化产出必须遵守 PR-0/PR-1/PR-2/PR-3/PR-4 和 IPR-0/IPR-1/IPR-2/IPR-3/IPR-4。
2. **本节对自身适用**: 自优化循环的每一轮都必须能证伪自身——如果循环退化 (测试减少、REAL 降级), 必须能被检测到并回滚。
3. **AI 是助手, 不是决策者**: 所有优化建议最终由人类用户决定是否 commit。AI 不得自主 commit/push。
4. **不得绕过 §12 真相表**: 任何优化必须同步更新 §12 对应行, 否则视为无效。

### 13.1 自优化循环 (Observe-Diagnose-Propose-Execute-Verify-Reflect)

自优化循环是六步闭环, 每一步都可被人类打断、质疑、回滚。

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AI Self-Optimization Loop                        │
│                                                                     │
│  ① Observe: 读取全量数据源, 建立当前基线                             │
│       ↓ 输出: 基线报告 (测试数、REAL/OPEN/PARTIAL/DECORATIVE 项数)     │
│  ② Diagnose: 找出 DECORATIVE/PARTIAL/OPEN 项 + 交互痛点 + 性能瓶颈   │
│       ↓ 输出: 优先级排序的改进清单 (每项附 §12 行号 + 证据)           │
│  ③ Propose: 提出改进方案, 遵循 IPR 规则 (先文档后代码, 单 primitive)   │
│       ↓ 输出: 提案 (改动摘要 + 预期影响 + 风险)                       │
│  ④ Execute: 实现代码 + 测试 + 文档更新                               │
│       ↓ 输出: 代码变更 + 测试结果 + 文档更新                          │
│  ⑤ Verify: 跑全量测试 + dogfood 验证 + 红蓝对抗                      │
│       ↓ 输出: 验证报告 (pass/fail + §12 更新)                         │
│  ⑥ Reflect: 更新 §12 真相表 + 记录教训 + 更新 CHANGELOG              │
│       ↓ 输出: 本轮总结 + 下一轮建议                                   │
│                                                                     │
│  循环结束后: 提示用户是否继续下一轮                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 13.1.1 Observe (观察)

AI 读取以下所有数据源, 建立当前基线的完整快照:

| 数据源 | 路径/方式 | 提取内容 |
|---|---|---|
| §12 执行真相表 | MASTER.md §12.1 | 每项能力评级 (REAL/PARTIAL/DECORATIVE/ABSENT) + 缺口描述 |
| §12.3 执行路线 | MASTER.md §12.3 | E0-E16 完成状态 |
| §12.2 文档降级 | MASTER.md §12.2 | 当前 OPEN 项 |
| 测试结果 | `pytest --tb=short --no-header -q 2>&1 \| tail -5` | 通过数 / 失败数 / 跳过数 |
| 测试覆盖率 | `pytest --cov --cov-report=term 2>&1 \| tail -10` | 总覆盖率 + 各模块覆盖率 |
| 红蓝对抗记录 | MASTER.md §1.3.1 | 历史 dogfood 发现 + 未决假设 |
| CHANGELOG | CHANGELOG.md | 最近迭代模式 + 已知问题 |
| 性能指标 | §3.1.5 定义的 p50/p90/p99 | token 数 / 步数 / 耗时 (如有工具) |
| IMPl.md | IMPL.md | IPR 规则 (确保优化不违反) |
| 代码模块 | `src/zall/` 目录结构 | 模块间依赖 + 接口定义 |

**输出格式**: 基线报告, 含:
- 总测试数: `N passed / M failed / K skipped`
- 覆盖率: `X%`
- §12.1 评级分布: `R 个 REAL, P 个 PARTIAL, D 个 DECORATIVE, A 个 ABSENT`
- §12.2 OPEN 项数: `O 个`
- 最近 3 轮迭代模式: CHANGELOG 分析

#### 13.1.2 Diagnose (诊断)

基于基线报告, 按以下优先级诊断:

1. **DECORATIVE 项 → REAL**: 接口存在但无消费者——这是最高优先级, 因为 DECORATIVE 是"假完成" (§12.0 定义)。
2. **PARTIAL 项 → REAL**: 闭环断裂或覆盖不全——次高优先级。
3. **ABSENT 项 → REAL**: 文档提了但代码不存在——仅限于 E 路线内的项, 不碰远期愿景。
4. **OPEN 项 → 关闭**: §12.2 残余 OPEN——修复已有问题比开新功能优先。
5. **交互痛点**: 从 dogfood 记录和 CHANGELOG 中提取用户反馈 (如"步数限制不合理"→ E14)。
6. **性能瓶颈**: §3.1.5 指标中的异常值 (p99 突增等)。

**约束**: 每轮只诊断 1-3 个问题 (与 13.3 约束一致)。不得一次性列出 10 个问题——那是信息过载, 不是诊断。

**输出格式**:
```
## 诊断结果 (优先级排序)

1. [P0] 能力: <§12.1 行名>
   当前评级: DECORATIVE | PARTIAL | ABSENT
   证据: (引用代码行 + 测试结果)
   建议: 升 REAL 需补 (从 §12.1 缺口描述提取)

2. [P1] OPEN 项: <描述>
   当前状态: OPEN
   证据: (引用 dogfood 记录 + 用户反馈)
   建议: 关闭方案

3. [P2] 性能/交互: <描述>
   指标: before → after 预期改善
```

#### 13.1.3 Propose (提案)

基于诊断结果, 提出具体改进方案。**必须遵循 IPR 规则**:

- **IPR-0**: 每个改进必须附带 ≥1 个 invariant 测试 (含反例)
- **IPR-1**: 每个改进必须引用文档章节号 (MASTER.md §12 行或 §1-§11 某节)
- **IPR-2**: 每轮只改 1-3 个 E 项, 单步只落 1 个 primitive
- **IPR-3**: 核心层不得引入模型 SDK
- **IPR-4**: 在 primitive SETTLED 前不得写主 Loop

**提案模板**:
```
## 提案: <标题>

### 目标
- 对应 §12.1 行: <行名>
- 当前评级: <DECORATIVE|PARTIAL|ABSENT>
- 目标评级: REAL
- 对应 E 路线: <E 编号> (若无则标注 NEW)

### 改动范围
1. <文件路径>: <改动描述> (对应 §文档节号)
2. <文件路径>: <改动描述> (对应 §文档节号)
3. <文件路径>: <改动描述> (对应 §文档节号)

### 测试计划
- 新增测试: <文件路径> (含反例)
- 全量测试: 预期 <N> passed / 0 failed

### 风险
- 退化风险: <描述>
- 回滚方案: <描述>

### 预期影响
- §12.1 评级变化: <X> 行 REAL
- 测试变化: +<N> 测试
- OPEN 关闭: <O> 项
```

#### 13.1.4 Execute (执行)

实现提案中的改动。**执行顺序严格固定**:

1. **先文档, 后代码**: 先更新 MASTER.md 对应节 (转 SETTLED 或开新 PENDING 行), 再写代码。违反此顺序 = 违反 IPR-1。
2. **先测试, 后实现**: 先写 invariant 测试 (含反例), 确认测试 fail, 再写实现代码让测试通过。违反此顺序 = 违反 IPR-0。
3. **单步不跨**: 单次改动只落 1 个 primitive + 它的 invariant 测试。禁止"顺手多写一点" (IPR-2)。
4. **文档同步**: 每次代码改动后, 同步更新 §12.1 对应行的评级和证据 file:line。
5. **不得 commit**: 所有改动留在工作区, 由用户决定是否 commit。

#### 13.1.5 Verify (验证)

执行后验证, 必须全部通过:

1. **全量测试**: `pytest --tb=short --no-header -q 2>&1` — 预期 0 failed。
2. **不变量测试**: 所有 invariant 测试 (含新增和已有) 必须通过。
3. **dogfood 验证**: 至少 1 次真实 API 调用验证 (如 `zall "简单任务"` 跑通基础流程)。
4. **红蓝对抗**: AI 自问"我的改进能否被驳倒?"——改进必须附带可证伪声明 (PR-0)。
5. **退化检查**: 对比基线报告——若测试数减少或 REAL 项数减少, 自动回滚。

**验证报告模板**:
```
## 验证报告

### 测试结果
- 全量测试: <N> passed / 0 failed / <K> skipped
- 新增测试: <N> tests (含 <M> 个反例)
- 覆盖率: <X>% (before: <Y>%)

### dogfood 验证
- 任务: <描述>
- 结果: <通过/失败>
- 耗时: <T> 秒

### 红蓝对抗
- 问题: <质疑>
- 回答: <反驳>
- 结论: <未驳倒/部分认输>

### 退化检查
- REAL 项数: <R> (before: <R_before>)
- PARTIAL 项数: <P> (before: <P_before>)
- DECORATIVE 项数: <D> (before: <D_before>)
- OPEN 项数: <O> (before: <O_before>)
- 结论: <无退化/已回滚>
```

#### 13.1.6 Reflect (反思)

本轮循环的总结, 写入 §13.7 自优化记录表和 CHANGELOG。

**反思模板**:
```
## 反思: 第 <N> 轮自优化

### 摘要
- 目标: <简述>
- 结果: <成功/部分成功/回滚>
- §12.1 变化: <X> 行升级, <Y> 行降级

### 教训
- 什么做对了: <描述>
- 什么做错了: <描述>
- 什么可以改进: <描述>

### 下一轮建议
- 推荐优先级: <P0/P1/P2>
- 推荐目标: <§12.1 行名或 OPEN 项>
- 备注: <其他>
```

### 13.2 触发方式

#### 13.2.1 手动 (当前, 推荐)

用户说"继续优化", AI 按 §13.1 循环跑一轮。用户可在任何步骤打断:
- 在 Observe 后: "跳过, 直接看诊断"
- 在 Diagnose 后: "这个优先级不对, 先做 X"
- 在 Propose 后: "方案太激进, 缩小范围"
- 在 Execute 后: "改动好, 我可以 review 了"
- 在 Verify 后: "这个 dogfood 不够, 再跑一个"

#### 13.2.2 半自动 (推荐, 当前可实现)

AI 跑完一轮后, 自动检查 §12 和测试结果:
- 若发现新的 OPEN 项 (测试失败导致可证伪率下降, 或 dogfood 发现新问题), 提示用户"发现新 OPEN 项, 是否继续优化?"
- 若上一轮成功 (REAL 项增加, 测试无退化), 提示用户"本轮成功, 已发现下一候选目标: <X>, 是否继续?"

#### 13.2.3 完全自动 (v2, 当前不做)

CI 定期跑自优化循环, 自动提交 PR。前提:
- 有完善的 CI 管线 (含全量测试 + dogfood 自动化)
- 有自动回滚机制 (PR 合并后若发现退化, 自动 revert)
- 有自动红蓝对抗 (另一 AI 实例自动驳斥改进)
- 标注为 v2, 本节不展开

### 13.3 约束 (安全阀)

| 编号 | 约束 | 违反后果 |
|---|---|---|
| S-1 | 每轮只改 1-3 个 E 项 (防 scope creep) | 超过 3 项, 本轮视为无效, 缩小范围重来 |
| S-2 | 每轮必须跑全量测试 + 至少 1 次 dogfood | 缺任一, 验证失败, 视为未完成 |
| S-3 | 必须更新 §12 真相表 + CHANGELOG | 缺任一, 不合规 |
| S-4 | 不得跳过 IPR 规则 (先文档后代码, 反例测试) | 跳过 = 违反 IPR-1, 回滚 |
| S-5 | 不得 commit/push (用户决定) | 违反 = 越权, 优化失效 |
| S-6 | 每轮必须有 before/after 基线对比 | 缺基线, 无法判断退化 |
| S-7 | 不得修改 §1 本体论和 §2 理论根基 (它们已在 v1.1 经受外部审计) | 修改 = 违反 v1.1 修订原则, 回滚 |
| S-8 | 不得写 Embodied 维度代码 (守接口不落码, §12.4) | 违反 §12.4, 回滚 |

### 13.4 反馈数据源

自优化循环的决策基于以下数据源 (按优先级排序):

| 优先级 | 数据源 | 用途 | 何时更新 |
|---|---|---|---|
| P0 | §12.1 能力真相表 | 确定当前评级, 发现 DECORATIVE/ABSENT 项 | 每轮结束时 |
| P0 | 测试结果 (pytest) | 判断退化, 确认不变量 | 每次执行后 |
| P1 | 红蓝对抗记录 (§1.3.1) | 发现未决假设, 驱动下一轮 | 每次 dogfood 后 |
| P1 | CHANGELOG | 分析迭代模式, 发现重复问题 | 每轮结束时 |
| P2 | 性能指标 (§3.1.5) | 发现性能瓶颈, 指导优化方向 | 有工具时 |
| P2 | IMPL.md | 约束优化行为, 确保合规 | 每轮开始时 |
| P3 | 代码结构 (src/zall/) | 发现模块间耦合, 重构机会 | 每轮开始时 |
| P3 | 用户反馈 (dogfood) | 发现交互痛点 | 每次 dogfood 后 |

### 13.5 防止退化

退化是自优化循环的最大风险——AI 可能"优化"出更差的代码。以下机制防止退化:

#### 13.5.1 Before/After 基线对比

每轮记录以下指标在 before 和 after 两个时间点:

| 指标 | 来源 | 退化判据 |
|---|---|---|
| 测试通过数 | `pytest` | after < before |
| REAL 项数 | §12.1 | after < before |
| PARTIAL 项数 | §12.1 | after > before (REAL → PARTIAL 降级) |
| DECORATIVE 项数 | §12.1 | after > before (REAL → DECORATIVE 降级) |
| OPEN 项数 | §12.2 | after > before (新增 OPEN) |
| 覆盖率 | `pytest --cov` | after < before - 2% |

**退化阈值**: 任何指标退化 → 触发回滚。

#### 13.5.2 自动回滚

若 after 比 before 差, AI 自动执行:
1. `git diff` 记录当前改动 (作为教训)
2. `git checkout -- .` 回滚所有未 commit 的改动
3. 在 §12.2 新增一条 OPEN: "自优化第 N 轮因 <退化指标> 回滚, 教训: <总结>"
4. 在 CHANGELOG 记录: "自优化第 N 轮回滚 (<原因>)"

#### 13.5.3 红蓝对抗 (PR-0 对自优化自身的执行)

AI 的改进必须能被驳倒:
- 每个改进必须附带可证伪声明: "如果改进有效, 则 <可观测结果> 成立。"
- 改进后, AI 必须尝试驳倒自己的改进: "有没有可能 <改进> 实际上 <负面效果>?"
- 如果 AI 自己驳倒了自己, 改进不合并, 记录教训。

例如:
```
改进: "把 anomaly 阈值从 50 降到 20"
可证伪声明: "如果阈值 20 更好, 则脏工作区误报率不增加, 且真实异常检测率不降低"
自驳斥: "如果脏工作区有 30 个文件改动, 阈值 20 会误报——需要基线机制"
结果: 改进不合并, 改为"加基线机制 + 保持阈值 50" (这正是 E1.7 的 OPEN 2 修正)
```

### 13.6 AI 提示词模板

提示词模板位于 `docs/AI_SELF_OPTIMIZE_PROMPT.md`, 可直接复制给 Claude Code 或其他 AI 使用。
模板包含: 角色定义、约束、循环步骤、输出格式、质量门禁、反例。

### 13.7 自优化记录表

| 轮次 | 日期 | 目标 (§12.1 行) | 结果 | 验证 | 退化 | 教训 |
|---|---|---|---|---|---|---|
| (首次使用后填写) | | | | | | |

### 13.8 本节自身的执行真相表

| 能力 | 宣称状态 | 实际评级 | 证据 | 缺口 |
|---|---|---|---|---|
| 自优化循环定义 (§13.1) | PENDING | **ABSENT** | 文档存在 | 首次运行后评级 |
| 触发方式 (§13.2) | PENDING | **ABSENT** | 文档存在 | 首次运行后评级 |
| 约束 (§13.3) | PENDING | **ABSENT** | 文档存在 | 首次运行后评级 |
| 反馈数据源 (§13.4) | PENDING | **ABSENT** | 文档存在 | 首次运行后评级 |
| 防止退化 (§13.5) | PENDING | **ABSENT** | 文档存在 | 首次运行后评级 |
| 提示词模板 (§13.6) | PENDING | **ABSENT** | `docs/AI_SELF_OPTIMIZE_PROMPT.md` | 首次使用后评级 |

> **使用规则**: 首次完成一轮自优化后, 将本节状态从 PENDING 转 SETTLED, 并在 §13.8 填入实际评级。这是 PR-0 对 §13 自身的执行——本节不能豁免自己的规则。
