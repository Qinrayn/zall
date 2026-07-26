"""zall.core.process_anchor — 外部可信锚点 (独立进程, 私钥不离开进程)。

对应 DESIGN.md §6.5.2 Phase 1:
  - ProcessTrustAnchor: TrustAnchor Protocol 的 IPC 客户端实现
  - Anchor Server: 独立进程, 持有 ed25519 私钥, 通过 socket/named pipe 通信

用法:
    from zall.core.process_anchor import ProcessTrustAnchor, start_anchor_server

    # 启动 anchor server (测试/开发环境)
    proc = start_anchor_server(background=True)

    # 使用
    anchor = ProcessTrustAnchor()
    ack = anchor.write_run_tail(run_id, last_event_hash, ts)

注意:
  - anchor server 必须先启动, 否则 write_run_tail 返回 None (诚实退让)
  - 生产环境应由 systemd / service manager 管理 anchor 进程
"""

from .client import ProcessTrustAnchor, start_anchor_server
from .protocol import AnchorRequest, AnchorResponse

__all__ = [
    "ProcessTrustAnchor",
    "start_anchor_server",
    "AnchorRequest",
    "AnchorResponse",
]