# zall · Implementation Principles

> 对应文档:`DESIGN.md`
> 元规则:5 条(IPR-0..4),与文档的 PR-0..4 精神**一一映射**,
> 但针对性解决"设计严 / 实现松"那种典型错误增量。
> 元规则优先于所有具体代码,违规的代码不允许合入。

---

## IPR-0  自证伪义务的代码形态(对应 PR-0)

每个 SETTLED 节(`DESIGN.md` §1–§6)具化为 ≥1 个 **invariant 测试**。

**测试不是"运行通过就算好",而是包含反例**:
- 测试必须断言"该不变量在何输入下不成立",并对应其失败到文档哪条被偷渡
- 测试目录维护一份 `INVARIANTS.md`,即**测试 ↔ 文档节** 的映射表
- 反例:如果有人修改实现让 invariant 失效,该测试**必须**失败

例:测 `GoalTriple.immutable` 时,不应只测"已构造的实例不可改"(正向),
而应同时断言 "Refiner 不能加新 intent 进 translation_of"(反例),且
对应文档条目 `§3.2.1 + §3.3 R1`——任何破坏 R1 (翻译禁加戏) 的实现都让该测试 fail。

**额外约束**:R-Metric 化 A/B/C (§2.0) 也要有反例测试 —
任何"没有反指标配对"或"上溯不到 §1.2"的指标计算函数,在 `test_metrics.py` 必须 fail。

## IPR-1  增量但不许错误的代码形态(对应 PR-1)

- 每段实现代码必须**对应文档中某一条 SETTLED / PENDING 条目的回填**:
  · 来自 SETTLED 条目 → 直接落码
  · 来自 PENDING 条目 → 落码前先在 DESIGN.md 把它转 SETTLED 或 OPEN
  · **不许凭直觉写无文档对应的代码**(凭直觉调直觉 = 错误增量)
- 纯自创实现的 helper(eg. 一个 helper function 文档没提)必须先在
  DESIGN.md 立一条 OPEN,再写实现。
- CI 检测:每段 PR 描述里**必须**显式引用 DESIGN.md 的章节号(eg. "实现 §4.2")。

## IPR-2  一步步来的代码形态(对应 PR-2)

**每轮提交不跨步**:
- 单次 implement step 仅落 **1 个 primitive** + 它的 invariant 测试
- Loop 主体留到 primitive 全部就位后单独一轮
- 禁止"顺手多写一点":顺手就是凭直觉增量,违反 PR-2
- 单 PR 的 diff 行数过得去也不行 — 按"指责切片大小"判定,不按行数

## IPR-3  模型无关的代码形态(对应 PR-3)

- `src/zall/core/` 下不得 import 任何模型 SDK
    (`openai` / `anthropic` / `zhipuai` / `google.generativeai` / `ollama` 等)
- 仅 4 个例外:**`cryptography`**(ed25519 / HMAC)、
  **`pydantic`** v2(schema 验证)、**`pytest`**(测试,仅 `tests/` 下)、stdlib
- ModelAdapter interface 在 `core/` 内;各家具体 Adapter
    (GLM / Claude / Gemini / Local等)在 `src/zall/adapters/` 子包
- CI lint 规则:任何 `core/` 模块文件出现禁用 import → **fail**
- 对应 v0.0.5 设计层 "锚点 ed25519/SHA-256 纯密码学,无模型依赖" 的代码投影

## IPR-4  不画架构图先立本体(对应 PR-4)

- 在 primitive 全部 SETTLED 前,**不许写综合编排**(`agent.py` 主 Loop)
- Primitive 是定义的"代码投影",投影未收尾前不许写主线
- "Primitive SETTLED" 的标志:
    · Interface 已编译通过 + invariant 测试 fail 在反例存在时通过
    · 文档节已对应到该 primitive 的 file_path
- 本规则持续到 S0 骨架全收尾

---

## 元规则与文档的映射表

| 文档元规则 | 代码层化身 | 何时检验 |
|---|---|---|
| PR-0 自证伪 | IPR-0 invariant + 反例测试 | 每次 commit |
| PR-1 不许错误增量 | IPR-1 文档节-代码 PR 双向引用 | 每次 commit |
| PR-2 一步步来 | IPR-2 单 primitive / 单 step | 每次 commit |
| PR-3 模型无关 | IPR-3 corelint | 每次 commit |
| PR-4 不画架构图先 | IPR-4 主 Loop 延后 | S0 之前 |

## 是否修订元规则
若需新增 IPR-x,通过更显明的红蓝对抗过程而非顺手添加。
顺手添加元规则 = 元规则归自己违反,违反元规则工作模式。
