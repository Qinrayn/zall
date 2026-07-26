# zall.spec — PyInstaller single-binary build (B: distribution "好装")
#
# 目的: 产出一个自包含的单文件可执行 (无需用户装 Python / venv / pip)，
# 对齐 Rust/TS agent 的"下载即用"分发体验。默认精简版 = 内联 REPL (不含 textual)。
#
# 构建:
#   pip install pyinstaller
#   pyinstaller zall.spec            # 精简单文件 → dist/zall(.exe)
#   带全屏 TUI:  ZALL_BUNDLE_TUI=1 pyinstaller zall.spec
#
# 注意: zall 通过 importlib 动态加载 adapters/tools/commands, 故用
# collect_submodules('zall') 全量收集, 避免运行时 ModuleNotFoundError。
import os
from PyInstaller.utils.hooks import collect_submodules

# 动态加载的 zall 子模块 (adapters/tools/commands/extensions/mcp/plugin 等) 全量收集
hiddenimports = collect_submodules("zall")

# 第三方依赖中可能被懒加载/条件导入的, 显式声明
hiddenimports += [
    "pydantic", "pydantic_core",
    "cryptography", "httpx", "rich", "prompt_toolkit", "yaml",
]
if os.environ.get("ZALL_BUNDLE_TUI"):
    # 可选: 打包全屏 TUI (--tui)。默认不含以保持体积精简 (默认走内联 REPL)。
    hiddenimports += collect_submodules("textual")

# 未安装的可选依赖不打包 (缺失即排除, 避免构建失败)
excludes = []
if not os.environ.get("ZALL_BUNDLE_TUI"):
    excludes.append("textual")

a = Analysis(
    ["src/zall/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="zall",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # CLI 工具: 保留控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
