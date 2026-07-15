"""Auto-update mechanism for seamless version upgrades.

Design:
  1. Check for updates: background thread checks PyPI for new versions on REPL start
  2. Non-blocking notification: shows a one-line hint in REPL banner when an update is available
  3. Manual trigger: /update command explicitly upgrades
  4. Rollback: pip version management supports rollback (pip install zall==<old_version>)

IPR constraints:
  IPR-0: Update check failure must not block startup (silent degradation)
  IPR-3: stdlib + subprocess only, no model SDK
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any


# PyPI JSON API URL (无需认证)
_PYPI_URL = "https://pypi.org/pypi/zall/json"

# checkinterval: 每 24 小时最多check一次
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# cachefilepath
def _cache_path() -> str:
    """获取升级checkcachefilepath。"""
    home = os.path.expanduser("~")
    if os.name == "nt":
        up = os.environ.get("USERPROFILE", "")
        if up and os.path.isdir(up):
            home = up
    return os.path.join(home, ".zall", "update_cache.json")


def get_current_version() -> str:
    """获取当前安装的 zall version。"""
    try:
        from zall import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _get_installed_version_pip() -> str:
    """通过 pip show 获取已安装version (备用)。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "zall"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "0.0.0"


def _fetch_latest_version() -> str | None:
    """从 PyPI 获取最新version号。

    使用 urllib (stdlib), 不引入 requests/httpx 依赖。
    失败时返回 None (静默降级)。
    """
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(_PYPI_URL, headers={"User-Agent": "zall/update-check"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            info = data.get("info")
            if info is None:
                return None
            version: str | None = info.get("version")
            return version
    except Exception:
        return None


def _load_cache() -> dict[str, Any]:
    """load升级checkcache。"""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    """save升级checkcache。"""
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _compare_versions(v1: str, v2: str) -> int:
    """比较两个语义化version号。return >0 if v1>v2, 0 if eq, <0 if v1<v2。"""
    def _parse(v: str) -> tuple[int, ...]:
        parts = v.strip().split(".")
        result = []
        for p in parts:
            # 提取数字部分 (ignore alpha/beta 等后缀)
            num = ""
            for ch in p:
                if ch.isdigit():
                    num += ch
                else:
                    break
            result.append(int(num) if num else 0)
        return tuple(result)
    p1 = _parse(v1)
    p2 = _parse(v2)
    # 补齐长度
    max_len = max(len(p1), len(p2))
    p1 = p1 + (0,) * (max_len - len(p1))
    p2 = p2 + (0,) * (max_len - len(p2))
    if p1 > p2:
        return 1
    elif p1 < p2:
        return -1
    return 0


def check_for_update(*, force: bool = False) -> dict[str, Any]:
    """check是否有新version可用。

    Args:
        force: True 时忽略缓存, 强制检查

    Returns:
        {
            "has_update": bool,
            "current": str,
            "latest": str | None,
            "checked_at": float,
        }
    """
    current = get_current_version()
    if current == "0.0.0":
        # 开发pattern (未通过 pip 安装), skipcheck
        return {"has_update": False, "current": current, "latest": None, "checked_at": 0}

    cache = _load_cache()
    now = time.time()

    # 非强制时, 24 小时内不重复check
    if not force:
        last_check = cache.get("checked_at", 0)
        if now - last_check < _CHECK_INTERVAL_SECONDS:
            # 用cache的结果
            return {
                "has_update": cache.get("has_update", False),
                "current": current,
                "latest": cache.get("latest"),
                "checked_at": last_check,
            }

    # check新version
    latest = _fetch_latest_version()
    has_update = False
    if latest and _compare_versions(latest, current) > 0:
        has_update = True

    result = {
        "has_update": has_update,
        "current": current,
        "latest": latest,
        "checked_at": now,
    }

    # savecache
    cache.update(result)
    _save_cache(cache)

    return result


def perform_update(out: Any = None) -> bool:
    """execute升级 (pip install --upgrade zall).

    Returns: True 表示升级成功
    """
    stream = out or sys.stderr
    stream.write("  upgrading zall...\n")
    stream.flush()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "zall"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            new_version = get_current_version_pip()
            stream.write(f"  upgraded to {new_version}\n")
            stream.flush()
            # 清除checkcache (下次启动重新check)
            _save_cache({})
            return True
        else:
            stream.write(f"  upgrade failed: {result.stderr[:200]}\n")
            stream.flush()
            return False
    except Exception as e:
        stream.write(f"  upgrade error: {e}\n")
        stream.flush()
        return False


def get_current_version_pip() -> str:
    """获取升级后的version (重新导入)。"""
    # pip install --upgrade 后, 当前process中的 __version__ 不会更新
    # 需要通过 pip show 获取
    return _get_installed_version_pip()


# 后台check (非blocking)
_check_thread: threading.Thread | None = None
_check_result: dict[str, Any] | None = None


def _bg_check() -> None:
    """后台check更新 (非blocking, 结果存全局variable)。"""
    global _check_result
    try:
        _check_result = check_for_update()
    except Exception:
        _check_result = {"has_update": False, "current": "0.0.0", "latest": None, "checked_at": 0}


def start_background_check() -> None:
    """启动后台更新checkthread (REPL 启动时调用, 非blocking)。"""
    global _check_thread
    if _check_thread is not None and _check_thread.is_alive():
        return  # 已在运行
    _check_thread = threading.Thread(target=_bg_check, daemon=True)
    _check_thread.start()


def get_update_hint() -> str | None:
    """获取更新prompt文本 (供 REPL banner 用)。

    如果有新版本可用, 返回提示字符串; 否则返回 None。
    """
    global _check_result
    if _check_result is None:
        return None
    if _check_result.get("has_update"):
        current = _check_result.get("current", "?")
        latest = _check_result.get("latest", "?")
        return f"update available: {current} -> {latest} (run /update)"
    return None
