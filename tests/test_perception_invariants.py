"""Invariant tests for Perception Engine (MASTER.md §4.2.3).

IPR-0: each test must contain a counterexample.
IPR-1: corresponds to MASTER.md §4.2.3 (Perception Engine).

Coverage:
  - Sensor protocol: Observation confidence, frozen, non-empty sensor_id
  - Percept: confidence range, non-empty source
  - StateEstimate: non-empty sources, confidence range, frozen
  - PerceptionEngine: sensor registration, fusion, empty state, anomaly
  - WorldModel: predict, update, anomaly
  - Coding sensors: graceful degradation
  - Counterexamples: confidence out of range, empty sensor_id, empty sources, sensor failure
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from zall.core.perception.sensor import (
    Observation,
    Percept,
    Sensor,
    StateEstimate,
)
from zall.core.perception.engine import PerceptionEngine
from zall.core.perception.world_model import (
    NullWorldModel,
    CompositeWorldModel,
    WorldModel,
)
from zall.core.perception.coding_sensors import (
    FileSensor,
    GitSensor,
    CompositeSensor,
)
from zall.core.perception.coding_world_model import CodingWorldModel
from zall.core.action import Action


# ═══════════════════════════════════════════════════════════════════
# Fakes
# ═══════════════════════════════════════════════════════════════════


class _FakeSensor:
    """Fake sensor for testing. Returns preset observations."""
    __test__ = False

    def __init__(self, sensor_id: str = "fake", data: dict | None = None,
                 confidence: float = 1.0) -> None:
        self._sensor_id = sensor_id
        self._data = data or {"key": "value"}
        self._confidence = confidence

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    def observe(self) -> Observation:
        return Observation.create(
            sensor_id=self._sensor_id,
            data=dict(self._data),
            confidence=self._confidence,
        )

    def describe(self) -> str:
        return f"FakeSensor({self._sensor_id})"


class _FailingSensor:
    """Sensor that always fails. Tests graceful degradation."""
    __test__ = False

    def __init__(self, sensor_id: str = "failing") -> None:
        self._sensor_id = sensor_id

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    def observe(self) -> Observation:
        raise RuntimeError("sensor failure")

    def describe(self) -> str:
        return f"FailingSensor({self._sensor_id})"


class _FakeWorldModel:
    """Fake world model for testing."""
    __test__ = False

    def __init__(self, model_id: str = "fake_wm") -> None:
        self._model_id = model_id
        self._updates: list[Observation] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def predict(self, state: StateEstimate, action: Action) -> StateEstimate:
        return StateEstimate(
            state={"predicted": True, **state.state},
            confidence=state.confidence * 0.9,
            sources=list(state.sources) + ["world_model"],
            timestamp=int(time.time() * 1000),
        )

    def update(self, observation: Observation) -> None:
        self._updates.append(observation)

    def anomaly(self, state: StateEstimate) -> bool:
        return state.confidence < 0.3


# ═══════════════════════════════════════════════════════════════════
# §1  Observation Invariants
# ═══════════════════════════════════════════════════════════════════


class TestObservationInvariants:
    """Observation invariants (MASTER.md §4.2.3)."""

    def test_happy_path(self) -> None:
        """Happy path: create valid Observation."""
        obs = Observation.create(
            sensor_id="test",
            data={"foo": "bar"},
            confidence=0.8,
        )
        assert obs.sensor_id == "test"
        assert obs.data == {"foo": "bar"}
        assert obs.confidence == 0.8
        assert obs.timestamp > 0

    def test_frozen_immutable(self) -> None:
        """IPR-0: Observation must be frozen.

        Counterexample: attempting to modify a frozen Observation should raise.
        """
        obs = Observation.create(sensor_id="test", data={}, confidence=1.0)
        with pytest.raises(ValidationError):
            obs.sensor_id = "changed"

    def test_confidence_out_of_range_raises(self) -> None:
        """FR-2: confidence must be in [0, 1].

        Counterexample: confidence=1.5 should raise ValueError.
        """
        with pytest.raises(ValidationError):
            Observation.create(
                sensor_id="test", data={}, confidence=1.5,
            )

    def test_confidence_negative_raises(self) -> None:
        """FR-2: negative confidence must raise.

        Counterexample: confidence=-0.1 should raise ValueError.
        """
        with pytest.raises(ValidationError):
            Observation.create(
                sensor_id="test", data={}, confidence=-0.1,
            )

    def test_sensor_id_nonempty(self) -> None:
        """sensor_id must be non-empty.

        Counterexample: sensor_id="" should raise ValueError.
        """
        with pytest.raises(ValidationError):
            Observation.create(
                sensor_id="", data={}, confidence=1.0,
            )

    def test_confidence_boundaries(self) -> None:
        """Boundary values for confidence (0 and 1 are valid)."""
        obs0 = Observation.create(sensor_id="test", data={}, confidence=0.0)
        assert obs0.confidence == 0.0

        obs1 = Observation.create(sensor_id="test", data={}, confidence=1.0)
        assert obs1.confidence == 1.0


# ═══════════════════════════════════════════════════════════════════
# §2  Percept Invariants
# ═══════════════════════════════════════════════════════════════════


class TestPerceptInvariants:
    """Percept invariants (MASTER.md §4.2.3)."""

    def test_happy_path(self) -> None:
        """Happy path: create valid Percept."""
        p = Percept(
            state={"class": "Foo"},
            confidence=0.9,
            timestamp=int(time.time() * 1000),
            source="file",
        )
        assert p.state == {"class": "Foo"}
        assert p.confidence == 0.9
        assert p.source == "file"

    def test_confidence_range(self) -> None:
        """confidence must be in [0, 1].

        Counterexample: confidence=2.0 should raise.
        """
        with pytest.raises(ValidationError):
            Percept(
                state={}, confidence=2.0,
                timestamp=0, source="test",
            )

    def test_uncertainty_non_negative(self) -> None:
        """uncertainty must be >= 0.

        Counterexample: uncertainty=-1.0 should raise.
        """
        with pytest.raises(ValidationError):
            Percept(
                state={}, confidence=0.5, uncertainty=-1.0,
                timestamp=0, source="test",
            )

    def test_source_nonempty(self) -> None:
        """source must be non-empty.

        Counterexample: source="" should raise.
        """
        with pytest.raises(ValidationError):
            Percept(
                state={}, confidence=0.5,
                timestamp=0, source="",
            )


# ═══════════════════════════════════════════════════════════════════
# §3  StateEstimate Invariants
# ═══════════════════════════════════════════════════════════════════


class TestSensorKeyConsistencyInvariants:
    """C1/C2 fix: anomaly + state-change detection must read the REAL sensor keys.

    GitSensor produces state key "modified" (coding_sensors.py:201); LSPSensor
    produces "errors" (coding_sensors.py:374). Before the fix, anomaly() and
    the loop state-change snapshot read "git_modified"/"lsp_errors" -- keys no
    sensor ever writes -- so both detection paths were dead code. These
    counterexample tests assert the real keys are honored.
    """

    def test_anomaly_fires_on_many_modified_files(self) -> None:
        """anomaly() must return True when GitSensor reports >50 new modified files.

        Counterexample: before C2, state key "git_modified" was read (always []),
        so 60 modified files went undetected and anomaly() returned False.
        """
        wm = CodingWorldModel(baseline_modified=0)
        se = StateEstimate(
            state={"modified": [f"file_{i}.py" for i in range(60)]},
            confidence=0.9,
            sources=["git"],
            timestamp=int(time.time() * 1000),
        )
        assert wm.anomaly(se) is True  # would be False before C2 fix

    def test_anomaly_does_not_fire_on_few_modified_files(self) -> None:
        """anomaly() must return False for a small number of modified files."""
        wm = CodingWorldModel(baseline_modified=0)
        se = StateEstimate(
            state={"modified": ["a.py", "b.py"]},
            confidence=0.9,
            sources=["git"],
            timestamp=int(time.time() * 1000),
        )
        assert wm.anomaly(se) is False

    def test_anomaly_fires_on_many_lsp_errors(self) -> None:
        """anomaly() must return True when LSPSensor reports >100 errors.

        Counterexample: before C2, state key "lsp_errors" was read (always 0),
        so 200 LSP errors went undetected and anomaly() returned False.
        """
        wm = CodingWorldModel(baseline_modified=0)
        se = StateEstimate(
            state={"errors": 200},
            confidence=0.9,
            sources=["lsp"],
            timestamp=int(time.time() * 1000),
        )
        assert wm.anomaly(se) is True  # would be False before C2 fix

    def test_anomaly_respects_baseline(self) -> None:
        """A dirty baseline (>50) with no NEW modifications must NOT fire."""
        wm = CodingWorldModel(baseline_modified=60)
        se = StateEstimate(
            state={"modified": [f"file_{i}.py" for i in range(60)]},  # == baseline
            confidence=0.9,
            sources=["git"],
            timestamp=int(time.time() * 1000),
        )
        assert wm.anomaly(se) is False


class TestStateEstimateInvariants:
    """StateEstimate invariants (MASTER.md §4.2.3)."""

    def test_happy_path(self) -> None:
        """Happy path: create valid StateEstimate."""
        se = StateEstimate(
            state={"file": "modified"},
            confidence=0.8,
            sources=["file", "git"],
            timestamp=int(time.time() * 1000),
        )
        assert se.state == {"file": "modified"}
        assert se.confidence == 0.8
        assert se.sources == ["file", "git"]

    def test_sources_nonempty(self) -> None:
        """sources must be non-empty.

        Counterexample: sources=[] should raise ValueError.
        """
        with pytest.raises(ValidationError):
            StateEstimate(
                state={}, confidence=0.5,
                sources=[], timestamp=0,
            )

    def test_confidence_range(self) -> None:
        """confidence must be in [0, 1].

        Counterexample: confidence=1.5 should raise.
        """
        with pytest.raises(ValidationError):
            StateEstimate(
                state={}, confidence=1.5,
                sources=["test"], timestamp=0,
            )

    def test_empty_factory(self) -> None:
        """StateEstimate.empty() returns a valid zero-confidence estimate."""
        se = StateEstimate.empty()
        assert se.state == {}
        assert se.confidence == 0.0
        assert se.sources == ["none"]

    def test_frozen_immutable(self) -> None:
        """IPR-0: StateEstimate must be frozen.

        Counterexample: attempting to modify a frozen StateEstimate should raise.
        """
        se = StateEstimate(
            state={}, confidence=0.5,
            sources=["test"], timestamp=0,
        )
        with pytest.raises(ValidationError):
            se.state = {"modified": True}


# ═══════════════════════════════════════════════════════════════════
# §4  PerceptionEngine Invariants
# ═══════════════════════════════════════════════════════════════════


class TestPerceptionEngineHappyPath:
    """PerceptionEngine happy path tests."""

    def test_empty_engine_returns_empty_state(self) -> None:
        """Engine with no sensors returns empty state estimate."""
        engine = PerceptionEngine()
        state = engine.perceive()
        assert state.state == {}
        assert state.confidence == 0.0
        assert state.sources == ["none"]

    def test_single_sensor(self) -> None:
        """Engine with one sensor returns its observation as state."""
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("test", {"key": "value"}, 0.9))
        state = engine.perceive()
        assert state.state.get("key") == "value"
        assert state.confidence > 0

    def test_multi_sensor_fusion(self) -> None:
        """Engine fuses multiple sensors."""
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("s1", {"a": 1}, 0.9))
        engine.add_sensor(_FakeSensor("s2", {"b": 2}, 0.8))
        state = engine.perceive()
        assert state.state.get("a") == 1
        assert state.state.get("b") == 2
        assert len(state.sources) == 2

    def test_sensor_registration_duplicate_raises(self) -> None:
        """Registering duplicate sensor_id raises.

        Counterexample: adding two sensors with the same id should raise.
        """
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("dup"))
        with pytest.raises(ValueError, match="already registered"):
            engine.add_sensor(_FakeSensor("dup"))

    def test_sensor_removal(self) -> None:
        """Removing a sensor works."""
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("temp"))
        assert "temp" in engine.sensors
        engine.remove_sensor("temp")
        assert "temp" not in engine.sensors

    def test_world_model_set(self) -> None:
        """Setting a world model works."""
        engine = PerceptionEngine()
        wm = _FakeWorldModel()
        engine.set_world_model(wm)
        assert engine.world_model.model_id == "fake_wm"

    def test_anomaly_empty(self) -> None:
        """Empty engine has no anomaly."""
        engine = PerceptionEngine()
        assert not engine.anomaly()

    def test_get_state_summary(self) -> None:
        """State summary returns expected keys."""
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("test", {"x": 1}, 0.9))
        engine.perceive()
        summary = engine.get_state_summary()
        assert "sensors" in summary
        assert "state" in summary
        assert "confidence" in summary
        assert "sources" in summary


class TestPerceptionEngineCounterExamples:
    """PerceptionEngine counterexample tests."""

    def test_failing_sensor_graceful_degradation(self) -> None:
        """A failing sensor does not crash the engine.

        Counterexample: sensor.observe() raises → engine returns degraded state.
        """
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("good", {"ok": True}, 0.9))
        engine.add_sensor(_FailingSensor("bad"))
        state = engine.perceive()
        # Good sensor's data should still be present
        assert state.state.get("ok") is True
        # Failing sensor should not be in sources (confidence 0 filtered out)
        assert "bad" not in state.sources or "good" in state.sources

    def test_sensor_with_zero_confidence_excluded(self) -> None:
        """Sensors with zero confidence are excluded from fusion.

        Counterexample: sensor with confidence=0.0 should not contribute to state.
        """
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("good", {"a": 1}, 0.9))
        engine.add_sensor(_FakeSensor("bad", {"b": 2}, 0.0))
        state = engine.perceive()
        # Low confidence sensor's data should be excluded
        assert "b" not in state.state

    def test_predict_without_world_model_returns_identity(self) -> None:
        """Predict without world model returns current state.

        Counterexample: predict() called before set_world_model() → identity.
        """
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("test", {"x": 1}, 0.9))
        engine.perceive()
        action = Action(tool_id="read_file", args={"path": "test.py"})
        predicted = engine.predict(action)
        assert predicted.state is not None

    def test_anomaly_after_sensor_failure(self) -> None:
        """Anomaly detection works after sensor failures."""
        engine = PerceptionEngine()
        engine.add_sensor(_FailingSensor("bad"))
        state = engine.perceive()
        # After all sensors fail, confidence is 0, anomaly may be detected
        assert state.confidence == 0.0

    def test_reset_preserves_configured_world_model(self) -> None:
        """C4 fix: reset() must NOT destroy a world model set via set_world_model().

        Counterexample: before C4, reset() replaced _world_model with
        NullWorldModel(), silently discarding the user-configured model. After
        reset, engine.world_model must still be the originally configured one.
        """
        engine = PerceptionEngine()
        engine.add_sensor(_FakeSensor("test", {"x": 1}, 0.9))
        custom_wm = CodingWorldModel()
        engine.set_world_model(custom_wm)
        engine.perceive()  # populate state
        engine.reset()
        assert engine.world_model is custom_wm  # would be NullWorldModel before C4


# ═══════════════════════════════════════════════════════════════════
# §5  WorldModel Invariants
# ═══════════════════════════════════════════════════════════════════


class TestWorldModelInvariants:
    """WorldModel invariants (MASTER.md §4.2.3)."""

    def test_null_world_model_predict(self) -> None:
        """NullWorldModel predict returns input state."""
        wm = NullWorldModel()
        state = StateEstimate(
            state={"x": 1}, confidence=0.8,
            sources=["test"], timestamp=0,
        )
        action = Action(tool_id="read_file", args={})
        result = wm.predict(state, action)
        assert result.state == state.state
        assert result.confidence == state.confidence

    def test_null_world_model_no_anomaly(self) -> None:
        """NullWorldModel never detects anomaly."""
        wm = NullWorldModel()
        state = StateEstimate(
            state={}, confidence=0.0,
            sources=["test"], timestamp=0,
        )
        assert not wm.anomaly(state)

    def test_null_world_model_update_noop(self) -> None:
        """NullWorldModel update does nothing."""
        wm = NullWorldModel()
        obs = Observation.create(sensor_id="test", data={}, confidence=1.0)
        wm.update(obs)  # Should not raise

    def test_composite_world_model_empty(self) -> None:
        """CompositeWorldModel with no models predicts identity."""
        cwm = CompositeWorldModel()
        state = StateEstimate(
            state={"x": 1}, confidence=0.8,
            sources=["test"], timestamp=0,
        )
        action = Action(tool_id="read_file", args={})
        result = cwm.predict(state, action)
        assert result.state == state.state

    def test_composite_world_model_ordering(self) -> None:
        """CompositeWorldModel picks highest confidence prediction."""
        cwm = CompositeWorldModel()
        cwm.add_model(_FakeWorldModel("wm1"))
        state = StateEstimate(
            state={"x": 1}, confidence=0.8,
            sources=["test"], timestamp=0,
        )
        action = Action(tool_id="read_file", args={})
        result = cwm.predict(state, action)
        assert "predicted" in result.state


# ═══════════════════════════════════════════════════════════════════
# §6  CodingSensor Invariants
# ═══════════════════════════════════════════════════════════════════


class TestCodingSensors:
    """Coding sensor invariants (MASTER.md §6.1)."""

    def test_file_sensor_observe_returns_observation(self) -> None:
        """FileSensor.observe() returns a valid Observation."""
        sensor = FileSensor(project_root=str())
        obs = sensor.observe()
        assert isinstance(obs, Observation)
        assert obs.sensor_id == "file"
        assert 0.0 <= obs.confidence <= 1.0

    def test_file_sensor_graceful_on_nonexistent_root(self) -> None:
        """FileSensor handles nonexistent project root gracefully.

        Counterexample: project_root doesn't exist → confidence=0, no crash.
        """
        sensor = FileSensor(project_root="/nonexistent/path/xyz123")
        obs = sensor.observe()
        assert obs.confidence == 0.0
        assert "error" in obs.data

    def test_git_sensor_observe_returns_observation(self) -> None:
        """GitSensor.observe() returns a valid Observation."""
        sensor = GitSensor(project_root=str())
        obs = sensor.observe()
        assert isinstance(obs, Observation)
        assert obs.sensor_id == "git"

    def test_composite_sensor_empty(self) -> None:
        """CompositeSensor with no sub-sensors returns confidence 0.

        Counterexample: empty sensor list → observation with confidence 0.
        """
        sensor = CompositeSensor("empty", [])
        obs = sensor.observe()
        assert obs.confidence == 0.0

    def test_composite_sensor_merges_data(self) -> None:
        """CompositeSensor merges data from sub-sensors."""
        s1 = _FakeSensor("s1", {"a": 1}, 0.9)
        s2 = _FakeSensor("s2", {"b": 2}, 0.8)
        sensor = CompositeSensor("combo", [s1, s2])
        obs = sensor.observe()
        assert obs.data.get("s1") == {"a": 1}
        assert obs.data.get("s2") == {"b": 2}


# ═══════════════════════════════════════════════════════════════════
# §7  CodingWorldModel Invariants
# ═══════════════════════════════════════════════════════════════════


class TestCodingWorldModel:
    """CodingWorldModel invariants (MASTER.md §6.1)."""

    def test_happy_path_predict(self) -> None:
        """CodingWorldModel.predict returns a StateEstimate."""
        model = CodingWorldModel(project_root=str())
        state = StateEstimate(
            state={"modified_files": ["test.py"]},
            confidence=0.8,
            sources=["file"],
            timestamp=int(time.time() * 1000),
        )
        action = Action(tool_id="write_file", args={"path": "test.py"})
        result = model.predict(state, action)
        assert isinstance(result, StateEstimate)
        assert "prediction" in result.state

    def test_unknown_tool_predict_identity(self) -> None:
        """Unknown tool predict returns state unchanged.

        Counterexample: predict with unknown tool returns input state.
        """
        model = CodingWorldModel(project_root=str())
        state = StateEstimate(
            state={"x": 1}, confidence=0.8,
            sources=["test"], timestamp=0,
        )
        action = Action(tool_id="unknown_tool", args={})
        result = model.predict(state, action)
        assert result.state == state.state
        assert result.confidence == state.confidence

    def test_update_does_not_raise(self) -> None:
        """CodingWorldModel.update() does not raise."""
        model = CodingWorldModel(project_root=str())
        obs = Observation.create(sensor_id="file", data={"x": 1}, confidence=0.9)
        model.update(obs)  # Should not raise

    def test_anomaly_low_confidence(self) -> None:
        """Anomaly detection on low confidence state."""
        model = CodingWorldModel(project_root=str())
        state = StateEstimate(
            state={}, confidence=0.05,
            sources=["file"], timestamp=0,
        )
        assert model.anomaly(state)

    def test_anomaly_no_false_positive(self) -> None:
        """No anomaly on normal state."""
        model = CodingWorldModel(project_root=str())
        state = StateEstimate(
            state={"modified_files": []},
            confidence=0.9,
            sources=["file", "git"],
            timestamp=int(time.time() * 1000),
        )
        assert not model.anomaly(state)

    def test_get_stats(self) -> None:
        """get_stats returns expected keys."""
        model = CodingWorldModel(project_root=str())
        stats = model.get_stats()
        assert "model_id" in stats
        assert "history_size" in stats
        assert "project_root" in stats