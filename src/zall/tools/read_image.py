"""zall.tools.read_image — Read image file tool (ACI design).

Corresponds to DESIGN.md §4.2 tool extension: read_image
Adds multimodal capability — agent can see screenshots, UI mockups, diagrams.

ACI Design notes:
  - 自动检测图片格式 (PNG/JPG/GIF/WebP/BMP)
  - 返回 base64 编码 + 元数据 (尺寸/格式/大小)
  - 图片过大 (>10MB) 时拒绝并给出提示
  - 通过 artifacts 返回结构化元数据, 供模型/下游使用
  - 保持轻量: 仅读取不动图 (GIF 只读第一帧), 不处理视频
  - 终端渲染: 支持 iTerm2 内联图像协议, Kitty 图形协议, 兜底路径显示

IPR constraints:
  IPR-0: invariant tests at tests/test_read_image_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (工具扩展)
  IPR-3: only stdlib + PIL, no model SDK
"""

from __future__ import annotations

import base64
import io as _io
import os
import sys
from pathlib import Path
from typing import Any

from zall._util.path import resolve_path

from zall.core.tool import Tool, ToolResult

# 图片最大大小 (超过此数reject, prevents context pollution)
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB

# 支持的图片格式 (MIME types)
SUPPORTED_FORMATS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


# ── 终端图像渲染能力检测 ──


def _detect_terminal_image_capability() -> str:
    """检测终端是否支持内联图像渲染.

    返回值:
        "iterm2"  — iTerm2 内联图像协议
        "kitty"   — Kitty 图形协议
        "sixel"   — Sixel 图形格式
        "none"    — 不支持, 退化为文本
    """
    term_program = os.environ.get("TERM_PROGRAM", "")
    term = os.environ.get("TERM", "")

    # iTerm2
    if term_program == "iTerm.app":
        return "iterm2"
    if "iTerm2" in term_program:
        return "iterm2"

    # Kitty: TERM=xterm-kitty 或 TERM_PROGRAM=Kitty
    if term_program == "Kitty":
        return "kitty"
    if "kitty" in term.lower():
        return "kitty"

    # WezTerm 也支持 iTerm2 protocol
    if term_program == "WezTerm":
        return "iterm2"

    # VS Code 集成终端支持有限图像 (通过 HTML)
    if term_program == "vscode":
        return "none"  # VS Code terminal 不支持直接图像协议

    # Windows Terminal / ConEmu: 不支持原生图像
    if os.environ.get("WT_SESSION"):
        return "none"

    return "none"


def _iterm2_inline_image(base64_data: str, mime: str, width: int = 0, height: int = 0) -> str:
    """construct iTerm2 内联图像转义serial.

    Protocol: ESC ] 1337 ; File = inline=1 ; size=<bytes> ; width=<px> : <base64> BEL
    width/height=0 表示自动缩放.
    """
    parts = ["inline=1"]
    if width > 0:
        parts.append(f"width={width}")
    if height > 0:
        parts.append(f"height={height}")
    header = ";".join(parts)
    return f"\033]1337;File={header}:{base64_data}\a"


def _kitty_graphics_protocol(base64_data: str, width: int, height: int) -> str:
    """construct Kitty 图形protocol转义serial (分块传输).

    Protocol: ESC _ G <params> ; <base64_data> ESC \\
    单块传输 (m=0) 适用于小图.
    """
    # 单块传输 (m=0): 整个 base64 一次发送
    params = f"f=32,s={len(base64_data)},v={width},{height}a=0,m=0"
    return f"\033_G{params};{base64_data}\033\\"


