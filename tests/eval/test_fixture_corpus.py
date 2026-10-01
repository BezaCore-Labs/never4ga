"""The fixture corpus, end to end, over a real vault and real SQLite.

This is the regression gate: it runs in CI, it costs nothing external, and it
fails when a retrieval lane stops working. It is *not* a relevance measurement --
every expectation here is true by construction, so a green run says the
plumbing connects and says nothing about relevance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.corpus import Ratification, load_cases
from tests.eval.fixture_vault import build_fixture_vault
from tests.eval.harness import CorpusRun, render, run_corpus
from tests.eval.retrieval import vault_retriever
from tests.eval.session import indexed_vault

CASES = Path(__file__).parent / "cases" / "fixture.toml"


@pytest.fixture
def run(tmp_path: Path) -> CorpusRun:
    vault = tmp_path / "vault"
    vault.mkdir()
    build_fixture_vault(vault)
    cases = load_cases(CASES)
    with indexed_vault(vault, tmp_path / "index.sqlite3") as session:
        retrievers = {
            depth: vault_retriever(session.metadata, session.text, session.graph, depth)
            for depth in {case.depth for case in cases}
        }
        return run_corpus(cases, lambda case: retrievers[case.depth](case), k=10)


class TestTheShippedCases:
    def test_none_of_them_is_stale(self) -> None:
        """A stale stamp is somebody having edited an expectation without re-reading it."""
        stale = [
            case.case_id for case in load_cases(CASES) if case.ratification is Ratification.STALE
        ]
        assert not stale, f"re-read and re-stamp: {', '.join(stale)}"

    def test_all_of_them_count(self) -> None:
        assert all(case.counts for case in load_cases(CASES))

    def test_they_are_ratified_by_construction_rather_than_by_a_person(self) -> None:
        """No shipped case may claim a relevance judgement made by a person."""
        assert {case.ratified_by for case in load_cases(CASES)} == {"construction"}


class TestTheLanesStillWork:
    def test_every_expectation_is_met(self, run: CorpusRun) -> None:
        assert run.report.failures == (), render(run)

    def test_everything_required_is_found(self, run: CorpusRun) -> None:
        assert run.report.recall == 1.0, render(run)

    def test_the_answer_is_at_or_near_the_top(self, run: CorpusRun) -> None:
        """A floor, not a pin, set below a perfect score on purpose.

        Because the relational lane corroborates, a well-linked document
        outranks the answer in the deep case: the `30_Knowledge/` note that
        answers it is barely linked to anything, and the workspace document
        beside it is linked to everything. That is the lane working as designed,
        not a defect. It is also the clearest argument in the fixture for what a
        semantic lane would be *for*, since the right answer is the one no
        relationship points at.
        """
        assert run.report.mrr >= 0.61, render(run)

    def test_the_lexical_lane_found_every_required_document(self, run: CorpusRun) -> None:
        assert run.report.lanes["text"] == 3, render(run)

    def test_the_report_says_how_far_one_case_moves_it(self, run: CorpusRun) -> None:
        assert run.report.single_case_swing == pytest.approx(1 / 3)
        assert "one case flipping" in render(run)
