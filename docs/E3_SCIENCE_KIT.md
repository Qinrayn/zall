# RFC: Science Kit (E3) - 首个领域扩展闭环

> 对应 MASTER.md §12.3 E3
> 状态: DESIGN (待 E1/E2 完成后落码)
> 日期: 2026-07-19
> dogfood 目标: ~/GF-consistency-framework (PPI 网络嵌入评估科研项目)

## 0. 为什么先做 Science Kit

六维本体论中, Science Kit 是**唯一能立刻真实闭环**的领域扩展:
- coding agent 已有 (tools/ + cli/commands/), 但它和 Claude Code 同质, 无差异化。
- Embodied Kit 守接口不落码 (§12.4), 无硬件无法验证。
- Science Kit 的 dogfood 目标 (GF-consistency) 就在隔壁, 是真实科研, 有正/负结果、多假设、可溯源需求。

E3 完成后, zall 是**唯一原生支持"假设驱动探索循环"的 agent 框架**--这是和 Claude Code / opencode / codex 拉开差异的关键。

## 1. 范围 (v1, 最小闭环)

本轮只做"假设生命周期 + 证据管理 + 溯源", 不做:
- 自动实验设计 (agent 自己提出实验方案) -- 留 v2
- 统计推断引擎 -- 复用 GF-consistency 已有的
- 论文生成 -- 留 v2

核心交付: 一个研究员能用 zall 记录"我假设 X -> 我跑了实验 E -> 证据 Z 支持/反驳 -> 假设状态更新 -> 全程可溯源"。

## 2. 模块设计

### 2.1 core/hypothesis.py

```python
class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    FALSIFIED = "falsified"
    REVISED = "revised"  # 证伪后修订为新版本

class Hypothesis(BaseModel):
    id: UUID
    claim: str                      # "Spectral 嵌入的 G-F Score 显著高于随机"
    prediction: str                 # "若成立, G-F > baseline, p < 0.05"
    confidence: float = 0.5         # [0,1]
    evidence_for: list[UUID] = []
    evidence_against: list[UUID] = []
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    version: int = 1
    revised_from: UUID | None = None    # 若是修订版, 指向原假设
    falsified_by: UUID | None = None    # 哪条 evidence 证伪了它
    created_by: str                 # AgentID
    created_at: int                 # timestamp
    immutable_after_lock: bool = False  # 对应 I-1 承诺不可撤销

    def lock(self): ...             # 锁定后 claim/prediction 不可改
    def add_evidence(self, ev_id, supports: bool): ...
    def revise(self, new_claim, new_prediction) -> "Hypothesis": ...  # 返回 v+1 新实例
    def falsify(self, evidence_id) -> None: ...
```

**不变量** (对应 I-1, I-10):
- H-1: locked 后 claim/prediction 不可变 (IPR-0 反例测试)
- H-2: revise 必须产生新 UUID + version+1 + revised_from 指向原 (不原地改)
- H-3: falsify 要求 evidence_against 非空, 且 falsified_by 指向其中一条
- H-4: NegativeResult 与 Positive Evidence 同等存储、同等索引 (I-10)

### 2.2 core/experiment.py

```python
class ExperimentStatus(str, Enum):
    DESIGNED = "designed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class ExperimentGoal(BaseModel):
    id: UUID
    hypothesis_id: UUID             # 验证哪个假设
    protocol: str                   # 实验步骤 (脚本路径 + 参数)
    data_snapshot: str              # 输入数据 hash (接 ScienceProvenance)
    result: dict | None = None      # 输出 (key metrics)
    status: ExperimentStatus = ExperimentStatus.DESIGNED
    reproducible: bool = True       # 是否可复现 (non_reproducible_by_construction 见 §3.1.4)
    started_at: int | None = None
    completed_at: int | None = None
```

### 2.3 core/evidence.py

```python
class EvidenceType(str, Enum):
    POSITIVE = "positive"           # 支持假设
    NEGATIVE = "negative"           # 反驳假设 (负结果, 同等珍视, I-10)
    INCONCLUSIVE = "inconclusive"

class Evidence(BaseModel):
    id: UUID
    hypothesis_id: UUID
    experiment_id: UUID
    type: EvidenceType
    metric: str                     # "G-F Score", "p-value", "rho"
    value: float
    threshold: float | None = None  # 判据阈值
    supports: bool                  # True=支持, False=反驳
    provenance: ScienceProvenance   # 见 2.4
    created_at: int

class NegativeResult(BaseModel):
    """负结果的一等公民包装 (I-10)。"""
    evidence: Evidence              # type=NEGATIVE 的 evidence
    what_failed: str
    conditions: dict
    diagnosis: str                  # 假设错? 实验设计错? 执行误差?
    value: str                      # 排除了什么可能性 (科学价值)
```

