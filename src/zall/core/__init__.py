"""zall.core —— agent 本体论 4 维的代码投影 (DESIGN.md §1.2)。

本包为**模型无关**的纯接口聚合层 (Protocol / ABC / Pydantic):

  goal        .  §3 Goal 维度的代码投影
  authority   .  §4 Authority 维度的代码投影
  accountabil.. .  §5 Accountability 维度的代码投影
  verifiabil.. .  §6 Verifiability 维度的代码投影

constraints (来自 IMPL.md):
  - IPR-3: 本包内**禁止** import 任何模型 SDK
  - IPR-4: 本包不写主 Loop; 主 Loop 在 zall.safety 之上的 orchestrator 中
  - IPR-0: 每个 primitive 必须 invariant test先于或同步落码
  - IPR-1: 每个 primitive 必须 DESIGN.md 节号对应
"""

# placeholder聚合, 本轮不立interface (守 IPR-2: 单 step only 1 primitive + invariant test)
# 下一轮起逐个推 primitive。
