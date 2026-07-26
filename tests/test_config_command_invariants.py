"""Invariant tests for /config command (F1) and sampling params (F2).

Corresponds to:
  F1: /config set/unset/show/guide - 配置一等化 (无重复 /model /provider)
  F2a/b/c: config schema + adapter sampling params + window override

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from zall.cli.commands.config import (
    _mask_key,
    _parse_value,
    _persist_config_key,
    _CONFIG_KEYS,
    cmd_config,
)


class TestMaskKey:
    """api_key 展示必须脱敏。"""

    def test_long_key_masked(self) -> None:
        """Counterexample: 长 key 不能原样显示, 必须脱敏。"""
        masked = _mask_key("sk-NJsnDTaAOFnyiC4tShyxUwMXKh6BpSty")
        assert "NJsnDTaAOFnyiC4t" not in masked  # 中间不能泄漏
        assert masked.endswith("pSty")  # 只显后4位
        assert masked.startswith("sk-")

    def test_short_key_masked(self) -> None:
        assert _mask_key("short") == "****"

    def test_empty_key(self) -> None:
        assert _mask_key("") == "(not set)"
        assert _mask_key("your-api-key-here") == "(not set)"


class TestParseValue:
    """值类型解析正确。"""

    def test_float(self) -> None:
        assert _parse_value("temperature", "0.3") == 0.3

    def test_int(self) -> None:
        assert _parse_value("max_tokens", "4096") == 4096
        assert _parse_value("window_size", "64000") == 64000

    def test_str(self) -> None:
        assert _parse_value("api_base", "https://x.com/v1") == "https://x.com/v1"

    def test_effort_valid(self) -> None:
        assert _parse_value("reasoning_effort", "high") == "high"
        assert _parse_value("reasoning_effort", "LOW") == "low"

    def test_effort_invalid_raises(self) -> None:
        """Counterexample: 非法 effort 值必须报错。"""
        with pytest.raises(ValueError):
            _parse_value("reasoning_effort", "extreme")

    def test_float_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_value("temperature", "not-a-number")


class TestPersistConfigKey:
    """持久化: 设置/清除单个 key, 保留其他段。

    用 config_path= 显式注入 tmp 路径, 不依赖 Path.home() (避免污染真实配置).
    """

    def test_set_preserves_auth(self, tmp_path) -> None:
        """设置 model 段 key 不能破坏 auth 段。

        Counterexample: 错误实现会覆盖/丢失 auth.api_key。
        """
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[auth]\napi_key = "sk-SECRET"\n\n[model]\nname = "m1"\n',
            encoding="utf-8",
        )
        _persist_config_key("temperature", 0.5, config_path=cfg)
        content = cfg.read_text(encoding="utf-8")
        assert "sk-SECRET" in content  # auth 保留
        assert "temperature = 0.5" in content  # 新 key 写入

    def test_unset_removes_key(self, tmp_path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[model]\nname = "m1"\ntemperature = 0.5\n',
            encoding="utf-8",
        )
        _persist_config_key("temperature", None, config_path=cfg)
        content = cfg.read_text(encoding="utf-8")
        assert "temperature" not in content  # 已清除
        assert 'name = "m1"' in content  # 其他 key 保留

    def test_set_api_key_persists(self, tmp_path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[auth]\napi_key = ""\n', encoding="utf-8")
        _persist_config_key("api_key", "sk-NEWKEY123456", config_path=cfg)
        content = cfg.read_text(encoding="utf-8")
        assert "sk-NEWKEY123456" in content


class TestConfigCommandShow:
    """/config 展示配置。"""

    def test_show_outputs_all_fields(self) -> None:
        buf = io.StringIO()
        result = cmd_config("", buf, None, {})
        assert result == "handled"
        out = buf.getvalue()
        # 必须包含所有配置字段
        for field in ("model", "api_key", "api_base", "timeout",
                      "window_size", "temperature", "max_tokens",
                      "top_p", "reasoning_effort"):
            assert field in out, f"missing field {field} in /config output"

    def test_show_masks_api_key(self) -> None:
        """Counterexample: /config show 不能原样显示 api_key。"""
        buf = io.StringIO()
        cmd_config("", buf, None, {})
        out = buf.getvalue()
        # 不应包含完整 key (若 env 设了长 key)
        env_key = os.environ.get("ZALL_API_KEY", "")
        if env_key and len(env_key) > 8:
            assert env_key not in out, "full api_key leaked in /config output"

    def test_guide_outputs_usage(self) -> None:
        buf = io.StringIO()
        cmd_config("guide", buf, None, {})
        out = buf.getvalue()
        assert "/config set" in out
        assert "reasoning_effort" in out

    def test_unknown_key_rejected(self) -> None:
        buf = io.StringIO()
        cmd_config("set bogus_key value", buf, None, {})
        out = buf.getvalue()
        assert "unknown key" in out.lower() or "valid:" in out.lower()
