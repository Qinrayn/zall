"""zall.safety.sensitive — 敏感文件识别 (凭证/私钥不进模型上下文).

风险: read_file / @ 引用会把文件内容注入模型上下文, 并随链式哈希
timeline 持久化 — .env / SSH 私钥 / 云凭证一旦被读, 密钥即泄漏进
会话存档与 API 请求。kimi utils/sensitive.py 同类防线的 zall 原创实现。

设计原则:
  - 只收录**高置信度**模式 (误报率极低): 宁可漏拦不可错拦正常开发文件。
  - 模板/示例文件豁免 (.env.example 等) — 它们本就该被读。
  - zall 特有: trust_anchor_key (ed25519 私钥, 审计链的签名根) 必须防护。
  - 判定纯函数、stdlib-only (IPR-3), 供工具层与 @ 注入共用。

逃生舱: 用户明确要读时可用 bash (cat/type) — bash 是用户信任边界内的
whitelist 工具, 责任归属清晰; read_file 是模型自主调用, 必须默认设防。

IPR constraints:
  IPR-0: tests/test_sensitive_file_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePath

# 高置信度敏感文件模式 (basename 匹配, 含 / 的按路径尾匹配)
SENSITIVE_PATTERNS: tuple[str, ...] = (
    # 环境变量 / 密钥文件
    ".env",
    ".env.*",
    # SSH 私钥 (公钥 .pub 不匹配)
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    # 云凭证 (路径式 + 裸名兜底)
    ".aws/credentials",
    ".gcp/credentials",
    "credentials",
    # 机器登录凭证
    ".netrc",
    "_netrc",  # Windows 变体
    # zall 自身的审计链签名私钥
    "trust_anchor_key",
)

# 模板/示例文件: 匹配 .env.* 但不敏感, 豁免
SENSITIVE_EXEMPTIONS: frozenset[str] = frozenset({
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
})


def is_sensitive_file(path: str) -> bool:
    """路径是否命中高置信度敏感文件模式 (纯函数, 大小写不敏感)。"""
    if not path:
        return False
    norm = path.replace("\\", "/")
    name = PurePath(norm).name.lower()
    if name in SENSITIVE_EXEMPTIONS:
        return False
    for pattern in SENSITIVE_PATTERNS:
        if "/" in pattern:
            # 路径式: 以模式结尾 (含目录边界)
            if norm.lower().endswith(pattern) or ("/" + pattern) in norm.lower():
                return True
        elif fnmatch.fnmatch(name, pattern):
            return True
    return False


def sensitive_refusal(path: str) -> str:
    """read_file 拒绝读取敏感文件时给模型的说明 (含替代路径指引)。"""
    return (
        f"[BLOCKED: {PurePath(str(path)).name} matches a sensitive-file pattern "
        "(credentials / private key / .env). Reading it would leak secrets into "
        "the model context and session archive. If the user explicitly wants "
        "this file read, ask them to open it themselves or use an explicit "
        "shell command.]"
    )


__all__ = [
    "SENSITIVE_EXEMPTIONS",
    "SENSITIVE_PATTERNS",
    "is_sensitive_file",
    "sensitive_refusal",
]
