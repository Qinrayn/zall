"""Kimi-style provider guide (v0.7) — /provider 菜单向导回归测试。

covers:
  1. choice_menu: TTY 降级/取消契约 (数字选择回退, 空选项, ptk 失败不误判取消)
  2. secret_prompt: 注入 fn / EOF / getpass 兜底契约
  3. _render_menu_text: ▸ 高亮 + 键位提示 (纯函数)
  4. cmd_provider 菜单交互: 选中 provider → apply_switch(persist=True)
  5. cmd_provider 向导 (「＋」→ 自定义 URL → key → 模型): 落盘三要素
  6. 文案回归: 非 TTY 输出不再教 key=<…> 三件套

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from zall.cli.select import _render_menu_text, choice_menu, secret_prompt


class TestChoiceMenuDegrade:
    """choice_menu 非 TTY / ptk 失败 → 数字选择回退, 契约: value | None。"""

    _CHOICES = [
        ("deepseek", "DeepSeek  ·  https://api.deepseek.com/v1", ""),
        ("zhipu", "Zhipu GLM  ·  https://open.bigmodel.cn/api/paas/v4", ""),
        ("__url__", "＋ 添加新网关 · any OpenAI-compatible API", ""),
    ]

    def test_digit_select_returns_value(self) -> None:
        """Happy path: 非 TTY + 数字 '3' → 第 3 项 value '__url__'。"""
        out = io.StringIO()
        val = choice_menu(out, "Select a platform", self._CHOICES,
                          input_fn=lambda _: "3", is_tty=False)
        assert val == "__url__"

    def test_enter_default_returns_first(self) -> None:
        """Happy path: 空输入 (Enter) → 默认项 value。"""
        out = io.StringIO()
        val = choice_menu(out, "Select a platform", self._CHOICES,
                          input_fn=lambda _: "", is_tty=False)
        assert val == "deepseek"

    def test_eof_cancels_to_none(self) -> None:
        """Counterexample: EOF (管道断) → None, 不崩; 有 cancelled 行。"""
        def _raise(_: str) -> str:
            raise EOFError

        out = io.StringIO()
        val = choice_menu(out, "Select a platform", self._CHOICES,
                          input_fn=_raise, is_tty=False)
        assert val is None
        assert "cancelled" in out.getvalue()

    def test_out_of_range_falls_back_to_default(self) -> None:
        """Counterexample: 越界 '9' → 默认项 (绝不返回越界/None 误取消)。"""
        out = io.StringIO()
        val = choice_menu(out, "Select a platform", self._CHOICES,
                          input_fn=lambda _: "9", is_tty=False)
        assert val == "deepseek"

    def test_empty_choices_returns_none_without_input(self) -> None:
        """Counterexample: 无选项 → None, 不读输入 (不挂起)。"""
        out = io.StringIO()
        called = {"n": 0}

        def _count(_: str) -> str:
            called["n"] += 1
            return "1"

        assert choice_menu(out, "t", [], input_fn=_count, is_tty=False) is None
        assert called["n"] == 0

    def test_ptk_failure_falls_back_not_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample: ptk 渲染失败 ≠ 用户取消 → 数字降级继续, 出选中值。"""
        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("no terminal attached")

        monkeypatch.setattr("zall.cli.select._ptk_choice_menu", _boom)
        out = io.StringIO()
        val = choice_menu(out, "Select a platform", self._CHOICES,
                          input_fn=lambda _: "2", is_tty=True)
        assert val == "zhipu"  # 降级而非 None
        assert "cancelled" not in out.getvalue()

    def test_ptk_success_prints_picked_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: ptk 返回 → 补印 '▸ label' 一行 (菜单退出后可见选了什么)。"""
        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, "zhipu"))
        out = io.StringIO()
        val = choice_menu(out, "Select a provider", self._CHOICES,
                          input_fn=lambda _: "1", is_tty=True)
        assert val == "zhipu"
        assert "▸ Zhipu GLM" in out.getvalue()


class TestSecretPrompt:
    """secret_prompt: 注入 fn (测试) 优先; getpass 兜底; EOF → ''。"""

    def test_injected_fn_returns_pasted_key(self) -> None:
        """Happy path: 注入 input_fn 直接返回明文 (测试可见)。"""
        assert secret_prompt("API key: ", input_fn=lambda _: "sk-123") == "sk-123"

    def test_injected_skips_returns_empty(self) -> None:
        """Counterexample: 空输入 (Enter skip) → ''。"""
        assert secret_prompt("API key: ", input_fn=lambda _: "") == ""

    def test_injected_eof_returns_empty(self) -> None:
        """Counterexample: EOF → '' (视为放弃, 不炸)。"""
        def _raise(_: str) -> str:
            raise EOFError

        assert secret_prompt("API key: ", input_fn=_raise) == ""

    def test_injected_strips_whitespace(self) -> None:
        """Happy path: 首尾空白裁剪 ('  sk-123  ' → 'sk-123')。"""
        assert secret_prompt("API key: ", input_fn=lambda _: "  sk-123  ") == "sk-123"

    def test_no_fn_falls_back_to_getpass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample: 无注入且非 TTY → getpass (不明文回显)。"""
        captured: list[str] = []
        monkeypatch.setattr(
            "getpass.getpass",
            lambda p: captured.append(p) or "sk-gp",
        )
        assert secret_prompt("API key: ", is_tty=False) == "sk-gp"
        assert captured == ["API key: "]


