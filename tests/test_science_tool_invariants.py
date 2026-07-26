"""E3.6 ScienceTool invariant tests -- agent-side science kit tool.

Corresponds to:
  MASTER.md §12.3 E3.6 (交互式 dogfood)
  src/zall/tools/science.py

IPR-0: 每个测试含反例.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from zall.extensions.science.store import ScienceStore
from zall.tools.science import ScienceTool


@pytest.fixture
def store(tmp_path) -> ScienceStore:
    return ScienceStore(base_dir=tmp_path / "science")


@pytest.fixture
def tool(store) -> ScienceTool:
    return ScienceTool(store=store)


# ── new_hypothesis ──


class TestNewHypothesis:
    def test_creates_hypothesis(self, tool: ScienceTool, store: ScienceStore) -> None:
        r = tool.execute({
            "action": "new_hypothesis",
            "claim": "X inhibits Y",
            "prediction": "Y decreases >30%",
        })
        assert r.success
        assert "hypothesis_id" in r.artifacts
        assert len(store.list_hypotheses()) == 1

    def test_missing_claim_fails(self, tool: ScienceTool) -> None:
        """Counterexample: missing claim -> failure, not crash."""
        r = tool.execute({"action": "new_hypothesis", "prediction": "P"})
        assert not r.success
        assert "claim" in r.output.lower()

    def test_missing_prediction_fails(self, tool: ScienceTool) -> None:
        """Counterexample: missing prediction -> failure."""
        r = tool.execute({"action": "new_hypothesis", "claim": "C"})
        assert not r.success
        assert "prediction" in r.output.lower()


# ── add_evidence ──


class TestAddEvidence:
    def _make_hyp(self, tool: ScienceTool) -> str:
        r = tool.execute({"action": "new_hypothesis", "claim": "C", "prediction": "P"})
        return r.artifacts["hypothesis_id"]

    def test_supports_evidence(self, tool: ScienceTool, store: ScienceStore) -> None:
        hid = self._make_hyp(tool)
        r = tool.execute({
            "action": "add_evidence",
            "hypothesis_id": hid,
            "metric": "G-F",
            "value": 0.163,
            "threshold": 0.128,
            "supports": True,
        })
        assert r.success
        h = store.get_hypothesis.__self__.get_hypothesis  # type: ignore
        # verify evidence_for updated
        from uuid import UUID
        hyp = store.list_hypotheses()[0]
        assert len(hyp.evidence_for) == 1

    def test_against_evidence(self, tool: ScienceTool) -> None:
        hid = self._make_hyp(tool)
        r = tool.execute({
            "action": "add_evidence",
            "hypothesis_id": hid,
            "metric": "p-value",
            "value": 0.08,
            "threshold": 0.05,
            "supports": False,
        })
        assert r.success
        assert r.artifacts["supports"] is False

    def test_missing_fields_fails(self, tool: ScienceTool) -> None:
        """Counterexample: missing metric/value/supports -> failure."""
        hid = self._make_hyp(tool)
        r = tool.execute({"action": "add_evidence", "hypothesis_id": hid})
        assert not r.success

    def test_bad_hypothesis_id_fails(self, tool: ScienceTool) -> None:
        """Counterexample: nonexistent hypothesis -> failure."""
        r = tool.execute({
            "action": "add_evidence",
            "hypothesis_id": "00000000-0000-0000-0000-000000000000",
            "metric": "m", "value": 1.0, "supports": True,
        })
        assert not r.success


# ── falsify ──


class TestFalsify:
    def test_falsify_after_against_evidence(self, tool: ScienceTool, store: ScienceStore) -> None:
        from zall.core.hypothesis import HypothesisStatus
        r = tool.execute({"action": "new_hypothesis", "claim": "C", "prediction": "P"})
        hid = r.artifacts["hypothesis_id"]
        r2 = tool.execute({
            "action": "add_evidence", "hypothesis_id": hid,
            "metric": "p", "value": 0.08, "threshold": 0.05, "supports": False,
        })
        eid = r2.artifacts["evidence_id"]
        r3 = tool.execute({
            "action": "falsify", "hypothesis_id": hid, "evidence_id": eid,
            "diagnosis": "underpowered", "value_desc": "need more n",
        })
        assert r3.success
        h = store.list_hypotheses()[0]
        assert h.status == HypothesisStatus.FALSIFIED

    def test_falsify_without_against_evidence_fails(self, tool: ScienceTool) -> None:
        """Counterexample (H-3): falsify without against evidence -> failure."""
        r = tool.execute({"action": "new_hypothesis", "claim": "C", "prediction": "P"})
        hid = r.artifacts["hypothesis_id"]
        r2 = tool.execute({
            "action": "falsify", "hypothesis_id": hid,
            "evidence_id": "00000000-0000-0000-0000-000000000000",
            "diagnosis": "x", "value_desc": "y",
        })
        assert not r2.success
        assert "evidence_against" in r2.output or "H-3" in r2.output


# ── revise ──


class TestRevise:
    def test_revise_creates_v2(self, tool: ScienceTool, store: ScienceStore) -> None:
        r = tool.execute({"action": "new_hypothesis", "claim": "v1", "prediction": "p1"})
        hid = r.artifacts["hypothesis_id"]
        r2 = tool.execute({
            "action": "revise", "hypothesis_id": hid,
            "new_claim": "v2", "new_prediction": "p2",
        })
        assert r2.success
        assert r2.artifacts["version"] == 2
        assert len(store.list_hypotheses()) == 2

    def test_revise_missing_fields_fails(self, tool: ScienceTool) -> None:
        """Counterexample: missing new_claim -> failure."""
        r = tool.execute({"action": "new_hypothesis", "claim": "C", "prediction": "P"})
        hid = r.artifacts["hypothesis_id"]
        r2 = tool.execute({"action": "revise", "hypothesis_id": hid, "new_claim": "x"})
        assert not r2.success


# ── list ──


class TestList:
    def test_empty_list(self, tool: ScienceTool) -> None:
        r = tool.execute({"action": "list"})
        assert r.success
        assert "No hypotheses" in r.output

    def test_list_shows_hypotheses(self, tool: ScienceTool) -> None:
        tool.execute({"action": "new_hypothesis", "claim": "visible claim", "prediction": "P"})
        r = tool.execute({"action": "list"})
        assert "visible claim" in r.output


# ── error handling ──


class TestErrorHandling:
    def test_unknown_action_fails_gracefully(self, tool: ScienceTool) -> None:
        """Counterexample: unknown action -> failure, not crash."""
        r = tool.execute({"action": "garbage"})
        assert not r.success
        assert "unknown action" in r.output.lower()

    def test_tool_id_and_schema(self, tool: ScienceTool) -> None:
        """Tool metadata for registry (OpenAI function calling format)."""
        assert tool.tool_id == "science"
        params = tool.schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert "new_hypothesis" in params["properties"]["action"]["enum"]

    def test_capabilities_declared(self, tool: ScienceTool) -> None:
        """E9: capabilities explicitly declared (read-only, whitelist)."""
        caps = tool.capabilities
        assert caps.is_read_only is True


# ── experiment ──


class TestExperiment:
    """Experiment lifecycle tests for ScienceTool."""

    def _make_hyp(self, tool: ScienceTool) -> str:
        r = tool.execute({"action": "new_hypothesis", "claim": "C", "prediction": "P"})
        return r.artifacts["hypothesis_id"]

    def test_new_experiment_creates(self, tool: ScienceTool, store: ScienceStore) -> None:
        """Creating an experiment returns id and status DESIGNED."""
        hid = self._make_hyp(tool)
        r = tool.execute({
            "action": "new_experiment",
            "hypothesis_id": hid,
            "protocol": "run_analysis.py --input data.csv",
            "data_snapshot": "sha256:abc123",
        })
        assert r.success
        assert "experiment_id" in r.artifacts
        assert r.artifacts["status"] == "designed"
        exps = store.list_experiments()
        assert len(exps) == 1
        assert exps[0].status.value == "designed"

    def test_run_experiment_state_transition(self, tool: ScienceTool, store: ScienceStore) -> None:
        """DESIGNED -> RUNNING transition via run_experiment."""
        hid = self._make_hyp(tool)
        r1 = tool.execute({
            "action": "new_experiment",
            "hypothesis_id": hid,
            "protocol": "run.sh",
            "data_snapshot": "sha256:abc",
        })
        eid = r1.artifacts["experiment_id"]
        r2 = tool.execute({"action": "run_experiment", "experiment_id": eid})
        assert r2.success
        assert r2.artifacts["status"] == "running"
        assert r2.artifacts["started_at"] is not None
        e = store.get_experiment(UUID(eid))
        assert e is not None
        assert e.status.value == "running"
        assert e.started_at is not None

    def test_complete_experiment(self, tool: ScienceTool, store: ScienceStore) -> None:
        """RUNNING -> COMPLETED, result stored."""
        hid = self._make_hyp(tool)
        r1 = tool.execute({
            "action": "new_experiment", "hypothesis_id": hid,
            "protocol": "run.sh", "data_snapshot": "sha256:abc",
        })
        eid = r1.artifacts["experiment_id"]
        tool.execute({"action": "run_experiment", "experiment_id": eid})
        r3 = tool.execute({
            "action": "complete_experiment",
            "experiment_id": eid,
            "result": {"G-F Score": 0.163, "p-value": 0.01},
        })
        assert r3.success
        assert r3.artifacts["status"] == "completed"
        e = store.get_experiment(UUID(eid))
        assert e is not None
        assert e.status.value == "completed"
        assert e.result == {"G-F Score": 0.163, "p-value": 0.01}
        assert e.completed_at is not None

    def test_fail_experiment(self, tool: ScienceTool, store: ScienceStore) -> None:
        """RUNNING -> FAILED, reason stored."""
        hid = self._make_hyp(tool)
        r1 = tool.execute({
            "action": "new_experiment", "hypothesis_id": hid,
            "protocol": "run.sh", "data_snapshot": "sha256:abc",
        })
        eid = r1.artifacts["experiment_id"]
        tool.execute({"action": "run_experiment", "experiment_id": eid})
        r3 = tool.execute({
            "action": "complete_experiment",
            "experiment_id": eid,
            "reason": "data insufficient, cannot converge",
        })
        assert r3.success
        assert r3.artifacts["status"] == "failed"
        assert r3.artifacts["reason"] == "data insufficient, cannot converge"
        e = store.get_experiment(UUID(eid))
        assert e is not None
        assert e.status.value == "failed"
        assert e.result == {"reason": "data insufficient, cannot converge"}

    def test_invalid_state_transition(self, tool: ScienceTool) -> None:
        """Counterexample: DESIGNED directly to COMPLETED fails."""
        hid = self._make_hyp(tool)
        r1 = tool.execute({
            "action": "new_experiment", "hypothesis_id": hid,
            "protocol": "run.sh", "data_snapshot": "sha256:abc",
        })
        eid = r1.artifacts["experiment_id"]
        r2 = tool.execute({
            "action": "complete_experiment",
            "experiment_id": eid,
            "result": {"G-F Score": 0.163},
        })
        assert not r2.success
        assert "cannot complete" in r2.output

    def test_evidence_links_to_experiment(self, tool: ScienceTool, store: ScienceStore) -> None:
        """add_evidence with experiment_id sets Evidence.experiment_id."""
        hid = self._make_hyp(tool)
        e1 = tool.execute({
            "action": "new_experiment", "hypothesis_id": hid,
            "protocol": "run.sh", "data_snapshot": "sha256:abc",
        })
        eid = e1.artifacts["experiment_id"]
        r2 = tool.execute({
            "action": "add_evidence",
            "hypothesis_id": hid,
            "experiment_id": eid,
            "metric": "G-F Score",
            "value": 0.163,
            "supports": True,
        })
        assert r2.success
        # Verify evidence stored with correct experiment_id
        ev_list = store.list_evidence(hid=UUID(hid))
        assert len(ev_list) == 1
        assert str(ev_list[0].experiment_id) == eid

    def test_list_includes_experiments(self, tool: ScienceTool, store: ScienceStore) -> None:
        """List output includes experiments section."""
        hid = self._make_hyp(tool)
        r = tool.execute({
            "action": "list",
        })
        # No experiments yet
        assert "No experiments yet" in r.output
        tool.execute({
            "action": "new_experiment", "hypothesis_id": hid,
            "protocol": "run.sh", "data_snapshot": "sha256:abc",
        })
        r2 = tool.execute({"action": "list"})
        assert "Experiments (" in r2.output
        assert "run.sh" in r2.output

    def test_new_experiment_missing_fields(self, tool: ScienceTool) -> None:
        """Counterexample: missing hypothesis_id fails."""
        r = tool.execute({
            "action": "new_experiment",
            "protocol": "run.sh",
            "data_snapshot": "sha256:abc",
        })
        assert not r.success
        assert "hypothesis_id" in r.output

        # Missing protocol
        r2 = tool.execute({
            "action": "new_experiment",
            "hypothesis_id": "00000000-0000-0000-0000-000000000000",
            "data_snapshot": "sha256:abc",
        })
        assert not r2.success
        assert "protocol" in r2.output

        # Missing data_snapshot
        r3 = tool.execute({
            "action": "new_experiment",
            "hypothesis_id": "00000000-0000-0000-0000-000000000000",
            "protocol": "run.sh",
        })
        assert not r3.success
        assert "data_snapshot" in r3.output

    def test_schema_has_experiment_fields(self, tool: ScienceTool) -> None:
        """Schema properties include experiment-related fields."""
        params = tool.schema["function"]["parameters"]
        props = params["properties"]
        assert "experiment_id" in props
        assert "protocol" in props
        assert "data_snapshot" in props
        assert "result" in props
        assert "reason" in props
        action_enum = props["action"]["enum"]
        assert "new_experiment" in action_enum
        assert "run_experiment" in action_enum
        assert "complete_experiment" in action_enum
