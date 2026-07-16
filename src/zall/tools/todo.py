"""zall.tools.todo — 进度待办清单tool (DESIGN.md §9.2.6 TodoWrite 投影).

对应设计:
  §9.2.6 待办列表 → Goal 进度投影 / Accountability
  §9.2.2 流式输出 → Verifiability (timeline 子集视图)

核心立场 (PR-4 在交互层落地):
  todo_list 是 **Goal 进度的呈现层投影**, 不是完成判据。
  - 它是一个 **显示型工具**: 不读写文件系统, 不执行命令, 无副作用。
  - todos 结果进 ToolResult.artifacts → 进 timeline (§6.1) → 渲染层消费。
  - TerminationCriterion 是纯函数 (§5.2), todo 全打勾 ≠ met;
    judge 用纯函数判定, 不读 todo。偷渡风险 §9.2.6 已点名, 此处守。
  - 因为是显示型 (无 Authority 边界可越), 默认 whitelist (免确认),
    与 read_file/grep 同列 (见 rules_file._default_safe_rules)。

模型用法: 调 todo_list(todos=[...]) 刷新进度清单; 每步开始前更新 in_progress,
完成后标 completed。渲染层把最新清单画成 checklist 面板 (TTY) / 纯文本 (非 TTY)。

IPR constraints:
  IPR-0: 测试在 tests/test_cli_interaction_v013.py (含反例)
  IPR-3: 仅 stdlib, 不 import 模型 SDK
  IPR-4: 本文件是 tool primitive, 不是主 Loop
"""

from __future__ import annotations

from typing import Any

from zall.core.tool import ToolResult

# Allowed progress statuses
_STATUS_ALLOWED = ("pending", "in_progress", "completed")
_MAX_TODOS = 30  # bounded: 防模型塞超长清单撑爆渲染


class TodoListTool:
    """进度待办清单tool (§9.2.6, 显示型, 无副作用)。

    execute(args) 校验 todos 列表, 返回最新清单到 artifacts["todos"]。
    校验失败也返回 success=False + 非空 error (守 ToolResult 不静默失败)。
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "todo_list"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "todo_list",
                "description": (
                    "Update task progress checklist (display-only, does not affect completion judgment). "
                    "Mark items as in_progress before starting sub-tasks, completed when done. "
                    "The entire list is replaced on each call (pass the full list each time). "
                    "Completion is judged by tests/criteria, NOT by this checklist."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "description": "完整的待办清单 (每次调用传入全量, 覆盖式更新)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "string",
                                        "description": "待办项描述",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": list(_STATUS_ALLOWED),
                                        "description": "pending | in_progress | completed",
                                    },
                                    "active_form": {
                                        "type": "string",
                                        "description": "进行中时的动名词形式 (可选, 仅展示)",
                                    },
                                },
                                "required": ["content", "status"],
                            },
                        },
                    },
                    "required": ["todos"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        raw = args.get("todos")
        if not isinstance(raw, list) or not raw:
            return ToolResult(
                success=False,
                output="",
                error="todo_list: 'todos' must be a non-empty list",
            )

        cleaned: list[dict[str, str]] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"todo_list: item {i} must be an object",
                )
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"todo_list: item {i} needs non-empty 'content'",
                )
            status = item.get("status", "pending")
            if status not in _STATUS_ALLOWED:
                status = "pending"
            entry: dict[str, str] = {"content": content.strip(), "status": status}
            active = item.get("active_form")
            if isinstance(active, str) and active.strip():
                entry["active_form"] = active.strip()
            cleaned.append(entry)

        if len(cleaned) > _MAX_TODOS:
            cleaned = cleaned[:_MAX_TODOS]

        done = sum(1 for t in cleaned if t["status"] == "completed")
        return ToolResult(
            success=True,
            output=f"updated {len(cleaned)} todos ({done} completed)",
            artifacts={
                "todos": cleaned,
                "todo_event": True,  # 标记: 渲染层识别为进度投影事件
            },
        )
