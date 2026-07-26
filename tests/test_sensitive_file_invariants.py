"""敏感文件防护不变量 (I-SENSITIVE, kimi utils/sensitive.py 对标).

风险: read_file / @ 引用把 .env / SSH 私钥 / 云凭证读进模型上下文,
密钥随链式哈希 timeline 持久化 — 一次误读即永久泄漏。

不变量 (each with counterexample):
  A  高置信度敏感模式命中 (.env / id_rsa / credentials / trust_anchor_key)。
  B  豁免与普通文件绝不误拦 (.env.example / main.py / id_rsa.pub) — 反例孪生。
  C  read_file 拒读敏感文件 (输出含 BLOCKED, 不含文件内容)。
  D  @ 引用跳过敏感文件 (注入占位说明, 不注入内容)。
  E  Windows 反斜杠路径同样命中 (跨平台)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.safety.sensitive import is_sensitive_file, sensitive_refusal


# ── A: 敏感模式命中 ──


def test_sensitive_patterns_hit() -> None:
    for p in (
        ".env",
        ".env.production",
        "project/.env",
        "id_rsa",
        "id_ed25519",
        ".ssh/id_ecdsa",
        ".aws/credentials",
        "home/user/.aws/credentials",
        ".netrc",
        "_netrc",
        ".zall/trust_anchor_key",  # zall 审计链签名私钥
        "credentials",
    ):
        assert is_sensitive_file(p), p


def test_case_insensitive() -> None:
    assert is_sensitive_file(".ENV")
    assert is_sensitive_file("ID_RSA")


# ── B: 豁免与普通文件不误拦 (反例孪生) ──


def test_exemptions_and_normal_files_pass() -> None:
    for p in (
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.dist",
        "main.py",
        "environment.py",       # 含 env 子串但不是 .env
        "id_rsa.pub",           # 公钥不敏感
        "my_credentials_doc.md",  # 非精确名
        "config.toml",
        "",
    ):
        assert not is_sensitive_file(p), p


# ── C: read_file 拒读 ──


def test_read_file_blocks_sensitive(tmp_path: Path) -> None:
    from zall.tools.read_file import ReadFileTool
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=sk-super-secret\n", encoding="utf-8")
    result = ReadFileTool().execute({"path": str(secret)})
    assert not result.success
    assert "BLOCKED" in result.output
    assert "sk-super-secret" not in result.output  # 内容绝不外泄


def test_read_file_allows_exempt_template(tmp_path: Path) -> None:
    """反例孪生: .env.example 必须正常可读 (误拦即破坏开箱体验)。"""
    from zall.tools.read_file import ReadFileTool
    tpl = tmp_path / ".env.example"
    tpl.write_text("API_KEY=your-key-here\n", encoding="utf-8")
    result = ReadFileTool().execute({"path": str(tpl)})
    assert result.success
    assert "your-key-here" in result.output


# ── D: @ 引用跳过 ──


def test_at_reference_skips_sensitive(tmp_path: Path) -> None:
    from zall.cli.file_complete import expand_at_references
    (tmp_path / ".env").write_text("TOKEN=leak-me\n", encoding="utf-8")
    expanded, injected = expand_at_references("check @.env", root=str(tmp_path))
    assert ".env" in injected            # 引用被识别
    assert "leak-me" not in expanded     # 但内容绝不注入
    assert "sensitive file skipped" in expanded


def test_at_reference_normal_file_still_injected(tmp_path: Path) -> None:
    """反例孪生: 普通文件注入行为不受影响。"""
    from zall.cli.file_complete import expand_at_references
    (tmp_path / "note.txt").write_text("hello-content\n", encoding="utf-8")
    expanded, injected = expand_at_references("see @note.txt", root=str(tmp_path))
    assert "note.txt" in injected
    assert "hello-content" in expanded


# ── E: Windows 反斜杠路径 ──


def test_backslash_paths_hit() -> None:
    assert is_sensitive_file(r"C:\Users\me\.aws\credentials")
    assert is_sensitive_file(r"C:\proj\.env")
    assert not is_sensitive_file(r"C:\proj\.env.example")


def test_refusal_message_names_file_without_content() -> None:
    msg = sensitive_refusal(r"C:\proj\.env")
    assert ".env" in msg and "BLOCKED" in msg


# ── F: grep 层过滤 (双引擎: rg + python 退化) ──


def test_grep_skips_sensitive_files(tmp_path: Path) -> None:
    """grep 命中行可能就是密钥本体 — 敏感文件整体跳过并警示。"""
    from zall.tools.grep import GrepTool
    (tmp_path / ".env").write_text("API_KEY=sk-leak-me\n", encoding="utf-8")
    (tmp_path / "app.py").write_text('API_KEY = os.environ["API_KEY"]\n',
                                     encoding="utf-8")
    result = GrepTool().execute({"pattern": "API_KEY", "path": str(tmp_path)})
    assert result.success
    assert "sk-leak-me" not in result.output       # 密钥行绝不外泄
    assert "app.py" in result.output               # 反例孪生: 普通命中保留
    assert "sensitive" in result.output            # 跳过警示可见


def test_grep_python_engine_skips_sensitive(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """退化 python 引擎同样过滤 (反例: 只修 rg 路径即漏洞)。"""
    import shutil as _shutil
    from zall.tools import grep as _grep_mod
    monkeypatch.setattr(_shutil, "which", lambda _name: None)  # 强制无 rg
    (tmp_path / ".env").write_text("TOKEN=py-leak\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("TOKEN placeholder\n", encoding="utf-8")
    result = _grep_mod.GrepTool().execute(
        {"pattern": "TOKEN", "path": str(tmp_path)})
    assert result.success
    assert "py-leak" not in result.output
    assert "ok.txt" in result.output


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
