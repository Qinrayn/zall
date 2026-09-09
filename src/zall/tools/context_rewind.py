"""zall.tools.context_rewind — 模型主动上下文回滚工具 (kimi SendDMail 对标, 原创).

设计要点 (kimi tools/dmail 逐行研读):
  - 工具本体只做一件事: 向 RewindMailbox 投递请求; 真正的回滚由 AgentLoop
    在本步工具执行完毕后统一施加 (截断消息 + 追加"给过去自己的信")。
  - 成功路径的反直觉输出: 回滚发生后本工具结果不会留在上下文里 —
    所以"如果模型还能看到这条输出, 说明回滚没有发生" (kimi 同款倒置语义)。
  - 失败路径 (坏 checkpoint_id 等) 输出清晰错误, 模型可修正重试。
  - 只读工具 (不碰文件系统/外部状态), whitelist 级安全。

IPR constraints:
  IPR-0: tests/test_context_rewind_invariants.py (含反例)
  IPR-3: stdlib only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zall.core.rewind import RewindError, RewindMailbox
from zall.core.tool import ToolResult
from zall.core.tool_kind import ToolKind

if TYPE_CHECKING:
    from zall.core.tool import ToolCapabilities


class ContextRewindTool:
    """context_rewind — 把上下文折叠回某个 [CHECKPOINT k] 锚点。"""

    __test__ = False

    def __init__(self, mailbox: RewindMailbox | None = None) -> None:
        # AgentLoop 通过 registry.get("context_rewind").mailbox 共享此信箱
        self.mailbox = mailbox or RewindMailbox()

    @property
    def tool_id(self) -> str:
        return "context_rewind"

    @property
    def kind(self) -> ToolKind:
        return ToolKind.READ

    @property
    def capabilities(self) -> ToolCapabilities:
        from zall.core.tool import ToolCapabilities, ToolScope
        return ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "context_rewind",
                "description": (
                    "Fold recent context back to an earlier [CHECKPOINT k] anchor "
                    "you can see in the conversation. Use this to proactively manage "
                    "your context window: after reading a large file / large search "
                    "results / a long debugging detour that is no longer needed, "
                    "rewind to the checkpoint BEFORE the bloat and carry only the "
                    "distilled findings forward in `message`. After the rewind you "
                    "will no longer see anything after that checkpoint — your "
                    "`message` is the ONLY memory that survives, so state clearly "
                    "what you did, what you learned, and what to do next (files "
                    "already written to disk stay written; this does NOT revert the "
                    "filesystem). Write the message to your past self, not the user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "checkpoint_id": {
                            "type": "integer",
                            "description": (
                                "An existing checkpoint id from a [CHECKPOINT k] "
                                "marker visible in the conversation."
                            ),
                            "minimum": 0,
                        },
                        "message": {
                            "type": "string",
                            "description": (
                                "The letter to your past self: what was done, what "
                                "was learned, what to do next. Must be self-"
                                "sufficient — nothing after the checkpoint survives."
                            ),
                        },
                    },
                    "required": ["checkpoint_id", "message"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            self.mailbox.request(
                int(args.get("checkpoint_id", -1)),
                str(args.get("message", "")),
            )
        except (RewindError, TypeError, ValueError) as e:
            return ToolResult(
                success=False,
                output=f"[context_rewind failed: {e}]",
                error=str(e),
            )
        # 倒置语义 (kimi 对标): 回滚成功时这条输出会随折叠一起消失 —
        # 模型若还能看到它, 说明回滚未发生 (例如被网关拦截)。
        return ToolResult(
            success=True,
            output=(
                "[If you can still read this, the context rewind did NOT happen. "
                "Otherwise you are already at the checkpoint with your message.]"
            ),
        )


__all__ = ["ContextRewindTool"]
