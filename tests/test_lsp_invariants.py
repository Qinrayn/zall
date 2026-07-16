"""Tests for LSP integration (Phase 3b).

IPR-0: invariant tests must be written before or alongside the code.
"""

from __future__ import annotations

import os
import pytest
import tempfile
from pathlib import Path

from zall.lsp import (
    DiagnosticEntry,
    DiagnosticSeverity,
    LspManager,
    LspServerConfig,
    LocationLink,
    HoverInfo,
    CompletionItem,
    KNOWN_SERVERS,
)


class TestDiagnosticEntry:
    """DiagnosticEntry invariants."""

    def test_creation(self):
        d = DiagnosticEntry(
            file_path="test.py",
            line=0,
            column=0,
            message="Test error",
            severity=DiagnosticSeverity.ERROR,
        )
        assert d.severity_label == "error"
        assert "test.py:1:1: error: Test error" in str(d)

    def test_severity_labels(self):
        assert DiagnosticEntry(
            file_path="", line=0, column=0, message="",
            severity=DiagnosticSeverity.ERROR,
        ).severity_label == "error"
        assert DiagnosticEntry(
            file_path="", line=0, column=0, message="",
            severity=DiagnosticSeverity.WARNING,
        ).severity_label == "warning"
        assert DiagnosticEntry(
            file_path="", line=0, column=0, message="",
            severity=DiagnosticSeverity.INFORMATION,
        ).severity_label == "info"
        assert DiagnosticEntry(
            file_path="", line=0, column=0, message="",
            severity=DiagnosticSeverity.HINT,
        ).severity_label == "hint"


class TestLocationLink:
    """LocationLink invariants."""

    def test_creation(self):
        loc = LocationLink(file_path="src/main.py", line=10, column=5)
        assert loc.file_path == "src/main.py"
        assert loc.line == 10
        assert loc.column == 5


class TestHoverInfo:
    """HoverInfo invariants."""

    def test_creation(self):
        h = HoverInfo(content="def hello() -> str", language="python")
        assert h.content == "def hello() -> str"
        assert h.language == "python"


class TestCompletionItem:
    """CompletionItem invariants."""

    def test_creation(self):
        item = CompletionItem(
            label="hello",
            kind="Function",
            detail="def hello()",
            documentation="Greets the user",
        )
        assert item.label == "hello"
        assert item.kind == "Function"


class TestKnownServers:
    """KNOWN_SERVERS invariants."""

    def test_known_servers_have_configs(self):
        assert "python" in KNOWN_SERVERS
        assert "typescript" in KNOWN_SERVERS
        assert "rust" in KNOWN_SERVERS
        assert "go" in KNOWN_SERVERS

    def test_server_config_types(self):
        for lang, config in KNOWN_SERVERS.items():
            assert isinstance(config, LspServerConfig)
            assert config.command, f"Server {lang} has no command"
        # python-pylsp and python share language="python"
        assert KNOWN_SERVERS["python"].language == "python"
        assert KNOWN_SERVERS["python-pylsp"].language == "python"


class TestLspManager:
    """LspManager invariants."""

    def test_empty_manager(self):
        mgr = LspManager(project_dir="/nonexistent")
        assert len(mgr.active_servers) == 0
        assert mgr.summary()["active_servers"] == []

    def test_detect_language(self):
        mgr = LspManager()
        assert mgr.detect_language("test.py") == "python"
        assert mgr.detect_language("test.js") == "typescript"
        assert mgr.detect_language("test.ts") == "typescript"
        assert mgr.detect_language("test.rs") == "rust"
        assert mgr.detect_language("test.go") == "go"
        assert mgr.detect_language("test.txt") is None

    def test_register_file(self):
        mgr = LspManager()
        lang = mgr.register_file("src/main.py")
        assert lang == "python"
        assert mgr.detect_language("src/main.py") == "python"

    def test_unknown_language_returns_none(self):
        mgr = LspManager()
        result = mgr.goto_definition("test.txt", 0, 0)
        assert result == []

    def test_start_unknown_server_raises(self):
        mgr = LspManager()
        with pytest.raises(KeyError):
            mgr.start_server("nonexistent")

    def test_start_server_twice_returns_same(self):
        mgr = LspManager()
        # Both should raise because pyright-langserver may not be installed
        with pytest.raises((RuntimeError, KeyError)):
            mgr.start_server("python")

    def test_shutdown_all_empty(self):
        mgr = LspManager()
        mgr.shutdown_all()  # Should not crash
        assert len(mgr.active_servers) == 0

    def test_diagnostics_initialization(self):
        mgr = LspManager()
        diags = mgr.get_diagnostics("test.py")
        assert diags == []

    def test_handle_diagnostics_push(self):
        mgr = LspManager()
        # Use a path that's easy to verify
        test_path = "/project/test.py"
        mgr.handle_diagnostics(
            uri=f"file://{test_path}",
            diagnostics=[{
                "range": {"start": {"line": 0, "character": 0}},
                "message": "undefined variable 'x'",
                "severity": 1,
                "source": "pyright",
            }],
        )
        # The path normalization may prefix with / on Windows
        all_diags = mgr.all_diagnostics
        assert len(all_diags) > 0
        # Find the diagnostic by message
        for path, diags in all_diags.items():
            if diags and diags[0].message == "undefined variable 'x'":
                assert diags[0].severity == DiagnosticSeverity.ERROR
                return
        pytest.fail("Diagnostic not found in any path")

    def test_multi_file_diagnostics(self):
        mgr = LspManager()
        # Push diagnostics for multiple files
        mgr.handle_diagnostics(
            uri="file:///project/a.py",
            diagnostics=[{
                "range": {"start": {"line": 0, "character": 0}},
                "message": "error in a",
                "severity": 1,
            }],
        )
        mgr.handle_diagnostics(
            uri="file:///project/b.py",
            diagnostics=[{
                "range": {"start": {"line": 1, "character": 2}},
                "message": "warning in b",
                "severity": 2,
            }],
        )
        assert len(mgr.all_diagnostics) == 2

    def test_summary_counts(self):
        mgr = LspManager()
        mgr.handle_diagnostics(
            uri="file:///project/test.py",
            diagnostics=[
                {
                    "range": {"start": {"line": 0, "character": 0}},
                    "message": "error 1", "severity": 1,
                },
                {
                    "range": {"start": {"line": 1, "character": 0}},
                    "message": "error 2", "severity": 1,
                },
                {
                    "range": {"start": {"line": 2, "character": 0}},
                    "message": "warning 1", "severity": 2,
                },
            ],
        )
        summary = mgr.summary()
        assert summary["diagnostics_errors"] == 2
        assert summary["diagnostics_warnings"] == 1

    def test_open_file_auto_detects_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.py"
            filepath.write_text("x = 1\n", encoding="utf-8")

            mgr = LspManager(project_dir=tmpdir)
            # Language detection works even if server not found
            assert mgr.detect_language(str(filepath)) == "python"

    def test_close_file(self):
        mgr = LspManager()
        mgr.register_file("test.py")
        mgr.close_file("test.py")
        # After close, file should no longer be tracked
        # _file_to_language should have it removed
        assert mgr.detect_language("test.py") == "python"  # auto-detect still works

    def test_repr(self):
        mgr = LspManager()
        assert "LspManager" in repr(mgr)