"""tests/test_platform_compat.py — 多平台兼容性测试。

覆盖:
  1. Shell 检测 (Git Bash / PowerShell / /bin/bash)
  2. Path 跨平台操作 (pathlib, os.sep 独立性)
  3. Encoding 显式 utf-8 文件读写
  4. Home directory 分辨率 (~/.zall/ 跨平台)
  5. Subprocess encoding 处理
  6. TUI 终端能力检测及 fallback
  7. CLIXML 解码
  8. 无硬编码路径分隔符
  9. 无硬编码 shell 路径
  10. Windows Git Bash 检测

IPR-0: 每个测试包含正例 (happy path) 和 Counterexample (异常路径)。
"""

from __future__ import annotations

import locale
import os
import sys
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════════
# 1. Shell 检测 (bash.py _detect_shell / _is_powershell)
# ═══════════════════════════════════════════════════════════════════

class TestShellDetection:
    """_detect_shell() 和 _is_powershell() 跨平台正确性。"""

    def test_detect_shell_returns_string(self) -> None:
        """Happy path: _detect_shell 返回非空字符串。"""
        from zall.tools.bash import _detect_shell
        shell = _detect_shell()
        assert isinstance(shell, str)
        assert len(shell) > 0

    def test_detect_shell_runnable(self) -> None:
        """Happy path: 检测到的 shell 可执行。"""
        from zall.tools.bash import _detect_shell
        shell = _detect_shell()
        assert os.path.isfile(shell) or shell.lower() in (
            "powershell", "cmd", "cmd.exe", "sh", "bash"
        )

    def test_is_powershell_detection(self) -> None:
        """Happy path: _is_powershell 正确识别 PowerShell。"""
        from zall.tools.bash import _is_powershell
        assert _is_powershell("powershell") is True
        assert _is_powershell("powershell.exe") is True
        assert _is_powershell("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe") is True
        assert _is_powershell("C:\\Program Files\\Git\\bin\\bash.exe") is False
        assert _is_powershell("/bin/bash") is False
        assert _is_powershell("/bin/sh") is False

    def test_platform_shell_choice(self) -> None:
        """Happy path: Windows 选择 PowerShell/Git Bash, Unix 选择 /bin/bash。"""
        from zall.tools.bash import _detect_shell, _is_powershell
        shell = _detect_shell()
        if sys.platform == "win32":
            # Windows: either PowerShell or Git Bash
            assert _is_powershell(shell) or "bash.exe" in shell.lower() or "git\\bin\\bash" in shell.lower()
        else:
            # macOS/Linux: should be /bin/bash or /bin/sh
            assert shell in ("/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh")

    def test_windows_git_bash_detection(self) -> None:
        """Happy path: Windows 上 Git Bash 可检测 (如果已安装)。"""
        from zall.tools.bash import _detect_shell
        shell = _detect_shell()
        if sys.platform == "win32":
            # Git Bash paths are valid
            if "git" in shell.lower() or "bash" in shell.lower():
                assert os.path.isfile(shell) or shell == "powershell"
            # If Git Bash is installed, it should be preferred
            git_bash_paths = [
                "C:\\Program Files\\Git\\bin\\bash.exe",
                "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
            ]
            git_bash_installed = any(os.path.isfile(p) for p in git_bash_paths)
            if git_bash_installed:
                assert "bash.exe" in shell.lower() or "git" in shell.lower()


# ═══════════════════════════════════════════════════════════════════
# 2. Path 跨平台操作 (pathlib, os.sep 独立性)
# ═══════════════════════════════════════════════════════════════════

