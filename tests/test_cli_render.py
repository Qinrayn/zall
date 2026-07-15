"""CLI render invariant test (§6.1 presentation layer projection).

IPR-0: each test must contain a counterexample.

Protected core invariants:
  1. text 模式: 每个 LoopEvent 产出 ≥1 行输出 (不丢事件)
  2. json 模式: 每事件输出一行valid JSON, 含 kind/step/payload
  3. json 模式: 不论 payload 内容, 输出仍是valid JSON (does not crash)
  4. 渲染器异常不传播 (observer 契约: 呈现层故障不得影响语义 ——
     不过 AgentLoop._emit 已吞异常, 这里测渲染器自身稳健性)
"""

from __future__ import annotations

import io
import json

from zall.core.loop import LoopEvent
from zall.cli.render import CliRenderer, render_egress_summary, render_goal_card


def _ev(kind: str, step: int = 1, **payload) -> LoopEvent:
    return LoopEvent(kind=kind, step=step, payload=payload)


# ──────────────────────────────────────────────────────────────────────────
# text pattern
# ──────────────────────────────────────────────────────────────────────────


class TestTextMode:
    def test_each_event_produces_output(self) -> None:
        """Happy path: 每种 LoopEvent kind 都产出 ≥1 行."""
        kinds = [
            "model_call", "model_token", "gate_decision", "tool_call_start",
            "tool_call_end", "tool_rejected", "override", "judge_result",
            "runaway", "length_exceeded", "error",
        ]
        for kind in kinds:
            buf = io.StringIO()
            r = CliRenderer(json_mode=False, stream=buf)
            r(_ev(kind, step=1, content="x", tool_id="t", args={}, success=True,
                  level="greylist", state="met", output="ok", error="e", report="r",
                  token="tok"))
            out = buf.getvalue()
            assert len(out.strip()) > 0 or out, f"kind={kind} 产出空输出"

    def test_model_call_shows_content(self) -> None:
        """Happy path: model_call 显示 content digest."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_call", step=1, content="let me read the file", stop_reason="tool_use",
              tool_calls=[]))
        out = buf.getvalue()
        assert "let me read" in out
        assert "step 1" in out

    def test_tool_end_shows_success_icon(self) -> None:
        """Happy path: tool_call_end success=True 显示 ✓."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True, output="done"))
        assert "✓" in buf.getvalue()

    def test_tool_end_shows_fail_icon(self) -> None:
        """Counterexample: tool_call_end success=False 显示 ✗ (not ✓)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=False, output="err",
              error="boom"))
        assert "✗" in buf.getvalue()
        assert "✓" not in buf.getvalue()

    def test_gate_greylist_shows_question(self) -> None:
        """Happy path: greylist 显示 ⚠."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("gate_decision", step=1, tool_id="bash", level="greylist", matched_rules=[]))
        assert "\u26a0" in buf.getvalue()

    def test_gate_blacklist_shows_bang(self) -> None:
        """Happy path: blacklist 显示 ✗."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("gate_decision", step=1, tool_id="bash", level="blacklist", matched_rules=["x"]))
        assert "\u2717" in buf.getvalue()
        assert "BLACKLIST" in buf.getvalue()

    def test_judge_met_shows_filled_circle(self) -> None:
        """Happy path: judge met 显示 ●."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("judge_result", step=1, state="met", report="all good"))
        assert "●" in buf.getvalue()
        assert "met" in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# json pattern
# ──────────────────────────────────────────────────────────────────────────


class TestJsonMode:
    def test_each_event_is_valid_json_line(self) -> None:
        """Happy path: 每eventoutput一行valid JSON, 含 kind/step/payload."""
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("model_call", step=1, content="hi", stop_reason="stop", tool_calls=[]))
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True, output="ok"))

        lines = [l for l in buf.getvalue().strip().split("\n") if l]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)  # 不 raise 即valid JSON
            assert "kind" in obj
            assert "step" in obj
            assert "payload" in obj

    def test_json_with_special_chars(self) -> None:
        """Counterexample: payload 含特殊字符 (引号/换行) → 仍是valid JSON (does not crash).

        如果渲染器不做 json.dumps, 特殊字符会破坏 JSON 结构.
        """
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True,
              output='line1\nline2 "quoted" {json}'))
        obj = json.loads(buf.getvalue().strip())
        assert obj["payload"]["output"] == 'line1\nline2 "quoted" {json}'

    def test_json_unicode(self) -> None:
        """Counterexample: payload 含non- ASCII (中文) → JSON does not crash且preserve."""
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("model_call", step=1, content="读取文件", stop_reason="stop", tool_calls=[]))
        obj = json.loads(buf.getvalue().strip())
        assert "读取文件" in obj["payload"]["content"]


