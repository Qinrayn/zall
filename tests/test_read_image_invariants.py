"""read_image tool invariant tests (§4.2 tool extension: read_image).

IPR-0: each invariant must include a counterexample.
IPR-1: corresponds to DESIGN.md §4.2 (tool extension).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.core.tool import Tool, ToolResult
from zall.tools.read_image import ReadImageTool


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def tool() -> ReadImageTool:
    return ReadImageTool()


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    """Create a 1x1 red PNG file."""
    import base64

    # minimum valid PNG (1x1 red像素)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ"
        "/PchI7wAAAABJRU5ErkJggg=="
    )
    path = tmp_path / "test.png"
    path.write_bytes(base64.b64decode(png_b64))
    return path


@pytest.fixture
def jpg_file(tmp_path: Path) -> Path:
    """Create a 2x2 JPEG file."""
    from PIL import Image

    path = tmp_path / "test.jpg"
    img = Image.new("RGB", (2, 2), color="red")
    img.save(path, format="JPEG")
    return path


@pytest.fixture
def large_file(tmp_path: Path) -> Path:
    """Create a超过 10MB 的假图片file."""
    from PIL import Image

    # Create a需要大存储的图片
    path = tmp_path / "large.png"
    # 16M x 1px RGBA ≈ 64MB raw, but PNG compression will help
    # Actually let's just create a fake large file
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024))
    return path


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    """Create a文本file (non-图片)."""
    path = tmp_path / "notes.txt"
    path.write_text("not an image", encoding="utf-8")
    return path


# ────────────────────────────────────────────────────────────────
# Protocol invariants
# ────────────────────────────────────────────────────────────────


class TestReadImageToolProtocol:
    """verify ReadImageTool 满足 Tool Protocol."""

    def test_is_tool(self, tool: ReadImageTool) -> None:
        """满足 Tool Protocol."""
        from typing import runtime_checkable

        assert isinstance(tool, Tool)

    def test_tool_id(self, tool: ReadImageTool) -> None:
        """tool_id 是 'read_image'."""
        assert tool.tool_id == "read_image"

    def test_schema_has_path_required(self, tool: ReadImageTool) -> None:
        """schema 的 required 含 'path'."""
        params = tool.schema["function"]["parameters"]
        assert "path" in params["required"]

    def test_schema_description(self, tool: ReadImageTool) -> None:
        """schema 有有意义的 description."""
        desc = tool.schema["function"]["description"]
        assert len(desc) > 20

    def test_execute_returns_tool_result(self, tool: ReadImageTool) -> None:
        """execute returns ToolResult instance."""
        result = tool.execute({"path": "/nonexistent/image.png"})
        assert isinstance(result, ToolResult)


# ────────────────────────────────────────────────────────────────
# Happy path tests
# ────────────────────────────────────────────────────────────────


class TestReadImageHappyPath:
    """正常read图片的场景."""

    def test_read_png(self, tool: ReadImageTool, png_file: Path) -> None:
        """read PNG returns success=True + 元数据."""
        result = tool.execute({"path": str(png_file)})
        assert result.success
        assert "PNG" in result.output
        assert len(result.artifacts["base64"]) > 0

    def test_read_jpg(self, tool: ReadImageTool, jpg_file: Path) -> None:
        """read JPG returns success=True + 元数据."""
        result = tool.execute({"path": str(jpg_file)})
        assert result.success
        assert result.artifacts["format"] == "JPEG"

    def test_artifacts_contain_metadata(self, tool: ReadImageTool, png_file: Path) -> None:
        """artifacts 包含完整元数据."""
        result = tool.execute({"path": str(png_file)})
        assert result.success
        meta = result.artifacts
        assert "width" in meta and meta["width"] >= 1
        assert "height" in meta and meta["height"] >= 1
        assert "format" in meta
        assert "mime_type" in meta
        assert "file_size_bytes" in meta and meta["file_size_bytes"] > 0
        assert "base64" in meta

    def test_mime_type_png(self, tool: ReadImageTool, png_file: Path) -> None:
        """PNG 图片的 MIME typecorrectly."""
        result = tool.execute({"path": str(png_file)})
        assert result.success
        assert result.artifacts["mime_type"] == "image/png"

    def test_mime_type_jpg(self, tool: ReadImageTool, jpg_file: Path) -> None:
        """JPG 图片的 MIME typecorrectly."""
        result = tool.execute({"path": str(jpg_file)})
        assert result.success
        assert result.artifacts["mime_type"] == "image/jpeg"

    def test_output_contains_description(self, tool: ReadImageTool, png_file: Path) -> None:
        """output包含可读的digestdescription."""
        result = tool.execute({"path": str(png_file)})
        assert result.success
        assert "Dimensions" in result.output
        assert "Format" in result.output
        assert "Base64" in result.output


# ────────────────────────────────────────────────────────────────
# Counterexamples (IPR-0)
# ────────────────────────────────────────────────────────────────


class TestReadImageCounterExamples:
    """Counterexampletest: verifyinputerror和边界条件handle."""

    def test_empty_path(self, tool: ReadImageTool) -> None:
        """Counterexample: 空 path → success=False + 友好error."""
        result = tool.execute({"path": ""})
        assert not result.success
        assert "required" in result.output.lower()

    def test_missing_path_key(self, tool: ReadImageTool) -> None:
        """Counterexample: 缺失 path parameter → success=False."""
        result = tool.execute({})
        assert not result.success

    def test_nonexistent_file(self, tool: ReadImageTool) -> None:
        """Counterexample: file not found → success=False + 友好error."""
        result = tool.execute({"path": "/nonexistent/image.png"})
        assert not result.success
        assert "not found" in result.output.lower()

    def test_path_is_directory(self, tool: ReadImageTool, tmp_path: Path) -> None:
        """Counterexample: path 是directory而non-file → 友好error."""
        result = tool.execute({"path": str(tmp_path)})
        assert not result.success
        assert "not a file" in result.output.lower()

    def test_unsupported_format(self, tool: ReadImageTool, tmp_path: Path) -> None:
        """Counterexample: 不支持的格式 (.svg, .ico) → 友好error."""
        svg_path = tmp_path / "test.svg"
        svg_path.write_text("<svg></svg>", encoding="utf-8")
        result = tool.execute({"path": str(svg_path)})
        assert not result.success
        assert "unsupported" in result.output.lower()

    def test_text_file_as_image(self, tool: ReadImageTool, text_file: Path) -> None:
        """Counterexample: non-图片file → PIL 报错 (不影响系统)."""
        result = tool.execute({"path": str(text_file)})
        assert not result.success
        # PIL 会报 "cannot identify image file"
        assert "error" in result.error.lower() or not result.success

    def test_large_image_rejected(self, tool: ReadImageTool, tmp_path: Path) -> None:
        """Counterexample: 图片超过 10MB → reject + prompt."""
        # create超过 10MB 的file
        large_path = tmp_path / "huge.png"
        large_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024))

        result = tool.execute({"path": str(large_path)})
        assert not result.success
        assert "too large" in result.output.lower() or "10 MB" in result.output

    def test_result_is_frozen(self, tool: ReadImageTool, png_file: Path) -> None:
        """Counterexample: construct后改 success → must raise (ToolResult frozen)."""
        result = tool.execute({"path": str(png_file)})
        assert result.success
        with pytest.raises((TypeError, ValueError)):
            result.success = False

    def test_output_non_empty_on_failure(self, tool: ReadImageTool) -> None:
        """Counterexample: 即使fail也有output, 不允许静默fail."""
        result = tool.execute({"path": ""})
        assert not result.success
        assert result.output  # output non-空