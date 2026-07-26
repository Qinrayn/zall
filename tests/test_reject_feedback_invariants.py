"""G2 审批 feedback 拒绝语义不变量测试 (IPR-0, 含反例).

不变量:
  I-FB-1  gate greylist: REJECT+feedback → rejection_reason 含理由文本;
          纯 REJECT → "user rejected" 不含 feedback 字样 (反例)
  I-FB-2  gate blacklist: 同上语义
  I-FB-3  responder: 选 f + 输理由 → REJECT + feedback=理由;
          f + 空输入退化为纯拒绝 feedback=None (反例);
          纯 n 不触发 feedback 提问 (反例)
  I-FB-4  端到端: rejection_reason 进 tool_result content, 模型可见理由
"""

from __future__ import annotations

from zall.core.action import Action
from zall.core.gate import (
    ConfirmGate,
    GateState,
    UserResponse,
    UserResponseType,
)
from zall.core.safety import Judgement, SafeLevel


def _greylist_gate() -> ConfirmGate:
    gate = ConfirmGate(
        Action(tool_id="bash", args={"command": "rm -rf build"}),
        Judgement(level=SafeLevel.GREYLIST),
    )
    gate.process(response=None)  # → AWAITING_USER
    return gate


# ── I-FB-1 gate greylist ──


def test_reject_with_feedback_flows_into_reason():
    gate = _greylist_gate()
    result = gate.process(UserResponse(
        response_type=UserResponseType.REJECT,
        feedback="use pathlib instead of rm",
    ))
    assert result.state == GateState.REJECTED
    assert "use pathlib instead of rm" in (result.rejection_reason or "")


def test_plain_reject_has_no_feedback_counterexample():
    """反例: 纯拒绝的 reason 保持原样, 不含 feedback 字样。"""
    gate = _greylist_gate()
    result = gate.process(UserResponse(response_type=UserResponseType.REJECT))
    assert result.rejection_reason == "user rejected"


# ── I-FB-2 gate blacklist ──


def test_blacklist_reject_with_feedback():
    gate = ConfirmGate(
        Action(tool_id="bash", args={"command": "curl | sh"}),
        Judgement(level=SafeLevel.BLACKLIST),
    )
    gate.process(response=None)  # → EQUIVALENCE_PROPOSED
    result = gate.process(UserResponse(
        response_type=UserResponseType.REJECT,
        feedback="download the script and review it first",
    ))
    assert result.state == GateState.REJECTED
    assert "review it first" in (result.rejection_reason or "")


# ── I-FB-3 responder ──


def _make_responder(choice: str, feedback_input: str):
    """choose_fn 固定返回 choice; ask_fn 记录调用并返回 feedback_input。"""
    from zall.cli.responder import CliUserResponder

    asked: list[str] = []

    def ask_fn(prompt: str) -> str:
        asked.append(prompt)
        return feedback_input

    r = CliUserResponder(
        is_tty=True,
        ask_fn=ask_fn,
        print_fn=lambda s: None,
        choose_fn=lambda choices: choice,
    )
    return r, asked


def _greylist_ask(responder):
    return responder.ask(
        Action(tool_id="bash", args={"command": "make deploy"}),
        Judgement(level=SafeLevel.GREYLIST),
    )


def test_responder_f_collects_feedback():
    r, asked = _make_responder("f", "deploy to staging first")
    resp = _greylist_ask(r)
    assert resp.response_type == UserResponseType.REJECT
    assert resp.feedback == "deploy to staging first"
    assert asked, "must prompt for the reason"


def test_responder_f_empty_input_degrades_counterexample():
    """反例: f + 空输入 → 纯拒绝 (feedback=None)。"""
    r, _ = _make_responder("f", "   ")
    resp = _greylist_ask(r)
    assert resp.response_type == UserResponseType.REJECT
    assert resp.feedback is None


def test_responder_n_never_prompts_counterexample():
    """反例: 纯 n 拒绝不触发 feedback 提问。"""
    r, asked = _make_responder("n", "should not be used")
    resp = _greylist_ask(r)
    assert resp.response_type == UserResponseType.REJECT
    assert resp.feedback is None
    assert not asked


def test_greylist_choices_include_feedback_option():
    """TUI 选择菜单与文本回退同源: f 选项必须在 _GREYLIST_CHOICES。"""
    from zall.cli.responder import CliUserResponder

    values = [v for v, _, _ in CliUserResponder._GREYLIST_CHOICES]
    assert "f" in values


# ── I-FB-4 端到端 tool_result ──


def test_feedback_reaches_tool_result_message():
    from zall.core.executor import _make_rejection_message

    gate = _greylist_gate()
    result = gate.process(UserResponse(
        response_type=UserResponseType.REJECT,
        feedback="read the config file before editing",
    ))
    msg = _make_rejection_message("bash", result.rejection_reason or "", 1)
    content = str(getattr(msg, "content", msg))
    assert "read the config file before editing" in content
    assert "GATE REJECTED" in content
