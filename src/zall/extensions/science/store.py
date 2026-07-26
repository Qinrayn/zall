"""ScienceStore — append-only JSONL persistence for E3 Science Kit (E3_SCIENCE_KIT.md §2.5).

Manages hypotheses, experiments, and evidence in .zall/science/ directory.
Append-only: updates append new versions, not overwrite. Reading returns the latest version.

IPR: extensions/ can import anything; core/ stays pure.
References: docs/E3_SCIENCE_KIT.md §2.5, MASTER.md §12.3 E3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from zall.core.evidence import Evidence, NegativeResult
from zall.core.experiment import ExperimentGoal
from zall.core.hypothesis import Hypothesis


def _ensure_uuid(val: Any) -> UUID:
    """Coerce a string or UUID to UUID."""
    if isinstance(val, UUID):
        return val
    return UUID(val)


# ──────────────────────────────────────────────────────────────────────────
# ScienceStore
# ──────────────────────────────────────────────────────────────────────────


class ScienceStore:
    """Append-only JSONL store for science data (E3_SCIENCE_KIT.md §2.5).

    Directory layout under base_dir:
        hypotheses.jsonl   — Hypothesis records (append-only, versioned)
        experiments.jsonl  — ExperimentGoal records
        evidence.jsonl     — Evidence + NegativeResult records (I-10: co-located)

    Args:
        base_dir: Storage directory. Defaults to ~/.zall/science/.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".zall" / "science"
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._hypotheses_file = self._base / "hypotheses.jsonl"
        self._experiments_file = self._base / "experiments.jsonl"
        self._evidence_file = self._base / "evidence.jsonl"

    # ── Hypothesis operations ──

    def add_hypothesis(self, h: Hypothesis) -> UUID:
        """Append a hypothesis and return its ID."""
        self._append_jsonl(self._hypotheses_file, h.model_dump(mode="json"))
        return h.id

    def get_hypothesis(self, hid: UUID) -> Hypothesis | None:
        """Get the latest snapshot of a hypothesis by ID, or None.

        同一 hypothesis 可能有多条记录 (add + 多次 update)。revise 会产生
        新 UUID, 所以同一 id 内的记录都是同一 hypothesis 的状态演进,
        取最后写入的一条 (append-only, 后写覆盖)。
        """
        best: dict[str, Any] | None = None
        for record in self._read_jsonl(self._hypotheses_file):
            if _ensure_uuid(record["id"]) == hid:
                best = record  # append-only: 后写入的覆盖, 保留最后一条
        if best is None:
            return None
        # Restore UUID fields that pydantic json serialization keeps as strings
        return self._dict_to_hypothesis(best)

    def list_hypotheses(self) -> list[Hypothesis]:
        """Return the latest snapshot of every hypothesis (by UUID).

        revise() 产生新 UUID 的新 hypothesis, 在列表中单独出现。
        同一 UUID 的多次 update 取最后写入一条。
        """
        latest: dict[UUID, dict[str, Any]] = {}
        for record in self._read_jsonl(self._hypotheses_file):
            rid = _ensure_uuid(record["id"])
            latest[rid] = record  # append-only: 后写入的覆盖
        return [self._dict_to_hypothesis(r) for r in latest.values()]

    def update_hypothesis(self, h: Hypothesis) -> None:
        """Append a new version of a hypothesis (append-only)."""
        self._append_jsonl(self._hypotheses_file, h.model_dump(mode="json"))

    # ── Experiment operations ──

    def add_experiment(self, e: ExperimentGoal) -> None:
        """Append an experiment record."""
        self._append_jsonl(self._experiments_file, e.model_dump(mode="json"))

    def get_experiment(self, eid: UUID) -> ExperimentGoal | None:
        """Get an experiment by ID (latest snapshot, append-only)."""
        best: dict[str, Any] | None = None
        for record in self._read_jsonl(self._experiments_file):
            if _ensure_uuid(record["id"]) == eid:
                best = record  # append-only: last write wins
        if best is None:
            return None
        return ExperimentGoal.model_validate(best)

    def update_experiment(self, e: ExperimentGoal) -> None:
        """Append a new version of an experiment (append-only)."""
        self._append_jsonl(self._experiments_file, e.model_dump(mode="json"))

    def list_experiments(self) -> list[ExperimentGoal]:
        """Return the latest snapshot of every experiment (by UUID)."""
        latest: dict[UUID, dict[str, Any]] = {}
        for record in self._read_jsonl(self._experiments_file):
            rid = _ensure_uuid(record["id"])
            latest[rid] = record
        return [ExperimentGoal.model_validate(r) for r in latest.values()]

    # ── Evidence operations ──

    def add_evidence(self, ev: Evidence | NegativeResult) -> None:
        """Append an evidence record (or NegativeResult, I-10: co-located).

        NegativeResult is stored alongside Evidence in the same JSONL file
        with a '_entry_type' discriminator. This ensures I-10: negative results
        are stored and indexed equally with positive evidence.
        """
        if isinstance(ev, NegativeResult):
            data = ev.model_dump(mode="json")
            data["_entry_type"] = "negative_result"
        else:
            data = ev.model_dump(mode="json")
            data["_entry_type"] = "evidence"
        self._append_jsonl(self._evidence_file, data)

    def list_evidence(self, hid: UUID | None = None) -> list[Evidence]:
        """List all evidence, optionally filtered by hypothesis_id.

        I-10: NegativeResult evidence is NOT filtered out — all evidence
        (positive, negative, inconclusive) is returned equally.
        """
        results: list[Evidence] = []
        for record in self._read_jsonl(self._evidence_file):
            entry_type = record.get("_entry_type", "evidence")
            if entry_type == "negative_result":
                # Extract the embedded Evidence from NegativeResult
                ev = Evidence.model_validate(record["evidence"])
            else:
                ev = Evidence.model_validate(record)
            if hid is None or ev.hypothesis_id == hid:
                results.append(ev)
        return results

    def list_negative_results(self) -> list[NegativeResult]:
        """Return all NegativeResult records (I-10: equal indexing)."""
        results: list[NegativeResult] = []
        for record in self._read_jsonl(self._evidence_file):
            if record.get("_entry_type") == "negative_result":
                results.append(NegativeResult.model_validate(record))
        return results

    # ── Internal helpers ──

    @staticmethod
    def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
        """Atomically append a JSON line to a file."""
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        """Read all JSON lines from a file, returning empty list if missing.

        容错: 损坏的 JSON 行静默跳过 (不崩溃整个模块)。
        """
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue  # 跳过损坏行
        return records

    @staticmethod
    def _dict_to_hypothesis(d: dict[str, Any]) -> Hypothesis:
        """Convert a JSON dict back to a Hypothesis, restoring UUID fields."""
        # pydantic v2 model_validate handles UUID strings automatically
        return Hypothesis.model_validate(d)