"""zall._util.file — File operations共享toolfunction。"""

from __future__ import annotations

import locale
from pathlib import Path


def _preferred_encoding() -> str:
    """Get the system's preferred encoding (Windows Chinese is GBK/CP936, do not hardcode UTF-8)."""
    try:
        enc = locale.getpreferredencoding(False)
        if enc:
            return enc
    except (ValueError, LookupError):
        pass
    return "utf-8"  # fallback


def detect_text_encoding(path: Path) -> str:
    """Detect text file encoding: try UTF-8 first, fallback to system preferred encoding.

    Most modern code/text files are UTF-8. On Chinese Windows, the system default is GBK,
    but opening a UTF-8 file with GBK produces mojibake. This function tries UTF-8 first
    (strict), and verifies via round-trip to avoid GBK text accidentally passing UTF-8
    validation (e.g., GBK bytes that happen to form valid UTF-8 sequences).

    Note: the returned encoding should be used WITHOUT errors='replace', because
    replacement characters (U+FFFD) cannot be encoded in the target encoding
    (e.g., GBK) when the content is sent to the API pipeline.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        # Strict UTF-8 decode
        decoded = raw.decode("utf-8")
        # Round-trip verification: re-encode must match original bytes exactly
        # This catches GBK text that happens to be valid UTF-8 (different characters)
        if decoded.encode("utf-8") == raw:
            return "utf-8"
    except (UnicodeDecodeError, OSError):
        pass

    # Try system encoding (GBK/CP936 on Chinese Windows)
    sys_enc = _preferred_encoding()
    if sys_enc.lower() != "utf-8":
        try:
            raw.decode(sys_enc)
            return sys_enc
        except (UnicodeDecodeError, LookupError):
            pass

    return "utf-8"  # best-effort fallback


def is_binary(path: Path) -> bool:
    """检测二进制file: 读前 8KB, check空字节。

    失败时返回 True (fail-safe: 二进制文件不读).
    B23: 统一 grep.py 的 _is_binary 和 read_file.py 的内联检测。
    """
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\0" in chunk
    except OSError:
        return True


def read_text_file(path: Path, encoding: str | None = None) -> str:
    """read文本file, 统一exceptionhandle。

    B22: 统一 edit_file.py 和 batch_edit.py 的文件读取逻辑。
    自动解析相对路径, 检查存在性/类型, 返回文件内容。
    抛出 OSError 时调用方自行处理。
    编码默认自动检测: 先尝试 UTF-8, 失败回退系统编码。
    """
    if encoding is None:
        encoding = detect_text_encoding(path)
        path = Path.cwd() / path
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"not a file: {path}")
    return path.read_text(encoding=encoding)


def atomic_write(path: Path, content: str, encoding: str | None = None) -> Path:
    """原子writefile — 使用唯一临时file名, 避免 concurrent write竞态。

    v2 fix: 旧实现 path.with_suffix(suffix + ".zall_tmp") 在两个 agent
    实例同时编辑同一文件时会产生相同临时文件路径, 导致竞态。
    新实现使用 uuid 生成唯一文件名, 保证并发安全。
    编码默认 UTF-8 (现代标准, 与 detect_text_encoding 的优先尝试一致)。

    Returns: 临时文件路径 (已被 os.replace 移走, 不再存在)
    Raises: OSError on write/replace failure (临时文件已清理)
    """
    import os
    import uuid
    if encoding is None:
        encoding = "utf-8"
    tmp_name = f".zall_tmp_{uuid.uuid4().hex[:8]}"
    tmp = path.parent / tmp_name
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise
    return tmp