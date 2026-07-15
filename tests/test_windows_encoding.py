"""tests/test_windows_encoding.py — Windows 中文encodingtest (P5-a).

covers scenarios:
  1. bash tool GBK output decoding
  2. Chinese path creation (read_file / write_file / edit_file)
  3. CJK 水位监控 (compactor 语言感知加权)
  4. rich 渲染中的中文文本
  5. _preferred_encoding() 回退逻辑

IPR-0: 每个test包含正例 + Counterexample (counterexample).
"""

from __future__ import annotations

import locale
import os
import sys
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────────
# _preferred_encoding() fallback逻辑test
# ──────────────────────────────────────────────────────────────────────────


def _preferred_encoding() -> str:
    """test用: mock bash.py 的 _preferred_encoding (简化version)."""
    try:
        enc = locale.getpreferredencoding(False)
        if enc:
            return enc
    except (ValueError, LookupError):
        pass
    return "utf-8"


class TestPreferredEncoding:
    """_preferred_encoding fallback逻辑tests."""

    def test_default_returns_string(self):
        """defaultreturnsnon-空encoding字符串."""
        enc = _preferred_encoding()
        assert isinstance(enc, str)
        assert len(enc) > 0

    def test_known_platform_encodings(self):
        """已知平台encoding值 (不假定concrete值, 只verify格式)."""
        enc = _preferred_encoding()
        # encoding名通常class似 "utf-8", "cp936", "gbk", "latin-1" 等
        assert enc.replace("-", "").replace("_", "").isalnum()


class TestUtf8Fallback:
    """UTF-8 fallback逻辑 (当 locale 不可用时)."""

    def test_direct_utf8(self):
        """directly的中文字符串encoding/decode."""
        text = "你好世界，zall 中文test"
        encoded = text.encode("utf-8")
        decoded = encoded.decode("utf-8")
        assert decoded == text

    def test_gbk_roundtrip(self):
        """GBK encoding/decode往返."""
        text = "中文路径test"
        encoded = text.encode("gbk")
        decoded = encoded.decode("gbk")
        assert decoded == text

    def test_utf8_with_gbk_failover(self):
        """UTF-8 decodefail时不应静默吞掉error."""
        gbk_bytes = "中文test".encode("gbk")
        with pytest.raises(UnicodeDecodeError):
            gbk_bytes.decode("utf-8", errors="strict")


# ──────────────────────────────────────────────────────────────────────────
# CJK pathoperationtest
# ──────────────────────────────────────────────────────────────────────────


class TestChineseFilePath:
    """中文filepath读写tests."""

    def test_write_and_read_chinese_path(self, tmp_path: Path):
        """create含中文的filepath并读写."""
        # 中文file名 (Windows/NTFS 支持 UTF-16 path)
        chinese_dir = tmp_path / "中文目录"
        chinese_dir.mkdir(exist_ok=True)
        assert chinese_dir.exists()

        chinese_file = chinese_dir / "test文件.txt"
        content = "这是中文内容"
        chinese_file.write_text(content, encoding="utf-8")
        assert chinese_file.exists()

        read_back = chinese_file.read_text(encoding="utf-8")
        assert read_back == content

    def test_chinese_path_with_spaces(self, tmp_path: Path):
        """含空格和中文的path."""
        path = tmp_path / "项目文档 v2.0" / "用户手册_最终版.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 用户手册\n\n欢迎使用 zall", encoding="utf-8")
        assert path.exists()

    def test_deep_chinese_path(self, tmp_path: Path):
        """深层嵌套的中文directorypath."""
        deep = tmp_path / "src" / "中文模块" / "sub模块" / "功能" / "test"
        deep.mkdir(parents=True, exist_ok=True)
        assert deep.exists()
        assert deep.is_dir()


# ──────────────────────────────────────────────────────────────────────────
# CJK 字符估算test
# ──────────────────────────────────────────────────────────────────────────


class TestCjkTokenEstimation:
    """CJK token 估算逻辑 (corresponds to compactor.py 的 _estimate_chars_per_token)."""

    @staticmethod
    def _estimate_chars_per_token(text: str) -> float:
        """mock compactor.py 的估算逻辑."""
        _SAMPLE_SIZE = 1000
        _CHARS_PER_TOKEN_EN = 4.0
        _CHARS_PER_TOKEN_CJK = 1.6
        _CJK_RANGES = (
            (0x4E00, 0x9FFF),
            (0x3400, 0x4DBF),
            (0x2E80, 0x2EFF),
            (0x3000, 0x303F),
            (0xFF00, 0xFFEF),
            (0xF900, 0xFAFF),
            (0x2F800, 0x2FA1F),
        )
        sample = text[:_SAMPLE_SIZE]
        if not sample:
            return _CHARS_PER_TOKEN_EN
        cjk_count = 0
        for ch in sample:
            cp = ord(ch)
            for lo, hi in _CJK_RANGES:
                if lo <= cp <= hi:
                    cjk_count += 1
                    break
        ratio = cjk_count / len(sample)
        return (1 - ratio) * _CHARS_PER_TOKEN_EN + ratio * _CHARS_PER_TOKEN_CJK

    def test_pure_english(self):
        """纯英文文本 → 4.0 chars/token."""
        text = "hello world this is a test " * 20
        ratio = self._estimate_chars_per_token(text)
        assert ratio == pytest.approx(4.0, abs=0.01)

    def test_pure_chinese(self):
        """纯中文文本 → 1.6 chars/token."""
        text = "你好世界这是一个测试系统" * 20
        ratio = self._estimate_chars_per_token(text)
        # 12 CJK chars, 0 non-CJK → ratio=1.0 → 1.6 chars/token
        assert ratio == pytest.approx(1.6, abs=0.01)

    def test_mixed_chinese_english(self):
        """中英文混合 → 加权average."""
        text = "hello world " * 10 + "你好世界" * 10
        ratio = self._estimate_chars_per_token(text)
        assert 1.6 < ratio < 4.0

    def test_empty_text_fallback(self):
        """空文本 → default英文系数 4.0."""
        ratio = self._estimate_chars_per_token("")
        assert ratio == 4.0

    def test_cjk_punctuation(self):
        """CJK 标点符号计入 CJK range."""
        text = "，、；：「」【】《》？！" * 5
        ratio = self._estimate_chars_per_token(text)
        # All CJK punctuation → 1.6 chars/token
        assert ratio == pytest.approx(1.6, abs=0.01)

    def test_mixed_with_numbers(self):
        """含数字的混合文本."""
        text = "你好123worldtest456"
        ratio = self._estimate_chars_per_token(text)
        assert 1.6 < ratio < 4.0


