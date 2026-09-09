#!/usr/bin/env python3
"""mypy 错误数 ratchet (基线只降不升).

ci 曾用 `mypy ... || true` — 类型检查形同虚设, 错误数可任意漂移。
本脚本用"基线 + 只降不升"把 mypy 变成有方向的约束:

      CI 无参运行:   当前错误数 > 基线 → 退出码 1 (CI 红)
      本地治理:      python scripts/mypy_ratchet.py --update
                     把当前错误数写入基线 (只允许减少或持平)

用法:
    python scripts/mypy_ratchet.py            # 检查 (CI)
    python scripts/mypy_ratchet.py --update   # 刷新基线 (本地修复后)
    python scripts/mypy_ratchet.py --print    # 打印当前错误数

IPR-3: stdlib only。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_BASELINE_PATH = Path(__file__).parent / "mypy_baseline.txt"
_MYPY_ARGS = ["src/zall/", "--ignore-missing-imports"]


def _count_errors() -> int:
    """在项目根运行 mypy, 解析错误总数; 失败返回 -1。"""
    result = subprocess.run(
        ["python", "-m", "mypy", *_MYPY_ARGS],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    lines = [l for l in output.splitlines() if l.strip()]
    if not lines:
        print("mypy produced no output (install missing?)")
        return -1
    tail = lines[-1]
    if "no issues found" in tail:
        return 0
    marker = "Found "
    if marker not in tail or " error" not in tail:
        print(f"unparsable mypy output: {tail!r}")
        return -1
    return int(tail.split(marker, 1)[1].split(" ")[0])


def _read_baseline() -> str:
    """读取基线文件, 跳过 # 注释行 (允许记录 mypy 版本)。"""
    for line in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _write_baseline(count: int) -> None:
    """写基线, 保留已有 # 注释行 (mypy 版本记录)。"""
    header = ""
    try:
        for line in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                header += line + "\n"
    except OSError:
        pass
    _BASELINE_PATH.write_text(f"{header}{count}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="write current error count as new baseline")
    parser.add_argument("--print", action="store_true",
                        help="print current error count only")
    args = parser.parse_args()

    current = _count_errors()
    if current < 0:
        return 1
    if args.print:
        print(current)
        return 0

    if not _BASELINE_PATH.exists():
        if not args.update:
            print(f"no baseline at {_BASELINE_PATH}; run with --update to create one")
            return 1
        _write_baseline(current)
        print(f"baseline created: {current} errors")
        return 0

    baseline = int(_read_baseline())
    if args.update:
        if current > baseline:
            print(f"refusing to raise baseline: current {current} > baseline {baseline}")
            return 1
        if current < baseline:
            _write_baseline(current)
            print(f"baseline lowered: {baseline} -> {current}")
        else:
            print(f"baseline unchanged: {current}")
        return 0

    print(f"mypy errors: {current} (baseline {baseline})")
    return 0 if current <= baseline else 1


if __name__ == "__main__":
    sys.exit(main())