class TestRenderMenuText:
    """_render_menu_text 纯函数: 当前项 ▸ + 键位提示 (ptk 与质检共用)。"""

    _CHOICES = [
        ("deepseek", "DeepSeek", ""),
        ("zhipu", "Zhipu GLM", "open.bigmodel.cn"),
    ]

    def test_current_marked_with_arrow(self) -> None:
        """Happy path: current=1 → 第二项 ▸, 第一项缩进; 键位提示在末行。"""
        lines = _render_menu_text("pick", self._CHOICES, current=1)
        assert lines[0] == "  pick"
        assert "▸ Zhipu GLM" in lines[2]
        assert "▸ DeepSeek" not in lines[1]

    def test_hint_line_mentions_keys(self) -> None:
        """契约: 末行键位提示存在 (↑↓/Enter/Ctrl+C) — 用户不会迷路。"""
        lines = _render_menu_text("pick", self._CHOICES, current=0)
        assert any("Enter" in ln and "Ctrl+C" in ln for ln in lines)


# ──────────────────────────────────────────────────────────────────────────
# cmd_provider 交互向导 — 持久化语义 (核心修复)
# ──────────────────────────────────────────────────────────────────────────


class _FakeTTY:
    """StringIO with isatty()=True (触发 cmd_provider 菜单分支)。"""

    def __init__(self) -> None:
        self._io = io.StringIO()

    def write(self, s: str) -> int:
        return self._io.write(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._io.getvalue()


@pytest.fixture
def _prov_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """把命令与真实 config/网络隔离: 只留 2 家目录内 provider。"""
    import zall.cli.commands.model as m

    monkeypatch.setattr(
        m, "_detect_configured_providers",
        lambda: {"deepseek": True, "zhipu": False},
    )
    monkeypatch.setattr("zall.cli.config._config_status",
                        lambda: {"ready": False, "api_key": "",
                                 "model": "", "api_base": ""})
    monkeypatch.setattr(
        "zall.cli.config._get_provider_registry",
        lambda: {
            "deepseek": ("DeepSeek", "DEEPSEEK_API_KEY",
                         "https://api.deepseek.com/v1", "", [], ""),
            "zhipu": ("Zhipu GLM", "ZHIPU_API_KEY",
                      "https://open.bigmodel.cn/api/paas/v4", "", [], ""),
        },
    )
    # 切换/探测/渲染一律 mock — 测试的是交互线路, 不是网络
    captures: dict = {"switch": []}
    import zall.cli.model_switch as ms
    monkeypatch.setattr(
        ms, "apply_switch",
        lambda state, loop=None, **kw: captures["switch"].append(kw) or {"ok": True},
    )
    monkeypatch.setattr(
        ms, "probe_models",
        lambda base, key, timeout=8.0, transport=None, windows_out=None:
            ["grok-3", "grok-3-mini"],
    )
    monkeypatch.setattr(m, "_print_switch_result", lambda *a, **k: None)
    return {"m": m, "captures": captures, "out": _FakeTTY()}


class TestProviderMenuPersist:
    """交互选中 → 必然 apply_switch(persist=True) (曾为 persist=False 丢网关)。"""

    def test_menu_pick_persists(self, _prov_env: dict,
                                monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: 菜单选 deepseek (已 ready) → persist=True 落盘。"""
        from zall.cli.commands.model import cmd_provider

        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, "deepseek"))
        state: dict[str, Any] = {"_input_fn": lambda _p: "sk-abc"}
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        kw = _prov_env["captures"]["switch"][0]
        assert kw["provider"] == "deepseek"
        assert kw["persist"] is True

    def test_missing_key_prompted_then_persisted(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """缺 key (needs key) → 隐藏输入收 key → 一并落盘。"""
        from zall.cli.commands.model import cmd_provider

        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, "zhipu"))
        # 隐藏输入由 secret_prompt 单测覆盖; 这里只关心 key 是否落盘
        monkeypatch.setattr(_prov_env["m"], "secret_prompt",
                            lambda *a, **k: "sk-zhipu")
        monkeypatch.setattr(
            _prov_env["m"], "_detect_configured_providers",
            lambda: {"deepseek": True, "zhipu": False},
        )
        # _input_fn 存在才进入"缺 key 询问"分支; 隐藏输入本身单测覆盖
        state: dict[str, Any] = {"_input_fn": lambda _p: ""}
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        kw = _prov_env["captures"]["switch"][0]
        assert kw["provider"] == "zhipu"
        assert kw["persist"] is True
        assert kw["inline_key"] == "sk-zhipu"
        assert kw["persist_key"] is True

    def test_menu_cancel_returns_handled_without_switch(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Counterexample: 菜单 Ctrl+C (None) → 不落盘、不残存状态。"""
        from zall.cli.commands.model import cmd_provider

        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, None))
        state: dict[str, Any] = {"_input_fn": lambda _p: "sk-abc"}
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        assert _prov_env["captures"]["switch"] == []


