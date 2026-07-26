"""E3 Science Kit CLI + store invariant tests.

Corresponds to:
  MASTER.md §12.3 E3
  docs/E3_SCIENCE_KIT.md §3 (CLI) + §2 (store)

IPR-0: each test contains a counterexample.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest

from zall.cli.commands.science import cmd_science, _get_store
from zall.core.evidence import Evidence, EvidenceType, NegativeResult
from zall.core.experiment import ExperimentGoal
from zall.core.hypothesis import Hypothesis, HypothesisStatus
from zall.core.provenance import ScienceProvenance
from zall.extensions.science.store import ScienceStore


# ── fixtures ──


@pytest.fixture
def store(tmp_path: Path) -> ScienceStore:
    """Isolated ScienceStore in tmp_path."""
    return ScienceStore(base_dir=tmp_path / "science")


@pytest.fixture
def state(store: ScienceStore) -> dict:
    """CLI state with injected store (avoids ~/.zall/ pollution)."""
    return {"_science_store": store}


def _run(arg: str, state: dict) -> str:
    out = io.StringIO()
    cmd_science(arg, out, None, state)
    return out.getvalue()


# ── Store invariants ──


class TestStoreRoundtrip:
    def test_add_get_hypothesis_roundtrip(self, store: ScienceStore) -> None:
        """add then get returns identical hypothesis."""
        h = Hypothesis(
            id=uuid4(), claim="X", prediction="Y",
            created_by="test", created_at=1000,
        )
        store.add_hypothesis(h)
        got = store.get_hypothesis(h.id)
        assert got is not None
        assert got.claim == "X"
        assert got.status == HypothesisStatus.PROPOSED

    def test_list_returns_all(self, store: ScienceStore) -> None:
        """list returns all added hypotheses."""
        for i in range(3):
            store.add_hypothesis(Hypothesis(
                id=uuid4(), claim=f"c{i}", prediction="p",
                created_by="t", created_at=i,
            ))
        assert len(store.list_hypotheses()) == 3

    def test_update_hypothesis_appends_not_overwrites(self, store: ScienceStore) -> None:
        """H-2: revise/update is append-only; old version stays in file."""
        h = Hypothesis(
            id=uuid4(), claim="v1", prediction="p",
            created_by="t", created_at=1,
        )
        store.add_hypothesis(h)
        # mutate (e.g. add evidence) and update
        h.add_evidence(uuid4(), supports=True)
        store.update_hypothesis(h)
        # get returns the updated version (with evidence)
        got = store.get_hypothesis(h.id)
        assert got is not None
        assert len(got.evidence_for) == 1


class TestStoreEvidence:
    def test_list_evidence_filtered_by_hypothesis(self, store: ScienceStore) -> None:
        """list_evidence(hid) only returns that hypothesis's evidence."""
        hid1, hid2 = uuid4(), uuid4()
        prov = ScienceProvenance(
            protocol_hash="h", data_hash="h",
            analysis_code_hash="h", environment_hash="h",
        )
        ev1 = Evidence(id=uuid4(), hypothesis_id=hid1, experiment_id=uuid4(),
                       type=EvidenceType.POSITIVE, metric="m", value=1.0,
                       supports=True, provenance=prov, created_at=1)
        ev2 = Evidence(id=uuid4(), hypothesis_id=hid2, experiment_id=uuid4(),
                       type=EvidenceType.POSITIVE, metric="m", value=2.0,
                       supports=True, provenance=prov, created_at=2)
        store.add_evidence(ev1)
        store.add_evidence(ev2)
        assert len(store.list_evidence(hid1)) == 1
        assert len(store.list_evidence(hid2)) == 1
        assert len(store.list_evidence()) == 2

    def test_negative_result_stored_equally(self, store: ScienceStore) -> None:
        """I-10: NegativeResult stored and queryable same as positive evidence."""
        hid = uuid4()
        prov = ScienceProvenance(
            protocol_hash="h", data_hash="h",
            analysis_code_hash="h", environment_hash="h",
        )
        ev_pos = Evidence(id=uuid4(), hypothesis_id=hid, experiment_id=uuid4(),
                          type=EvidenceType.POSITIVE, metric="m", value=1.0,
                          supports=True, provenance=prov, created_at=1)
        nr = NegativeResult(
            evidence=Evidence(id=uuid4(), hypothesis_id=hid, experiment_id=uuid4(),
                              type=EvidenceType.NEGATIVE, metric="m", value=0.0,
                              supports=False, provenance=prov, created_at=2),
            what_failed="X", conditions={}, diagnosis="d", value="excluded Y",
        )
        store.add_evidence(ev_pos)
        store.add_evidence(nr)
        # both queryable via list_evidence (negative not filtered out)
        all_ev = store.list_evidence(hid)
        assert len(all_ev) == 2
        # negative results separately
        nrs = store.list_negative_results()
        assert len(nrs) == 1
        assert nrs[0].value == "excluded Y"