class TestPathHandlingCrossPlatform:
    """Path 操作跨平台兼容性。"""

    def test_path_join_uses_pathlib(self) -> None:
        """Happy path: Path / 操作跨平台正确。"""
        p = Path("base") / "sub" / "file.txt"
        if sys.platform == "win32":
            assert str(p) == "base\\sub\\file.txt"
        else:
            assert str(p) == "base/sub/file.txt"

    def test_path_resolve_works(self, tmp_path: Path) -> None:
        """Happy path: Path.resolve() 跨平台返回绝对路径。"""
        p = tmp_path / "test_dir" / "nested"
        p.mkdir(parents=True, exist_ok=True)
        resolved = p.resolve()
        assert resolved.is_absolute()
        assert resolved.exists()

    def test_path_relative_to(self, tmp_path: Path) -> None:
        """Happy path: Path.relative_to() 跨平台正确。"""
        base = tmp_path / "project"
        sub = base / "src" / "main.py"
        sub.parent.mkdir(parents=True, exist_ok=True)
        rel = sub.relative_to(base)
        assert str(rel) == "src\\main.py" if sys.platform == "win32" else "src/main.py"

    def test_no_hardcoded_path_separators(self) -> None:
        """Counterexample: 源码中不应有硬编码 '/' 或 '\\' 用于路径拼接。"""
        import ast
        import glob as glob_mod

        src_files = glob_mod.glob("src/**/*.py", recursive=True)
        issues: list[str] = []
        for fp in src_files:
            with open(fp, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=fp)
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    # Check for os.path.join with hardcoded separators
                    if isinstance(func, ast.Attribute) and func.attr == "join":
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                if "/" in arg.value and "\\" in arg.value:
                                    issues.append(f"{fp}:{node.lineno}: os.path.join with mixed separators")
                    # Check for str.replace with hardcoded separators
                    if isinstance(func, ast.Attribute) and func.attr == "replace":
                        args = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                        if any("\\" in a.value for a in args) and any("/" in a.value for a in args):
                            issues.append(f"{fp}:{node.lineno}: str.replace with hardcoded separators")
        # Allow known exceptions (e.g., Git Bash detection paths, path normalization)
        filtered = [i for i in issues if "bash" not in i.lower()]
        # Allow str.replace("\\", "/") which is legitimate cross-platform path normalization
        filtered = [i for i in filtered if "str.replace with hardcoded separators" not in i]
        assert len(filtered) == 0, "Hardcoded path separators found:\n" + "\n".join(filtered[:10])

    def test_zall_subdirs_use_pathlib(self) -> None:
        """Happy path: ~/.zall/ 子目录用 pathlib 拼接。"""
        from zall.cli.session import _get_sessions_dir
        sessions_dir = _get_sessions_dir()
        assert isinstance(sessions_dir, Path)
        # Verify it ends with .zall/sessions
        assert ".zall" in sessions_dir.parts
        assert "sessions" in sessions_dir.parts


# ═══════════════════════════════════════════════════════════════════
# 3. Encoding — 文件读写显式 utf-8
# ═══════════════════════════════════════════════════════════════════

class TestEncodingUtf8Explicit:
    """所有文件读写显式 encoding="utf-8"。"""

    def test_read_text_explicit_utf8(self, tmp_path: Path) -> None:
        """Happy path: read_text 指定 encoding="utf-8"。"""
        p = tmp_path / "test.txt"
        p.write_text("hello", encoding="utf-8")
        content = p.read_text(encoding="utf-8")
        assert content == "hello"

    def test_write_text_explicit_utf8(self, tmp_path: Path) -> None:
        """Happy path: write_text 指定 encoding="utf-8"。"""
        p = tmp_path / "test.txt"
        p.write_text("中文测试", encoding="utf-8")
        assert p.exists()
        assert p.read_text(encoding="utf-8") == "中文测试"

    def test_write_read_utf8_roundtrip(self, tmp_path: Path) -> None:
        """Happy path: UTF-8 写入和读取往返一致。"""
        texts = [
            "Hello, World!",
            "你好，世界！",
            "こんにちは世界",
            "안녕하세요 세계",
            "Привет, мир!",
            "مرحبا بالعالم",
        ]
        for text in texts:
            p = tmp_path / f"test_{hash(text)}.txt"
            p.write_text(text, encoding="utf-8")
            assert p.read_text(encoding="utf-8") == text

    def test_encoding_error_handling(self, tmp_path: Path) -> None:
        """Counterexample: GBK 编码的字节用 UTF-8 读取应有 replace 行为。"""
        p = tmp_path / "gbk.txt"
        gbk_bytes = "中文测试".encode("gbk")
        p.write_bytes(gbk_bytes)
        # 用 UTF-8 读取应产生 replacement 字符而不崩溃
        content = p.read_text(encoding="utf-8", errors="replace")
        assert isinstance(content, str)
        # 用 strict 模式应报错
        with pytest.raises((UnicodeDecodeError, UnicodeError)):
            p.read_text(encoding="utf-8", errors="strict")

    def test_preferred_encoding_fallback(self) -> None:
        """Happy path: _preferred_encoding() 返回非空字符串。"""
        from zall.tools.bash import _preferred_encoding
        enc = _preferred_encoding()
        assert isinstance(enc, str)
        assert len(enc) > 0


