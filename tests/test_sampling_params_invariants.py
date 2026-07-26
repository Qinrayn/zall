"""Invariant tests for adapter sampling params (F2b).

Corresponds to:
  F2b: temperature/max_tokens/top_p/reasoning_effort 经 config -> adapter -> body
  body 仅在参数非 None 时包含 (反例: 不该给不支持 provider 发, 导致 400)

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import pytest


def _make_adapter(**kwargs):
    """Build an OpenAICompatAdapter with test creds + given sampling params."""
    from zall.adapters.openai_compat import OpenAICompatAdapter
    return OpenAICompatAdapter(
        api_key="sk-test", api_base="https://example.com/v1",
        model="test-model", **kwargs,
    )


class TestBuildBodySamplingParams:
    """_build_body 必须在采样参数非 None 时包含, None 时不含。"""

    def test_temperature_in_body_when_set(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter(temperature=0.3)
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert body["temperature"] == 0.3
        a.close()

    def test_temperature_absent_when_none(self) -> None:
        """Counterexample: temperature=None 时不应进 body (避免给不支持 provider 发 400)."""
        from zall.core.model import ToolChoice
        a = _make_adapter()  # all None
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert "temperature" not in body
        a.close()

    def test_max_tokens_in_body_when_set(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter(max_tokens=4096)
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert body["max_tokens"] == 4096
        a.close()

    def test_max_tokens_absent_when_none(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter()
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert "max_tokens" not in body
        a.close()

    def test_top_p_in_body_when_set(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter(top_p=0.9)
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert body["top_p"] == 0.9
        a.close()

    def test_reasoning_effort_in_body_when_set(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter(reasoning_effort="high")
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert body["reasoning_effort"] == "high"
        a.close()

    def test_reasoning_effort_absent_when_none(self) -> None:
        """Counterexample: reasoning_effort=None 时不应进 body."""
        from zall.core.model import ToolChoice
        a = _make_adapter()
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert "reasoning_effort" not in body
        a.close()

    def test_reasoning_effort_normalized_lowercase(self) -> None:
        """reasoning_effort 大小写不敏感, 统一转小写。"""
        from zall.core.model import ToolChoice
        a = _make_adapter(reasoning_effort="HIGH")
        assert a._reasoning_effort == "high"
        a.close()

    def test_all_params_together(self) -> None:
        from zall.core.model import ToolChoice
        a = _make_adapter(temperature=0.5, max_tokens=2048, top_p=0.95,
                          reasoning_effort="medium")
        body = a._build_body([], [], ToolChoice.AUTO, stream=False)
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 2048
        assert body["top_p"] == 0.95
        assert body["reasoning_effort"] == "medium"
        a.close()


class TestConfigToAdapterWiring:
    """F2b 端到端: config 中的采样参数经 _build_adapter 到达 adapter。"""

    def test_build_adapter_injects_sampling_params(self, monkeypatch) -> None:
        """config 中的 temperature/max_tokens 经 _build_adapter 传入 adapter。"""
        monkeypatch.setenv("ZALL_API_KEY", "sk-test")
        monkeypatch.setenv("ZALL_TEMPERATURE", "0.7")
        monkeypatch.setenv("ZALL_MAX_TOKENS", "1024")
        monkeypatch.setenv("ZALL_REASONING_EFFORT", "low")
        from zall.cli.config import _build_adapter, _clear_provider_registry_cache
        _clear_provider_registry_cache()
        a = _build_adapter("openai", model="gpt-4o-mini")
        assert a._temperature == 0.7
        assert a._max_tokens == 1024
        assert a._reasoning_effort == "low"
        a.close()

    def test_build_adapter_no_params_when_unset(self, monkeypatch) -> None:
        """Counterexample: 未设采样参数时 adapter 各项为 None (不发 body)."""
        for v in ("ZALL_TEMPERATURE", "ZALL_MAX_TOKENS", "ZALL_TOP_P",
                  "ZALL_REASONING_EFFORT"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("ZALL_API_KEY", "sk-test")
        from zall.cli.config import _build_adapter, _clear_provider_registry_cache
        _clear_provider_registry_cache()
        a = _build_adapter("openai", model="gpt-4o-mini")
        assert a._temperature is None
        assert a._max_tokens is None
        assert a._reasoning_effort is None
        a.close()
