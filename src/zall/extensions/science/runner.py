"""zall.extensions.science.runner — 科研模块运行器 (Argus runner 对标, 原创实现)。

两种执行模式:
  - in-process: 直调 core.proof_gate 认证器 (交互场景, 秒级, 零子进程开销)
  - script:     subprocess 跑 experiments/ 驱动脚本, 流式回显 (Argus execute_script
                对标), 完成后读 artifact 报告 JSON, 按目录 report_tier_path 提取 tier

分级哲学差异 (Proof Gate 精神贯彻到运行器): Argus 用正则扫输出猜 severity;
zall 的 tier 只来自验证器输出 (ProofCertificate.tier / 报告 tier 字段) —
**分级即证明**, 绝不从字符串猜认识论状态。

IPR-3: 本模块不 import 任何模型 SDK。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

_REPO_ROOT = Path(__file__).resolve().parents[4]

# 覆盖系统预设 (演示/交互两用; erdos1950 = 经典覆盖, nearmiss = 去掉 23:24
# 后存在未覆盖剩余类 23 → 必 REFUTED, 用于展示反例输出)
COVERING_PRESETS: dict[str, list[tuple[int, int]]] = {
    "erdos1950": [(0, 2), (0, 3), (1, 4), (3, 8), (7, 12), (23, 24)],
    "nearmiss": [(0, 2), (0, 3), (1, 4), (3, 8), (7, 12)],
}

_VALID_TIERS = frozenset({"proven", "corroborated", "refuted", "unknown"})


@dataclass
class RunOutcome:
    """一次模块运行的结果 (tier 分级 + 人类可读依据 + 机器可读产物)。"""

    module_id: str
    name: str
    tier: str = "unknown"          # proven/corroborated/refuted/unknown/error
    claim: str = ""
    detail: str = ""
    output: str = ""               # script 模式的流式输出全文 (in-process 为空)
    seconds: float = 0.0
    report_path: str | None = None
    ok: bool = True                # 执行本身是否成功 (tier=refuted 也是 ok=True: 证伪是结果不是故障)
    options: dict[str, str] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.tier == "error" or not self.ok


# ── 选项解析 ──


def parse_congruences(spec: str) -> list[tuple[int, int]] | None:
    """"a:m,a:m,..." → [(a, m), ...]; 解析失败返回 None (调用方报错, 不猜)。"""
    out: list[tuple[int, int]] = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            return None
        a, _, m = part.partition(":")
        try:
            ai, mi = int(a.strip()), int(m.strip())
        except ValueError:
            return None
        if mi <= 0:
            return None
        out.append((ai % mi, mi))
    return out or None


def parse_poly(spec: str) -> list[int] | None:
    """"c0,c1,c2" (低次→高次) → [c0, c1, c2]; 失败返回 None。"""
    try:
        coeffs = [int(c.strip()) for c in spec.replace(";", ",").split(",") if c.strip()]
    except ValueError:
        return None
    return coeffs or None


def _opt(options: dict[str, str], *names: str, default: str = "") -> str:
    """取选项值 (连字符/下划线两种写法都认)。"""
    for n in names:
        for variant in (n, n.replace("-", "_"), n.replace("_", "-")):
            v = options.get(variant)
            if v:
                return str(v)
    return default


# ── in-process certifier ──


def run_certifier(mod: Any, options: dict[str, str]) -> RunOutcome:
    """直调 proof_gate 认证器; tier/claim/detail 取自 ProofCertificate。"""
    from zall.core import proof_gate

    base = RunOutcome(module_id=mod.id, name=mod.name, options=dict(options))
    fn = getattr(proof_gate, mod.certifier, None)
    if fn is None:
        base.tier, base.ok = "error", False
        base.detail = f"unknown certifier: {mod.certifier}"
        return base
    t0 = time.monotonic()
    try:
        if mod.certifier == "verify_covering_system":
            preset = _opt(options, "preset").lower()
            spec = _opt(options, "congruences")
            if spec:
                congruences = parse_congruences(spec)
                if congruences is None:
                    base.tier, base.ok = "error", False
                    base.detail = f"cannot parse congruences: {spec!r} (want a:m,a:m,...)"
                    return base
            elif preset in COVERING_PRESETS:
                congruences = COVERING_PRESETS[preset]
            else:
                congruences = COVERING_PRESETS["erdos1950"]  # 默认跑经典覆盖
            cert = fn(congruences, claim=f"covering system {congruences} covers every integer")
        elif mod.certifier == "verify_square_identity":
            spec = _opt(options, "poly")
            poly = parse_poly(spec) if spec else None
            if poly is None:
                base.tier, base.ok = "error", False
                base.detail = f"need poly=c0,c1,... (got {spec!r})"
                return base
            bound_s = _opt(options, "search-bound", "search_bound", default="64")
            try:
                bound = max(1, int(bound_s))
            except ValueError:
                bound = 64
            cert = fn(poly, search_bound=bound)
        else:
            base.tier, base.ok = "error", False
            base.detail = f"no in-process adapter for certifier: {mod.certifier}"
            return base
    except Exception as e:  # 认证器自身故障 = error (不是 unknown: 区分"没验出"与"没验成")
        base.tier, base.ok = "error", False
        base.detail = f"certifier raised: {type(e).__name__}: {e}"
        base.seconds = time.monotonic() - t0
        return base
    base.seconds = time.monotonic() - t0
    base.tier = cert.tier.value
    base.claim = cert.claim
    base.detail = cert.detail
    if cert.bound is not None:
        base.detail += f" (bound={cert.bound})"
    return base


# ── script 模式 (subprocess, 流式) ──


def _tier_from_report(report_path: Path, tier_path: str) -> tuple[str, str]:
    """从报告 JSON 按 dot-path 提取 tier; bool 值语义: true→proven, false→unknown。"""
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown", "report unreadable"
    node: Any = data
    for key in tier_path.split("."):
        if not isinstance(node, dict) or key not in node:
            return "unknown", f"tier path not found in report: {tier_path}"
        node = node[key]
    if isinstance(node, bool):
        return ("proven" if node else "unknown"), f"{tier_path}={node}"
    tier = str(node).lower()
    return (tier if tier in _VALID_TIERS else "unknown"), f"{tier_path}={node}"


def run_script(mod: Any, options: dict[str, str], *, out: TextIO | None = None,
               timeout: int = 900, stream: bool = True) -> RunOutcome:
    """subprocess 跑驱动脚本 (cwd=脚本目录), 流式回显, 读报告定 tier。"""
    base = RunOutcome(module_id=mod.id, name=mod.name, options=dict(options))
    script = _REPO_ROOT / mod.script
    if not script.is_file():
        base.tier, base.ok = "error", False
        base.detail = f"script not found: {mod.script}"
        return base
    fmt = {k.replace("-", "_"): str(v) for k, v in options.items()}
    fmt.setdefault("out_dir", "results")
    fmt.setdefault("residual_bound", "20000")
    fmt.setdefault("coef_max", "6")
    try:
        args = [a.format(**fmt) for a in mod.script_args]
    except KeyError as e:
        base.tier, base.ok = "error", False
        base.detail = f"script_args placeholder missing option: {e}"
        return base
    cmd = [sys.executable, str(script), *args]
    t0 = time.monotonic()
    chunks: list[str] = []
    rc = -1
    timed_out = False
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(script.parent),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            chunks.append(line)
            if stream and out is not None:
                out.write(line if line.endswith("\n") else line + "\n")
                out.flush()
            if time.monotonic() - t0 > timeout:
                timed_out = True
                proc.kill()
                break
        proc.stdout.close()
        rc = proc.wait(timeout=10)
    except OSError as e:
        base.tier, base.ok = "error", False
        base.detail = f"spawn failed: {e}"
        base.seconds = time.monotonic() - t0
        return base
    base.seconds = time.monotonic() - t0
    base.output = "".join(chunks)
    if timed_out:
        base.tier, base.ok = "error", False
        base.detail = f"timed out after {timeout}s (partial output kept)"
        return base
    if rc != 0:
        base.tier, base.ok = "error", False
        base.detail = f"script exited {rc}"
        return base
    # 报告 → tier (分级来自 artifact, 不从 stdout 猜)
    if mod.report and mod.report_tier_path:
        report_path = script.parent / mod.report
        if report_path.is_file():
            base.report_path = str(report_path)
            base.tier, why = _tier_from_report(report_path, mod.report_tier_path)
            base.claim = mod.description
            base.detail = f"report: {report_path.name} · {why}"
        else:
            base.tier = "unknown"
            base.detail = f"report missing: {mod.report}"
    else:
        base.tier = "unknown"
        base.detail = "no report tier path declared"
    return base


# ── 统一入口 / 批跑 ──


def run_module(mod: Any, options: dict[str, str], *, out: TextIO | None = None,
               timeout: int = 900, stream: bool = True) -> RunOutcome:
    if mod.is_in_process:
        return run_certifier(mod, options)
    if mod.script:
        return run_script(mod, options, out=out, timeout=timeout, stream=stream)
    err = RunOutcome(module_id=mod.id, name=mod.name, tier="error", ok=False)
    err.detail = "module declares neither certifier nor script"
    return err


def run_batch(modules: list[Any], options_map: dict[str, dict[str, str]], *,
              out: TextIO | None = None, timeout: int = 900) -> list[RunOutcome]:
    """批跑 (Argus run_modules 对标): 进度条带 ETA, 模块输出捕获不外流 (跑完看报告)。"""
    from zall.cli.render import _shared_console, batch_progress

    outcomes: list[RunOutcome] = []
    if not modules:
        return outcomes
    console = _shared_console(out) if out is not None else None
    with batch_progress(console=console) as prog:
        task = prog.add_task("run", total=len(modules), name="…")
        for mod in modules:
            prog.update(task, name=f"{mod.id}. {mod.name}")
            outcomes.append(run_module(
                mod, options_map.get(mod.id, {}),
                out=None, timeout=timeout, stream=False,
            ))
            prog.advance(task)
    return outcomes
