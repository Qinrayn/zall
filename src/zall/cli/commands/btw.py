"""zall.cli.commands.btw — /btw 侧问 (kimi soul/btw.py 对标, 原创实现).

kimi 巧思 (逐行研读后按 zall 同步架构重写, 全部保留):
  1. **不污染主上下文**: 问题与回答都不写入主 loop 的消息历史 —
     用户可以在长任务中途随口问一句而不打乱 agent 的工作记忆。
  2. **prompt cache 对齐省钱**: 侧问请求复用主对话的完整消息前缀 +
     相同的工具 schema 列表 — 供应商前缀缓存命中, 只为增量付费。
  3. **DenyAll 语义**: 工具定义可见 (为缓存) 但执行一律拒绝;
     maxTurns=2 — 模型第一轮误调工具时把拒绝结果喂回, 给第二次机会。

用法: /btw <question>   (需要已有活跃对话)
"""

from __future__ import annotations

import threading
from typing import Any

from zall.cli.commands._common import _CATEGORY_SESSION, slash_command
from zall.core.model import Message

_BTW_MAX_TURNS = 2
# 硬墙钟上限: complete() 自身不带超时参数 (超时在 config 层), 侧问是轻量
# 一次性请求, 不能让一次卡死的 API 调用阻塞 REPL 主线程。
_BTW_TIMEOUT_S = 120.0

_SIDE_QUESTION_REMINDER = (
    "[side question] This is a quick side question from the user. "
    "Answer directly in a single text response.\n"
    "- You are a lightweight one-off instance; the main task continues "
    "independently — do NOT reference being interrupted.\n"
    "- Do NOT call any tools. Tool definitions are visible only for technical "
    "reasons (prompt cache); every call will be rejected.\n"
    "- Answer ONLY from what you already know from the conversation. "
    "If you don't know, say so directly.\n\n"
)


def _complete_with_timeout(
    adapter: Any,
    messages: list[Message],
    tool_schemas: list[dict[str, Any]],
    timeout: float,
) -> Any | None:
    """Daemon 线程中调用 complete()，超时返回 None。

    超时返回后线程仍会存活直到底层请求结束 — 它只向本地 list 写结果,
    不触碰任何共享状态, 即使迟到也无害。
    """
    result: list[Any] = []
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            result.append(adapter.complete(messages, tool_schemas))
        except BaseException as e:  # noqa: BLE001 — 转交主线程重新抛出
            errors.append(e)

    t = threading.Thread(target=_run, name="btw-complete", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if errors:
        raise errors[0]
    return result[0]


@slash_command(
    "/btw",
    description="side question: quick answer without touching the main conversation",
    category=_CATEGORY_SESSION,
)
def cmd_btw(arg: str, out: Any, loop: Any | None = None,
            state: dict[str, Any] | None = None) -> str:
    """侧问: 基于当前对话上下文快速回答, 不写入主历史。"""
    question = (arg or "").strip()
    if not question:
        out.write("  usage: /btw <question>   (answers without touching the main conversation)\n")
        return "handled"
    if loop is None or not loop.messages:
        out.write("  /btw needs an active conversation (send a message first)\n")
        return "handled"

    # 复用主对话前缀 + 相同工具 schema (prompt cache 对齐); 绝不改动 loop.messages
    side_messages: list[Message] = list(loop.messages)
    side_messages.append(Message.user(_SIDE_QUESTION_REMINDER + question))
    try:
        tool_schemas = [t.schema for t in loop._tools.tools]
    except Exception:
        tool_schemas = []

    adapter = loop.model_adapter
    baseline_len = len(loop.messages)
    try:
        for turn in range(_BTW_MAX_TURNS):
            resp = _complete_with_timeout(adapter, side_messages, tool_schemas,
                                          _BTW_TIMEOUT_S)
            if resp is None:
                out.write("  (btw) timed out after "
                          f"{_BTW_TIMEOUT_S:.0f}s — try again with the main "
                          "conversation\n")
                break
            text = (resp.content or "").strip()
            # 文本且无工具调用 → 采纳 (混合 text+tool 视为不完整前言, 不采纳)
            if text and not resp.tool_calls:
                out.write(f"  (btw) {text}\n")
                break
            if not resp.tool_calls:
                out.write("  (btw) no answer produced — try rephrasing\n")
                break
            # DenyAll: 拒绝所有调用; 还有轮次 → 喂回拒绝结果给第二次机会
            if turn + 1 < _BTW_MAX_TURNS:
                side_messages.append(Message.assistant(
                    content=resp.content, tool_calls=resp.tool_calls))
                for tc in resp.tool_calls:
                    side_messages.append(Message.tool_result(
                        content=("Tool calls are disabled for side questions. "
                                 "Answer with text only."),
                        tool_call_id=tc.id,
                        tool_id=tc.tool_id,
                    ))
            else:
                names = ", ".join(tc.tool_id for tc in resp.tool_calls)
                out.write(f"  (btw) model kept trying to call tools ({names}) "
                          "instead of answering — ask in the main conversation\n")
    except Exception as e:
        out.write(f"  (btw) failed: {e}\n")

    # 不变量 (I-BTW): 主上下文纹丝不动 — 显式检查而非 assert
    # (assert 在 python -O 下被剥离, 这里是用户数据完整性承诺)
    if len(loop.messages) != baseline_len:
        out.write("  (btw) internal error: main context was modified\n")
    return "handled"
