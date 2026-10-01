"""Running a corpus, and what the run refuses to do.

A run reports each lane's contribution so that a failure is diagnosable, and a
baseline is recorded before anything is added. Both are properties of the run
rather than of a metric, so they are tested here.
"""

from __future__ import annotations

import pytest

from tests.eval.corpus import EvalCase, Ratification
from tests.eval.harness import RankedResult, Retriever, render, run_case, run_corpus


def _case(case_id: str, *, ratification: Ratification = Ratification.RATIFIED) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        task="scope resolution",
        workspace="Never4gA",
        depth="focused",
        requires=("a.md", "b.md"),
        forbids=("bad.md",),
        deprioritises=("old.md",),
        why="",
        ratification=ratification,
        ratification_note="note",
        expectation_hash="deadbeef",
    )


def _retriever(result: RankedResult) -> Retriever:
    def retrieve(case: EvalCase) -> RankedResult:
        return result

    return retrieve


class TestOneCase:
    def test_records_what_was_found_and_how_long_it_took(self) -> None:
        found = RankedResult(ranking=("a.md", "b.md"), lanes={"text": ("a.md", "b.md")})
        result = run_case(_case("one"), _retriever(found), k=10)
        assert result.recall_at(10) == 1.0
        assert result.first_required == 1
        assert result.latency_ms >= 0.0

    def test_lanes_are_credited_only_for_documents_the_case_required(self) -> None:
        """A lane that found ten irrelevant documents contributed nothing."""
        found = RankedResult(
            ranking=("a.md", "noise.md"),
            lanes={"text": ("a.md", "noise.md"), "graph": ("noise.md",)},
        )
        result = run_case(_case("one"), _retriever(found), k=10)
        assert result.lanes == {"text": 1, "graph": 0}


class TestAWholeCorpus:
    def test_an_unratified_case_is_skipped_and_named(self) -> None:
        cases = [_case("counted"), _case("drafted", ratification=Ratification.DRAFT)]
        found = RankedResult(ranking=("a.md", "b.md"), lanes={"text": ("a.md", "b.md")})
        run = run_corpus(cases, _retriever(found), k=10)
        assert [result.case_id for result in run.results] == ["counted"]
        assert run.skipped == (("drafted", "note"),)

    def test_a_corpus_of_nothing_but_drafts_reports_that_rather_than_a_score(self) -> None:
        run = run_corpus(
            [_case("drafted", ratification=Ratification.DRAFT)],
            _retriever(RankedResult(ranking=(), lanes={})),
            k=10,
        )
        assert run.results == ()
        with pytest.raises(ValueError, match="no ratified cases"):
            _ = run.report

    def test_the_rendered_report_states_the_resolution_of_the_corpus(self) -> None:
        cases = [_case(f"case-{n}") for n in range(4)]
        found = RankedResult(ranking=("a.md", "b.md"), lanes={"text": ("a.md", "b.md")})
        text = render(run_corpus(cases, _retriever(found), k=10))
        assert "Recall@10" in text
        assert "0.250" in text  # one case in four
        assert "one case" in text

    def test_the_rendered_report_names_every_skipped_case(self) -> None:
        run = run_corpus(
            [_case("counted"), _case("drafted", ratification=Ratification.DRAFT)],
            _retriever(RankedResult(ranking=("a.md", "b.md"), lanes={})),
            k=10,
        )
        assert "drafted" in render(run)

    def test_the_rendered_report_names_violations_rather_than_only_counting_them(self) -> None:
        found = RankedResult(ranking=("bad.md", "a.md"), lanes={})
        text = render(run_corpus([_case("one")], _retriever(found), k=10))
        assert "forbidden bad.md" in text
        assert "required b.md" in text


class TestProvisionalRuns:
    """Drafts can be run to be read, never to be recorded.

    Ratifying a case means knowing what it does. Running it is how you find
    out, so the harness will, and then says on every line of the output that
    the result is not a baseline.
    """

    def test_drafts_run_when_asked_and_the_run_says_it_is_provisional(self) -> None:
        cases = [_case("counted"), _case("drafted", ratification=Ratification.DRAFT)]
        found = RankedResult(ranking=("a.md", "b.md"), lanes={"text": ("a.md", "b.md")})
        run = run_corpus(cases, _retriever(found), k=10, include_drafts=True)
        assert [result.case_id for result in run.results] == ["counted", "drafted"]
        assert run.provisional

    def test_a_provisional_report_refuses_to_read_as_a_baseline(self) -> None:
        run = run_corpus(
            [_case("drafted", ratification=Ratification.DRAFT)],
            _retriever(RankedResult(ranking=("a.md", "b.md"), lanes={})),
            k=10,
            include_drafts=True,
        )
        text = render(run)
        assert "PROVISIONAL" in text
        assert "not a baseline" in text

    def test_a_provisional_report_does_not_call_the_drafts_ratified(self) -> None:
        """The word "ratified" in a report must mean somebody ratified it."""
        run = run_corpus(
            [_case("counted"), _case("drafted", ratification=Ratification.DRAFT)],
            _retriever(RankedResult(ranking=("a.md", "b.md"), lanes={})),
            k=10,
            include_drafts=True,
        )
        text = render(run)
        assert "1 ratified, 1 unratified" in text
        assert "2 ratified" not in text

    def test_a_normal_run_is_never_provisional(self) -> None:
        run = run_corpus(
            [_case("counted")],
            _retriever(RankedResult(ranking=("a.md", "b.md"), lanes={})),
            k=10,
        )
        assert not run.provisional
        assert "PROVISIONAL" not in render(run)
