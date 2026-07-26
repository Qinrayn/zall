# PARADIGM — 可证伪经验机 (Falsifiable Experience Machine)

> zall 的北极星文档。所有架构取舍以本文为准。
> 一句话：**zall 是一台"靠熬过证伪而成长"的持续学习通用智能体。**

---

## 0. 定位（诚实）

zall 不是 AGI，也不宣称是。zall 的北极星是一个**持续学习的、可证伪的通用智能体**：
它在连续的经验流上运行，不断**猜想**解法、用可复现的手段**反驳**，只把**熬过反驳**的
经验蒸馏进技能库，从而**跨会话地、可累积地变强**。

这个方向别人还没占住：大厂在拼模型能力，把"验证"当内部 RL 基建；而 zall 把
**可证伪 / 可复现**做成 agent 的**学习引擎本身**——这是差异化，也是护城河。

---

## 1. 前沿依据（本范式站在这些结论上）

- **经验时代**（Silver & Sutton, 2025）：下一代 agent 活在**连续经验流**里，从自身交互
  与 grounded reward 学习，而非静态人类数据。→ 我们的"经验流"器官。
- **The Bitter Lesson**（Sutton）：轻框架 + 宽动作空间 + 让搜索/算力干活，胜过手工雕规则。
  → 通用动作空间 = 代码执行；少而宽的工具。
- **开放式演化**（Clune / POET / Darwin Gödel Machine）：永不停生成"新颖且可学"的任务。
  → Context Agent 灵感合成。
- **技能库/终身学习**（Voyager；Lifelong-LLM-Agent survey, TPAMI 2026 的"感知-记忆-动作"三器官）：
  学到的技能**沉淀、复利**。→ 技能记忆器官。
- **验证器制导搜索**（AlphaProof / o-系列 / test-time compute）：用验证器当 ground truth 去搜索。
- **🔑 关键共识**（2025–26 多篇："self-improvement only works where outcomes are verifiable"、
  reward-hacking in self-improving code agents）：**自我改进只在结果可验证处成立；
  不可验证处必然 reward-hack 崩塌。**

**推论**：一个持续自我改进的通用 agent，其瓶颈不是模型或循环，而是 **reward（验证）**。
可信验证器不是审计功能，是让"开放式自我改进不塌"的**地基**。这正是 zall 本来就站的地方。

---

## 2. 核心循环：猜想 → 反驳 → 蒸馏（Popper）

```
        ┌──────────── 经验流 Experience Stream (跨会话持久) ────────────┐
        │  每个任务/解法/结果/验证 都是经验, 不再随会话丢弃              │
        └───────────────────────────┬─────────────────────────────────┘
   ① 猜想 Conjecture                 │                 ④ 蒸馏 Distillation
   (open-ended proposer)      ┌──────┴──────┐         (Voyager 式技能复利)
   Blue + Coordinator 并行     │ ② 通用动作空间 │  熬过反驳者 → 技能库 → 永久变强
   + Context Agent 灵感        │ 代码执行/sandbox│              ↑
        │                     │ (Bitter Lesson)│              │
        ▼                     └──────┬──────┘                 │
   ③ 反驳 Refutation ────────────────┘─────────────────────────┘
   (可复现证伪 = reward 引擎 🛡️ 护城河)
   Red 对抗 + sandbox 跑 + judge/eval + 链式哈希可复现
```

**唯一硬不变量（Popperian Gate）**：**只有熬过反驳（verified）的猜想，才准进入技能库/记忆。**
这条保证了学习是**安全、可累积**的——不会把 reward-hack 的伪增益沉淀下来。

---

## 3. 五个器官 → zall 现有种子映射

| 器官 | 职责 | zall 现有种子 | 缺口 |
|---|---|---|---|
| ① 经验流 | 跨会话记录 task/解法/验证结果 | RunRecorder 时间线 + ExperienceBank | **持久化跨会话**（Step 1）|
| ② 通用动作空间 | 代码执行=通用动作 | 精简工具 + sandbox | Lean 化（Step 0）|
| ③ 反驳/验证 | 对抗 + 可复现证伪=reward | `red_blue.Red` + sandbox + judge + 链式哈希 | 通用化到非编程任务 |
| ④ 蒸馏/技能记忆 | 熬过证伪者→可复用技能, 复利 | `skills/` + `auto_learn` + EB.best | **技能蒸馏 + recall**（Step 1）|
| ① 猜想 | 开放式提议 + 灵感合成 | `red_blue.Blue` + `Coordinator` + Context Agent | 开放式任务生成（Step 3）|

**结论**：五器官皆有种子，缺的不是零件，是**把它们拼成"经验流→猜想→证伪→蒸馏→复利"的脊柱并持久化**。

---

## 4. 演进路线（每步可量化 dogfood）

- **Step 0 — 地基（Lean 化）** ✅：`lean` 工具集预设（7 少而宽工具）+ `lean` 系统提示（仅 base+env）+ 真实 token 水位计数（API usage 而非字符估算）。均 **opt-in**，不破坏全功能默认路径。
  > **热循环解耦（完成）**：感知块（~95 行，每步唯一大块）已抽到 `core/loop_perception.py`（薄委托，loop.py 净减 ~92 行，行为等价）；六维仅测试用、链校验每 run 一次，本就不在每步热路径。
- **Step 1 — 持久经验流 + 技能复利**（本次）：`ExperienceStore` 跨会话持久；
  只把 verified 经验蒸馏成技能；新任务开始时 `recall` 相关技能注入 prompt。
  **可量化证据**：同类任务第 2 次（新会话）能召回上次的技能 → 真正"跨会话变强"。
