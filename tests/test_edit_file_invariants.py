"""edit_file tool invariant test (§4.2 tool layer).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. old_string no match → success=False + prompt
  2. old_string 多处匹配 → success=False + 列出位置
  3. file not found → success=False
  4. old_string for空 → success=False
  5. path for空 → success=False
  6. construct后改 success → raise (ToolResult frozen)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.core.tool import Tool, ToolResult
from zall.tools.edit_file import EditFileTool


@pytest.fixture
def tool() -> EditFileTool:
    return EditFileTool()


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """Create a示例file."""
    path = tmp_path / "sample.py"
    path.write_text(
        "def hello():\n"
        "    print('hello')\n"
        "\n"
        "def world():\n"
        "    print('world')\n",
        encoding="utf-8",
    )
    return path


class TestEditFileProtocol:
    """verify EditFileTool 满足 Tool Protocol."""

    def test_is_tool(self, tool: EditFileTool) -> None:
        """满足 Tool Protocol."""
        assert isinstance(tool, Tool)

    def test_tool_id(self, tool: EditFileTool) -> None:
        """tool_id 是 'edit_file'."""
        assert tool.tool_id == "edit_file"

    def test_schema_has_path_required(self, tool: EditFileTool) -> None:
        """schema 的 required 含 'path'."""
        params = tool.schema["function"]["parameters"]
        assert "path" in params["required"]

    def test_schema_has_old_string_required(self, tool: EditFileTool) -> None:
        """schema 的 required 含 'old_string'."""
        params = tool.schema["function"]["parameters"]
        assert "old_string" in params["required"]

    def test_schema_has_new_string_required(self, tool: EditFileTool) -> None:
        """schema 的 required 含 'new_string'."""
        params = tool.schema["function"]["parameters"]
        assert "new_string" in params["required"]

    def test_execute_returns_tool_result(self, tool: EditFileTool, sample_file: Path) -> None:
        """execute returns ToolResult instance."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert isinstance(result, ToolResult)


class TestEditFileHappyPath:
    """正常editfile的场景."""

    def test_single_replacement(self, tool: EditFileTool, sample_file: Path) -> None:
        """单处replace成功."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert result.success
        assert "Replaced" in result.output

    def test_file_updated(self, tool: EditFileTool, sample_file: Path) -> None:
        """filecontent实际被修改."""
        tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        content = sample_file.read_text(encoding="utf-8")
        assert "print('hi')" in content
        assert "print('hello')" not in content

    def test_artifacts_contain_path(self, tool: EditFileTool, sample_file: Path) -> None:
        """artifacts 含 path."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert result.success
        assert "path" in result.artifacts

    def test_artifacts_contain_old_new_lines(self, tool: EditFileTool, sample_file: Path) -> None:
        """artifacts 含 old_lines 和 new_lines."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert result.success
        assert "old_lines" in result.artifacts
        assert "new_lines" in result.artifacts

    def test_artifacts_contain_diff(self, tool: EditFileTool, sample_file: Path) -> None:
        """artifacts 含 diff (unified diff)."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert result.success
        assert "diff" in result.artifacts
        assert len(result.artifacts["diff"]) > 0

    def test_multiline_replacement(self, tool: EditFileTool, sample_file: Path) -> None:
        """多行replace成功."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "def hello():\n    print('hello')",
            "new_string": "def greet():\n    print('greet')",
        })
        assert result.success
        assert "Replaced" in result.output


class TestEditFileCounterExamples:
    """Counterexampletest: verifyinputerror和边界条件handle."""

    def test_empty_path(self, tool: EditFileTool) -> None:
        """Counterexample: 空 path → success=False + 友好error."""
        result = tool.execute({
            "path": "",
            "old_string": "x",
            "new_string": "y",
        })
        assert not result.success
        assert result.error is not None

    def test_missing_path(self, tool: EditFileTool) -> None:
        """Counterexample: 缺失 path → success=False."""
        result = tool.execute({
            "old_string": "x",
            "new_string": "y",
        })
        assert not result.success

    def test_file_not_found(self, tool: EditFileTool) -> None:
        """Counterexample: file not found → success=False + 友好error."""
        result = tool.execute({
            "path": "/nonexistent/file.py",
            "old_string": "x",
            "new_string": "y",
        })
        assert not result.success
        assert "not found" in result.output.lower()

    def test_empty_old_string(self, tool: EditFileTool, sample_file: Path) -> None:
        """Counterexample: old_string for空 → success=False."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "",
            "new_string": "y",
        })
        assert not result.success
        assert "empty" in result.output.lower()

    def test_old_string_not_found(self, tool: EditFileTool, sample_file: Path) -> None:
        """Counterexample: old_string no match → success=False + prompt."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "nonexistent_string_xyz",
            "new_string": "y",
        })
        assert not result.success
        assert "not found" in result.output.lower()

    def test_old_string_multiple_matches(self, tool: EditFileTool, tmp_path: Path) -> None:
        """Counterexample: old_string 多处匹配 → success=False + 列出位置."""
        path = tmp_path / "multi.py"
        path.write_text("x = 1\ny = 2\nx = 3\n", encoding="utf-8")
        result = tool.execute({
            "path": str(path),
            "old_string": "x",
            "new_string": "z",
        })
        assert not result.success
        assert "multiple matches" in result.output.lower() or "matched" in result.output.lower()

    def test_result_is_frozen(self, tool: EditFileTool, sample_file: Path) -> None:
        """Counterexample: construct后改 success → must raise (ToolResult frozen)."""
        result = tool.execute({
            "path": str(sample_file),
            "old_string": "print('hello')",
            "new_string": "print('hi')",
        })
        assert result.success
        with pytest.raises((TypeError, ValueError)):
            result.success = False

    def test_output_non_empty_on_failure(self, tool: EditFileTool) -> None:
        """Counterexample: 即使fail也有output, 不允许静默fail."""
        result = tool.execute({"path": "", "old_string": "", "new_string": ""})
        assert not result.success
        assert result.output

    def test_directory_instead_of_file(self, tool: EditFileTool, tmp_path: Path) -> None:
        """Counterexample: path 是directory → success=False + 友好error."""
        result = tool.execute({
            "path": str(tmp_path),
            "old_string": "x",
            "new_string": "y",
        })
        assert not result.success
        assert "not a file" in result.output.lower()