"""AAS 规范层 invariant test (corresponds to docs/spec/AGENT_ALIGNMENT_SPEC.md §E)。

与 tests/ 下的 zall 内部 invariant 不同:
- tests/ 下测的是 zall 参照实现 (上溯 DESIGN.md §1-§6)
- tests/spec/ 测的是 *规范自身* (上溯 AAS §B / §E)

两者故意独立: 任何第三方 agent 可声称遵守 AAS 而不采纳 zall 任何内部
形态 (见 AAS §G)。故本目录的 import 严禁触及 zall.core.*。
"""
