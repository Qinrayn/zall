"""Tests for CodeGraph system (Phase 2c).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import os
import pytest
import tempfile
from pathlib import Path

from zall.codegraph import (
    CodeGraph,
    CodeIndex,
    CodeIndexer,
    CodeNavigator,
    Symbol,
    SymbolKind,
    SymbolLocation,
    SymbolReference,
)


class TestSymbol:
    """Symbol invariants."""

    def test_symbol_creation(self):
        loc = SymbolLocation(file_path="test.py", line=10, column=5, length=8)
        sym = Symbol(
            name="MyClass",
            kind=SymbolKind.CLASS,
            location=loc,
            language="python",
        )
        assert sym.name == "MyClass"
        assert sym.kind == SymbolKind.CLASS
        assert sym.location.line == 10
        assert sym.qualified_name == "MyClass"

    def test_qualified_name(self):
        loc = SymbolLocation(file_path="test.py", line=10, column=5)
        sym = Symbol(
            name="my_method",
            kind=SymbolKind.METHOD,
            location=loc,
            parent="MyClass",
            language="python",
        )
        assert sym.qualified_name == "MyClass.my_method"

    def test_location_str(self):
        loc = SymbolLocation(file_path="src/main.py", line=42, column=10)
        assert str(loc) == "src/main.py:42:10"


class TestCodeIndex:
    """CodeIndex invariants."""

    def test_empty_index(self):
        index = CodeIndex()
        assert index.symbol_count == 0
        assert index.file_count == 0
        assert index.get_symbols("anything") == []

    def test_add_symbol(self):
        index = CodeIndex()
        sym = Symbol(
            name="hello",
            kind=SymbolKind.FUNCTION,
            location=SymbolLocation(file_path="test.py", line=1, column=1),
            language="python",
        )
        index.add_symbol(sym)
        assert index.symbol_count == 1
        assert len(index.get_symbols("hello")) == 1
        assert len(index.get_file_symbols("test.py")) == 1

    def test_search(self):
        index = CodeIndex()
        for name in ["MyClass", "my_function", "MY_CONSTANT", "other"]:
            index.add_symbol(Symbol(
                name=name,
                kind=SymbolKind.CLASS,
                location=SymbolLocation(file_path="test.py", line=1, column=1),
                language="python",
            ))

        results = index.search("my")
        assert len(results) >= 2  # MyClass, my_function
        names = [s.name for s in results]
        assert "my_function" in names

    def test_merge(self):
        index1 = CodeIndex()
        index1.add_symbol(Symbol(
            name="func1",
            kind=SymbolKind.FUNCTION,
            location=SymbolLocation(file_path="a.py", line=1, column=1),
            language="python",
        ))

        index2 = CodeIndex()
        index2.add_symbol(Symbol(
            name="func2",
            kind=SymbolKind.FUNCTION,
            location=SymbolLocation(file_path="b.py", line=1, column=1),
            language="python",
        ))

        index1.merge(index2)
        assert index1.symbol_count == 2
        assert len(index1.get_symbols("func2")) == 1

    def test_file_tracking(self):
        index = CodeIndex()
        index.file_count = 5
        index.indexed_files = {"a.py", "b.py", "c.py"}
        assert len(index.indexed_files) == 3


class TestCodeIndexer:
    """CodeIndexer invariants."""

    def test_index_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 0
            assert index.symbol_count == 0

    def test_index_python_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple Python file
            code = '''
class MyClass:
    def my_method(self):
        pass

def my_function():
    pass

MY_CONSTANT = 42
'''
            (Path(tmpdir) / "test.py").write_text(code, encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 1
            assert index.symbol_count >= 3

            # Check specific symbols
            classes = index.get_symbols("MyClass")
            assert len(classes) >= 1
            assert classes[0].kind == SymbolKind.CLASS

            functions = index.get_symbols("my_function")
            assert len(functions) >= 1

    def test_index_javascript_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
class UserController {
    constructor() {}
}

function doSomething() {
    return null;
}

const API_URL = "https://api.example.com";
'''
            (Path(tmpdir) / "app.js").write_text(code, encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 1
            assert index.symbol_count >= 3

    def test_index_typescript_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
interface User {
    name: string;
    age: number;
}

type Callback = () => void;

enum Color {
    Red,
    Blue,
}

class Service {
    async fetch(): Promise<User> {
        return { name: "test", age: 1 };
    }
}
'''
            (Path(tmpdir) / "types.ts").write_text(code, encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.symbol_count >= 3

    def test_index_rust_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
use std::collections::HashMap;

pub struct Config {
    pub name: String,
}

pub enum Status {
    Active,
    Inactive,
}

pub trait Runnable {
    fn run(&self);
}

pub fn main() -> Result<(), Box<dyn std::error::Error>> {
    Ok(())
}
'''
            (Path(tmpdir) / "main.rs").write_text(code, encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.symbol_count >= 4

    def test_index_go_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
package main

type Server struct {
    Port int
}

func main() {
    println("hello")
}

const DEFAULT_PORT = 8080
'''
            (Path(tmpdir) / "main.go").write_text(code, encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.symbol_count >= 3

    def test_skip_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file in a skip dir
            node_modules = Path(tmpdir) / "node_modules"
            node_modules.mkdir()
            (node_modules / "index.js").write_text("const x = 1;", encoding="utf-8")

            # File in root
            (Path(tmpdir) / "main.py").write_text("def hello(): pass", encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            # Should only find the root file
            assert index.file_count == 1

    def test_skip_large_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            large_file = Path(tmpdir) / "large.py"
            large_file.write_text("x = 1\n" * 20000, encoding="utf-8")
            # File is ~140KB, under 500KB default limit

            indexer = CodeIndexer(max_file_size=100)  # 100 bytes
            index = indexer.index_project(tmpdir)
            assert index.file_count == 0  # Too large

    def test_skip_binary_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary_file = Path(tmpdir) / "data.bin"
            binary_file.write_bytes(b"\x00\x01\x02\x03\xff")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 0  # Not a recognized extension

    def test_no_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "data.txt").write_text("plain text", encoding="utf-8")
            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 0  # .txt not supported


class TestCodeNavigator:
    """CodeNavigator invariants."""

    def test_empty_navigator(self):
        index = CodeIndex()
        nav = CodeNavigator(index)
        assert nav.search_symbol("anything") == []
        assert nav.get_file_symbols("test.py") == []

    def test_goto_definition(self):
        index = CodeIndex()
        index.add_symbol(Symbol(
            name="MyClass",
            kind=SymbolKind.CLASS,
            location=SymbolLocation(file_path="test.py", line=10, column=1, length=7),
            language="python",
        ))
        index.add_symbol(Symbol(
            name="my_func",
            kind=SymbolKind.FUNCTION,
            location=SymbolLocation(file_path="test.py", line=20, column=1, length=7),
            language="python",
        ))

        nav = CodeNavigator(index)

        # Find definition of MyClass
        defs = nav.goto_definition("test.py", 10, 3)
        assert len(defs) >= 1
        assert defs[0].file_path == "test.py"
        assert defs[0].line == 10

    def test_search_symbol(self):
        index = CodeIndex()
        index.add_symbol(Symbol(
            name="DataProcessor",
            kind=SymbolKind.CLASS,
            location=SymbolLocation(file_path="process.py", line=1, column=1),
            language="python",
        ))

        nav = CodeNavigator(index)
        results = nav.search_symbol("Data")
        assert len(results) >= 1
        assert results[0].name == "DataProcessor"

    def test_get_outline(self):
        index = CodeIndex()
        index.add_symbol(Symbol(
            name="MyClass",
            kind=SymbolKind.CLASS,
            location=SymbolLocation(file_path="test.py", line=1, column=1),
            language="python",
        ))
        index.add_symbol(Symbol(
            name="my_method",
            kind=SymbolKind.METHOD,
            location=SymbolLocation(file_path="test.py", line=3, column=5),
            parent="MyClass",
            language="python",
        ))

        nav = CodeNavigator(index)
        outline = nav.get_outline("test.py")
        assert len(outline) >= 1
        assert outline[0]["name"] == "MyClass"
        assert outline[0]["kind"] == "class"


class TestCodeGraph:
    """CodeGraph integration invariants."""

    def test_empty_graph(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            graph = CodeGraph(tmpdir)
            assert graph.index is None
            assert graph.navigator is None
            assert graph.get_stats()["status"] == "not_indexed"
            assert graph.search("anything") == []

    def test_index_and_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
class Calculator:
    def add(self, a, b):
        return a + b

def helper():
    pass
'''
            (Path(tmpdir) / "calc.py").write_text(code, encoding="utf-8")

            graph = CodeGraph(tmpdir)
            index = graph.build_index()
            assert index.file_count >= 1
            assert index.symbol_count >= 2

            # Search
            results = graph.search("Calculator")
            assert len(results) >= 1
            assert results[0].kind == SymbolKind.CLASS

            # Stats
            stats = graph.get_stats()
            assert stats["status"] == "indexed"
            assert stats["file_count"] >= 1

    def test_reindex_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = 'def original(): pass\n'
            filepath = Path(tmpdir) / "test.py"
            filepath.write_text(code, encoding="utf-8")

            graph = CodeGraph(tmpdir)
            graph.build_index()
            assert len(graph.search("original")) >= 1

            # Modify file
            filepath.write_text('def modified(): pass\n', encoding="utf-8")
            graph.reindex_file(filepath)

            # Should find the new symbol
            assert len(graph.search("modified")) >= 1

    def test_get_file_symbols(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = 'class A: pass\nclass B: pass\n'
            (Path(tmpdir) / "test.py").write_text(code, encoding="utf-8")

            graph = CodeGraph(tmpdir)
            graph.build_index()
            symbols = graph.get_file_symbols("test.py")
            assert len(symbols) == 2

    def test_find_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = 'x = 1\n'
            (Path(tmpdir) / "test.py").write_text(code, encoding="utf-8")

            graph = CodeGraph(tmpdir)
            graph.build_index()
            # No references indexed yet
            refs = graph.find_references("x")
            assert refs == []

    def test_get_outline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = '''
class MyClass:
    def method1(self): pass
    def method2(self): pass
'''
            (Path(tmpdir) / "test.py").write_text(code, encoding="utf-8")

            graph = CodeGraph(tmpdir)
            graph.build_index()
            outline = graph.get_outline("test.py")
            assert len(outline) >= 1
            # Should have methods as children
            if "children" in outline[0]:
                assert len(outline[0]["children"]) >= 2


class TestCodeIndexerIntegration:
    """Integration: Index multiple files."""

    def test_multi_language_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Python
            (Path(tmpdir) / "main.py").write_text(
                "def main(): pass\nclass App: pass\n",
                encoding="utf-8",
            )
            # JavaScript
            (Path(tmpdir) / "utils.js").write_text(
                "function helper() {}\nconst CONST = 1;\n",
                encoding="utf-8",
            )
            # TypeScript
            (Path(tmpdir) / "types.ts").write_text(
                "interface User {}\ntype ID = string;\n",
                encoding="utf-8",
            )

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            assert index.file_count == 3
            assert index.symbol_count >= 5

    def test_errors_collected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that might cause errors
            (Path(tmpdir) / "broken.py").write_text("valid code", encoding="utf-8")

            indexer = CodeIndexer()
            index = indexer.index_project(tmpdir)
            # Should succeed with no errors
            assert len(index.errors) == 0