# ──────────────────────────────────────────────────────────────────────────
# egress summary
# ──────────────────────────────────────────────────────────────────────────


class TestEgressSummary:
    def test_summary_contains_state_and_counts(self) -> None:
        """Happy path: summary 含 final_state / steps / tools / models."""
        buf = io.StringIO()
        render_egress_summary(
            run_id="abc", final_state="met", step_count=3, tool_calls=2,
            model_calls=3, error=None, session_dir="/tmp/s", stream=buf,
        )
        out = buf.getvalue()
        assert "met" in out
        assert "3" in out  # steps
        assert "2" in out  # tools
        assert "/tmp/s" in out

    def test_summary_with_error(self) -> None:
        """Counterexample: error non-空 → summary 显示 ✗ + error."""
        buf = io.StringIO()
        render_egress_summary(
            run_id="abc", final_state="undecidable", step_count=1, tool_calls=0,
            model_calls=1, error="something broke", session_dir=None, stream=buf,
        )
        out = buf.getvalue()
        assert "✗" in out
        assert "something broke" in out


# ──────────────────────────────────────────────────────────────────────────
# model_token streaming (P2)
# ──────────────────────────────────────────────────────────────────────────


class TestModelTokenStreaming:
    def test_token_printed_inline(self) -> None:
        """Happy path: model_token 逐个 inline 打印 (不换行)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_token", step=1, token="Hel", accumulated="Hel"))
        r(_ev("model_token", step=1, token="lo", accumulated="Hello"))
        out = buf.getvalue()
        # 两个 token 拼在一起, 不换行
        assert "Hello" in out
        assert out.count("\n") == 0  # token 之间不换行

    def test_first_token_has_prefix(self) -> None:
        """Happy path: 首个 token 加 step 前缀."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_token", step=1, token="Hi", accumulated="Hi"))
        out = buf.getvalue()
        assert "step 1" in out
        assert "Hi" in out

    def test_model_call_after_tokens_only_newline(self) -> None:
        """Happy path: streaming显示过 token 后, model_call 只补换行 (不重复显示 content)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_token", step=1, token="Hello", accumulated="Hello"))
        r(_ev("model_call", step=1, content="Hello", stop_reason="stop", tool_calls=[]))
        out = buf.getvalue()
        # model_call 不应重复打印 content (non- TTY pattern, 不走 Markdown)
        assert out.count("Hello") == 1

    def test_model_call_without_tokens_shows_summary(self) -> None:
        """Counterexample: 无 token streaming时, model_call 显示 content digest (P1 行for)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_call", step=1, content="let me read", stop_reason="tool_use",
              tool_calls=[]))
        out = buf.getvalue()
        assert "let me read" in out
        assert "step 1" in out

    def test_json_mode_emits_token_lines(self) -> None:
        """Happy path: json pattern下 model_token 也output NDJSON 行."""
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("model_token", step=1, token="Hi", accumulated="Hi"))
        obj = json.loads(buf.getvalue().strip())
        assert obj["kind"] == "model_token"
        assert obj["payload"]["token"] == "Hi"


# ──────────────────────────────────────────────────────────────────────────
# TTY downgrade (P3: non- TTY 纯文本, TTY 彩色)
# ──────────────────────────────────────────────────────────────────────────