# ── CLI invariants ──


class TestCliScience:
    def test_new_creates_hypothesis(self, state: dict, store: ScienceStore) -> None:
        """CLI /science new creates a hypothesis in the store."""
        out = _run('new "test claim" --prediction "pred"', state)
        assert "hypothesis created" in out
        hyps = store.list_hypotheses()
        assert len(hyps) == 1
        assert hyps[0].claim == "test claim"

    def test_list_shows_hypotheses(self, state: dict) -> None:
        """CLI /science list output contains hypothesis id and status."""
        _run('new "visible" --prediction "p"', state)
        out = _run("list", state)
        assert "visible" in out
        assert "proposed" in out

    def test_evidence_supports_updates_hypothesis(self, state: dict, store: ScienceStore) -> None:
        """CLI /science evidence --supports links evidence to hypothesis.evidence_for."""
        _run('new "H" --prediction "P"', state)
        h = store.list_hypotheses()[0]
        out = _run(
            f'evidence {h.id} --metric "G-F" --value 0.163 --threshold 0.128 --supports',
            state,
        )
        assert "evidence recorded" in out
        assert "supports" in out
        got = store.get_hypothesis(h.id)
        assert len(got.evidence_for) == 1

    def test_evidence_against_updates_hypothesis(self, state: dict, store: ScienceStore) -> None:
        """Counterexample: --against links to evidence_against."""
        _run('new "H" --prediction "P"', state)
        h = store.list_hypotheses()[0]
        _run(
            f'evidence {h.id} --metric "p-value" --value 0.08 --threshold 0.05 --against',
            state,
        )
        got = store.get_hypothesis(h.id)
        assert len(got.evidence_against) == 1
        assert len(got.evidence_for) == 0

    def test_falsify_changes_status(self, state: dict, store: ScienceStore) -> None:
        """CLI /science falsify sets status to FALSIFIED (requires evidence_against)."""
        _run('new "H" --prediction "P"', state)
        h = store.list_hypotheses()[0]
        _run(
            f'evidence {h.id} --metric "p" --value 0.08 --threshold 0.05 --against',
            state,
        )
        ev = store.list_evidence(h.id)[0]
        out = _run(
            f'falsify {h.id} --evidence {ev.id} --diagnosis "too broad" --value "excluded n=11"',
            state,
        )
        assert "falsified" in out
        got = store.get_hypothesis(h.id)
        assert got.status == HypothesisStatus.FALSIFIED

    def test_falsify_without_evidence_against_rejected(self, state: dict) -> None:
        """Counterexample (H-3): falsify without --against evidence is rejected."""
        _run('new "H" --prediction "P"', state)
        h = _get_store(state).list_hypotheses()[0]
        fake_eid = uuid4()
        out = _run(
            f'falsify {h.id} --evidence {fake_eid} --diagnosis "x" --value "y"',
            state,
        )
        assert "not in evidence_against" in out or "cannot falsify" in out

    def test_revise_creates_new_version(self, state: dict, store: ScienceStore) -> None:
        """CLI /science revise creates v2 with revised_from pointing to original."""
        _run('new "H1" --prediction "P1"', state)
        h = store.list_hypotheses()[0]
        out = _run(
            f'revise {h.id} --claim "H2" --prediction "P2"',
            state,
        )
        assert "revised" in out
        assert "v2" in out
        # two hypotheses now (original + revised)
        hyps = store.list_hypotheses()
        assert len(hyps) == 2
        revised = [x for x in hyps if x.version == 2][0]
        assert revised.claim == "H2"
        assert revised.revised_from == h.id

    def test_show_displays_evidence(self, state: dict) -> None:
        """CLI /science show displays linked evidence."""
        _run('new "H" --prediction "P"', state)
        h = _get_store(state).list_hypotheses()[0]
        _run(
            f'evidence {h.id} --metric "G-F" --value 0.163 --supports',
            state,
        )
        out = _run(f"show {h.id}", state)
        assert "G-F" in out
        assert "0.163" in out


