"""zall.tools.science - Science Kit agent tool (E3.6 交互式 dogfood).

Corresponds to:
  MASTER.md §12.3 E3.6 (交互式 dogfood)
  docs/E3_SCIENCE_KIT.md

设计:
  把 Science Kit 的假设/证据管理能力暴露为 agent 工具,
  让 agent 在科研任务中自主提出假设、记录证据、证伪假设。
  这是 zall 与所有 coding agent 拉开差异的关键: agent 不只是写代码,
  还能做假设驱动的科学探索。

  与 cli/commands/science.py (REPL slash 命令) 共享 ScienceStore,
  但本工具是给模型调用的 (通过 tool_call), 不是给用户敲 /science 的。

操作 (通过 args["action"] 分发):
  - new_hypothesis: 创建假设 (claim, prediction, confidence?)
  - add_evidence: 记录证据 (hypothesis_id, metric, value, supports, threshold?)
  - falsify: 证伪假设 (hypothesis_id, evidence_id, diagnosis, value)
  - revise: 修订假设 (hypothesis_id, new_claim, new_prediction)
  - list: 列出所有假设

IPR constraints:
  IPR-3: 仅 stdlib + zall core, 不 import 模型 SDK
  显示型工具 (无文件系统副作用, 除 ScienceStore 的 JSONL 追加)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from zall.core.evidence import Evidence, EvidenceType
from zall.core.experiment import ExperimentGoal
from zall.core.hypothesis import Hypothesis
from zall.core.provenance import ScienceProvenance
from zall.core.tool import ToolCapabilities, ToolKind, ToolNamespace, ToolResult, ToolScope
from zall.extensions.science.store import ScienceStore


def _build_agent_provenance(
    protocol_path: str = "",
    data_path: str = "",
    code_path: str = "",
) -> ScienceProvenance:
    """Part D (真溯源): 用真实文件 SHA-256 构造 ScienceProvenance (agent 侧)。

    提供路径则算真实哈希; 不提供则降级为带标记占位符 "sha256:unspecified-<field>",
    明确表示未溯源 (而非伪装成已哈希的 "sha256:agent")。环境哈希始终真实。
    """
    from zall._util.hash_utils import environment_hash, hash_file

    def _h(path: str, field: str) -> str:
        if path:
            try:
                return hash_file(path)
            except OSError:
                return f"sha256:unreadable-{field}"
        return f"sha256:unspecified-{field}"

    return ScienceProvenance(
        protocol_hash=_h(protocol_path, "protocol"),
        data_hash=_h(data_path, "data"),
        analysis_code_hash=_h(code_path, "code"),
        environment_hash=environment_hash(),
    )


class ScienceTool:
    """Science Kit agent tool -- 假设驱动的科研探索 (§12.3 E3).

    让 agent 通过 tool_call 管理科研假设生命周期:
    提出假设 -> 记录证据 -> 证伪/确认 -> 修订 -> 新版本。

    Usage (model side):
      science(action="new_hypothesis", claim="X inhibits Y", prediction="Y decreases >30%")
      science(action="add_evidence", hypothesis_id="...", metric="IC50", value=0.5, supports=true)
      science(action="falsify", hypothesis_id="...", evidence_id="...", diagnosis="too broad")
      science(action="revise", hypothesis_id="...", new_claim="...", new_prediction="...")
      science(action="list")
    """

    __test__ = False

    def __init__(self, store: ScienceStore | None = None) -> None:
        self._store = store or ScienceStore()

    @property
    def tool_id(self) -> str:
        return "science"

    @property
    def capabilities(self) -> ToolCapabilities:
        # science 工具只追加自己的 JSONL 存储, 不碰用户代码/文件系统
        # 标为只读 -> whitelist, 免确认 (与 todo_list 同列)
        return ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)

    @property
    def kind(self) -> ToolKind:
        return ToolKind.OTHER

    @property
    def namespace(self) -> ToolNamespace:
        return ToolNamespace.ZALL

    @property
    def tool_version(self) -> str:
        return "1.1.0"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "science",
                "description": (
                    "Science kit: manage research hypotheses, record evidence, "
                    "falsify/revise claims, and run experiments. "
                    "Actions: new_hypothesis, add_evidence, "
                    "falsify, revise, list, new_experiment, run_experiment, "
                    "complete_experiment. "
                    "Use this to do hypothesis-driven science."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["new_hypothesis", "add_evidence", "falsify", "revise", "list",
                                     "new_experiment", "run_experiment", "complete_experiment"],
                            "description": "Science kit operation to perform",
                        },
                        "claim": {
                            "type": "string",
                            "description": "Hypothesis claim (for new_hypothesis)",
                        },
                        "prediction": {
                            "type": "string",
                            "description": "Testable prediction (for new_hypothesis)",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Initial confidence [0,1] (for new_hypothesis, default 0.5)",
                        },
                        "hypothesis_id": {
                            "type": "string",
                            "description": "UUID of the hypothesis (for add_evidence/falsify/revise)",
                        },
                        "evidence_id": {
                            "type": "string",
                            "description": "UUID of the falsifying evidence (for falsify)",
                        },
                        "metric": {
                            "type": "string",
                            "description": "Metric name, e.g. 'G-F Score', 'p-value' (for add_evidence)",
                        },
                        "value": {
                            "type": "number",
                            "description": "Metric value (for add_evidence)",
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Decision threshold (for add_evidence, optional)",
                        },
                        "supports": {
                            "type": "boolean",
                            "description": "True=supports hypothesis, False=against (for add_evidence)",
                        },
                        "protocol_path": {
                            "type": "string",
                            "description": "Path to experiment script (hashed for real provenance, for add_evidence)",
                        },
                        "data_path": {
                            "type": "string",
                            "description": "Path to input data (hashed for real provenance, for add_evidence)",
                        },
                        "code_path": {
                            "type": "string",
                            "description": "Path to analysis code (hashed for real provenance, for add_evidence)",
                        },
                        "diagnosis": {
                            "type": "string",
                            "description": "Why falsified (for falsify)",
                        },
                        "value_desc": {
                            "type": "string",
                            "description": "Scientific value: what possibility is excluded (for falsify)",
                        },
                        "new_claim": {
                            "type": "string",
                            "description": "Revised claim (for revise)",
                        },
                        "new_prediction": {
                            "type": "string",
                            "description": "Revised prediction (for revise)",
                        },
                        "experiment_id": {
                            "type": "string",
                            "description": "UUID of the experiment (for run_experiment/complete_experiment/add_evidence)",
                        },
                        "protocol": {
                            "type": "string",
                            "description": "Experiment protocol (script path + params, for new_experiment)",
                        },
                        "data_snapshot": {
                            "type": "string",
                            "description": "Input data snapshot hash (for new_experiment)",
                        },
                        "result": {
                            "type": "object",
                            "description": "Experiment result dict (for complete_experiment)",
                            "additionalProperties": True,
                        },
                        "reason": {
                            "type": "string",
                            "description": "Failure reason (for complete_experiment when failing)",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action", "")
        try:
            if action == "new_hypothesis":
                return self._new_hypothesis(args)
            elif action == "add_evidence":
                return self._add_evidence(args)
            elif action == "falsify":
                return self._falsify(args)
            elif action == "revise":
                return self._revise(args)
            elif action == "list":
                return self._list()
            elif action == "new_experiment":
                return self._new_experiment(args)
            elif action == "run_experiment":
                return self._run_experiment(args)
            elif action == "complete_experiment":
                return self._complete_experiment(args)
            else:
                return ToolResult(
                    success=False,
                    output=f"unknown action: {action}. Valid: new_hypothesis, add_evidence, falsify, revise, list, new_experiment, run_experiment, complete_experiment",
                    error=f"unknown action: {action}. Valid: new_hypothesis, add_evidence, falsify, revise, list, new_experiment, run_experiment, complete_experiment",
                )
        except Exception as e:
            msg = f"science tool error ({action}): {type(e).__name__}: {e}"
            return ToolResult(
                success=False,
                output=msg,
                error=msg,
            )

    def _new_hypothesis(self, args: dict[str, Any]) -> ToolResult:
        claim = args.get("claim", "").strip()
        prediction = args.get("prediction", "").strip()
        if not claim or not prediction:
            return ToolResult(
                output="new_hypothesis requires 'claim' and 'prediction'",
                success=False,
                error="new_hypothesis requires 'claim' and 'prediction'",
            )
        confidence = args.get("confidence", 0.5)
        import time
        h = Hypothesis(
            id=uuid4(),
            claim=claim,
            prediction=prediction,
            confidence=float(confidence),
            created_by="agent",
            created_at=int(time.time()),
        )
        self._store.add_hypothesis(h)
        return ToolResult(
            success=True,
            output=f"Hypothesis created: {h.id}\n  claim: {h.claim}\n  prediction: {h.prediction}\n  status: {h.status.value}",
            artifacts={"hypothesis_id": str(h.id), "status": h.status.value},
        )

    def _add_evidence(self, args: dict[str, Any]) -> ToolResult:
        import time
        hid_str = args.get("hypothesis_id", "")
        hid = self._parse_uuid(hid_str)
        if hid is None:
            return ToolResult(success=False, output="invalid or missing hypothesis_id", error="invalid or missing hypothesis_id")
        h = self._store.get_hypothesis(hid)
        if h is None:
            return ToolResult(success=False, output=f"hypothesis not found: {hid_str}", error=f"hypothesis not found: {hid_str}")
        metric = args.get("metric", "")
        value = args.get("value")
        supports = args.get("supports")
        if not metric or value is None or supports is None:
            return ToolResult(
                output="add_evidence requires metric, value, supports",
                success=False,
                error="add_evidence requires metric, value, supports",
            )
        threshold = args.get("threshold")
        # Optional experiment_id: if provided, link evidence to experiment
        eid_str = args.get("experiment_id", "")
        if eid_str:
            experiment_id = self._parse_uuid(eid_str)
            if experiment_id is None:
                return ToolResult(success=False, output=f"invalid experiment_id: {eid_str}", error=f"invalid experiment_id: {eid_str}")
        else:
            experiment_id = uuid4()
        # Part D (真溯源): 用真实文件 SHA-256 (若 agent 提供路径), 否则降级为标记占位符。
        # 占位符从 "sha256:agent" 改为 "sha256:unspecified-<field>", 明确表示未溯源。
        protocol_path = args.get("protocol_path", "") or ""
        data_path = args.get("data_path", "") or ""
        code_path = args.get("code_path", "") or ""
        prov = _build_agent_provenance(
            protocol_path=protocol_path, data_path=data_path, code_path=code_path,
        )
        ev_type = EvidenceType.POSITIVE if supports else EvidenceType.NEGATIVE
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=hid,
            experiment_id=experiment_id,
            type=ev_type,
            metric=metric,
            value=float(value),
            threshold=float(threshold) if threshold is not None else None,
            supports=bool(supports),
            provenance=prov,
            created_at=int(time.time()),
        )
        self._store.add_evidence(ev)
        h.add_evidence(ev.id, supports=bool(supports))
        self._store.update_hypothesis(h)
        tag = "supports" if supports else "against"
        return ToolResult(
            success=True,
            output=f"Evidence recorded ({tag}): {ev.id}\n  {metric} = {value}" + (f" (threshold {threshold})" if threshold is not None else ""),
            artifacts={"evidence_id": str(ev.id), "supports": bool(supports)},
        )

    def _falsify(self, args: dict[str, Any]) -> ToolResult:
        hid_str = args.get("hypothesis_id", "")
        eid_str = args.get("evidence_id", "")
        hid = self._parse_uuid(hid_str)
        eid = self._parse_uuid(eid_str)
        if hid is None or eid is None:
            return ToolResult(success=False, output="invalid or missing hypothesis_id/evidence_id", error="invalid or missing hypothesis_id/evidence_id")
        h = self._store.get_hypothesis(hid)
        if h is None:
            return ToolResult(success=False, output=f"hypothesis not found: {hid_str}", error=f"hypothesis not found: {hid_str}")
        diagnosis = args.get("diagnosis", "")
        value_desc = args.get("value_desc", "")
        if eid not in h.evidence_against:
            return ToolResult(
                output=f"evidence {eid_str} not in evidence_against; record with supports=false first (H-3 invariant)",
                success=False,
                error=f"evidence {eid_str} not in evidence_against; record with supports=false first (H-3 invariant)",
            )
        try:
            h.falsify(eid)
        except ValueError as e:
            return ToolResult(success=False, output=f"cannot falsify: {e}", error=f"cannot falsify: {e}")
        self._store.update_hypothesis(h)
        return ToolResult(
            success=True,
            output=f"Hypothesis falsified: {h.id}\n  diagnosis: {diagnosis}\n  value: {value_desc}",
            artifacts={"status": h.status.value},
        )

    def _revise(self, args: dict[str, Any]) -> ToolResult:
        hid_str = args.get("hypothesis_id", "")
        hid = self._parse_uuid(hid_str)
        if hid is None:
            return ToolResult(success=False, output="invalid or missing hypothesis_id", error="invalid or missing hypothesis_id")
        h = self._store.get_hypothesis(hid)
        if h is None:
            return ToolResult(success=False, output=f"hypothesis not found: {hid_str}", error=f"hypothesis not found: {hid_str}")
        new_claim = args.get("new_claim", "").strip()
        new_prediction = args.get("new_prediction", "").strip()
        if not new_claim or not new_prediction:
            return ToolResult(success=False, output="revise requires new_claim and new_prediction", error="revise requires new_claim and new_prediction")
        revised = h.revise(new_claim=new_claim, new_prediction=new_prediction)
        self._store.add_hypothesis(revised)
        return ToolResult(
            success=True,
            output=f"Hypothesis revised: {revised.id} (v{revised.version})\n  revised_from: {h.id}\n  new claim: {revised.claim}",
            artifacts={"hypothesis_id": str(revised.id), "version": revised.version},
        )

    def _new_experiment(self, args: dict[str, Any]) -> ToolResult:
        hid_str = args.get("hypothesis_id", "")
        hid = self._parse_uuid(hid_str)
        if hid is None:
            return ToolResult(success=False, output="new_experiment requires 'hypothesis_id'", error="new_experiment requires 'hypothesis_id'")
        protocol = args.get("protocol", "").strip()
        data_snapshot = args.get("data_snapshot", "").strip()
        if not protocol or not data_snapshot:
            return ToolResult(
                output="new_experiment requires 'protocol' and 'data_snapshot'",
                success=False,
                error="new_experiment requires 'protocol' and 'data_snapshot'",
            )
        e = ExperimentGoal(
            id=uuid4(),
            hypothesis_id=hid,
            protocol=protocol,
            data_snapshot=data_snapshot,
        )
        self._store.add_experiment(e)
        return ToolResult(
            success=True,
            output=f"Experiment created: {e.id}\n  hypothesis_id: {e.hypothesis_id}\n  protocol: {e.protocol}\n  status: {e.status.value}",
            artifacts={"experiment_id": str(e.id), "status": e.status.value},
        )

    def _run_experiment(self, args: dict[str, Any]) -> ToolResult:
        eid_str = args.get("experiment_id", "")
        eid = self._parse_uuid(eid_str)
        if eid is None:
            return ToolResult(success=False, output="run_experiment requires 'experiment_id'", error="run_experiment requires 'experiment_id'")
        e = self._store.get_experiment(eid)
        if e is None:
            return ToolResult(success=False, output=f"experiment not found: {eid_str}", error=f"experiment not found: {eid_str}")
        try:
            e.start()
        except ValueError as exc:
            return ToolResult(success=False, output=f"cannot start experiment: {exc}", error=f"cannot start experiment: {exc}")
        self._store.update_experiment(e)
        return ToolResult(
            success=True,
            output=f"Experiment started: {e.id}\n  status: {e.status.value}\n  started_at: {e.started_at}",
            artifacts={"experiment_id": str(e.id), "status": e.status.value, "started_at": e.started_at},
        )

    def _complete_experiment(self, args: dict[str, Any]) -> ToolResult:
        eid_str = args.get("experiment_id", "")
        eid = self._parse_uuid(eid_str)
        if eid is None:
            return ToolResult(success=False, output="complete_experiment requires 'experiment_id'", error="complete_experiment requires 'experiment_id'")
        e = self._store.get_experiment(eid)
        if e is None:
            return ToolResult(success=False, output=f"experiment not found: {eid_str}", error=f"experiment not found: {eid_str}")
        reason = args.get("reason", "").strip()
        if reason:
            # Fail path
            try:
                e.fail(reason)
            except ValueError as exc:
                return ToolResult(success=False, output=f"cannot fail experiment: {exc}", error=f"cannot fail experiment: {exc}")
            self._store.update_experiment(e)
            return ToolResult(
                success=True,
                output=f"Experiment failed: {e.id}\n  reason: {reason}\n  status: {e.status.value}",
                artifacts={"experiment_id": str(e.id), "status": e.status.value, "reason": reason},
            )
        # Complete path
        result = args.get("result")
        if result is None:
            return ToolResult(
                success=False,
                output="complete_experiment requires 'result' dict (or 'reason' to fail)",
                error="complete_experiment requires 'result' dict (or 'reason' to fail)",
            )
        try:
            e.complete(dict(result))
        except ValueError as exc:
            return ToolResult(success=False, output=f"cannot complete experiment: {exc}", error=f"cannot complete experiment: {exc}")
        self._store.update_experiment(e)
        return ToolResult(
            success=True,
            output=f"Experiment completed: {e.id}\n  result: {e.result}\n  status: {e.status.value}\n  completed_at: {e.completed_at}",
            artifacts={"experiment_id": str(e.id), "status": e.status.value, "result": e.result},
        )

    def _list(self) -> ToolResult:
        hyps = self._store.list_hypotheses()
        lines = []
        if not hyps:
            lines.append("No hypotheses yet.")
        else:
            lines.append(f"Hypotheses ({len(hyps)}):")
            for h in hyps:
                lines.append(
                    f"  {str(h.id)[:8]} v{h.version} {h.status.value:10s} "
                    f"for={len(h.evidence_for)} against={len(h.evidence_against)} "
                    f"| {h.claim[:50]}"
                )
        exps = self._store.list_experiments()
        if not exps:
            lines.append("No experiments yet.")
        else:
            lines.append(f"Experiments ({len(exps)}):")
            for e in exps:
                rid = str(e.hypothesis_id)[:8]
                lines.append(
                    f"  {str(e.id)[:8]} {e.status.value:10s} "
                    f"hyp={rid} | {e.protocol[:40]}"
                )
        return ToolResult(success=True, output="\n".join(lines))

    @staticmethod
    def _parse_uuid(s: str) -> UUID | None:
        try:
            return UUID(s)
        except (ValueError, AttributeError, TypeError):
            return None