- **Step 2 — 猜想–反驳循环通用化** ✅（原语）：`red_blue` + `sandbox_verifier` 用真实执行做 grounded 反驳（非模型自评）。
- **Step 3 — 开放式任务生成** ✅（原语）：`open_ended` 从已验证技能生成“新颖且可学”新任务 → 永续轮（Popperian Gate 只回写 verified）。

> **落地（成品）✅**：以上 Steps 1–3 已由用户命令 **`/lab`**（别名 `/selfplay`）串成能直接跑的闭环——`/lab <task>` 模型提解→沙盒证伪→蒸馏技能；`/lab` 开放式一轮；`/lab skills|stats` 查看。`RedBlueLoop.on_event` 观察者把“猜想→反驳→蒸馏”每步 live 渲染。

---

## 5. 诚实的边界（可证伪精神本身）

- **不是 AGI**；是"持续学习的可证伪通用 agent"这一真实北极星。
- **最硬未解问题 = 验证器覆盖**：开放式任务常无 ground-truth 验证器（"写首动人的诗"）。
  弱验证处（LLM-judge）自我改进弱且可能自欺。**立场：可验证处强（代码/数学/可测），
  不可验证处谦卑 + 红队 + 明确标注低置信。** 承认这个缺口本身就是护城河。
- **算力/模型依赖**：持续自改进烧调用；现实形态是**后台/批处理学习进程**，非实时。
- **单人现实**：把循环 + 验证基座做扎实，让模型 + 搜索干重活（Bitter Lesson），不手工雕智能。

---

## 5.1 Proof Tier — 波普尔闸门的形式化精化 (SETTLED, 原语落码)

> 对应 primitive: `core/proof_gate.py`; 不变量测试: `tests/test_proof_gate_invariants.py`;
> dogfood: `experiments/erdos_straus/prove.py`。

**问题**：反驳与确认在逻辑上不对称。对全称命题 `∀n P(n)`：
- **反驳**只需一个反例 → `sandbox_verifier` 真实执行即可 (可判定、廉价)。
- **确认**永远无法由有限次执行得到 (休谟/波普尔归纳问题)：沙盒只能
  **在界内佐证 (corroborate)**，不能**证明 (prove)**。

旧闭环把两者混同：有界搜索把 `H1` 标为 `CONFIRMED`，靠报告里手写
"NOT a proof" 打补丁——机器本身分不清"界内佐证"与"已证明"。这正是 §5
所警告的 reward-hacking 面：自改进循环若把"佐证"当"证明"蒸馏成技能，闭环塌。

**修正**：把认识论状态升为一等公民 `VerificationTier`：

| Tier | 含义 | 授予者 |
|---|---|---|
| `REFUTED` | 已给出反例 | 沙盒执行 (可判定) |
| `CORROBORATED` | 界内佐证, **未证明** (须携带界 N) | 沙盒/有界搜索 |
| `PROVEN` | 对全称命题持有**机器可核验证书** | Proof Gate |
| `UNKNOWN` | 以上皆非 | — |

**Proof Gate**：仅当拿到机器可核验证书时才授予 `PROVEN`。对**可判定片段**
(ℚ 上有理函数恒等式) 证书用纯 Python 精确算术核验 (零多项式系数检验)，
守 IPR-3——无重型工具链、离线、确定性。证书本身可被证伪：错误恒等式的
差多项式非零 → Gate 判 `REFUTED` (IPR-0 反例)。

**Lean 的定位 (诚实结论)**：
- 可判定片段 (恒等式/有限实例) → **不需要 Lean**；纯精确算术即给出机器可核验的全称证明。
- 经验/算法命题 ("代码过测"/"界内无反例") → Lean 是错工具，沙盒执行才是正确的 grounded 验证器。
- Lean/Coq 仅在命题**离开可判定片段** (需归纳/引理/情形分析) 时才有价值。
- **架构立场**：Lean 永不进核心依赖 (违反 IPR-3/离线/模型无关)；仅作 **opt-in**
  `Prover` 适配器，沿用 `TrustAnchor`/`red_fn` 的注入协议模式，按需启用。

**dogfood 证据**：`prove.py` 用 3 条机器核验的参数化恒等式证明 Erdős–Straus
对**密度 5/6** 的整数成立 (真 ∀-证明, 无外部工具)，残余 `{n≡1,5 (mod 12)}`
诚实标 `CORROBORATED`——已知无单一多项式恒等式能覆盖 (所需埃及分裂乘子随 n 增长)。
证书 + 溯源哈希 + 链式时间线锚定构成完整可复核证据链。

**学习门接线 (系统落地)**：`ExperienceStore`（Popperian Gate）现消费 `VerificationTier`：
只有可蒸馏层进技能召回池，且机器强制那道墙——`CORROBORATED`（界内佐证）永不进
`proven_skills()`，在 recall 注入里显式标为 `[CORROBORATED n<N]` 而非 `[PROVEN]`。
`record_certificate()` 把 proof_gate 证书按 tier 直接落库。这样自改进循环无法把
"佐证"当"证明"跨会话蒸馏——§5 的 reward-hacking 面被机器堵死。
不变量测试: `tests/test_experience_tier_gate_invariants.py` (含反例: 佐证绝不进 proven)。

---

## 6. 与既有设计文档的关系

- `MASTER.md` = 六维本体论 + 工程不变量（IPR/PR-0/链式哈希）——是**地基与约束**。
- `PARADIGM.md`（本文）= **北极星与演进方向**——回答"zall 最终要长成什么"。
- 冲突时：本文定方向，MASTER 定底线（可证伪/可复现/模型无关不可违背）。
