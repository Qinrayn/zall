"""CI 钩子:检验 IPR-3 (core 模型无关)。

对应 IMPL.md IPR-3:
  `src/zall/core/` 下任何 .py 文件不允许 import 下列 SDK:
    openai, anthropic, zhipuai, google.generativeai, ollama, langchain, ...
  异常 import 触发本脚本退出码 1, 让 CI fail。

v0.0.7-impl 自纠:
  最早版本用字符串子串匹配 ("import xxx" / "from xxx"),
  会误判 docstring 中出现 "import openai" 字样的行。
  PR-0 占位脚本也须自纠 —— 改用 AST 检查 `ast.Import` / `ast.ImportFrom` 节点,
  这样 docstring / 注释 naturally 不参与,不用单独跳过。

退出码:
  0: 通过
  1: 发现违禁 import
  2: 配置错误(eg. 目录不存在)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# 违禁 import 顶层包名 (IPR-3):
# agent 编排框架 + 模型 SDK 都禁于 core。
FORBIDDEN_TOP_MODULES = frozenset(
    {
        "openai",
        "anthropic",
        "zhipuai",
        "ollama",
        "langchain",
        "langgraph",
        "autogen",
        "crewai",
    }
)

# google.generativeai 是子包,顶层 google 不必禁,特单独识别。
FORBIDDEN_SUBPATHS = frozenset(
    {
        ("google", "generativeai"),
    }
)

CORE_DIR = Path(__file__).resolve().parent.parent / "src" / "zall" / "core"


def _top_module_chain(node: ast.ImportFrom | ast.Import) -> list[tuple[str, ...]]:
    """对 Import / ImportFrom 节点, 返回其导入路径的前缀链。"""
    chains: list[tuple[str, ...]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = tuple(alias.name.split("."))
            chains.append(parts)
    elif isinstance(node, ast.ImportFrom):
        # node.module 可能是 None (eg. from . import x); 那种情况 module path 不在禁集合
        if node.module:
            base = tuple(node.module.split("."))
            for alias in node.names:
                chains.append(base + (alias.name.split(".")[0],))
            # 同时记录 base 自身, 以防 from google.generativeai import xx —— base 已纳入子包
            chains.append(base)
    return chains


def _is_forbidden(chain: tuple[str, ...]) -> bool:
    if not chain:
        return False
    top = (chain[0],)
    if chain[0] in FORBIDDEN_TOP_MODULES:
        return True
    # 子包路径检查 (前缀匹配)
    for sub in FORBIDDEN_SUBPATHS:
        if chain[: len(sub)] == sub:
            return True
    return False


def main() -> int:
    if not CORE_DIR.is_dir():
        print(f"FAIL: core dir not found at {CORE_DIR}", file=sys.stderr)
        return 2

    offenders: list[tuple[Path, int, str]] = []
    for py in CORE_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            print(f"FAIL: cannot parse {py}: {exc}", file=sys.stderr)
            return 1
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for chain in _top_module_chain(node):
                if _is_forbidden(chain):
                    src_line = ast.get_source_segment(py.read_text(encoding="utf-8"), node)
                    offenders.append((py, node.lineno, src_line or "<unparseable>"))
                    break

    if offenders:
        print("FAIL: forbidden model SDK imports in `core/`:", file=sys.stderr)
        for py, ln, src in offenders:
            print(f"  {py}:{ln}: {src.strip()}", file=sys.stderr)
        return 1

    print("OK: no forbidden imports in core/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
