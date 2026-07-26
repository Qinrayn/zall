"""zall.core.coordinator — 多 agent 并行编排原语 (MASTER.md §6 Social Kit + §11.2)。

Coordinator 只负责"派活 + 聚合"(参考 Claude Code 的 Coordinator 模式):把一组
独立子任务并行分发给 runner (通常是只读 subagent), 收集结果与错误, 产出结构化
聚合报告。它自己不干活 (角色分离), 也不让单个任务失败拖垮整体。

zall 已有线程级并行 subagent (tools/spawn_subagent.py 的 ThreadPoolExecutor +
list_subagents)。本模块把"批量分发 + 有序聚合 + 容错"抽象为可复用、可测试的核心
原语, 并提供 from_subagent_tool() 把 runner 接到既有 SpawnSubagentTool (真实消费者)。

设计原则:
  - 角色分离: Coordinator 只 dispatch + aggregate, 子任务逻辑由注入的 runner 承担
  - 隔离容错: 单任务异常被捕获为 TaskResult(ok=False, error=...), 不中断其他任务 (IPR-0)
  - 有界并发: max_workers 限制并行度, 防资源耗尽
  - 确定性聚合: 结果严格按任务提交顺序返回 (可复现, 守 §6.1 精神)

IPR constraints:
  IPR-0: invariant tests at tests/test_coordinator_invariants.py (含反例)
  IPR-3: stdlib only (concurrent.futures), 不 import 模型 SDK — runner 由调用方注入
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class CoordinatorTask:
    """一个待分发的子任务 (immutable)。

    task_id: 用于归因/聚合排序的唯一标识 (调用方保证唯一)。
    prompt:  子任务描述 (subagent 的 user_raw)。
    meta:    透传给 runner 的额外参数 (如 subagent_type / write_access)。
    """

    task_id: str
    prompt: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    """单个子任务的执行结果 (immutable)。

    ok=False 时 error 非空; 失败不抛异常, 而是被捕获为结果 (I-10 精神: 负结果同等记录)。
    """

    task_id: str
    ok: bool
    output: str = ""
    error: str | None = None
    elapsed_ms: int = 0


@dataclass(frozen=True)
class CoordinationReport:
    """一次编排的聚合报告 (immutable)。结果按提交顺序排列。"""

    results: tuple[TaskResult, ...]
    elapsed_ms: int

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    def summary(self) -> str:
        return (
            f"coordinator: {self.succeeded}/{self.total} succeeded, "
            f"{self.failed} failed ({self.elapsed_ms}ms)"
        )


# runner: 把一个 CoordinatorTask 变成输出字符串; 失败可抛异常 (由 Coordinator 捕获)。
TaskRunner = Callable[[CoordinatorTask], str]


class Coordinator:
    """多 agent 并行编排器 — 只 dispatch + aggregate (角色分离)。

    Usage:
        coord = Coordinator(runner=my_runner, max_workers=5)
        report = coord.run([CoordinatorTask("t1", "分析 auth 模块"), ...])
        print(report.summary())

    runner 通常是"跑一个只读 subagent 并返回其结论"的可调用对象;
    见 Coordinator.from_subagent_tool() 把它接到既有 SpawnSubagentTool。
    """

    __test__ = False  # 防 pytest 误收 (类名不含 Test, 保险)

    DEFAULT_MAX_WORKERS: int = 5

    def __init__(self, runner: TaskRunner, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {max_workers}")
        self._runner = runner
        self._max_workers = max_workers

    def run(self, tasks: list[CoordinatorTask]) -> CoordinationReport:
        """并行执行所有任务, 返回按提交顺序聚合的报告。

        - 空任务列表 → 空报告 (诚实退让, 不崩溃)。
        - 单任务异常 → 捕获为 TaskResult(ok=False), 不影响其他任务。
        - 结果顺序 == 输入顺序 (确定性可复现)。
        """
        task_list = list(tasks)
        if not task_list:
            return CoordinationReport(results=(), elapsed_ms=0)

        started = time.monotonic()
        workers = min(self._max_workers, len(task_list))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # futures 按提交顺序排列; _run_one 永不抛异常, 故 f.result() 安全且保序。
            futures = [pool.submit(self._run_one, t) for t in task_list]
            results = tuple(f.result() for f in futures)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CoordinationReport(results=results, elapsed_ms=elapsed_ms)

    def _run_one(self, task: CoordinatorTask) -> TaskResult:
        """执行单个任务, 捕获异常为失败结果 (致命信号除外)。"""
        t0 = time.monotonic()
        try:
            output = self._runner(task)
            return TaskResult(
                task_id=task.task_id,
                ok=True,
                output=output or "",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        except (KeyboardInterrupt, SystemExit):
            raise  # 致命信号必须传播, 不得吞 (对齐 loop._emit 的 B5)
        except Exception as e:
            return TaskResult(
                task_id=task.task_id,
                ok=False,
                error=f"{type(e).__name__}: {e}",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

    @classmethod
    def from_subagent_tool(
        cls,
        spawn_tool: Any,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> "Coordinator":
        """用既有 SpawnSubagentTool 构造 Coordinator (真实消费者接线)。

        每个任务同步跑一个 subagent (parallel=False, 由 Coordinator 自己并行调度),
        提取其 ToolResult.output; 子任务失败 (success=False) 抛异常 → 被 run() 捕获。
        """

        def _runner(task: CoordinatorTask) -> str:
            # meta 先入, 必要字段后设 — 防 meta 覆盖 prompt/parallel (Bug6 fix)
            args: dict[str, Any] = dict(task.meta)
            args["prompt"] = task.prompt
            args["parallel"] = False
            res = spawn_tool.execute(args)
            output = getattr(res, "output", "") or ""
            if not getattr(res, "success", True):
                raise RuntimeError(getattr(res, "error", None) or output or "subagent failed")
            return output

        return cls(_runner, max_workers=max_workers)
