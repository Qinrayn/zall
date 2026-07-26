"""确认门可选择菜单 seam test (Part C).

关键安全约束 (DESIGN.md §4.5 + PR-0): choose_fn 只改"怎么收集主选择", 不改
_ask_greylist 的 raw->response 决策映射。这些 test 锁死:
  1. choose_fn 返回 y/n/a/e → 正确映射 ACCEPT/REJECT/always/MODIFY
  2. choose_fn 异常/空 → 安全默认 REJECT (不放行)
  3. --yes / 非 TTY 自动路径不调 choose_fn (行为不变)
  4. 'always' (a) 不豁免 blacklist (PR-0 防线)
  5. choose_fn 优先于 ask_fn (文本回退) 收集主选择

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import pytest

from zall.cli.responder import CliUserResponder
from zall.core.action import Action
from zall.core.gate import UserResponseType
from zall.core.safety import Judgement, SafeLevel


@pytest.fixture(autouse=True)
def _isolate_always_allow(tmp_path, monkeypatch):
    """把 always-allow 持久化重定向到 tmp, 防止真实配置污染 / 测试间串味。"""
    target = tmp_path / "always_allow.json"
    monkeypatch.setattr("zall.cli.responder._always_allow_path", lambda: target)
    yield


def _grey() -> Judgement:
    return Judgement(level=SafeLevel.GREYLIST)


def _black() -> Judgement:
    return Judgement(level=SafeLevel.BLACKLIST)


def _bash(cmd: str = "ls") -> Action:
    return Action(tool_id="bash", args={"command": cmd})


def _responder(choose_value: str, *, ask_seq=None, yes=False, is_tty=True) -> CliUserResponder:
    """构造一个 choose_fn 返回固定值的 responder。ask_seq 供 edit/blacklist 文本路径。"""
    ask_iter = iter(ask_seq or [])

    def _ask(_prompt: str) -> str:
        return next(ask_iter)

    return CliUserResponder(
        yes=yes, is_tty=is_tty,
        ask_fn=_ask, print_fn=lambda _s: None,
        choose_fn=lambda _choices: choose_value,
    )


# ──────────────────────────────────────────────────────────────────────────
# choose_fn → 决策映射 (与文本路径同一真源)
# ──────────────────────────────────────────────────────────────────────────


class TestChooseMapping:
    def test_choice_y_accepts(self) -> None:
        """Happy path: 选 'allow once' (y) → ACCEPT。"""
        r = _responder("y")
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.ACCEPT

    def test_choice_n_rejects(self) -> None:
        """Counterexample: 选 'reject' (n) → REJECT (不误放行)。"""
        r = _responder("n")
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT

    def test_choice_a_always_accepts_and_persists(self) -> None:
        """Happy path: 选 'always allow' (a) → ACCEPT, 且同工具二次调用自动放行。"""
        r = _responder("a")
        resp1 = r.ask(_bash(), _grey())
        assert resp1.response_type == UserResponseType.ACCEPT
        # 二次相同工具 → session allow 命中, 不再调 choose_fn (choose_fn 仍返回 'a' 也无妨)
        # 改用一个会抛异常的 choose_fn 证明二次未经过收集器:
        r._choose_fn = lambda _c: (_ for _ in ()).throw(AssertionError("should not ask again"))
        resp2 = r.ask(_bash(), _grey())
        assert resp2.response_type == UserResponseType.ACCEPT

    def test_choice_e_edit_returns_modify(self) -> None:
        """Happy path: 选 'edit params' (e) → MODIFY + 新 action (走 ask_fn 收集新值)。"""
        # edit 走 _edit_action → 逐参数 ask_fn; bash 只有 command 一个参数
        r = _responder("e", ask_seq=["echo edited"])
        resp = r.ask(_bash("rm -rf /x"), _grey())
        assert resp.response_type == UserResponseType.MODIFY
        assert resp.modified_action is not None
        assert resp.modified_action.args["command"] == "echo edited"

    def test_unknown_choice_rejects(self) -> None:
        """Counterexample: 未知返回值 (如 '') → 落到 default REJECT (安全)。"""
        r = _responder("")
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT


# ──────────────────────────────────────────────────────────────────────────
# choose_fn 健壮性 — 异常/空 → 安全默认
# ──────────────────────────────────────────────────────────────────────────


class TestChooseRobustness:
    def test_choose_fn_exception_defaults_reject(self) -> None:
        """Counterexample: choose_fn 抛异常 → REJECT (绝不误放行)。"""
        def _boom(_choices):
            raise RuntimeError("ui crashed")

        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: "", print_fn=lambda _s: None,
            choose_fn=_boom,
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT

    def test_choose_fn_none_value_defaults_reject(self) -> None:
        """Counterexample: choose_fn 返回 None → 归一为 'n' → REJECT。"""
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: "", print_fn=lambda _s: None,
            choose_fn=lambda _c: None,  # type: ignore[arg-type,return-value]
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT

    def test_choose_fn_priority_over_ask_fn(self) -> None:
        """Happy path: 同时给 choose_fn + ask_fn 时, 主选择走 choose_fn (ask_fn 不被主选择消费)。

        Counterexample: 若实现错用 ask_fn 收集主选择, 这里 ask_fn 返回 'y' 会误 ACCEPT。
        """
        ask_calls = {"n": 0}

        def _ask(_p: str) -> str:
            ask_calls["n"] += 1
            return "y"  # 若被误用作主选择 → ACCEPT (错误)

        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=_ask, print_fn=lambda _s: None,
            choose_fn=lambda _c: "n",  # 主选择: reject
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT  # 走 choose_fn
        assert ask_calls["n"] == 0  # ask_fn 未被主选择调用


# ──────────────────────────────────────────────────────────────────────────
# 自动路径不变 (--yes / 非 TTY 不弹菜单)
# ──────────────────────────────────────────────────────────────────────────


class TestAutoPathsUnchanged:
    def test_yes_auto_accepts_without_choose(self) -> None:
        """Happy path: --yes greylist 自动 ACCEPT, 不调 choose_fn。"""
        called = {"n": 0}

        def _choose(_c):
            called["n"] += 1
            return "n"

        r = CliUserResponder(
            yes=True, is_tty=True,
            ask_fn=lambda _: "", print_fn=lambda _s: None, choose_fn=_choose,
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.ACCEPT
        assert called["n"] == 0  # 未弹菜单

    def test_non_tty_auto_rejects_without_choose(self) -> None:
        """Counterexample: 非 TTY greylist → 自动 REJECT, 不调 choose_fn (非交互不阻塞)。"""
        called = {"n": 0}

        def _choose(_c):
            called["n"] += 1
            return "y"  # 若被调用会误 ACCEPT

        r = CliUserResponder(
            yes=False, is_tty=False,
            ask_fn=lambda _: "", print_fn=lambda _s: None, choose_fn=_choose,
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.REJECT
        assert called["n"] == 0

    def test_no_choose_fn_falls_back_to_text(self) -> None:
        """Happy path: 未注入 choose_fn → 文本回退 (ask_fn) 收集主选择, 决策映射一致。"""
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _: "y", print_fn=lambda _s: None,
            choose_fn=None,
        )
        resp = r.ask(_bash(), _grey())
        assert resp.response_type == UserResponseType.ACCEPT


# ──────────────────────────────────────────────────────────────────────────
# PR-0: 'always' 不豁免 blacklist
# ──────────────────────────────────────────────────────────────────────────


class TestBlacklistNotExempt:
    def test_always_does_not_bypass_blacklist(self) -> None:
        """Counterexample: greylist 选 'always' 后, blacklist 仍需显式 override 理由。

        blacklist 走 ask_fn (override reason), 与 choose_fn 无关; 空理由 → REJECT。
        """
        # choose_fn 给 greylist 返回 'a'; ask_fn 给 blacklist 的 override reason 返回 ''
        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _p: "",  # blacklist override reason = 空 → cancel
            print_fn=lambda _s: None,
            choose_fn=lambda _c: "a",
        )
        # greylist 'a' → 加入 always-allow 集
        r.ask(_bash("ls"), _grey())
        # 不同工具 blacklist → 'a' 不豁免; 空 override → REJECT
        black = Action(tool_id="rm_tool", args={"command": "rm -rf"})
        resp = r.ask(black, _black())
        assert resp.response_type == UserResponseType.REJECT

    def test_blacklist_override_uses_ask_not_choose(self) -> None:
        """Happy path: blacklist 显式非空 override 理由 → OVERRIDE (choose_fn 不参与)。"""
        called = {"n": 0}

        def _choose(_c):
            called["n"] += 1
            return "y"

        r = CliUserResponder(
            yes=False, is_tty=True,
            ask_fn=lambda _p: "I accept the risk",  # 非空 override reason
            print_fn=lambda _s: None, choose_fn=_choose,
        )
        black = Action(tool_id="rm_tool", args={"command": "rm -rf"})
        resp = r.ask(black, _black())
        assert resp.response_type == UserResponseType.OVERRIDE
        assert resp.override_text == "I accept the risk"
        assert called["n"] == 0  # blacklist 不经 choose_fn


# ──────────────────────────────────────────────────────────────────────────
# 选项定义结构契约
# ──────────────────────────────────────────────────────────────────────────


class TestGreylistChoicesContract:
    def test_choice_values_match_decision_keys(self) -> None:
        """结构契约: _GREYLIST_CHOICES 的 value 必须是 _ask_greylist 可识别的 y/n/f/a/e。

        Counterexample: 若某选项 value 拼错 (如 'yes' 而非 'y'), 决策映射会落到 REJECT。
        """
        values = [v for (v, _label, _desc) in CliUserResponder._GREYLIST_CHOICES]
        assert values == ["y", "n", "f", "a", "e"]

    def test_each_choice_has_label_and_desc(self) -> None:
        """结构契约: 每个选项是 (value, label, desc) 三元组, label 非空。"""
        for value, label, desc in CliUserResponder._GREYLIST_CHOICES:
            assert value and label
            assert isinstance(desc, str)
