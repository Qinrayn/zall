"""zall.core.perception.coding_world_model — 轻量级 Coding World Model。

Corresponds to:
  §4.2.3  World Model: 轻量级实现 (coding agent)
  §6.1    Coding Kit: 代码领域的预测模型

设计:
  CodingWorldModel 是轻量级世界模型, 基于:
    1. 代码图静态分析: 如果改这行代码, 哪些符号/测试受影响
    2. 文件影响分析: 文件 A 的修改 → 哪些文件可能受影响
    3. Git 历史经验: 类似修改的历史成功率

  这是一个"轻量级"实现——不依赖神经网络, 不依赖外部服务。
  只做基于代码结构和 git 历史的静态分析预测。

IPR constraints:
  IPR-3: stdlib only, no model SDK
  IPR-0: 预测失败时返回输入状态 (置信度降级), 不崩溃
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from zall.core.action import Action
from zall.core.perception.sensor import Observation, StateEstimate

# 常见测试文件命名模式
_TEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"test_", re.IGNORECASE),
    re.compile(r"_test\.", re.IGNORECASE),
    re.compile(r"spec\.", re.IGNORECASE),
    re.compile(r"tests/", re.IGNORECASE),
    re.compile(r"__tests__/", re.IGNORECASE),
)


def _is_test_file(filepath: str) -> bool:
    """判断文件路径是否为测试文件。"""
    return any(p.search(filepath) for p in _TEST_PATTERNS)


# 常见源文件扩展名 (按语言)
_SOURCE_EXTS: dict[str, frozenset[str]] = {
    "python": frozenset({".py"}),
    "javascript": frozenset({".js", ".jsx", ".ts", ".tsx"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "cpp": frozenset({".cpp", ".c", ".h", ".hpp"}),
}


class CodingWorldModel:
    """轻量级 Coding World Model。

    基于代码结构和 git 历史预测行动后果。
    不依赖外部服务, 不依赖神经网络。

    Usage:
        model = CodingWorldModel(project_root="/path/to/project")
        estimate = model.predict(current_state, action)
        # estimate.state 包含预测后的状态

    预测能力:
      - 文件修改影响范围: 改文件 A 会影响哪些文件/模块
      - 测试预测: 改文件 A 可能导致哪些测试失败
      - 语法风险: 修改是否可能引入语法错误
    """

    __test__ = False  # 防 pytest 收集

    def __init__(
        self,
        project_root: str | None = None,
        model_id: str = "coding_light_v1",
        baseline_modified: int = 0,
    ) -> None:
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._model_id = model_id
        self._history: list[dict[str, Any]] = []
        """历史记录 (用于累积经验)"""

        # 缓存: module -> set of files
        self._module_cache: dict[str, set[str]] | None = None

        # v1.1: 工作区基线 — run 启动时已 modified 的文件数, 用于 anomaly 检测
        self._baseline_modified: int = baseline_modified
        """Run 启动时 git 工作区已修改的文件数基线。"""

    @property
    def baseline_modified(self) -> int:
        return self._baseline_modified

    def set_baseline_modified(self, count: int) -> None:
        """设置 run 启动时的 git modified 文件数基线。"""
        self._baseline_modified = count

    @property
    def model_id(self) -> str:
        return self._model_id

    def predict(self, state: StateEstimate, action: Action) -> StateEstimate:
        """预测行动后果。

        分析策略:
          1. 如果是写文件操作 → 预测影响范围
          2. 如果是 bash 命令 → 预测可能的影响
          3. 其他操作 → 返回当前状态 (置信度不变)

        Args:
            state: 当前状态估计
            action: 要执行的行动

        Returns:
            预测后的状态估计
        """
        tool_id = action.tool_id
        args = action.args

        if tool_id in ("write_file", "edit_file", "batch_edit"):
            return self._predict_file_modification(state, action)
        elif tool_id == "bash" and "git" in args.get("command", ""):
            return self._predict_git_operation(state, action)
        else:
            # 无法预测 → 返回当前状态, 置信度不变
            return StateEstimate(
                state=dict(state.state),
                confidence=state.confidence,
                covariance=dict(state.covariance),
                sources=list(state.sources) + ["world_model"],
                timestamp=state.timestamp,
            )

    def update(self, observation: Observation) -> None:
        """从观测更新世界模型。

        记录观测到的事实, 用于未来预测。
        """
        self._history.append({
            "sensor_id": observation.sensor_id,
            "timestamp": observation.timestamp,
            "data": dict(observation.data),
            "confidence": observation.confidence,
        })

        # 限制历史大小
        if len(self._history) > 1000:
            self._history = self._history[-500:]

    def anomaly(self, state: StateEstimate) -> bool:
        """检测状态是否异常。

        异常条件:
          1. 状态置信度接近 0 (所有传感器失效)
          2. 检测到大量文件修改 (可能是错误操作) — 只计 run 期间新增
          3. 检测到致命错误 (LSP 报告 error)

        v1.1: 只检测 run 期间新增的 modified 文件数, 忽略启动时基线。
              如果 baseline_modified > 50 且无新增, 不触发 anomaly。

        Returns:
            True 如果检测到异常
        """
        s = state.state

        # 1. 置信度异常
        if state.confidence < 0.1 and state.sources != ["none"]:
            return True

        # 2. 大量文件修改异常 — 只计 run 期间新增 (v1.1)
        modified = s.get("modified", []) or s.get("modified_files", [])
        if isinstance(modified, list):
            current_count = len(modified)
            # 若启动时基线就很高, 且运行期间无新增, 不触发
            if self._baseline_modified > 50 and current_count <= self._baseline_modified:
                pass  # 工作区启动时就很脏, 不是 zall 造成的
            elif current_count - self._baseline_modified > 50:
                return True

        # 3. LSP 致命错误
        # C2 fix: LSPSensor 真实输出键是 "errors" (coding_sensors.py:374),
        # 非 "lsp_errors"。旧键名永远读到 0, 使本异常分支从未触发。
        lsp_errors = s.get("errors", 0) or 0
        if isinstance(lsp_errors, (int, float)) and lsp_errors > 100:
            return True

        return False

    # ── Internal prediction methods ──

    def _predict_file_modification(
        self, state: StateEstimate, action: Action
    ) -> StateEstimate:
        """预测文件修改的影响范围。

        分析:
          1. 提取目标文件路径
          2. 查找该文件的导入依赖关系 (哪些文件 import 它)
          3. 预测哪些测试文件可能受影响
          4. 预测语法风险
        """
        args = action.args
        target_path = (
            args.get("path")
            or args.get("file_path")
            or ""
        )
        if not target_path:
            return StateEstimate(
                state=dict(state.state),
                confidence=state.confidence,
                covariance=dict(state.covariance),
                sources=list(state.sources) + ["world_model"],
                timestamp=state.timestamp,
            )

        # 构建预测
        predictions: dict[str, Any] = {}
        predictions["target_file"] = target_path
        predictions["is_test_file"] = _is_test_file(target_path)

        # 预测影响范围 (基于文件扩展名和包结构)
        affected = self._predict_affected_files(target_path)
        predictions["affected_files"] = affected[:20]
        predictions["affected_count"] = len(affected)

        # 预测可能受影响的测试
        affected_tests = [f for f in affected if _is_test_file(f)]
        predictions["affected_tests"] = affected_tests[:10]
        predictions["affected_test_count"] = len(affected_tests)

        # 预测语法风险
        risk = self._estimate_syntax_risk(target_path, action)
        predictions["syntax_risk"] = risk

        # 更新状态的预测部分
        new_state = dict(state.state)
        new_state["prediction"] = predictions

        return StateEstimate(
            state=new_state,
            confidence=state.confidence * 0.8,  # 预测置信度低于观测
            covariance=dict(state.covariance),
            sources=list(state.sources) + ["world_model"],
            timestamp=int(time.time() * 1000),
        )

    def _predict_affected_files(self, target_path: str) -> list[str]:
        """预测哪些文件可能受目标文件修改影响。

        基于项目结构分析:
          - 同模块的文件
          - import 目标文件的文件 (如果有代码图)
          - 对应的测试文件
        """
        affected: set[str] = set()
        target = Path(target_path)
        ext = target.suffix.lower()

        # 1. 同目录文件
        try:
            parent = target.parent
            if parent.is_dir():
                for f in parent.iterdir():
                    if f.is_file() and f.suffix.lower() == ext:
                        affected.add(f.name)
        except OSError:
            pass

        # 2. 对应的测试文件
        stem = target.stem
        test_candidates = [
            f"test_{stem}{ext}",
            f"{stem}_test{ext}",
            f"test_{stem}.py",
            f"{stem}_test.py",
        ]
        try:
            for candidate in test_candidates:
                candidate_path = target.parent / candidate
                if candidate_path.exists():
                    affected.add(str(candidate_path))
        except OSError:
            pass

        # 3. (§4.2.3) codegraph 引用分析已移除: 原实现构造有误 (传 CodeIndex 而非路径),
        #    且按文件路径查符号引用恒为空 — 死代码, 移除以免误导 + 省去无谓全项目索引。
        return sorted(affected)

    def _estimate_syntax_risk(
        self, target_path: str, action: Action
    ) -> dict[str, Any]:
        """估计语法风险。

        基于:
          - 文件扩展名 (某些语言语法更严格)
          - 操作类型 (write_file > edit_file)
          - 文件大小 (大文件风险更高)
        """
        ext = Path(target_path).suffix.lower()
        tool_id = action.tool_id

        risk_score = 0.0

        # 语言风险
        if ext in (".py",):
            risk_score += 0.1  # Python 缩进敏感
        elif ext in (".rs",):
            risk_score += 0.3  # Rust 编译更严格
        elif ext in (".ts", ".tsx"):
            risk_score += 0.2  # TypeScript 类型检查
        else:
            risk_score += 0.05

        # 操作风险
        if tool_id == "write_file":
            risk_score += 0.2  # 全量写入风险更高
        elif tool_id == "edit_file":
            risk_score += 0.1  # 编辑风险中等
        elif tool_id == "batch_edit":
            risk_score += 0.3  # 批量编辑风险最高

        # 文件大小风险
        try:
            size = Path(target_path).stat().st_size
            if size > 10000:
                risk_score += 0.2
            elif size > 5000:
                risk_score += 0.1
        except OSError:
            risk_score += 0.1  # 新文件风险略高

        risk_score = min(risk_score, 1.0)

        return {
            "score": risk_score,
            "level": "high" if risk_score > 0.5 else (
                "medium" if risk_score > 0.2 else "low"
            ),
            "factors": {
                "language": ext,
                "operation": tool_id,
            },
        }

    def _predict_git_operation(
        self, state: StateEstimate, action: Action
    ) -> StateEstimate:
        """预测 Git 操作的影响。

        目前仅返回当前状态 + 标记为已预测。
        Git 操作的影响难以静态预测, 置信度保留。
        """
        new_state = dict(state.state)
        new_state["prediction"] = {
            "git_operation": True,
            "note": "git operation impact cannot be statically predicted",
        }
        return StateEstimate(
            state=new_state,
            confidence=state.confidence,
            covariance=dict(state.covariance),
            sources=list(state.sources) + ["world_model"],
            timestamp=int(time.time() * 1000),
        )

    def get_stats(self) -> dict[str, Any]:
        """返回模型统计信息。"""
        return {
            "model_id": self._model_id,
            "history_size": len(self._history),
            "project_root": str(self._project_root),
        }