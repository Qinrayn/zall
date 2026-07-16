"""zall.codegraph — 代码图索引系统 (Codebase Graph).

Inspired by Grok Build's xai-codebase-graph. Provides multi-language symbol
extraction, code indexing, and navigation for code understanding.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │  CodeGraph                                                   │
  │  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐ │
  │  │ Indexer  │→ │ CodeIndex│  │ Navigator                  │ │
  │  │ (scan FS)│  │ (mem)    │  │ - goto_definition          │ │
  │  └──────────┘  └──────────┘  │ - find_references          │ │
  │                              │ - search_symbol            │ │
  │  ┌──────────┐  ┌──────────┐ │ - get_file_symbols         │ │
  │  │ Parser   │  │ Cache    │ └────────────────────────────┘ │
  │  │ (per-lang)│ │ (disk)   │                                │
  │  └──────────┘  └──────────┘                                │
  └──────────────────────────────────────────────────────────────┘

Supported languages:
  - Python (.py)
  - JavaScript/TypeScript (.js, .ts, .jsx, .tsx)
  - Rust (.rs)
  - Go (.go)
  - Java/Kotlin (.java, .kt)
  - C/C++ (.c, .cpp, .h, .hpp)
  - Ruby (.rb)
  - PHP (.php)
  - Swift (.swift)

Usage:
    graph = CodeGraph("/path/to/project")
    graph.index()
    symbols = graph.search_symbol("MyClass")
    defs = graph.goto_definition("src/main.py", 10, 5)
    refs = graph.find_references("my_function")

IPR constraints:
  IPR-3: stdlib / pydantic only, no model SDK
  IPR-0: invariant tests at tests/test_codegraph_invariants.py
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════
# §1  Symbol Types
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SymbolLocation:
    """符号在源码中的位置。"""
    file_path: str
    line: int
    column: int
    length: int = 0

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}:{self.column}"


class SymbolKind(str, Enum):
    """符号类型分类。"""
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    INTERFACE = "interface"
    TYPE = "type"
    ENUM = "enum"
    MODULE = "module"
    NAMESPACE = "namespace"
    IMPORT = "import"
    DECORATOR = "decorator"
    UNKNOWN = "unknown"


class SymbolKindExt:
    STRUCT = "struct"
    TRAIT = "trait"
    PROTOCOL = "protocol"


# 将缺失的枚举值添加到 SymbolKind
for _name in ("STRUCT", "TRAIT", "PROTOCOL"):
    if not hasattr(SymbolKind, _name):
        setattr(SymbolKind, _name, _name.lower())


@dataclass(frozen=True)
class Symbol:
    """源码中的一个符号定义。"""
    name: str
    kind: SymbolKind
    location: SymbolLocation
    parent: Optional[str] = None
    """父符号名 (如类的成员方法)"""
    docstring: str = ""
    signature: str = ""
    """函数签名或类型声明"""
    language: str = ""

    @property
    def qualified_name(self) -> str:
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


@dataclass(frozen=True)
class SymbolReference:
    """符号引用 (使用位置)。"""
    name: str
    location: SymbolLocation
    language: str = ""


# ═══════════════════════════════════════════════════════════════════
# §2  Code Index
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CodeIndex:
    """代码索引 — 内存中的符号数据库。"""
    symbols: dict[str, list[Symbol]] = field(default_factory=dict)
    """符号名 -> 定义列表 (支持重名符号)"""
    by_file: dict[str, list[Symbol]] = field(default_factory=dict)
    """文件路径 -> 符号列表"""
    references: dict[str, list[SymbolReference]] = field(default_factory=dict)
    """符号名 -> 引用列表"""
    file_count: int = 0
    symbol_count: int = 0
    indexed_at: float = 0.0
    indexed_files: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols.setdefault(symbol.name, []).append(symbol)
        self.by_file.setdefault(symbol.location.file_path, []).append(symbol)
        self.symbol_count += 1

    def add_reference(self, ref: SymbolReference) -> None:
        self.references.setdefault(ref.name, []).append(ref)

    def get_symbols(self, name: str) -> list[Symbol]:
        return self.symbols.get(name, [])

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        return self.by_file.get(file_path, [])

    def get_references(self, name: str) -> list[SymbolReference]:
        return self.references.get(name, [])

    def search(self, query: str, max_results: int = 20) -> list[Symbol]:
        """搜索符号名 (模糊匹配)。"""
        q = query.lower()
        results: list[Symbol] = []
        for name, syms in self.symbols.items():
            if q in name.lower():
                results.extend(syms)
                if len(results) >= max_results:
                    break
        return results[:max_results]

    def merge(self, other: CodeIndex) -> None:
        """合并另一个索引 (用于增量更新)。"""
        for name, syms in other.symbols.items():
            self.symbols.setdefault(name, []).extend(syms)
        for fpath, syms in other.by_file.items():
            self.by_file.setdefault(fpath, []).extend(syms)
        for name, refs in other.references.items():
            self.references.setdefault(name, []).extend(refs)
        self.file_count = len(self.by_file)
        self.symbol_count = len(self.symbols)


# ═══════════════════════════════════════════════════════════════════
# §3  Language Parsers
# ═══════════════════════════════════════════════════════════════════


# 每种语言的符号提取正则表达式
_LANGUAGE_PATTERNS: dict[str, dict[str, Any]] = {
    "python": {
        "extensions": {".py"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^class\s+(\w+)(?:\(.*?\))?\s*:",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*[^:]+)?\s*:",
                re.MULTILINE,
            ),
            SymbolKind.CONSTANT: re.compile(
                r"^([A-Z][A-Z0-9_]+)\s*=\s*",
                re.MULTILINE,
            ),
            SymbolKind.IMPORT: re.compile(
                r"^(?:from\s+(\S+)\s+)?import\s+(\S+)",
                re.MULTILINE,
            ),
            SymbolKind.DECORATOR: re.compile(
                r"^@(\w+)",
                re.MULTILINE,
            ),
        },
        "class_method": re.compile(
            r"^\s+(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*[^:]+)?\s*:",
            re.MULTILINE,
        ),
    },
    "javascript": {
        "extensions": {".js", ".jsx", ".mjs", ".cjs"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.CONSTANT: re.compile(
                r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=",
                re.MULTILINE,
            ),
            SymbolKind.TYPE: re.compile(
                r"^(?:export\s+)?(?:interface|type)\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "typescript": {
        "extensions": {".ts", ".tsx"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.CONSTANT: re.compile(
                r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=",
                re.MULTILINE,
            ),
            SymbolKind.INTERFACE: re.compile(
                r"^(?:export\s+)?interface\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.TYPE: re.compile(
                r"^(?:export\s+)?type\s+(\w+)\s*=",
                re.MULTILINE,
            ),
            SymbolKind.ENUM: re.compile(
                r"^(?:export\s+)?enum\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "rust": {
        "extensions": {".rs"},
        "patterns": {
            SymbolKind.FUNCTION: re.compile(
                r"^(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.STRUCT: re.compile(
                r"^(?:pub\s+)?struct\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.ENUM: re.compile(
                r"^(?:pub\s+)?enum\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.TRAIT: re.compile(
                r"^(?:pub\s+)?(?:unsafe\s+)?trait\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.TYPE: re.compile(
                r"^(?:pub\s+)?type\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.CONSTANT: re.compile(
                r"^(?:pub\s+)?const\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.MODULE: re.compile(
                r"^(?:pub\s+)?mod\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "go": {
        "extensions": {".go"},
        "patterns": {
            SymbolKind.FUNCTION: re.compile(
                r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(",
                re.MULTILINE,
            ),
            SymbolKind.TYPE: re.compile(
                r"^type\s+(\w+)\s+(?:struct|interface|func)",
                re.MULTILINE,
            ),
            SymbolKind.CONSTANT: re.compile(
                r"^const\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.VARIABLE: re.compile(
                r"^var\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "java": {
        "extensions": {".java", ".kt"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:public|private|protected)?\s*(?:abstract|final|static)?\s*"
                r"(?:class|interface|enum|record)\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:public|private|protected)?\s*(?:static|final|abstract|synchronized)?\s*"
                r"(?:\w+(?:<[^>]*>)?)\s+(\w+)\s*\(",
                re.MULTILINE,
            ),
        },
    },
    "cpp": {
        "extensions": {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:class|struct)\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.ENUM: re.compile(
                r"^enum\s+(?:class\s+)?(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.NAMESPACE: re.compile(
                r"^namespace\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:static\s+|inline\s+|virtual\s+)?"
                r"(?:\w+(?:\s*\*)?(?:\s*&)?)\s+(\w+)\s*\(",
                re.MULTILINE,
            ),
        },
    },
    "ruby": {
        "extensions": {".rb"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^class\s+(\w+(?:::\w+)*)",
                re.MULTILINE,
            ),
            SymbolKind.MODULE: re.compile(
                r"^module\s+(\w+(?:::\w+)*)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^def\s+(?:self\.)?(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "php": {
        "extensions": {".php"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:abstract|final)?\s*class\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.INTERFACE: re.compile(
                r"^interface\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:public|private|protected)?\s*(?:static)?\s*function\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
    "swift": {
        "extensions": {".swift"},
        "patterns": {
            SymbolKind.CLASS: re.compile(
                r"^(?:public|private|internal|open)?\s*(?:final)?\s*class\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.STRUCT: re.compile(
                r"^(?:public|private|internal)?\s*struct\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.ENUM: re.compile(
                r"^(?:public|private|internal)?\s*enum\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.PROTOCOL: re.compile(
                r"^(?:public|private|internal)?\s*protocol\s+(\w+)",
                re.MULTILINE,
            ),
            SymbolKind.FUNCTION: re.compile(
                r"^(?:public|private|internal|open)?\s*(?:static|class)?\s*func\s+(\w+)",
                re.MULTILINE,
            ),
        },
    },
}



# 将缺失的枚举值添加到 SymbolKind
for _name in ("STRUCT", "TRAIT", "PROTOCOL"):
    if not hasattr(SymbolKind, _name):
        setattr(SymbolKind, _name, _name.lower())


# ═══════════════════════════════════════════════════════════════════
# §4  Indexer
# ═══════════════════════════════════════════════════════════════════


class CodeIndexer:
    """代码索引器 — 扫描文件系统并提取符号。"""

    # 默认跳过的目录
    SKIP_DIRS = frozenset({
        ".git", "__pycache__", "node_modules", "venv", ".venv",
        ".tox", "dist", "build", ".egg-info", "target",
        ".pytest_cache", ".ruff_cache", ".mypy_cache",
        ".zall", ".zcode", ".idea", ".vscode",
        "vendor", "bundle", ".bundle",
    })

    def __init__(
        self,
        skip_dirs: set[str] | None = None,
        max_file_size: int = 500 * 1024,  # 500KB
    ) -> None:
        self._skip_dirs = frozenset(skip_dirs) if skip_dirs else self.SKIP_DIRS
        self._max_file_size = max_file_size

    def index_project(self, project_dir: str | Path) -> CodeIndex:
        """索引整个项目。"""
        project_dir = Path(project_dir)
        if not project_dir.is_dir():
            raise ValueError(f"Not a directory: {project_dir}")

        index = CodeIndex()
        index.indexed_at = time.time()

        for root, dirs, files in os.walk(str(project_dir), topdown=True):
            # 过滤跳过目录
            dirs[:] = [d for d in dirs if d not in self._skip_dirs
                       and not d.startswith(".")]

            for filename in files:
                filepath = Path(root) / filename
                rel_path = str(filepath.relative_to(project_dir))

                try:
                    if filepath.stat().st_size > self._max_file_size:
                        continue

                    ext = filepath.suffix.lower()
                    lang = self._detect_language(ext)
                    if lang is None:
                        continue

                    content = filepath.read_text(encoding="utf-8", errors="replace")
                    symbols = self._parse_file(content, lang, rel_path)

                    for sym in symbols:
                        index.add_symbol(sym)

                    index.file_count += 1
                    index.indexed_files.add(rel_path)

                except (OSError, UnicodeDecodeError) as e:
                    index.errors.append(f"{rel_path}: {e}")

        return index

    def index_file(self, file_path: str | Path, project_dir: str | Path) -> CodeIndex:
        """索引单个文件 (增量更新用)。"""
        file_path = Path(file_path)
        project_dir = Path(project_dir)

        try:
            rel_path = str(file_path.relative_to(project_dir))
        except ValueError:
            rel_path = str(file_path)

        if not file_path.is_file():
            return CodeIndex()

        try:
            ext = file_path.suffix.lower()
            lang = self._detect_language(ext)
            if lang is None:
                return CodeIndex()

            content = file_path.read_text(encoding="utf-8", errors="replace")
            symbols = self._parse_file(content, lang, rel_path)

            index = CodeIndex()
            for sym in symbols:
                index.add_symbol(sym)
            index.file_count = 1
            index.indexed_files.add(rel_path)

            return index
        except (OSError, UnicodeDecodeError):
            return CodeIndex()

    def _detect_language(self, extension: str) -> str | None:
        """根据文件扩展名检测语言。"""
        for lang, config in _LANGUAGE_PATTERNS.items():
            if extension in config["extensions"]:
                return lang
        return None

    def _parse_file(self, content: str, language: str, file_path: str) -> list[Symbol]:
        """解析文件中的符号。"""
        config = _LANGUAGE_PATTERNS.get(language)
        if config is None:
            return []

        symbols: list[Symbol] = []
        lines = content.split("\n")
        patterns = config["patterns"]

        # 找 class 范围 (用于确定方法属于哪个类)
        current_class: str | None = None
        class_indent: int = 0

        for line_idx, line in enumerate(lines, 1):
            # 检测当前类范围
            stripped = line
            indent = len(line) - len(line.lstrip())

            # 如果缩进回到了类级别以下, 退出当前类
            if current_class is not None and indent <= class_indent and stripped.strip():
                if not stripped.strip().startswith(("#", "//", "/*", "*", "*/")):
                    current_class = None

            # 匹配符号
            for kind, pattern in patterns.items():
                match = pattern.search(stripped)
                if match:
                    name = match.group(1)
                    if name is None:
                        continue
                    # 提取签名
                    signature = stripped.strip()

                    # 检测是否为类定义
                    if kind in (SymbolKind.CLASS, SymbolKind.STRUCT,
                                SymbolKind.INTERFACE, SymbolKind.TRAIT):
                        current_class = name
                        class_indent = indent

                    symbol = Symbol(
                        name=name,
                        kind=kind,
                        location=SymbolLocation(
                            file_path=file_path,
                            line=line_idx,
                            column=stripped.find(name) + 1 if name in stripped else 1,
                            length=len(name),
                        ),
                        parent=current_class if kind == SymbolKind.FUNCTION
                        and current_class else None,
                        signature=signature[:200],
                        language=language,
                    )
                    symbols.append(symbol)
                    break

            # Python 类方法检测
            if language == "python" and current_class is not None:
                method_pattern = config.get("class_method")
                if method_pattern:
                    match = method_pattern.search(stripped)
                    if match and indent > class_indent:
                        name = match.group(1)
                        signature = stripped.strip()
                        symbols.append(Symbol(
                            name=name,
                            kind=SymbolKind.METHOD,
                            location=SymbolLocation(
                                file_path=file_path,
                                line=line_idx,
                                column=stripped.find(name) + 1,
                                length=len(name),
                            ),
                            parent=current_class,
                            signature=signature[:200],
                            language=language,
                        ))

        return symbols


# ═══════════════════════════════════════════════════════════════════
# §5  Navigator
# ═══════════════════════════════════════════════════════════════════


class CodeNavigator:
    """代码导航器 — 基于索引的查询服务。

    对应 Grok Build 的 Navigator。
    """

    def __init__(self, index: CodeIndex) -> None:
        self._index = index

    @property
    def index(self) -> CodeIndex:
        return self._index

    def goto_definition(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[SymbolLocation]:
        """跳转到定义 — 在指定位置查找符号的定义。

        Args:
            file_path: 当前文件路径
            line: 行号 (1-indexed)
            column: 列号 (1-indexed)

        Returns:
            符号定义位置列表
        """
        # 先找当前文件该位置的符号名
        symbols = self._index.get_file_symbols(file_path)
        name_at_pos = None

        for sym in symbols:
            loc = sym.location
            if loc.line == line:
                # 检查列范围
                if loc.column <= column <= loc.column + loc.length:
                    name_at_pos = sym.name
                    break

        if name_at_pos is None:
            # 尝试从行内容推断
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if 1 <= line <= len(lines):
                    text = lines[line - 1]
                    # 提取光标附近的标识符
                    word = self._extract_word_at(text, column - 1)
                    if word:
                        name_at_pos = word
            except OSError:
                pass

        if name_at_pos is None:
            return []

        return [
            sym.location for sym in self._index.get_symbols(name_at_pos)
        ]

    def find_references(self, name: str) -> list[SymbolReference]:
        """查找符号的所有引用。"""
        return self._index.get_references(name)

    def search_symbol(
        self,
        query: str,
        max_results: int = 20,
    ) -> list[Symbol]:
        """搜索符号。"""
        return self._index.search(query, max_results=max_results)

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        """获取文件中的所有符号。"""
        return self._index.get_file_symbols(file_path)

    def get_outline(self, file_path: str) -> list[dict[str, Any]]:
        """获取文件的大纲 (树形结构)。"""
        symbols = self._index.get_file_symbols(file_path)
        # 组织为树形 (顶级符号 + 子符号)
        top_level = []
        children: dict[str, list[Symbol]] = {}

        for sym in symbols:
            if sym.parent:
                children.setdefault(sym.parent, []).append(sym)
            else:
                top_level.append(sym)

        outline = []
        for sym in top_level:
            entry = {
                "name": sym.name,
                "kind": sym.kind.value,
                "line": sym.location.line,
                "signature": sym.signature,
            }
            if sym.name in children:
                entry["children"] = [
                    {
                        "name": c.name,
                        "kind": c.kind.value,
                        "line": c.location.line,
                        "signature": c.signature,
                    }
                    for c in children[sym.name]
                ]
            outline.append(entry)

        return outline

    @staticmethod
    def _extract_word_at(text: str, col: int) -> str | None:
        """从文本的指定列提取标识符。"""
        if col < 0 or col >= len(text):
            return None
        # 找到标识符边界
        start = col
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._"):
            start -= 1
        end = col
        while end < len(text) and (text[end].isalnum() or text[end] in "._"):
            end += 1

        word = text[start:end].strip()
        if word and re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", word):
            return word
        return None


# ═══════════════════════════════════════════════════════════════════
# §6  CodeGraph — 统一入口
# ═══════════════════════════════════════════════════════════════════


class CodeGraph:
    """代码图 — 统一入口点。

    管理索引、缓存、导航。

    Usage:
        graph = CodeGraph("/path/to/project")
        graph.index()
        symbols = graph.search("MyClass")
        defs = graph.goto_definition("src/main.py", 10, 5)
    """

    def __init__(
        self,
        project_dir: str | Path,
        cache_dir: str | Path | None = None,
        skip_dirs: set[str] | None = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._index: CodeIndex | None = None
        self._navigator: CodeNavigator | None = None
        self._indexer = CodeIndexer(skip_dirs=skip_dirs or set())
        self._cache_dir = Path(cache_dir) if cache_dir else None

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def index(self) -> CodeIndex | None:
        return self._index

    @property
    def navigator(self) -> CodeNavigator | None:
        return self._navigator

    def build_index(self) -> CodeIndex:
        """索引整个项目。"""
        self._index = self._indexer.index_project(self._project_dir)
        self._navigator = CodeNavigator(self._index)
        self._save_cache()
        return self._index

    def reindex_file(self, file_path: str | Path) -> None:
        """增量更新单个文件。"""
        if self._index is None:
            self.build_index()
            return

        file_index = self._indexer.index_file(file_path, self._project_dir)
        if file_index.symbol_count > 0:
            # 移除旧符号
            rel_path = str(Path(file_path).relative_to(self._project_dir))
            old_count = len(self._index.by_file.get(rel_path, []))
            if old_count > 0:
                # 简单处理: 移除该文件的所有旧符号
                for sym in self._index.by_file.get(rel_path, []):
                    name_syms = self._index.symbols.get(sym.name, [])
                    self._index.symbols[sym.name] = [
                        s for s in name_syms
                        if s.location.file_path != rel_path
                    ]
                self._index.by_file.pop(rel_path, None)

            # 合并新符号
            self._index.merge(file_index)

    def search(self, query: str, max_results: int = 20) -> list[Symbol]:
        """搜索符号。"""
        if self._index is None:
            return []
        return self._index.search(query, max_results=max_results)

    def goto_definition(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[SymbolLocation]:
        """跳转到定义。"""
        if self._navigator is None:
            return []
        return self._navigator.goto_definition(file_path, line, column)

    def find_references(self, name: str) -> list[SymbolReference]:
        """查找引用。"""
        if self._index is None:
            return []
        return self._index.get_references(name)

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        """获取文件符号。"""
        if self._index is None:
            return []
        return self._index.get_file_symbols(file_path)

    def get_outline(self, file_path: str) -> list[dict[str, Any]]:
        """获取文件大纲。"""
        if self._navigator is None:
            return []
        return self._navigator.get_outline(file_path)

    def get_stats(self) -> dict[str, Any]:
        """获取索引统计。"""
        if self._index is None:
            return {"status": "not_indexed"}
        return {
            "status": "indexed",
            "file_count": self._index.file_count,
            "symbol_count": self._index.symbol_count,
            "indexed_at": self._index.indexed_at,
            "error_count": len(self._index.errors),
        }

    # ── Cache ──

    def _cache_path(self) -> Path | None:
        if self._cache_dir:
            return self._cache_dir / "codegraph_index.json"
        return None

    def _save_cache(self) -> None:
        cache_path = self._cache_path()
        if cache_path is None or self._index is None:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            data = {
                "version": 1,
                "indexed_at": self._index.indexed_at,
                "file_count": self._index.file_count,
                "symbol_count": self._index.symbol_count,
                "files": list(self._index.indexed_files),
            }
            cache_path.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def load_cache(self) -> bool:
        """加载缓存。返回 True 如果成功。"""
        cache_path = self._cache_path()
        if cache_path is None or not cache_path.is_file():
            return False
        try:
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("version") == 1:
                # 缓存只存元数据, 实际索引需要重新构建
                # 但可以跳过未变化的文件
                return True
        except (OSError, json.JSONDecodeError):
            pass
        return False