### 2.4 core/provenance.py (ScienceProvenance)

```python
class ScienceProvenance(BaseModel):
    """实验 protocol + data + code + env 的完整血缘, 接入 Verifiability timeline。"""
    protocol_hash: str              # 实验脚本 hash
    data_hash: str                  # 输入数据 hash
    analysis_code_hash: str         # 分析代码 hash
    environment_hash: str           # env snapshot hash (requirements.txt + python version)
    lineage: list[str] = []         # 衍生自哪些 prior experiment
    timeline_anchor: str | None = None  # 接到 RunRecorder 的 anchor
```

**接入点**: ScienceProvenance.timeline_anchor 在实验完成时, 调用 RunRecorder.anchor_to() 把整个 provenance 锚定到 timeline。这样科研结果的可复现性直接复用 Verifiability 维度, 不重造轮子。

### 2.5 extensions/science/ (领域扩展, 非核心)

ScienceKit extension: 注册 ScienceGoalType, 提供 science 相关工具 (log_evidence, falsify_hypothesis 等) 给 agent。核心数据结构在 core/, 扩展在 extensions/science/。这符合 FR-0 统一性原则: 核心抽象普适, 领域特化在扩展层。

## 3. CLI

```
zall science new-hypothesis "Spectral 的 G-F Score 显著高于随机" \
    --prediction "G-F > 0.128, p < 0.05" \
    --project ~/GF-consistency-framework

zall science log-experiment --hypothesis <id> \
    --protocol scripts/compute_gf_and_embeddings.py \
    --data data/yeast_ppi.csv \
    --result results/gf_scores.json

zall science log-evidence --hypothesis <id> --experiment <id> \
    --metric "G-F Score" --value 0.163 --threshold 0.128 --supports

zall science falsify --hypothesis <id> --evidence <ev_id> \
    --diagnosis "假设过于宽泛" --value "排除了无阈值约束的版本"

zall science status [--hypothesis <id>]   # 显示假设树 + 证据 + 状态
zall science repro [--experiment <id>]     # 验证 provenance + 重放
```

存储: `.zall/science/hypotheses.jsonl` (append-only, 接入 timeline), `.zall/science/experiments.jsonl`, `.zall/science/evidence.jsonl`。append-only 对应 Verifiability 不可篡改原则。

## 4. dogfood 计划 (E3.6)

在 GF-consistency-framework 上跑至少 3 个真实假设:

1. **H1 (positive)**: "Spectral 嵌入的 G-F Score 显著高于 greedy-modularity baseline"
   - 已有证据: G-F=0.163 > 0.128, Bonferroni 9/30 显著
   - 预期: CONFIRMED
2. **H2 (negative)**: "G-F Score 与 Link Pred AUC 在 n=11 显著相关"
   - 已有证据: rho=+0.591, P=0.056, FDR P=0.080 -- 不显著
   - 预期: FALSIFIED (这是负结果, I-10 同等珍视)
3. **H3 (revised)**: "G-F Score 与 Link Pred AUC 在 n≥25 显著相关"
   - 由 H2 修订而来 (revised_from = H2)
   - 已有证据: n=25, rho=+0.750, P<0.001
   - 预期: CONFIRMED

H2->H3 的修订链正好测试 revise() 和 NegativeResult 闭环。这是用真实科研验证 zall 设计的最好方式。

## 5. 红蓝对抗预期 (E3.7)

GF-consistency 的真实使用可能驳倒的 zall 设计假设:
- "Hypothesis.lock() 后不可改" -- 科研中假设常被细化, lock 语义是否太硬?
- "Evidence.supports 是 bool" -- 真实证据常有程度, 是否该用 float?
- "NegativeResult 独立类型" -- 是否多余 (Evidence.type=NEGATIVE 已够)?

这些预期问题记入 §1.3 红蓝对抗记录, 驳倒的就改设计。

## 6. 落码顺序 (E1/E2 完成后启动)

1. core/hypothesis.py + tests/test_hypothesis_invariants.py (H-1..H-4)
2. core/experiment.py + core/evidence.py + core/provenance.py
3. extensions/science/__init__.py (注册 GoalType)
4. cli/commands/science.py (CLI 子命令)
5. 接入 RunRecorder.anchor_to (ScienceProvenance.timeline_anchor)
6. dogfood H1/H2/H3
7. 红蓝对抗记录回填 MASTER.md §1.3 + §12.1 表格更新

## 7. 不做的事 (防止 scope creep)

- 不做自动文献综述 (Elicit 已做, 非差异化)
- 不做实验自动设计 (v2)
- 不做统计推断 (复用 GF-consistency 已有)
- 不做论文生成 (v2)
- 不做 Embodied 任何代码 (§12.4)