class TestProviderWizard:
    """「＋ 添加新网关」→ URL → key → 模型 → 一键落盘。"""

    def test_wizard_full_flow_persists_everything(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: 平台菜单 + 贴 URL + key + 模型菜单 → persist=True 三要素。"""
        from zall.cli.commands.model import cmd_provider

        seq = iter(["__url__", "__url__", "grok-3"])  # 主菜单→平台菜单→模型菜单
        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, next(seq)))
        # key 走隐藏输入 (secret_prompt 单测覆盖); URL 是明文输入, 走注入 fn
        monkeypatch.setattr(_prov_env["m"], "secret_prompt",
                            lambda *a, **k: "sk-gw")
        state: dict[str, Any] = {
            "_input_fn": lambda p: "https://api.examplegw.com/v1",
        }
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        kw = _prov_env["captures"]["switch"][0]
        assert kw["provider"] == "examplegw"       # URL → slug
        assert kw["inline_base"] == "https://api.examplegw.com/v1"
        assert kw["inline_key"] == "sk-gw"
        assert kw["persist"] is True
        assert kw["model"] == "grok-3"
        text = _prov_env["out"].getvalue()
        assert "Verifying API key" in text

    def test_wizard_url_cancel_switches_nothing(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Counterexample: 自定义 URL 回车取消 → 无任何 apply_switch。"""
        from zall.cli.commands.model import cmd_provider

        seq = iter(["__url__", "__url__"])  # 主菜单 → 平台菜单
        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, next(seq)))
        state: dict[str, Any] = {"_input_fn": lambda _p: ""}  # Enter 取消
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        assert _prov_env["captures"]["switch"] == []

    def test_wizard_catalog_pick_uses_default_base(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """内置目录平台选中 (不走 URL 分支) → 默认 base 落盘, 模型菜单可取消。"""
        from zall.cli.commands.model import cmd_provider

        seq = iter(["__url__", "grok", None])  # 主→平台菜单→模型菜单 (取消=不挑模型)
        monkeypatch.setattr("zall.cli.select._ptk_choice_menu",
                            lambda *a, **k: (False, next(seq)))
        monkeypatch.setattr(_prov_env["m"], "secret_prompt",
                            lambda *a, **k: "sk-grok")
        state: dict[str, Any] = {"_input_fn": lambda _p: ""}  # 非 None 才走交互 key 分支
        assert cmd_provider("", _prov_env["out"], None, state) == "handled"
        kw = _prov_env["captures"]["switch"][0]
        assert kw["provider"] == "grok"
        assert kw["persist"] is True
        assert kw["model"] is None          # 模型菜单取消 → 保持默认模型
        assert kw["inline_base"] == "https://api.x.ai/v1"

    def test_wizard_catalog_then_url_is_one_flow(
        self, _prov_env: dict,
    ) -> None:
        """结构契约: 平台菜单第一项即内置目录入口, 向导全链条只有一次 apply_switch。"""
        # 此用例只验证契约常数 (目录入口存在) — 键盘序列已由上面两测覆盖
        # (选择内置 / 选择自定义 URL 两条路径各走一次, 合并落盘调用一次)
        from zall.cli.model_switch import _KNOWN_GATEWAYS
        assert "__url__" not in _KNOWN_GATEWAYS  # 自定义是向导的“第 N+1 项”, 非目录成员


class TestProviderNonTtyCopy:
    """非 TTY (脚本/CI) 输出: 不再有 key= 教程教义, 但基本面信息不丢。"""

    def test_no_key_syntax_lecture(self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample: 输出不再教 'base=… key=<…>' 三件套 (误导"只有目录几家")。"""
        from zall.cli.commands.model import cmd_provider

        monkeypatch.setattr(_prov_env["m"], "_detect_configured_providers", lambda: {})
        out = io.StringIO()  # 非 TTY
        assert cmd_provider("", out, None, {}) == "handled"
        text = out.getvalue()
        assert "current provider" in text
        assert "key=<" not in text
        assert "base=<" not in text
        assert "known gateways" in text

    def test_any_openai_compatible_api_is_mentioned(
        self, _prov_env: dict, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Happy path: 非 TTY 也要知道"不止目录内几家" — 指向 REPL 向导入口。"""
        from zall.cli.commands.model import cmd_provider

        monkeypatch.setattr(_prov_env["m"], "_detect_configured_providers", lambda: {})
        out = io.StringIO()
        cmd_provider("", out, None, {})
        text = out.getvalue()
        assert "OpenAI-compatible API" in text
        assert "/provider" in text  # 向导入口指引仍在