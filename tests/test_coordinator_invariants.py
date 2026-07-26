"""Coordinator 多 agent 并行编排不变量 (MASTER.md §6 Social Kit + §11.2)。

不变量:
  C-1 有序聚合: 结果顺序 == 输入顺序 (确定性可复现, §6.1 精神)。
  C-2 隔离容错: 单任务异常被捕获为 TaskResult(ok=False), 不中断其他任务 (IPR-0)。
  C-3 诚实退让: 空任务列表 → 空报告, 不崩溃。
  C-4 有界并发: max_workers < 1 → 构造报错; 并行度不超过 max_workers。

IPR-0: 每个测试含 counterexample。
IPR-3: runner 由测试注入, 不依赖模型。
"""

from __future__ import annotations

import threading
import time

import pytest

from zall.core.coordinator import (
    CoordinationReport,
    Coordinator,
    CoordinatorTask,
    TaskResult,
)


def _tasks(n: int) -> list[CoordinatorTask]:
    return [CoordinatorTask(task_id=f"t{i}", prompt=f"task-{i}") for i in range(n)]


# ──────────────────────────────────────────────────────────────────────────
# C-1 有序聚合
# ──────────────────────────────────────────────────────────────────────────


class TestOrderedAggregation:
    def test_results_preserve_input_order(self) -> None:
        """Happy path: 即使并行乱序完成, 结果仍按输入顺序返回。

        Counterexample: 若按完成顺序聚合, 慢任务在前时顺序错乱 → 此断言 fail。
        """

        def runner(task: CoordinatorTask) -> str:
            # 让 t0 最慢, 若按完成序聚合它会排到最后 (反例触发点)
            delay = 0.05 if task.task_id == "t0" else 0.0
            time.sleep(delay)
            return f"done:{task.prompt}"

        coord = Coordinator(runner, max_workers=4)
        report = coord.run(_tasks(4))

        assert [r.task_id for r in report.results] == ["t0", "t1", "t2", "t3"]
        assert report.results[0].output == "done:task-0"
        assert report.total == 4
        assert report.succeeded == 4
        assert report.failed == 0

    def test_all_success_report_counts(self) -> None:
        coord = Coordinator(lambda t: "ok", max_workers=2)
        report = coord.run(_tasks(3))
        assert report.succeeded == 3
        assert report.failed == 0
        assert "3/3 succeeded" in report.summary()


# ──────────────────────────────────────────────────────────────────────────
# C-2 隔离容错 (counterexample-driven)
# ──────────────────────────────────────────────────────────────────────────


class TestFaultIsolation:
    def test_single_failure_does_not_abort_others(self) -> None:
        """Counterexample: 一个任务抛异常, 其余任务仍成功, 整体不崩溃。

        若失败未被隔离 (异常冒泡), run() 会抛出 → 此测试 fail。
        """

        def runner(task: CoordinatorTask) -> str:
            if task.task_id == "t1":
                raise ValueError("boom")
            return "ok"

        coord = Coordinator(runner, max_workers=4)
        report = coord.run(_tasks(3))

        assert report.total == 3
        assert report.succeeded == 2
        assert report.failed == 1
        failed = [r for r in report.results if not r.ok]
        assert len(failed) == 1
        assert failed[0].task_id == "t1"
        assert failed[0].error is not None
        assert "boom" in failed[0].error
        # 失败任务的 output 为空, 成功任务不受影响
        assert report.results[0].ok is True
        assert report.results[2].ok is True

    def test_fatal_signal_propagates(self) -> None:
        """Counterexample: KeyboardInterrupt/SystemExit 必须传播, 不得被吞成失败结果。"""

        def runner(task: CoordinatorTask) -> str:
            raise KeyboardInterrupt()

        coord = Coordinator(runner, max_workers=1)
        with pytest.raises(KeyboardInterrupt):
            coord.run([CoordinatorTask("t0", "x")])


# ──────────────────────────────────────────────────────────────────────────
# C-3 诚实退让
# ──────────────────────────────────────────────────────────────────────────


class TestHonestRetreat:
    def test_empty_tasks_returns_empty_report(self) -> None:
        """Counterexample: 空任务不应崩溃, 应返回空报告 (total=0)。"""
        coord = Coordinator(lambda t: "ok")
        report = coord.run([])
        assert isinstance(report, CoordinationReport)
        assert report.total == 0
        assert report.succeeded == 0
        assert report.failed == 0
        assert report.elapsed_ms == 0


# ──────────────────────────────────────────────────────────────────────────
# C-4 有界并发
# ──────────────────────────────────────────────────────────────────────────


class TestBoundedConcurrency:
    def test_max_workers_below_one_raises(self) -> None:
        """Counterexample: max_workers < 1 无意义 → 构造必须报错。"""
        with pytest.raises(ValueError):
            Coordinator(lambda t: "ok", max_workers=0)

    def test_concurrency_never_exceeds_max_workers(self) -> None:
        """并行度不超过 max_workers (探针记录峰值并发)。

        Counterexample: 若无并发上限, 峰值并发会 == 任务数 (>max_workers) → fail。
        """
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        def runner(task: CoordinatorTask) -> str:
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.02)
            with lock:
                state["active"] -= 1
            return "ok"

        coord = Coordinator(runner, max_workers=2)
        report = coord.run(_tasks(6))
        assert report.succeeded == 6
        assert state["peak"] <= 2, f"peak concurrency {state['peak']} exceeded max_workers=2"


# ──────────────────────────────────────────────────────────────────────────
# from_subagent_tool: 真实消费者接线
# ──────────────────────────────────────────────────────────────────────────


class TestFromSubagentTool:
    def test_delegates_to_spawn_tool_and_extracts_output(self) -> None:
        """Happy path: runner 委托给 spawn_tool.execute 并提取 output。"""

        class _FakeResult:
            def __init__(self, success: bool, output: str, error: str | None = None) -> None:
                self.success = success
                self.output = output
                self.error = error

        class _FakeSpawnTool:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def execute(self, args: dict) -> _FakeResult:
                self.calls.append(args)
                if "fail" in args["prompt"]:
                    return _FakeResult(False, "", error="subagent error")
                return _FakeResult(True, f"result:{args['prompt']}")

        spawn = _FakeSpawnTool()
        coord = Coordinator.from_subagent_tool(spawn, max_workers=3)
        report = coord.run([
            CoordinatorTask("t0", "analyze A"),
            CoordinatorTask("t1", "fail B"),
            CoordinatorTask("t2", "analyze C"),
        ])

        assert report.total == 3
        assert report.succeeded == 2
        assert report.failed == 1
        # spawn 被调用时 parallel=False (由 Coordinator 自己并行调度)
        assert all(c["parallel"] is False for c in spawn.calls)
        # 失败任务捕获了子 agent 的 error
        t1 = next(r for r in report.results if r.task_id == "t1")
        assert t1.ok is False
        assert t1.error is not None and "subagent error" in t1.error