class TestTtyFallback:
    def test_non_tty_no_ansi_codes(self) -> None:
        """Counterexample: non- TTY (StringIO) output不含 ANSI 转义码.

        StringIO.isatty() == False → rich 自动降级, 不输出色彩码.
        如果non- TYY 还输出 ANSI, 管道/CI 会看到乱码.
        """
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True, output="ok"))
        r(_ev("judge_result", step=1, state="met", report="ok"))
        out = buf.getvalue()
        # 不含 ANSI 转义serial (以 \x1b[ 开头)
        assert "\x1b[" not in out

    def test_non_tty_still_has_content(self) -> None:
        """Happy path: non- TTY downgrade后仍有content (纯文本, 含tool名 Claude Code 式大写)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True, output="hello"))
        out = buf.getvalue()
        assert "Bash" in out or "bash" in out  # 显示名或 tool_id
        assert "hello" in out

    def test_json_mode_unaffected_by_tty(self) -> None:
        """Happy path: json pattern不受 TTY 影响 (始终 NDJSON)."""
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("model_call", step=1, content="hi", stop_reason="stop", tool_calls=[]))
        obj = json.loads(buf.getvalue().strip())
        assert obj["kind"] == "model_call"
        # 不含 ANSI
        assert "\x1b[" not in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# 紧凑tool展示 + verbose (新增)
# ──────────────────────────────────────────────────────────────────────────


class TestCompactToolEnd:
    def test_compact_shows_icon_and_chars(self) -> None:
        """Happy path: default紧凑pattern显示 ✓ + tool名 + digest."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)  # verbose=False 默认
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True,
              output="hello world"))
        out = buf.getvalue()
        assert "✓" in out
        assert "Bash" in out or "bash" in out  # Claude Code 式显示名

    def test_compact_fail_shows_x(self) -> None:
        """Counterexample: 紧凑patternfail显示 ✗ (not ✓)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=False,
              output="err", error="boom"))
        out = buf.getvalue()
        assert "✗" in out
        assert "✓" not in out

    def test_verbose_shows_full_output(self) -> None:
        """Happy path: verbose=True 显示完整output (含 Panel title).

        non- TTY 降级: verbose non- TTY 仍显示首行, 但语义是"完整模式".
        这里verify verbose 下输出不含 "chars" 紧凑标记.
        """
        buf = io.StringIO()
        r = CliRenderer(stream=buf, verbose=True)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True,
              output="line1\nline2\nline3"))
        out = buf.getvalue()
        assert "✓" in out
        # verbose non- TTY 显示首行, 紧凑pattern有 "chars" 后缀; verbose 无
        assert "chars" not in out

    def test_set_verbose_toggles(self) -> None:
        """Happy path: set_verbose(True) 后切换到完整output."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r.set_verbose(True)
        r(_ev("tool_call_end", step=1, tool_id="bash", success=True,
              output="hello"))
        out = buf.getvalue()
        assert "✓" in out
        assert "chars" not in out  # verbose 模式无紧凑标记


# ──────────────────────────────────────────────────────────────────────────
# Goal 卡片 (v0.0.20 Bug A 回归: non- TTY 不得leak rich markup)
# ──────────────────────────────────────────────────────────────────────────


class TestGoalCard:
    def _goal(self):
        from zall.core.goal import (
            AcceptanceContract,
            GoalStatement,
            GoalTriple,
            GoalType,
            TerminationState,
        )

        class _Term:
            exposed_dependency_set = None

            def __call__(self, state):
                return TerminationState.UNDECIDABLE

        return GoalTriple(
            statement=GoalStatement(
                intent="hello world",
                rewriting="hello world",
                rewrite_confidence=1.0,
                goal_type=GoalType.UNKNOWN,
            ),
            termination=_Term(),
            acceptance=AcceptanceContract(baseline_frozen_at="test"),
        )

    def test_non_tty_strips_markup(self) -> None:
        """Counterexample (Bug A): non- TTY output不得含 rich markup 字面量标签.

        旧实现 out.write(lines[0]) 把 [bold yellow]Goal[/] 原样写出 → 管道/脚本
        里是字面量.修复后走 console 渲染剥离.
        """
        buf = io.StringIO()  # isatty() False
        render_goal_card(self._goal(), "none", buf)
        out = buf.getvalue()
        assert "Goal" in out
        assert "hello world" in out
        # 不得leak任何 rich markup 标签
        assert "[bold" not in out
        assert "[/]" not in out
        assert "[cyan" not in out
        assert "[dim" not in out

    def test_non_tty_no_ansi(self) -> None:
        """Counterexample: non- TTY Goal 卡片不含 ANSI 转义码."""
        buf = io.StringIO()
        render_goal_card(self._goal(), "none", buf)
        assert "\x1b[" not in buf.getvalue()
