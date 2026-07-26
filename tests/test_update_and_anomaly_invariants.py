"""E2E 轮修复的不变量测试 (2026-07-26).

覆盖三项真实 API e2e 发现的缺陷:
  1. 依赖混淆防护: PyPI 上的 `zall` 是同名陌生包 — dev/本地安装绝不
     提示更新、绝不执行 pip upgrade (update.py _is_dev_install)。
  2. anomaly 基线初始化顺序: _project_root 必须先于
     _init_baseline_modified 赋值 (loop.py), 否则脏仓库下每步误报。
  3. anomaly 渲染去重: 只在 False→True 翻转沿打印一次 (render.py)。

IPR-0: 每个测试含反例。
"""

from __future__ import annotations

import io
import json
from typing import Any

from zall.cli import update as update_mod


# ══════════════════════════════════════════════════════════════════════════
# 1. 依赖混淆防护 (update.py)
# ══════════════════════════════════════════════════════════════════════════


class TestDevInstallGuard:
    """PyPI 'zall' 非本项目 — dev 安装必须完全绕过更新链路。"""

    def test_source_run_is_dev(self, monkeypatch) -> None:
        """未经 pip 安装 (metadata 抛异常) → 保守判定 dev。"""
        from importlib import metadata

        def _raise(name):
            raise metadata.PackageNotFoundError(name)

        monkeypatch.setattr(metadata, "distribution", _raise)
        assert update_mod._is_dev_install() is True

    def test_editable_and_file_url_are_dev(self, monkeypatch) -> None:
        """editable / file:// 安装 → dev; 反例: PyPI 正式安装 → 非 dev。"""
        from importlib import metadata

        class _Dist:
            def __init__(self, direct: str | None) -> None:
                self._direct = direct

            def read_text(self, name: str) -> str | None:
                return self._direct

        # editable 安装
        monkeypatch.setattr(
            metadata, "distribution",
            lambda n: _Dist(json.dumps(
                {"url": "file:///c:/Users/x/zall", "dir_info": {"editable": True}})),
        )
        assert update_mod._is_dev_install() is True
        # file:// 非 editable
        monkeypatch.setattr(
            metadata, "distribution",
            lambda n: _Dist(json.dumps({"url": "file:///tmp/zall"})),
        )
        assert update_mod._is_dev_install() is True
        # 反例: PyPI wheel 安装 (无 direct_url.json) → 非 dev
        monkeypatch.setattr(metadata, "distribution", lambda n: _Dist(None))
        assert update_mod._is_dev_install() is False

    def test_check_for_update_suppressed_for_dev(self, monkeypatch) -> None:
        """dev 安装: 即便 PyPI 有 '更新' 也返回 has_update=False。"""
        monkeypatch.setattr(update_mod, "_is_dev_install", lambda: True)
        monkeypatch.setattr(update_mod, "get_current_version", lambda: "0.0.1")
        monkeypatch.setattr(update_mod, "_fetch_latest_version", lambda: "9.9.9")
        result = update_mod.check_for_update(force=True)
        assert result["has_update"] is False
        assert result["latest"] is None

    def test_check_for_update_normal_install_counterexample(self, monkeypatch, tmp_path) -> None:
        """反例: 正式安装的更新检查照常工作 (防护不误伤)。"""
        monkeypatch.setattr(update_mod, "_is_dev_install", lambda: False)
        monkeypatch.setattr(update_mod, "get_current_version", lambda: "0.0.1")
        monkeypatch.setattr(update_mod, "_fetch_latest_version", lambda: "0.0.2")
        monkeypatch.setattr(update_mod, "_cache_path",
                            lambda: str(tmp_path / "update_cache.json"))
        result = update_mod.check_for_update(force=True)
        assert result["has_update"] is True
        assert result["latest"] == "0.0.2"

    def test_perform_update_refuses_dev_without_pip(self, monkeypatch) -> None:
        """dev 安装: perform_update 直接拒绝, 绝不触碰 pip。"""
        import subprocess

        monkeypatch.setattr(update_mod, "_is_dev_install", lambda: True)

        def _boom(*a, **k):
            raise AssertionError("pip must not be invoked for dev install")

        monkeypatch.setattr(subprocess, "run", _boom)
        buf = io.StringIO()
        assert update_mod.perform_update(buf) is False
        assert "disabled" in buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# 2. anomaly 基线初始化顺序 (loop.py)
# ══════════════════════════════════════════════════════════════════════════


class TestBaselineInitOrder:
    """_project_root 必须先于 _init_baseline_modified 赋值。

    旧顺序下 AttributeError 被吞, 基线恒 0 — 脏仓库 (>50 存量改动)
    每步误报 anomaly。用源码顺序守卫 (不依赖 git 环境)。
    """

    def test_project_root_assigned_before_baseline_init(self) -> None:
        """源码守卫: _project_root 赋值行必须出现在 _init_baseline_modified() 调用之前。"""
        import inspect

        from zall.core.loop import AgentLoop

        src = inspect.getsource(AgentLoop.__init__)
        assign_pos = src.find("self._project_root: str =")
        call_pos = src.find("self._init_baseline_modified()")
        assert assign_pos != -1, "_project_root 赋值不见了"
        assert call_pos != -1, "_init_baseline_modified() 调用不见了"
        assert assign_pos < call_pos, (
            "_project_root 必须在 _init_baseline_modified() 之前赋值, "
            "否则 cwd=self._project_root 抛 AttributeError 被吞, 基线恒 0"
        )

    def test_no_duplicate_project_root_assignment(self) -> None:
        """反例守卫: __init__ 内 _project_root 只赋值一次 (防回归重复赋值)。"""
        import inspect

        from zall.core.loop import AgentLoop

        src = inspect.getsource(AgentLoop.__init__)
        assert src.count("self._project_root: str =") == 1


# ══════════════════════════════════════════════════════════════════════════
# 3. anomaly 渲染去重 (render.py)
# ══════════════════════════════════════════════════════════════════════════


class _TtyBuf(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102
        return True


def _make_tty_renderer():
    from zall.cli.render import CliRenderer

    buf = _TtyBuf()
    r = CliRenderer(stream=buf, disable_spinner=True)
    r._is_tty = True  # 强制 TTY 路径 (StringIO isatty 已 True, 双保险)
    return r, buf


def _perception_event(anomaly: bool, step: int = 1) -> Any:
    from zall.core.loop_events import LoopEvent

    return LoopEvent(
        kind="perception_state",
        step=step,
        payload={"confidence": 0.95, "anomaly": anomaly},
    )


class TestAnomalyRenderDedup:
    """anomaly 警告只在 False→True 翻转沿打印一次。"""

    def test_sustained_anomaly_prints_once(self) -> None:
        """持续异常 5 步 → 只打印 1 条 (旧行为打 5 条刷屏)。"""
        r, buf = _make_tty_renderer()
        for i in range(5):
            r(_perception_event(True, step=i))
        assert buf.getvalue().count("anomaly detected") == 1

    def test_no_anomaly_prints_nothing_counterexample(self) -> None:
        """反例: 无异常 → 一条也不打。"""
        r, buf = _make_tty_renderer()
        for i in range(3):
            r(_perception_event(False, step=i))
        assert "anomaly detected" not in buf.getvalue()

    def test_reprints_on_new_rising_edge(self) -> None:
        """恢复正常后再次异常 → 第二条 (翻转沿语义, 非只打一次)。"""
        r, buf = _make_tty_renderer()
        r(_perception_event(True, step=1))
        r(_perception_event(False, step=2))
        r(_perception_event(True, step=3))
        assert buf.getvalue().count("anomaly detected") == 2