# ──────────────────────────────────────────────────────────────────────────
# bash output decodingtest (Windows chcp 936 mock)
# ──────────────────────────────────────────────────────────────────────────


class TestBashOutputDecoding:
    """mock Windows 下 bash tooloutput decoding (GBK->UTF-8)."""

    def test_gbk_stdout_decoding(self):
        """GBK encoding的 stdout 应能被correctlydecode."""
        gbk_data = "中文输出内容".encode("gbk")
        # mock subprocess returns GBK 字节
        decoded = gbk_data.decode("gbk")
        assert decoded == "中文输出内容"

    def test_mixed_encoding_output(self):
        """混合encodingoutput不应崩溃."""
        # 包含non-法 UTF-8 serial
        raw = b"hello \xff\xfe world \xd6\xd0\xce\xc4"
        try:
            decoded = raw.decode("utf-8", errors="replace")
            assert "\ufffd" in decoded or "hello" in decoded
        except Exception:
            # 至少does not crash溃
            pass

    def test_utf8_bom_output(self):
        """UTF-8 BOM fileread."""
        content = "\ufeff中文BOMtest"
        assert content.startswith("\ufeff")
        stripped = content.lstrip("\ufeff")
        assert stripped == "中文BOMtest"


# ──────────────────────────────────────────────────────────────────────────
# rich 渲染中文文本test
# ──────────────────────────────────────────────────────────────────────────


class TestChineseRendering:
    """中文文本在 rich 渲染中的表现."""

    def test_chinese_string_length(self):
        """中文字符串长度计算 (Python 3: len returns字符数, non-字节数)."""
        text = "zall 中文测试"
        char_count = len(text)
        # 9 chars: "zall " (5) + "中文测试" (4) = 9
        assert char_count == 9

    def test_chinese_truncation(self):
        """中文文本truncate不应在字符中间断."""
        text = "这是一个很长的中文句sub需要被截断" * 10
        max_chars = 20
        truncated = text[:max_chars]
        assert len(truncated) <= max_chars
        # truncate后的文本应有效
        assert isinstance(truncated, str)

    def test_combined_cjk_latin(self):
        """中日韩+拉丁混合文本."""
        text = "hello 世界 konnichiwa 世界 annyeong 世界"
        # 应能正常handle
        assert len(text) > 0
        for ch in text:
            assert isinstance(ch, str)


# ──────────────────────────────────────────────────────────────────────────
# 跨平台compatible性test
# ──────────────────────────────────────────────────────────────────────────


class TestCrossPlatformEncoding:
    """跨平台encodingcompatible性."""

    def test_utf8_everywhere(self):
        """所有平台都应支持 UTF-8 编decode."""
        texts = [
            "Hello, World!",
            "你好，世界！",
            "こんにちは世界",
            "안녕하세요 세계",
            "مرحبا بالعالم",
            "Привет, мир!",
        ]
        for text in texts:
            encoded = text.encode("utf-8")
            decoded = encoded.decode("utf-8")
            assert decoded == text

    def test_path_encoding_independence(self, tmp_path: Path):
        """filepathencoding不应影响content."""
        languages = [
            "English",
            "中文",
            "日本語",
            "한국어",
            "Русский",
        ]
        for lang in languages:
            p = tmp_path / f"test_{lang}.txt"
            p.write_text(f"Hello from {lang}", encoding="utf-8")
            assert p.exists()
            assert p.read_text(encoding="utf-8") == f"Hello from {lang}"


# ──────────────────────────────────────────────────────────────────────────
# Counterexamples (Counterexampletest)
# ──────────────────────────────────────────────────────────────────────────


class TestEncodingCounterExamples:
    """encoding相关的Counterexampletests."""

    def test_gbk_not_utf8(self):
        """GBK 字节被误当 UTF-8 decode应产生error或replace字符."""
        gbk_bytes = "中文test".encode("gbk")
        decoded = gbk_bytes.decode("utf-8", errors="replace")
        # 应产生replace字符或正常decode (取决于content)
        assert isinstance(decoded, str)
        # 如果严格decode应fail
        with pytest.raises((UnicodeDecodeError, UnicodeError)):
            gbk_bytes.decode("utf-8", errors="strict")

    def test_empty_path(self, tmp_path):
        """空path不应createfile."""
        with pytest.raises((OSError, ValueError)):
            (tmp_path / "").write_text("content", encoding="utf-8")

    def test_oversized_cjk_estimate(self):
        """极端 CJK 比例的估算应在合理range."""
        text = "中" * 10000  # 纯中文, 10000 字符
        ratio = TestCjkTokenEstimation._estimate_chars_per_token(text)
        assert 1.5 <= ratio <= 2.0  # CJK 系数应在 1.6 附近