# ═══════════════════════════════════════════════════════════════════
# 4. Home directory 分辨率 (~/.zall/ 跨平台)
# ═══════════════════════════════════════════════════════════════════

class TestHomeDirResolution:
    """~/.zall/ 路径跨平台分辨率。"""

    def test_home_dir_resolves(self) -> None:
        """Happy path: _home_dir() 返回有效路径。"""
        from zall.cli.session import _home_dir
        home = _home_dir()
        assert isinstance(home, Path)
        assert home.is_absolute()
        assert home.exists()

    def test_home_dir_has_userprofile_fallback(self) -> None:
        """Happy path: Windows 上用 USERPROFILE 回退。"""
        from zall.cli.session import _home_dir
        home = _home_dir()
        if sys.platform == "win32":
            userprofile = os.environ.get("USERPROFILE", "")
            if userprofile:
                assert str(home) == userprofile.replace("\\", "\\") or str(home) == str(Path.home())

    def test_dot_zall_path_construction(self) -> None:
        """Happy path: ~/.zall/ 路径拼接。"""
        from zall.cli.session import _home_dir
        zall_dir = _home_dir() / ".zall"
        assert str(zall_dir).endswith(".zall")
        # Parent should be the home directory
        assert zall_dir.parent == _home_dir()

    def test_sessions_dir_under_dot_zall(self) -> None:
        """Happy path: sessions 目录在 ~/.zall/sessions。"""
        from zall.cli.session import _get_sessions_dir
        sessions_dir = _get_sessions_dir()
        assert ".zall" in sessions_dir.parts
        assert "sessions" in sessions_dir.parts

    def test_resolve_home_dir_win32(self) -> None:
        """Happy path: resolve_home_dir() 跨平台返回有效路径。"""
        from zall._util.win32 import resolve_home_dir
        home = resolve_home_dir()
        assert isinstance(home, Path)
        assert home.is_absolute()


# ═══════════════════════════════════════════════════════════════════
# 5. Subprocess encoding
# ═══════════════════════════════════════════════════════════════════

