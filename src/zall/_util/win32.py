"""zall._util.win32 — Windows 专用toolfunction（encoding、控制台、path）。

统一管理 Windows 平台的特殊处理，避免代码散落在 cli/app.py 和 safety/config.py 中。

Functions:
  ensure_utf8_stdio()   — 重配置 stdout/stderr 为 UTF-8, 防 GBK 控制台符号乱码
  set_console_title()   — 设置 Windows 控制台标题
  resolve_home_dir()    — 处理中文用户名时 Path.home() 编码问题
  restrict_file_acls()  — 设置文件 ACL 仅当前用户可访问
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def ensure_utf8_stdio() -> None:
    """重config stdout/stderr 为 UTF-8 (Windows GBK 控制台防符号乱码)。

    v0.3.0 (A6): 仅在当前 codepage 非 UTF-8 时才 spawn chcp, 避免每次启动都付
    ~50-100ms 子进程税 (现代 Windows Terminal / VS Code 已原生 UTF-8)。
    v0.5.0: 设置 Windows 控制台标题为 "zall"。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if sys.platform == "win32":
        enc = (getattr(sys.stdout, "encoding", "") or "").lower()
        if "utf-8" in enc or "utf8" in enc:
            pass  # 已是 UTF-8
        else:
            try:
                # 使用 binary pattern避免 cp936 下 chcp output的 UnicodeDecodeError。
                subprocess.run(["chcp", "65001"], capture_output=True, timeout=3,
                               shell=True)
            except Exception:
                pass


def set_console_title(title: str = "✦ zall") -> None:
    """setting Windows 控制台title (带 emoji 前缀增加辨识度)。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


def resolve_home_dir() -> Path:
    """获取用户 home directory，handle Windows 中文用户名encoding问题。

    Windows 中文用户名时 Path.home() 可能因编码问题返回错误路径,
    增加 USERPROFILE 环境变量回退。
    """
    _home = Path.home()
    if sys.platform == "win32":
        _userprofile = os.environ.get("USERPROFILE", "")
        if _userprofile and _userprofile != str(_home):
            try:
                _alt_home = Path(_userprofile)
                if _alt_home.is_dir():
                    _home = _alt_home
            except Exception:
                pass
    return _home


def restrict_file_acls(path: Path) -> None:
    """settingfile ACL 仅当前用户可访问 (Windows 专用)。

    使用 icacls 限制文件权限，失败静默（至少尝试了）。
    """
    if sys.platform != "win32":
        return
    try:
        username = os.environ.get("USERNAME", "")
        if username:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r",
                 f"{username}:F"],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass  # ACL 设置失败不阻断