class ReadImageTool:
    """Read image file tool (ACI design).

    读取图片文件, 返回 base64 编码 + 元数据。
    专为多模态模型设计: 模型可在 system prompt 中看到图片。

    schema 设计:
        path: 必填, 图片文件路径
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "read_image"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_image",
                "description": (
                    "Read an image file and return its base64 encoding and metadata. "
                    "Supports PNG, JPG, GIF, WebP, and BMP formats. "
                    "Returns image dimensions, format, and file size. "
                    "Use this when you need to see screenshots, UI mockups, diagrams, "
                    "or any visual information the user has saved as an image."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Path to the image file (absolute or relative to cwd). "
                                "Supported formats: PNG, JPG, GIF, WebP, BMP"
                            ),
                        },
                    },
                    "required": ["path"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        path_str = args.get("path", "")
        if not path_str:
            return ToolResult(
                success=False,
                output="[ERROR: path argument is required]",
                error="path is required",
            )

        # parsepath
        path = resolve_path(path_str)

        # checkfile存在
        if not path.exists():
            return ToolResult(
                success=False,
                output=f"[ERROR: file not found: {path}]",
                error=f"file not found: {path}",
            )

        # check是file (非directory)
        if not path.is_file():
            return ToolResult(
                success=False,
                output=f"[ERROR: not a file: {path}]",
                error=f"not a file: {path}",
            )

        # checkextension名
        ext = path.suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            supported = ", ".join(SUPPORTED_FORMATS.keys())
            return ToolResult(
                success=False,
                output=(
                    f"[ERROR: unsupported image format '{ext}'. "
                    f"Supported formats: {supported}]"
                ),
                error=f"unsupported format: {ext}",
            )

        # checkfile大小
        try:
            file_size = path.stat().st_size
        except OSError as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot stat {path}: {e}]",
                error=str(e),
            )

        if file_size > MAX_IMAGE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return ToolResult(
                success=False,
                output=(
                    f"[ERROR: image too large ({size_mb:.1f} MB). "
                    f"Maximum size is 10 MB. Consider compressing or resizing the image.]"
                ),
                error="image too large",
                artifacts={"file_size_bytes": file_size},
            )

        # read图片
        try:
            from PIL import Image, ImageOps

            img = Image.open(path)

            # GIF: 只读第一帧 (在 exif_transpose 前check是否动画)
            is_animated = getattr(img, "is_animated", False)

            # v0.0.30: 应用 EXIF Orientation 修正 (v0.0.6 fix: 先save format)
            original_format = img.format
            img = ImageOps.exif_transpose(img) or img  # type: ignore[assignment]
            # exif_transpose 可能return新 Image (format=None), resume原格式
            if img.format is None:
                img.format = original_format

            width, height = img.size
            img_format = img.format or ext.lstrip(".").upper()
            mode = img.mode  # RGB, RGBA, L, etc.

            # read字节: GIF 只preserve第一帧 (转为 PNG), 其余读原file
            if is_animated:
                buf = _io.BytesIO()
                img.save(buf, format="PNG", save_all=False)
                raw_bytes = buf.getvalue()
                mime = "image/png"
            else:
                with open(path, "rb") as f:
                    raw_bytes = f.read()
                mime = SUPPORTED_FORMATS.get(ext, "image/png")

            b64_data = base64.b64encode(raw_bytes).decode("ascii")
            # mime 已在第 177 行正确设为 "image/png" (GIF 转 PNG 后)
            # 非 GIF 在第 181 行已正确setting, 此处不再覆盖 (v0.1.1 fix)

            # buildoutput (文本digest + 终端内联图像)
            size_kb = file_size / 1024
            terminal_cap = _detect_terminal_image_capability()

            # 生成终端渲染行: 只对支持图像的终端发射转义serial
            terminal_line = ""
            if terminal_cap == "iterm2" and len(b64_data) < 500_000:
                terminal_line = _iterm2_inline_image(b64_data, mime, min(width, 120), 0)
            elif terminal_cap == "kitty" and len(b64_data) < 500_000:
                terminal_line = _kitty_graphics_protocol(b64_data, width, height)

            # path链接 (供用户手动打开)
            path_line = f"  Path: file://{path.resolve().as_posix()}"

            output = (
                f"[Image: {path.name}]\n"
                f"  Format: {img_format}\n"
                f"  Dimensions: {width} x {height} px\n"
                f"  Color mode: {mode}\n"
                f"  File size: {size_kb:.1f} KB\n"
                f"  Base64: {len(b64_data)} chars\n"
                f"{path_line}\n"
            )
            if terminal_line:
                # 第一行放转义serial (部分终端在文本前后显示)
                output = terminal_line + "\n" + output

            return ToolResult(
                success=True,
                output=output,
                artifacts={
                    "path": str(path),
                    "format": img_format,
                    "width": width,
                    "height": height,
                    "mode": mode,
                    "file_size_bytes": file_size,
                    "mime_type": mime,
                    "base64": b64_data,
                },
            )

        except ImportError:
            return ToolResult(
                success=False,
                output=(
                    "[ERROR: Pillow (PIL) is not installed. "
                    "Install it with: pip install Pillow]"
                ),
                error="Pillow not installed",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: cannot read image {path}: {e}]",
                error=str(e),
            )