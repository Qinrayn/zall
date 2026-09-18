"""zall._util.toml — Shared TOML parsing utilities (stdlib-only, IPR-3).

Consolidates value unquoting, comment stripping, section header parsing,
and the unified fallback TOML loader used across the codebase.

Each format-specific parser (rules, mcp, skills) keeps its own [[array]]
logic but calls through to the shared functions here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ── Unified TOML loader (try stdlib → tomli → fallback) ──


def load_toml_simple(path: Path) -> dict[str, Any]:
    """Load a TOML file, preferring stdlib tomllib (3.11+) or tomli (3.10).

    Falls back to the built-in minimal parser when neither is available.
    Handles simple [section] / key = value format.

    BOM: 编辑器/旧版写入可能留下 UTF-8 BOM, tomllib 会拒收 (实测 2026-09-18:
    用户 config 带 BOM → 静默回落宽松解析器 → [[providers]] 数组/数字全失效),
    故读取时统一剥离。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return _load_toml_fallback(path)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")

    # Try Python 3.11+ stdlib
    if sys.version_info >= (3, 11):
        try:
            import tomllib
            return dict(tomllib.loads(text))
        except ImportError:
            pass
        except Exception:
            # Strict parsers reject duplicate tables/keys (TOML spec). zall's
            # config writers can produce recoverable-but-invalid files, so fall
            # back to the lenient parser (last-value-wins) instead of crashing.
            return _load_toml_fallback(path)

    # Try tomli backport (optional dependency for Python 3.10)
    try:
        import tomli  # type: ignore[import-not-found]
        return dict(tomli.loads(text))
    except ImportError:
        pass
    except Exception:
        # Same rationale as tomllib above: recover from duplicate sections.
        return _load_toml_fallback(path)

    # Fallback: custom minimal parser
    return _load_toml_fallback(path)


def _load_toml_fallback(path: Path) -> dict[str, Any]:
    """Minimal TOML parser (fallback when tomllib/tomli unavailable).

    Uses shared strip_inline_comment / unquote_value from this module.
    Supports [section] headers, key = value pairs, and [[array-of-tables]].

    值类型: 字符串 / 数字 (int·float) / 布尔 / 单层字符串数组 — 2026-09-18:
    此前一律返回字符串, [[providers]] 的 model_prefixes (数组) 与
    window_size/price_* (数字) 全部静默失效。
    """
    config: dict[str, Any] = {}
    current: dict[str, Any] = config
    current_array: list[dict[str, Any]] | None = None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("[[") and s.endswith("]]"):
                # Array of tables: [[providers]]
                section = s[2:-2].strip()
                entry: dict[str, Any] = {}
                if section not in config:
                    config[section] = []
                current_array = config[section]
                if isinstance(current_array, list):
                    current_array.append(entry)
                current = entry
            elif s.startswith("[") and s.endswith("]"):
                # Regular section: [model]
                section = s[1:-1].strip()
                current = config.setdefault(section, {})
                current_array = None
            elif "=" in s:
                key, _, val = s.partition("=")
                key = key.strip()
                # 注意顺序: 先判数组/标量类型再解引号 — 引号内的 "[...]" 是
                # 字符串不是数组; 数字/布尔/数组由 _parse_scalar_or_array 收敛
                val = strip_inline_comment(val)
                current[key] = _parse_scalar_or_array(val.strip())
    return config


