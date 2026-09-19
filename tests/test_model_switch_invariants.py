"""tests/test_model_switch_invariants.py — provider/model 热切换的单源真相。

覆盖:
  - provider_endpoint 的 api_key/api_base 解析链 (env → [keys] → 旧字段 → 通用)
  - 自建网关不被"归属"到已知 provider (切换时不夺走用户代理)
  - apply_switch 热切换: 先换入 loop + 子代理工具, 再关旧 adapter
  - [keys] 段落盘读取不变形 (provider_keys 与 safety.config 对齐)
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from zall.cli.model_switch import Endpoint, SwitchResult, apply_switch, provider_endpoint


def _fake_registry() -> dict[str, Any]:
    return {
        "agnes": ("Agnes AI", "ZALL_API_KEY", "https://apihub.agnes-ai.com/v1",
                  "https://apihub.agnes-ai.com", ("agnes-",),
                  "zall.adapters.openai_compat:OpenAICompatAdapter"),
        "openai": ("OpenAI-compatible", "OPENAI_API_KEY", "https://api.openai.com/v1",
                   "https://platform.openai.com/api-keys", ("gpt-", "o1", "o3"),
                   "zall.adapters.openai_compat:OpenAICompatAdapter"),
        "anthropic": ("Anthropic", "ANTHROPIC_API_KEY", None,
                      "https://console.anthropic.com/settings/keys", ("claude-",),
                      "zall.adapters.anthropic:AnthropicAdapter"),
        "gemini": ("Google Gemini", "GOOGLE_API_KEY", None,
                   "https://aistudio.google.com/app/apikey", ("gemini-",),
                   "zall.adapters.gemini:GeminiAdapter"),
        "ollama": ("Ollama (local)", "OLLAMA_HOST", "http://localhost:11434",
                   "https://ollama.com", ("llama3", "qwen"),
                   "zall.adapters.ollama:OllamaAdapter"),
        "deepseek": ("DeepSeek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1",
                     "https://platform.deepseek.com/api_keys", ("deepseek-",),
                     "zall.adapters.openai_compat:OpenAICompatAdapter"),
        "sensenova": ("SenseNova", "SENSENOVA_API_KEY", "https://token.sensenova.cn/v1",
                      "https://platform.sensenova.cn", ("sensenova-",),
                      "zall.adapters.openai_compat:OpenAICompatAdapter"),
    }


def _cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "api_key": "sk-generic-aaaa",
        "model": "agnes-2.0-flash",
        "api_base": "https://apihub.agnes-ai.com/v1",
        "timeout": 120.0,
        "providers": [],
        "provider": "",
        "provider_keys": {},
        "temperature": None, "max_tokens": None, "top_p": None,
        "reasoning_effort": None, "window_size": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: Any) -> None:
    for k in ("ZALL_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
              "OLLAMA_HOST", "ZALL_API_BASE", "ZALL_PROVIDER"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def _registry(monkeypatch: Any) -> None:
    import zall.cli.config as cfg_mod
    import zall.safety.config as safety_mod

    monkeypatch.setattr(cfg_mod, "_get_provider_registry", _fake_registry)
    # apply_switch 内部 load_config() 读用户真实 ~/.zall/config.toml —
    # 测试必须与真实环境隔离, 否则结果随机器漂移
    monkeypatch.setattr(safety_mod, "load_config", lambda: _cfg())


class TestProviderEndpoint:
    """api_key / api_base 解析链 — 单一真相源, 不重复硬编码。"""

    def test_env_key_wins_over_config(self, _registry: Any) -> None:
        os.environ["OPENAI_API_KEY"] = "sk-openai-from-env"
        ep = provider_endpoint("openai", _cfg(api_base="https://api.openai.com/v1"))
        assert ep.api_key == "sk-openai-from-env"
        assert ep.key_source == "env OPENAI_API_KEY"

    def test_keys_section_wins_over_generic(self, _registry: Any) -> None:
        ep = provider_endpoint(
            "openai", _cfg(api_base="https://api.openai.com/v1",
                           provider_keys={"openai": "sk-openai-pk"}))
        assert ep.api_key == "sk-openai-pk"
        assert ep.key_source == "config [keys].openai"

    def test_legacy_fallback_key(self, _registry: Any) -> None:
        ep = provider_endpoint(
            "anthropic", _cfg(anthropic_api_key="sk-ant-legacy"))
        assert ep.api_key == "sk-ant-legacy"
        assert ep.key_source == "config [anthropic_api_key]"

    def test_generic_key_used_for_current_provider(self, _registry: Any) -> None:
        """通用 api_key 在端点没变时仍然可用 (不告警)。"""
        ep = provider_endpoint("agnes", _cfg())
        assert ep.api_key == "sk-generic-aaaa"
        assert ep.key_source == "config [auth].api_key (generic)"
        assert ep.warn == ""

    def test_generic_key_keeps_gateway_without_own_key(self, _registry: Any) -> None:
        """无专属 key → 端点跟钥匙走 (保留网关), generic key 可能就是网关的。"""
        ep = provider_endpoint("openai", _cfg(api_key="sk-generic-aaaa"))
        assert ep.api_base == "https://apihub.agnes-ai.com/v1"   # 网关保留
        assert ep.key_source == "config [auth].api_key (generic)"
        assert ep.warn == ""   # 端点没挪窝, 通用 key 仍然可信

    def test_generic_key_warns_on_inline_cross_base(self, _registry: Any) -> None:
        """显式换端点但只有 generic key → 如实告警 (大概率 401)。"""
        ep = provider_endpoint(
            "openai", _cfg(api_key="sk-generic-aaaa"),
            inline_base="https://api.openai.com/v1")
        assert ep.api_base == "https://api.openai.com/v1"
        assert ep.base_source == "inline (this session)"
        assert ep.warn != ""
        assert "generic key may not work" in ep.warn

    def test_no_key_reports_none(self, _registry: Any) -> None:
        ep = provider_endpoint("openai", _cfg(api_key="", provider_keys={}))
        assert ep.api_key == ""
        assert ep.key_source == "none"

    def test_custom_gateway_base_is_kept(self, _registry: Any) -> None:
        """自建网关 (host 不在 registry) 切换 provider 时不夺走用户代理。"""
        ep = provider_endpoint("openai", _cfg(api_base="https://my-proxy.example.com/v1"))
        assert ep.api_base == "https://my-proxy.example.com/v1"
        assert ep.base_source == "config (custom gateway)"

    def test_known_gateway_base_follows_switch(self, _registry: Any) -> None:
        """配置 base 属于别家 → 目标 provider 有专属 key 时跟随默认端点。"""
        ep = provider_endpoint(
            "agnes", _cfg(api_base="https://api.openai.com/v1",
                          provider_keys={"agnes": "sk-agnes-own"}))
        assert ep.base_source.startswith(
            "provider default (config base belongs to openai)")
        assert ep.api_base == "https://apihub.agnes-ai.com/v1"

    def test_known_gateway_base_kept_without_own_key(self, _registry: Any) -> None:
        """目标 provider 无专属 key → 保留网关 (拿网关跑别家模型的真实场景)。"""
        ep = provider_endpoint("deepseek", _cfg(api_base="https://token.sensenova.cn/v1"))
        assert ep.api_base == "https://token.sensenova.cn/v1"
        assert "kept" in ep.base_source

    def test_gateway_follows_when_env_key_present(self, _registry: Any,
                                                  monkeypatch: Any) -> None:
        monkeypatch.setenv("ZALL_API_KEY", "sk-agnes-env")
        ep = provider_endpoint("agnes", _cfg(api_base="https://api.openai.com/v1"))
        assert ep.api_base == "https://apihub.agnes-ai.com/v1"

    def test_keeps_base_when_host_is_the_same_provider(self, _registry: Any) -> None:
        """配置的 base 与目标 provider 同 host → 保留 (不认为是别的 provider)。"""
        ep = provider_endpoint("agnes", _cfg())
        assert ep.base_source == "config"
        assert ep.api_base == "https://apihub.agnes-ai.com/v1"

    def test_ollama_host(self, _registry: Any) -> None:
        ep = provider_endpoint("ollama", _cfg())
        assert ep.api_base == "http://localhost:11434"

    def test_unknown_provider_keeps_config_base(self, _registry: Any) -> None:
        """registry 外的 provider (如自定义) 用配置里的端点, 不回退硬编码。"""
        ep = provider_endpoint("my-llm", _cfg(api_base="https://my-endpoint.com/v1"))
        assert ep.api_base == "https://my-endpoint.com/v1"
        assert ep.base_source == "config"  # 无 registry 默认值, 配置就是唯一来源
        ep2 = provider_endpoint("my-llm", _cfg(api_base=""))
        assert ep2.api_base == ""
        assert ep2.base_source == "provider default"

    def test_ctor_params_unknown_provider(self, _registry: Any) -> None:
        """未知 provider 的构造器签名回退到 OpenAICompat 形状。"""
        from zall.cli.model_switch import _adapter_ctor_params
        assert "api_base" in _adapter_ctor_params("no-such-provider")


class TestApplySwitch:
    """热切换: 正在运行的会话立即生效, 无需 /clear。"""

    def _fake_adapter(self, name: str = "fake") -> Any:
        class _A:
            closed = False
            model_name = name

            def close(self) -> None:
                self.closed = True

            def __repr__(self) -> str:
                return f"<_A {name}>"
        return _A()

    def _fake_loop(self, adapter: Any) -> Any:
        class _L:
            def __init__(self) -> None:
                self._model = adapter
                self._tools = _Tools()

            def set_model_adapter(self, new: Any) -> Any:
                old = self._model
                self._model = new
                return old

            @property
            def model_adapter(self) -> Any:
                return self._model
        return _L()

    def test_switch_records_model_and_provider(self, _registry: Any) -> None:
        state: dict[str, Any] = {}
        res = apply_switch(state, None, model="gpt-4o-mini", provider="openai")
        assert res.ok
        assert res.provider == "openai"
        assert res.model == "gpt-4o-mini"
        assert state["model"] == "gpt-4o-mini"
        assert state["provider"] == "openai"
        assert state["_adapter"] is res.adapter

    def test_switch_reuses_live_loop(self, _registry: Any) -> None:
        old = self._fake_adapter("old")
        loop = self._fake_loop(old)
        state: dict[str, Any] = {"_adapter": old, "_input_fn": None}
        res = apply_switch(state, loop, model="claude-sonnet-4", provider="anthropic")
        assert res.ok
        assert res.live is True
        assert loop.model_adapter is res.adapter   # loop 已换新
        assert state["_adapter"] is res.adapter    # state 已换新
        assert old.closed                          # 旧 adapter 被关闭

    def test_switch_closes_old_state_adapter_when_no_loop(self, _registry: Any) -> None:
        old = self._fake_adapter("old")
        state: dict[str, Any] = {"_adapter": old, "_input_fn": None}
        apply_switch(state, None, model="gemini-2.5-pro", provider="gemini")
        assert old.closed

    def test_unknown_provider_fails_without_side_effect(self, _registry: Any) -> None:
        state: dict[str, Any] = {"_adapter": self._fake_adapter("kept"), "_input_fn": None}
        res = apply_switch(state, None, provider="nope")
        assert res.ok is False
        assert "three fields are enough" in res.error   # 拒绝时教三件套用法
        assert state["_adapter"].model_name == "kept"   # 现状未被破坏

    def test_three_fields_adhoc_gateway(self, _registry: Any) -> None:
        """OpenCode 对齐: base + key + model 三件套直接接入, 免预注册。"""
        state: dict[str, Any] = {"_input_fn": None}
        res = apply_switch(
            state, None, provider="mygw", model="my-model",
            inline_base="https://gw.example.com/v1", inline_key="sk-gw-1")
        assert res.ok, res.error
        assert res.provider == "mygw"
        assert res.model == "my-model"
        assert state["provider"] == "mygw"
        assert state["_adapter"] is res.adapter
        assert res.endpoint.api_base == "https://gw.example.com/v1"
        assert res.endpoint.key_source == "inline (this session)"

    def test_three_fields_missing_key_teaches_usage(self, _registry: Any) -> None:
        """只有 base 没有 key → 拒绝并给三件套用法。"""
        res = apply_switch(
            state=None, loop=None, provider="mygw",
            inline_base="https://gw.example.com/v1")
        assert res.ok is False
        assert "three fields are enough" in res.error

    def test_custom_provider_case_insensitive_lookup(self, _registry: Any) -> None:
        """自定义名大小写: 原名不在表里但小写在 → 归一。"""
        res = apply_switch(
            state=None, loop=None, provider="MyGw", model="m1",
            inline_base="https://x.example.com/v1", inline_key="sk-1")
        assert res.ok
        assert res.provider == "MyGw"   # 保留用户输入原名

    def test_syncs_spawn_subagent_tool(self, _registry: Any) -> None:
        old = self._fake_adapter("old")
        loop = self._fake_loop(old)
        # 模拟子代理工具持有旧 adapter 引用
        loop._tools._spawn._current_adapter = old
        state: dict[str, Any] = {"_adapter": old, "_input_fn": None}
        apply_switch(state, loop, model="gpt-4o-mini", provider="openai")
        assert loop._tools._spawn._current_adapter is not old  # 已换入新的

    def test_persist_key_writes_keys_section(self, _registry: Any, tmp_path: Any) -> None:
        from zall.cli.provider_keys import persist_provider_key, remove_provider_key
        path = tmp_path / "config.toml"
        persist_provider_key("deepseek", "sk-ds-123", config_path=path)
        persist_provider_key("openai", "sk-oai-456", config_path=path)
        text = path.read_text(encoding="utf-8")
        assert 'deepseek = "sk-ds-123"' in text
        assert 'openai = "sk-oai-456"' in text
        # 删除一个不影响另一个
        remove_provider_key("deepseek", config_path=path)
        text = path.read_text(encoding="utf-8")
        assert 'deepseek = ' not in text
        assert 'openai = "sk-oai-456"' in text

    def test_persist_custom_provider_no_collateral_damage(
            self, _registry: Any, tmp_path: Any) -> None:
        """[[providers]] 落盘: 新条目追加 / 同名更新 / 邻居条目一行不动。

        回归锁: 旧实现把新 provider 的 api_base 补进了相邻条目 (sensenova
        的端点被夺走), 新条目本身反而丢失 — 三场景各反例锁定。
        """
        from zall.cli.provider_keys import persist_custom_provider
        p = tmp_path / "config.toml"
        p.write_text(
            "[model]\n"
            'name = "deepseek-chat"\n'
            'api_base = "https://token.sensenova.cn/v1"\n'
            "\n"
            "[[providers]]\n"
            'name = "sensenova"\n'
            'adapter = "openai-compat"\n'
            'api_base = "https://token.sensenova.cn/v1"\n'
            "\n"
            "[ui]\n"
            'theme = "attic"\n',
            encoding="utf-8")

        # 新条目追加
        persist_custom_provider("mygw", "https://gw.example.com/v1", config_path=p)
        text = p.read_text(encoding="utf-8")
        assert text.count("[[providers]]") == 2
        assert 'name = "mygw"' in text
        assert "https://gw.example.com/v1" in text
        # 邻居条目与无关段落一行不动
        assert "https://token.sensenova.cn/v1" in text
        assert 'theme = "attic"' in text
        assert 'name = "deepseek-chat"' in text

        # 同名条目: 只动 api_base, 其余字段 (window_size 等) 保留
        p.write_text(
            "[[providers]]\n"
            'name = "mygw"\n'
            'adapter = "openai-compat"\n'
            'api_base = "https://old.example.com/v1"\n'
            "window_size = 64000\n"
            "\n"
            "[[providers]]\n"
            'name = "sensenova"\n'
            'adapter = "openai-compat"\n'
            'api_base = "https://token.sensenova.cn/v1"\n',
            encoding="utf-8")
        persist_custom_provider("mygw", "https://gw.example.com/v1", config_path=p)
        text = p.read_text(encoding="utf-8")
        assert "https://old.example.com" not in text
        assert "https://gw.example.com/v1" in text
        assert "window_size = 64000" in text
        assert "https://token.sensenova.cn/v1" in text
        assert text.count("[[providers]]") == 2  # 未产生重复条目

    def test_persisted_custom_provider_is_switchable(
            self, tmp_path: Any, monkeypatch: Any) -> None:
        """三件套 -p 之后, [[providers]] 落盘 → registry 合并 → 一键切回。

        不用 _registry fixture — 本测试走真实 registry 合并路径 (读 config.toml)。
        """
        import zall.safety.config as safety_mod

        home = tmp_path / "home"
        home.mkdir(parents=True)
        monkeypatch.setattr(safety_mod, "CONFIG_DIR", home / ".zall")
        monkeypatch.chdir(home)  # 隔离 cwd 级 .zall/config.toml

        state: dict[str, Any] = {"_input_fn": None}
        res = apply_switch(
            state, None, provider="mygw", model="my-model",
            inline_base="https://gw.example.com/v1", inline_key="sk-gw-1",
            persist=True, persist_key=True)
        assert res.ok, res.error
        # 落盘后 registry 应含 mygw (合并自 [[providers]]); lru_cache 需先清
        from zall.cli.config import _clear_provider_registry_cache, _get_provider_registry
        _clear_provider_registry_cache()
        assert "mygw" in _get_provider_registry()
        # 再切回 (不给 base/key): 走真实 load_config —
        # [model].api_base 已落成 gw.example.com, [keys].mygw 有专属 key
        state2: dict[str, Any] = {"_input_fn": None}
        res2 = apply_switch(state2, None, provider="mygw")
        assert res2.ok, res2.error
        assert res2.endpoint.api_base == "https://gw.example.com/v1"
        assert res2.endpoint.api_key == "sk-gw-1"
        assert res2.endpoint.key_source == "config [keys].mygw"

    def test_apply_switch_persists_model_and_key(self, _registry: Any, tmp_path: Any,
                                                 monkeypatch: Any) -> None:
        import pathlib

        import zall.cli.provider_keys as pk_mod
        import zall.safety.config as safety_mod

        home = tmp_path / "home"
        monkeypatch.setattr(pathlib.Path, "home", lambda *a: home)
        monkeypatch.setattr(safety_mod, "CONFIG_DIR", home / ".zall")

        path = home / ".zall" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('[model]\nname = "agnes-2.0-flash"\n', encoding="utf-8")

        def _patched(path_: Any = None) -> Any:
            return path
        monkeypatch.setattr(pk_mod, "_config_path", _patched)

        state: dict[str, Any] = {"_input_fn": None}
        res = apply_switch(
            state, None, model="deepseek-chat", provider="deepseek",
            persist=True, inline_key="sk-ds-inline", persist_key=True)
        assert res.ok
        assert res.persisted is True
        assert any("key persist failed" in n for n in res.notes) is False
        text = path.read_text(encoding="utf-8")
        assert 'name = "deepseek-chat"' in text
        assert 'deepseek = "sk-ds-inline"' in text


class FakeSpawnTool:
    """测试用子代理工具, 暴露 swap_model_provider。"""

    def __init__(self) -> None:
        self._current_adapter = None
        self.swapped: list[Any] = []

    def swap_model_provider(self, adapter: Any) -> None:
        self._current_adapter = adapter
        self.swapped.append(adapter)


class _Tools:
    def __init__(self) -> None:
        self._spawn = FakeSpawnTool()

    def get(self, key: str) -> Any:
        return self._spawn if key == "spawn_subagent" else None

class TestProviderListIncludesCustom:
    """2026-09-19 实测反馈: /provider 列表只列内置 6 家, 用户配好的自定义
    provider 看不见, "列不完所有的提供商"。修复: 列表读合并注册表。"""

    def test_custom_provider_shown_in_list(self, tmp_path: Any, monkeypatch: Any,
                                           capsys: Any) -> None:
        import io as _io

        import zall.safety.config as safety_mod
        from zall.cli.commands.model import cmd_provider
        from zall.cli.config import _clear_provider_registry_cache

        home = tmp_path / "home"
        home.mkdir(parents=True)
        monkeypatch.setattr(safety_mod, "CONFIG_DIR", home / ".zall")
        (home / ".zall").mkdir()
        (home / ".zall" / "config.toml").write_text(
            '[model]\nname = "my-model"\napi_base = "https://gw.example.com/v1"\n'
            '[auth]\napi_key = "sk-x"\n'
            "[[providers]]\n"
            'name = "mygw"\n'
            'display = "My Gateway"\n'
            'adapter = "openai-compat"\n'
            'api_base = "https://gw.example.com/v1"\n',
            encoding="utf-8")
        _clear_provider_registry_cache()
        try:
            out = _io.StringIO()
            out.isatty = lambda: False  # type: ignore[method-assign]
            cmd_provider("", out, loop=None, state={})
            text = out.getvalue()
        finally:
            _clear_provider_registry_cache()
        assert "mygw" in text, f"custom provider missing from list:\n{text}"
        assert "(custom)" in text
        assert "gw.example.com" in text
        # 反例: 内置 6 家仍然都在
        for builtin in ("openai", "anthropic", "gemini", "deepseek", "ollama", "agnes"):
            assert builtin in text, f"builtin {builtin} missing"
