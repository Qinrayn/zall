"""zall.tools —— 本地tool集。

DESIGN.md 推荐的 8 个核心工具:
  read_file / write_file / edit_file / bash / grep / glob / list_dir / spawn_subagent

但本轮**不起**任何工具,onlyplaceholder。守 IPR-4: 在 primitive SETTLED 前
不写编排代码; Tool 是 primitive, deferred。

Authority 三层名单 (§4.2 context_judge) 决定每个 tool_call 该走
whitelist / greylist / blacklist, 详见 `zall/safety/`。
"""