def _parse_scalar_or_array(val: str) -> Any:
    """宽松解析器专用的值解析: 字符串数组 / 数字 / 布尔 / 字符串。

    只支持单层数组 (TOML 多维数组在 zall 配置中无使用场景)。
    """
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        items: list[str] = []
        buf: list[str] = []
        quote: str | None = None
        i = 0
        while i < len(inner):
            ch = inner[i]
            if quote:
                if ch == "\\" and quote == '"' and i + 1 < len(inner):
                    buf.append(ch)
                    buf.append(inner[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                else:
                    buf.append(ch)
            elif ch in ('"', "'"):
                quote = ch
            elif ch == ",":
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
            i += 1
        tail = "".join(buf).strip()
        if tail:
            items.append(tail)
        return [unquote_value(item) for item in items if item != ""]
    if val in ("true", "false"):
        return val == "true"
    # 数字 (int / float, 含负号)
    if val and (val[0].isdigit() or (val[0] in "+-" and len(val) > 1 and val[1].isdigit())):
        try:
            if any(c in val for c in ".eE") and "0x" not in val.lower():
                return float(val)
            return int(val)
        except ValueError:
            pass
    return unquote_value(val)


def strip_inline_comment(val: str) -> str:
    """去除 TOML 值中的内联注释 (# 在引号外部时视为注释开始).

    正确处理:
      - "sk-xxx" # comment  → "sk-xxx"
      - "key#with#hash"     → "key#with#hash" (引号内的 # 不是注释)
      - 'single#quoted'     → 'single#quoted'
    """
    in_single_quote = False
    in_double_quote = False
    for i, ch in enumerate(val):
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif ch == "#" and not in_single_quote and not in_double_quote:
            return val[:i]
    return val


def unquote_value(val: str) -> str:
    """parse TOML 值, handle引号包裹和简单转义.

    支持:
      - "double quoted"   → double quoted (含 \\n \\t \\\" \\\\ 转义)
      - 'single quoted'   → single quoted (不转义)
      - bareword          → bareword (无引号)
      - "c" # comment     → c (行内注释)

    注意: 先剥离行内注释再处理引号, 确保 "val" # comment 正确提取。
    """
    val = val.strip()
    if not val:
        return ""

    # 先handle引号包裹: 找到匹配的引号, ignore行内注释
    if val[0] in ('"', "'"):
        quote = val[0]
        # 找到配对的引号结束位置
        i = 1
        while i < len(val):
            if val[i] == '\\' and i + 1 < len(val):
                i += 2  # 跳过转义序列
                continue
            if val[i] == quote:
                inner = val[1:i]
                if quote == '"':
                    return _unescape_double_quoted(inner)
                else:
                    return inner  # 单引号不转义
            i += 1
        # 未闭合引号: 按裸值handle (剥离行内注释)
        return strip_inline_comment(val).strip()

    # 无引号裸值: 剥离行内注释
    return strip_inline_comment(val).strip()


def _unescape_double_quoted(inner: str) -> str:
    """handle双引号字符串的转义serial。"""
    result = []
    i = 0
    while i < len(inner):
        if inner[i] == '\\' and i + 1 < len(inner):
            nxt = inner[i + 1]
            if nxt == '"':
                result.append('"')
            elif nxt == '\\':
                result.append('\\')
            elif nxt == 'n':
                result.append('\n')
            elif nxt == 't':
                result.append('\t')
            elif nxt == 'r':
                result.append('\r')
            else:
                result.append(nxt)
            i += 2
        else:
            result.append(inner[i])
            i += 1
    return ''.join(result)


def extract_section_name(line: str) -> tuple[str, str] | None:
    """从 TOML section 行提取段名, return (section_name, rest) 或 None.

    处理:
      [auth]             → ('auth', '')
      [model] # comment  → ('model', '# comment')
      [nested.table]     → ('nested.table', '')
      #[comment]         → None (注释行)
      [[array]]          → ('array', '')  (array-of-tables 去掉外层 [[ ]])
    """
    text = line.strip()
    if not text or text[0] != '[':
        return None
    # skip注释 (注释行的 [#...] 不被识别为 section)
    if text.startswith("#["):
        return None

    # 检测是否为 [[array]] 格式
    is_array = text.startswith("[[")

    bracket_end = -1
    i = 2 if is_array else 1
    in_quote = False
    quote_char = None
    while i < len(text):
        ch = text[i]
        if in_quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote_char:
                in_quote = False
        else:
            if ch in ('"', "'"):
                in_quote = True
                quote_char = ch
            elif ch == ']':
                bracket_end = i
                break
            elif ch == '#':
                return None
        i += 1

    if bracket_end == -1:
        return None

    # [[array]] 需要两个 ]]
    if is_array and (bracket_end + 1 >= len(text) or text[bracket_end + 1] != ']'):
        return None

    end_idx = bracket_end + (1 if is_array else 0)
    sec_name = text[2 if is_array else 1:bracket_end].strip()
    rest = text[end_idx + 1:].strip()
    if not sec_name:
        return None
    return (sec_name, rest)