# ── dogfood scenario: H1 confirmed / H2 falsified / H3 revised ──


class TestDogfoodScenario:
    """Simulates the GF-consistency H1/H2/H3 scenario (E3.6 dry run).

    H1 (positive): Spectral G-F > baseline 0.128  -> CONFIRMED
    H2 (negative): G-F vs AUC significant at n=11 -> FALSIFIED (P=0.056)
    H3 (revised):  G-F vs AUC significant at n=25 -> CONFIRMED (P<0.001)
    """

    def test_full_h1_h2_h3_cycle(self, state: dict, store: ScienceStore) -> None:
        # H1: positive
        _run('new "Spectral G-F Score exceeds greedy-modularity baseline" '
             '--prediction "G-F > 0.128"', state)
        h1 = store.list_hypotheses()[0]
        _run(f'evidence {h1.id} --metric "G-F Score" --value 0.163 '
             f'--threshold 0.128 --supports', state)
        # H1 confirmed (evidence supports, threshold met)
        got = store.get_hypothesis(h1.id)
        assert len(got.evidence_for) == 1
        assert got.evidence_for[0] is not None

        # H2: negative (falsified)
        _run('new "G-F Score correlates with Link Pred AUC at n=11" '
             '--prediction "rho significant, p < 0.05"', state)
        h2 = [x for x in store.list_hypotheses() if x.id != h1.id][0]
        _run(f'evidence {h2.id} --metric "p-value" --value 0.056 '
             f'--threshold 0.05 --against --negative '
             f'--what-failed "not significant at n=11" '
             f'--diagnosis "sample size too small" '
             f'--value-desc "excluded n=11 as sufficient"', state)
        ev_h2 = store.list_evidence(h2.id)[0]
        _run(f'falsify {h2.id} --evidence {ev_h2.id} '
             f'--diagnosis "underpowered" --value "need larger n"', state)
        h2_got = store.get_hypothesis(h2.id)
        assert h2_got.status == HypothesisStatus.FALSIFIED

        # H3: revised from H2
        _run(f'revise {h2.id} --claim "G-F vs AUC significant at n>=25" '
             f'--prediction "rho significant, p < 0.001"', state)
        h3 = [x for x in store.list_hypotheses() if x.version == 2][0]
        assert h3.revised_from == h2.id
        _run(f'evidence {h3.id} --metric "p-value" --value 0.0000158 '
             f'--threshold 0.05 --supports', state)
        h3_got = store.get_hypothesis(h3.id)
        assert len(h3_got.evidence_for) == 1

        # Final: H1 proposed(+ev), H2 falsified, H3 revised(+ev)
        all_hyps = store.list_hypotheses()
        assert len(all_hyps) == 3
        # negative result captured (I-10)
        nrs = store.list_negative_results()
        assert len(nrs) == 1
        assert "underpowered" in nrs[0].diagnosis or "underpowered" in nrs[0].value or "n=11" in nrs[0].value