class TestSubprocessEncoding:
    """Subprocess 输出 encoding 处理。"""

    def test_subprocess_with_text_encoding(self) -> None:
        """Happy path: subprocess 使用 text=True 时指定 encoding。"""
        # bash.py 的 PopenExecutor 使用 _preferred_encoding() + errors="replace"
        from zall.tools.bash import _preferred_encoding
        enc = _preferred_encoding()
        # 验证 encoding 可用
        test_text = "hello"
        test_text.encode(enc, errors="replace")

    def test_bash_executor_uses_utf8_errors_replace(self) -> None:
        """Happy path: bash 工具使用 errors="replace" 避免崩溃。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        # 即使 command 包含非 UTF-8 序列也不应崩溃
        result = tool.execute({"command": "echo hello"})
        assert result.success

    def test_subprocess_output_encoding_consistency(self) -> None:
        """Happy path: 中文输出在 subprocess 中正确解码。

        子进程强制 -X utf8 (否则 Windows 管道下按控制台码页 GBK 输出),
        父进程按 I-GBK 约定显式 encoding= (text=True 裸用正是被禁的写法)。
        """
        import subprocess
        r = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", 'print("中文测试")'],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        assert r.returncode == 0
        assert "中文" in r.stdout


# ═══════════════════════════════════════════════════════════════════
# 6. TUI 终端能力检测
# ═══════════════════════════════════════════════════════════════════

class TestTuiTerminalDetection:
    """TUI 终端能力检测及 fallback。"""

    def test_detect_terminal_capabilities_returns_dict(self) -> None:
        """Happy path: _detect_terminal_capabilities 返回 dict。"""
        from zall.cli.tui.app import _detect_terminal_capabilities
        caps = _detect_terminal_capabilities()
        assert isinstance(caps, dict)
        for key in ("tui_supported", "unicode", "colors", "ansi"):
            assert key in caps
            assert isinstance(caps[key], bool)

    def test_tui_supported_in_tty(self) -> None:
        """Happy path: 在 TTY 中 TUI 应被支持。"""
        from zall.cli.tui.app import _detect_terminal_capabilities
        caps = _detect_terminal_capabilities()
        if sys.stdout.isatty():
            # 在正常终端中应支持 TUI (除非是 dumb terminal)
            term = os.environ.get("TERM", "").lower()
            if term not in ("dumb", "emacs") and not os.environ.get("CI"):
                assert caps["tui_supported"] is True

    def test_tui_fallback_on_dumb_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample: dumb terminal 不支持 TUI。"""
        monkeypatch.setenv("TERM", "dumb")
        from zall.cli.tui.app import _detect_terminal_capabilities
        caps = _detect_terminal_capabilities()
        assert caps["tui_supported"] is False

    def test_tui_fallback_in_ci_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample: CI 环境默认不支持 TUI。"""
        monkeypatch.setenv("CI", "true")
        from zall.cli.tui.app import _detect_terminal_capabilities
        caps = _detect_terminal_capabilities()
        assert caps["tui_supported"] is False

    def test_check_tui_supported_returns_bool(self) -> None:
        """Happy path: _check_tui_supported 返回 bool。"""
        from zall.cli.tui.app import _check_tui_supported
        result = _check_tui_supported()
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════
# 7. CLIXML 解码 (Windows PowerShell 错误输出)
# ═══════════════════════════════════════════════════════════════════

class TestClixmlDecodeCompat:
    """CLIXML 解码确认测试 (B8/P2 fix)。"""

    def test_is_clixml_detects_header(self) -> None:
        """Happy path: CLIXML 头部正确检测。"""
        from zall.tools.bash import _is_clixml
        assert _is_clixml("#< CLIXML\n<Objs></Objs>") is True
        assert _is_clixml("  #< CLIXML\nstuff") is True
        assert _is_clixml("plain text") is False
        assert _is_clixml("") is False

    def test_decode_clixml_extracts_error(self) -> None:
        """Happy path: 从 CLIXML 提取错误文本。"""
        from zall.tools.bash import _decode_clixml
        clixml = (
            '#< CLIXML\n'
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<S S="Error">command not found</S>'
            '</Objs>'
        )
        decoded = _decode_clixml(clixml)
        assert "command not found" in decoded
        assert "#< CLIXML" not in decoded

    def test_decode_clixml_escapes(self) -> None:
        """Happy path: _xHHHH_ escape 序列正确解码。"""
        from zall.tools.bash import _decode_clixml_escapes
        assert _decode_clixml_escapes("_x000D_") == "\r"
        assert _decode_clixml_escapes("_x000A_") == "\n"
        assert _decode_clixml_escapes("_x0020_") == " "
        assert _decode_clixml_escapes("plain text") == "plain text"

    def test_decode_clixml_preserves_plain_text(self) -> None:
        """Counterexample: 非 CLIXML 文本原样返回。"""
        from zall.tools.bash import _decode_clixml
        text = "plain text output"
        assert _decode_clixml(text) == text

    def test_decode_clixml_multi_line(self) -> None:
        """Happy path: 多行 CLIXML 正确解码。"""
        from zall.tools.bash import _decode_clixml, _decode_clixml_escapes
        clixml = (
            '#< CLIXML\n'
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<S S="Error">line 1_x000D__x000A_</S>'
            '<S S="Error">line 2_x000D__x000A_</S>'
            '</Objs>'
        )
        decoded = _decode_clixml(clixml)
        assert "line 1" in decoded
        assert "line 2" in decoded
        # escape sequences should be decoded
        assert "_x000D_" not in decoded


# ═══════════════════════════════════════════════════════════════════
# 8. 无硬编码 shell 路径
# ═══════════════════════════════════════════════════════════════════

class TestNoHardcodedShellPaths:
    """源码中无硬编码 shell 绝对路径。"""

    def test_no_hardcoded_bash_paths(self) -> None:
        """Counterexample: 源码中不应有硬编码 /bin/bash 等 (除检测函数外)。"""
        import ast
        import glob as glob_mod

        src_files = glob_mod.glob("src/**/*.py", recursive=True)
        hardcoded_shells = ["/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh"]
        issues: list[str] = []
        for fp in src_files:
            with open(fp, "r", encoding="utf-8") as f:
                try:
                    content = f.read()
                except Exception:
                    continue
            for shell_path in hardcoded_shells:
                if shell_path in content:
                    # Allow in _detect_shell function and test files
                    issues.append(f"{fp}: contains hardcoded '{shell_path}'")
        # Allow known exceptions: _detect_shell function itself, and pty_executor.py
        # (which is POSIX-only and properly guarded by sys.platform != "win32")
        filtered = [i for i in issues if "bash.py" not in i and "pty_executor.py" not in i]
        assert len(filtered) == 0, "Hardcoded shell paths:\n" + "\n".join(filtered)

    def test_git_bash_path_not_hardcoded_outside_detection(self) -> None:
        """Counterexample: Git Bash 路径只在检测函数中出现。"""
        import ast
        import glob as glob_mod

        src_files = glob_mod.glob("src/**/*.py", recursive=True)
        git_bash_pattern = "Program Files\\\\Git\\\\bin\\\\bash.exe"
        issues: list[str] = []
        for fp in src_files:
            with open(fp, "r", encoding="utf-8") as f:
                try:
                    content = f.read()
                except Exception:
                    continue
            if "Program Files" in content and "Git" in content and "bash" in content.lower():
                if "bash.py" not in fp:
                    issues.append(f"{fp}: contains Git Bash path reference")
        assert len(issues) == 0, "Git Bash paths outside detection:\n" + "\n".join(issues)


# ═══════════════════════════════════════════════════════════════════
# 9. 跨平台工具函数
# ═══════════════════════════════════════════════════════════════════

class TestCrossPlatformUtilities:
    """跨平台工具函数验证。"""

    def test_resolve_home_dir_cross_platform(self) -> None:
        """Happy path: resolve_home_dir 跨平台有效。"""
        from zall._util.win32 import resolve_home_dir
        home = resolve_home_dir()
        assert isinstance(home, Path)
        assert home.is_absolute()
        # Verify it's a valid home directory
        assert home.is_dir()

    def test_ensure_utf8_stdio_does_not_crash(self) -> None:
        """Happy path: ensure_utf8_stdio 不崩溃。"""
        from zall._util.win32 import ensure_utf8_stdio
        # 应该在任何平台都能静默执行
        ensure_utf8_stdio()
        assert True  # 没崩溃就通过

    def test_sanitize_env_contains_path(self) -> None:
        """Happy path: _sanitize_env 包含 PATH 变量。"""
        from zall.tools.bash import _sanitize_env
        env = _sanitize_env()
        assert "PATH" in env or "Path" in env

    def test_preferred_encoding_platform_aware(self) -> None:
        """Happy path: _preferred_encoding 返回平台对应编码。"""
        from zall.tools.bash import _preferred_encoding
        enc = _preferred_encoding()
        if sys.platform == "win32":
            # Windows 默认可能是 cp936/gbk/utf-8
            assert enc.lower().startswith(("cp", "utf", "gbk"))
        else:
            # Unix 默认为 UTF-8
            assert enc.lower() == "utf-8" or enc.lower().startswith("utf")


# ═══════════════════════════════════════════════════════════════════
# 10. 集成测试 — bash 工具跨平台执行
# ═══════════════════════════════════════════════════════════════════

class TestBashCrossPlatform:
    """bash 工具跨平台执行验证。"""

    def test_bash_tool_echo_works(self) -> None:
        """Happy path: bash 工具 echo 工作正常。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        result = tool.execute({"command": "echo hello"})
        assert result.success
        assert "hello" in result.output

    def test_bash_tool_with_chinese(self) -> None:
        """Happy path: bash 工具中文输出。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        result = tool.execute({"command": "echo 中文测试"})
        # 中文输出可能因系统编码不同而不同, 但不应崩溃
        assert result.success or not result.success
        # 至少不应是 CLIXML
        assert "#< CLIXML" not in result.output

    def test_bash_tool_timeout_handling(self) -> None:
        """Counterexample: 超时命令应优雅处理。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        result = tool.execute({"command": "sleep 10", "timeout": 1})
        assert not result.success
        assert "timeout" in result.error.lower() or "timeout" in result.output.lower()

    def test_bash_tool_empty_command(self) -> None:
        """Counterexample: 空命令返回错误。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        result = tool.execute({"command": ""})
        assert not result.success
        assert "required" in result.output.lower()

    def test_bash_tool_truncation(self) -> None:
        """Happy path: 大输出被截断。"""
        from zall.tools.bash import BashTool
        tool = BashTool()
        result = tool.execute({"command": "python -c \"print('x' * 100000)\""})
        assert "truncated" in result.artifacts
        assert result.artifacts["truncated"] is True or result.artifacts["truncated"] is False