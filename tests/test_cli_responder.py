"""CliUserResponder invariant test (§4.5 + §6.4 + PR-0).

IPR-0: each test must contain a counterexample.

Protected core invariants (PR-0 defense line):
  1. blacklist 在 --yes 模式下仍 REJECT (--yes 绝不放行 blacklist)
  2. blacklist non- TTY 永远 REJECT
  3. blacklist override 需non-空理由 (空理由 → reject)
  4. greylist --yes 自动 ACCEPT
  5. greylist non- TTY 默认 REJECT
"""

from __future__ import annotations

from zall.core.action import Action
from zall.core.gate import UserResponseType
from zall.core.safety import Judgement, SafeLevel
from zall.cli.responder import CliUserResponder


def _grey_judgement() -> Judgement:
    return Judgement(
        level=SafeLevel.GREYLIST,
        matched_rule_ids=("grey_1",),
    )


def _black_judgement() -> Judgement:
    return Judgement(
        level=SafeLevel.BLACKLIST,
        matched_rule_ids=("core_deny_x",),
    )


def _action() -> Action:
    return Action(tool_id="bash", args={"command": "rm -rf /tmp/x"})


# ──────────────────────────────────────────────────────────────────────────
# greylist
# ──────────────────────────────────────────────────────────────────────────


class TestGreylist:
    def test_yes_mode_auto_accept(self) -> None:
        """Happy path: --yes pattern greylist 自动 ACCEPT."""
        r = CliUserResponder(yes=True, is_tty=True)
        resp = r.ask(_action(), _grey_judgement())
        assert resp.response_type == UserResponseType.ACCEPT

    def test_non_tty_default_reject(self) -> None:
        """Counterexample: greylist non- TTY default REJECT (不blockingpipeline/CI)."""
        r = CliUserResponder(yes=False, is_tty=False)
        resp = r.ask(_action(), _grey_judgement())
        assert resp.response_type == UserResponseType.REJECT

    def test_interactive_yes(self) -> None:
        """Happy path: 交互式input y → ACCEPT."""
        answers = iter(["y"])
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=lambda _: next(answers),
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _grey_judgement())
        assert resp.response_type == UserResponseType.ACCEPT

    def test_interactive_default_reject(self) -> None:
        """Counterexample: 交互式空input → REJECT (defaultsecurity)."""
        answers = iter([""])
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=lambda _: next(answers),
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _grey_judgement())
        assert resp.response_type == UserResponseType.REJECT

    def test_interactive_suspend(self) -> None:
        """Happy path: input s → TIMEOUT (suspend)."""
        answers = iter(["s"])
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=lambda _: next(answers),
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _grey_judgement())
        assert resp.response_type == UserResponseType.TIMEOUT


# ──────────────────────────────────────────────────────────────────────────
# blacklist (PR-0 defense line — 最关键)
# ──────────────────────────────────────────────────────────────────────────


class TestBlacklist:
    def test_yes_mode_still_rejects_blacklist(self) -> None:
        """Counterexample: --yes pattern下 blacklist 仍 REJECT (PR-0 defense line).

        这是整个 responder 最重要的invariant:
          --yes 是便利开关, not安全开关.
          如果 --yes 能放行 blacklist, 等于 agent 可自动执行 rm -rf / push --force.
        """
        r = CliUserResponder(yes=True, is_tty=True, print_fn=lambda _: None)
        resp = r.ask(_action(), _black_judgement())
        assert resp.response_type == UserResponseType.REJECT

    def test_non_tty_always_reject(self) -> None:
        """Counterexample: blacklist non- TTY 永远 REJECT."""
        r = CliUserResponder(yes=False, is_tty=False)
        resp = r.ask(_action(), _black_judgement())
        assert resp.response_type == UserResponseType.REJECT

    def test_override_empty_reason_rejects(self) -> None:
        """Counterexample: override 空理由 → REJECT (§6.4 override_text 须non-空)."""
        answers = iter([""])
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=lambda _: next(answers),
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _black_judgement())
        assert resp.response_type == UserResponseType.REJECT

    def test_override_with_reason(self) -> None:
        """Happy path: override non-空理由 → OVERRIDE + override_text non-空 (§6.4 audit)."""
        answers = iter(["need to clean tmp dir for the test"])
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=lambda _: next(answers),
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _black_judgement())
        assert resp.response_type == UserResponseType.OVERRIDE
        assert resp.override_text is not None
        assert len(resp.override_text) > 0

    def test_override_eof_rejects(self) -> None:
        """Counterexample: override 时 EOF/Ctrl-D → REJECT (does not crash)."""
        def boom(_: str) -> str:
            raise EOFError
        r = CliUserResponder(yes=False, is_tty=True, ask_fn=boom,
                             print_fn=lambda _: None)
        resp = r.ask(_action(), _black_judgement())
        assert resp.response_type == UserResponseType.REJECT


# ──────────────────────────────────────────────────────────────────────────
# whitelist fallback
# ──────────────────────────────────────────────────────────────────────────


class TestWhitelistFallback:
    def test_whitelist_unexpected_reject(self) -> None:
        """Counterexample: WHITELIST 不该到 ask(); 到了 → 保守 REJECT.

        WHITELIST 在 gate._initial_dispatch 里directly EXECUTING, 不调 ask().
        如果 responder 收到 whitelist judgement, 说明调用方有 bug → 保守 reject.
        """
        white_j = Judgement(level=SafeLevel.WHITELIST, matched_rule_ids=("w1",))
        r = CliUserResponder(yes=True, is_tty=True)
        resp = r.ask(_action(), white_j)
        assert resp.response_type == UserResponseType.REJECT
