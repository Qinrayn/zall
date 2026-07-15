"""zall._util.string — 字符串operation共享toolfunction。"""

from __future__ import annotations


def unquote(s: str) -> str:
    """TOML 风格去引号 + 剥离行内注释。

    B24: 统一 skills/loader.py 和 mcp/config.py 的 _unquote 实现。
    使用更完善的版本 (loader.py 版, 支持 " 和 ' 引号 + 行内 # 注释)。
    """
    s = s.strip()
    if s and s[0] in ('"', "'"):
        quote = s[0]
        end = s.find(quote, 1)
        if end != -1:
            return s[1:end]
    # 非引号 / 未闭合: 按首个 # 剥离行内注释
    h = s.find("#")
    if h != -1:
        s = s[:h]
    return s.strip()