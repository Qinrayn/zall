#!/usr/bin/env python3
"""Build a self-contained single-file `zall` binary via PyInstaller.

B (distribution): gives users a "download-and-run" experience like Rust/TS
agents — no Python / venv / pip required on the target machine.

Usage:
    python scripts/build_binary.py            # lean inline-REPL binary
    python scripts/build_binary.py --tui      # include full-screen TUI (larger)

Output: dist/zall  (or dist/zall.exe on Windows)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "zall.spec"


def main() -> int:
    # 1) ensure PyInstaller is available
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not installed. Run:  pip install pyinstaller", file=sys.stderr)
        return 2

    env = dict(os.environ)
    if "--tui" in sys.argv:
        env["ZALL_BUNDLE_TUI"] = "1"
        print("building WITH full-screen TUI (textual bundled)")
    else:
        print("building lean inline-REPL binary (use --tui to include full-screen)")

    # 2) build
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=str(ROOT), env=env,
    )
    if r.returncode != 0:
        print("PyInstaller build failed", file=sys.stderr)
        return r.returncode

    # 3) smoke-test the produced binary
    exe = ROOT / "dist" / ("zall.exe" if sys.platform == "win32" else "zall")
    if not exe.exists():
        print(f"expected binary not found: {exe}", file=sys.stderr)
        return 1
    print(f"built: {exe}  ({exe.stat().st_size / 1e6:.1f} MB)")
    smoke = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    print("smoke `zall --version`:", (smoke.stdout or smoke.stderr).strip())
    return 0 if smoke.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
