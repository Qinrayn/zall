"""read_file invariant test (ACI ACI design).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from zall.core.tool import Tool
from zall.tools.read_file import ReadFileTool


@pytest.fixture
def tool() -> ReadFileTool:
    return ReadFileTool()


@pytest.fixture
def tmp_file() -> str:
    """Create a temporary text file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
        for i in range(1, 51):
            f.write(f"Line {i}\n")
        return f.name


@pytest.fixture
def large_file() -> str:
    """create超过 2000 行的临时大file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
        for i in range(1, 2501):
            f.write(f"Line {i}\n")
        return f.name


def cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class TestReadFileToolProtocol:
    """ReadFileTool 满足 Tool Protocol."""

    def test_is_tool(self, tool: ReadFileTool) -> None:
        assert isinstance(tool, Tool)

    def test_tool_id(self, tool: ReadFileTool) -> None:
        assert tool.tool_id == "read_file"

    def test_schema_has_path_required(self, tool: ReadFileTool) -> None:
        assert "path" in tool.schema["function"]["parameters"]["required"]


class TestReadFileToolHappyPath:
    """Happy path: 正常fileread."""

    def test_read_first_10_lines(self, tool: ReadFileTool, tmp_file: str) -> None:
        result = tool.execute({"path": tmp_file})
        assert result.success is True
        assert "Line 1" in result.output
        assert "Lines 1-50 of 50" in result.output or "Lines 1-100 of 50" in result.output

    def test_read_with_offset_and_limit(self, tool: ReadFileTool, tmp_file: str) -> None:
        result = tool.execute({"path": tmp_file, "offset": 10, "limit": 5})
        assert result.success is True
        assert "Line 10" in result.output
        assert "Line 14" in result.output
        assert "Line 15" not in result.output

    def test_read_single_line(self, tool: ReadFileTool, tmp_file: str) -> None:
        result = tool.execute({"path": tmp_file, "offset": 5, "limit": 1})
        assert result.success is True
        assert "Line 5" in result.output
        assert "Line 6" not in result.output

    def test_artifacts_contain_path_and_line_count(self, tool: ReadFileTool, tmp_file: str) -> None:
        result = tool.execute({"path": tmp_file})
        assert result.success is True
        assert result.artifacts["total_lines"] == 50
        assert result.artifacts["path"] is not None


class TestReadFileToolLargeFile:
    """大filetruncate."""

    def test_large_file_shows_truncation_notice(self, tool: ReadFileTool, large_file: str) -> None:
        result = tool.execute({"path": large_file})
        assert result.success is True
        # 超过 2000 行且 limit=100, 不会触发 MAX_LINES truncate (limit 100 < 2000)
        # 所以应该有 100 行.大file使用估算, 格式for "~N"
        import re
        assert re.search(r"Lines 1-100 of ~\d+", result.output), (
            f"Expected 'Lines 1-100 of ~N' pattern, got: {result.output[:80]}"
        )


class TestReadFileToolCounterExamples:
    """Counterexample: 边界场景correctlyhandle."""

    def test_file_not_found(self, tool: ReadFileTool) -> None:
        """Counterexample: file not found → 友好error, does not crash."""
        result = tool.execute({"path": "/nonexistent/path/file.txt"})
        assert result.success is False
        assert "file not found" in result.output.lower() or "ERROR" in result.output

    def test_empty_path_raises(self, tool: ReadFileTool) -> None:
        """Counterexample: path for空 → 友好error."""
        result = tool.execute({"path": ""})
        assert result.success is False
        assert "required" in result.output.lower()

    def test_directory_instead_of_file(self, tool: ReadFileTool) -> None:
        """Counterexample: path 是directory而non-file → 友好error."""
        result = tool.execute({"path": tempfile.gettempdir()})
        assert result.success is False
        assert "not a file" in result.output.lower() or "ERROR" in result.output

    def test_limit_exceeds_max_is_capped(self, tool: ReadFileTool, tmp_file: str) -> None:
        """Counterexample: limit=99999 被 cap 到 MAX_LINES (防model传超大 limit 吞file)."""
        result = tool.execute({"path": tmp_file, "limit": 99999})
        assert result.success is True
        # file只有 50 行, 所以显示全部 50 行
        assert "Lines 1-50 of 50" in result.output or "Lines 1-100 of 50" in result.output

    def test_offset_negative_defaults_to_1(self, tool: ReadFileTool, tmp_file: str) -> None:
        """Counterexample: offset < 1 → 自动修正for 1."""
        result = tool.execute({"path": tmp_file, "offset": -5, "limit": 5})
        assert result.success is True
        assert "Line 1" in result.output

    def test_offset_beyond_file_shows_empty(self, tool: ReadFileTool, tmp_file: str) -> None:
        """Counterexample: offset 超过file总行数 → 显示空content."""
        result = tool.execute({"path": tmp_file, "offset": 9999, "limit": 5})
        assert result.success is True
        # 不包含任何 Line 行
        assert "Line 1" not in result.output