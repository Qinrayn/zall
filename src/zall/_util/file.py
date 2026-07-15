"""zall._util.file — File operations共享toolfunction。"""

from __future__ import annotations

from pathlib import Path
from typing import IO


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


def read_text_file(path: Path, encoding: str = "utf-8") -> str:
    """read文本file, 统一exceptionhandle。

    B22: 统一 edit_file.py 和 batch_edit.py 的文件读取逻辑。
    自动解析相对路径, 检查存在性/类型, 返回文件内容。
    抛出 OSError 时调用方自行处理。
    """
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"not a file: {path}")
    return path.read_text(encoding=encoding)


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> Path:
    """原子writefile — 使用唯一临时file名, 避免 concurrent write竞态。

    v2 fix: 旧实现 path.with_suffix(suffix + ".zall_tmp") 在两个 agent
    实例同时编辑同一文件时会产生相同临时文件路径, 导致竞态。
    新实现使用 uuid 生成唯一文件名, 保证并发安全。

    Returns: 临时文件路径 (已被 os.replace 移走, 不再存在)
    Raises: OSError on write/replace failure (临时文件已清理)
    """
    import os
    import uuid
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