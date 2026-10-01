"""What the harness measures, and what it refuses to claim.

The harness reports four numbers -- Recall@k, the rank of the first required
result, MRR and latency -- and meets one obligation that matters more than any
of them: if the corpus cannot distinguish two configurations, it says so rather
than report a difference.
"""

from __future__ import annotations

import pytest

from tests.eval.metrics import (
    CaseResult,
    CorpusReport,
    compare,
    first_required_rank,
    recall_at_k,
    reciprocal_rank,
)


def _result(
    case_id: str,
    ranking: tuple[str, ...],
    required: tuple[str, ...],
    *,
    latency_ms: float = 1.0,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        ranking=ranking,
        required=required,
        forbidden=(),
        deprioritised=(),
        latency_ms=latency_ms,
        lanes={},
    )


class TestRecallAtK:
    def test_counts_only_the_first_k(self) -> None:
        ranking = ("a", "b", "c", "d")
        assert recall_at_k(ranking, ("a", "d"), k=2) == 0.5
        assert recall_at_k(ranking, ("a", "d"), k=4) == 1.0

    def test_a_case_requiring_nothing_is_not_a_free_pass(self) -> None:
        """It is a case that cannot be scored, not a case that scored 1.0."""
        with pytest.raises(ValueError, match="requires at least one document"):
            recall_at_k(("a",), (), k=10)

    def test_missing_everything_is_zero_rather_than_undefined(self) -> None:
        assert recall_at_k(("a", "b"), ("z",), k=10) == 0.0


class TestRankOfTheFirstRequiredResult:
    def test_is_one_based_because_a_human_reads_it(self) -> None:
        assert first_required_rank(("a", "b", "c"), ("b",)) == 2

    def test_is_none_when_nothing_required_was_found(self) -> None:
        assert first_required_rank(("a", "b"), ("z",)) is None

    def test_reciprocal_rank_is_zero_when_nothing_was_found(self) -> None:
        assert reciprocal_rank(("a",), ("z",)) == 0.0
        assert reciprocal_rank(("a", "b"), ("b",)) == 0.5


class TestViolations:
    def test_a_forbidden_document_is_judged_at_k_like_everything_else(self) -> None:
        """`forbids` means "not in the first k", not "nowhere in the tail".

        Retrieval is measured before the budget, so a lane that ORs its terms
        returns everything matching any of them -- including a document that
        matched only on a common word. That is `terms_from_task` working as
        documented, and holding it against the ranking's tail would make the
        corpus fail for a reason the design deliberately accepts.
        """
        result = CaseResult(
            case_id="one",
            ranking=("a", "b", "bad"),
            required=("a",),
            forbidden=("bad",),
            deprioritised=(),
            latency_ms=1.0,
            lanes={},
        )
        assert result.violations(k=2) == ()
        assert "forbidden bad" in result.violations(k=3)[0]


class TestTheCorpusRefusesToOverclaim:
    """The discrimination problem, stated in the output rather than around it."""

    def test_one_case_flipping_is_the_resolution_of_the_corpus(self) -> None:
        report = CorpusReport.of([_result(f"case-{n}", ("a",), ("a",)) for n in range(20)], k=10)
        assert report.case_count == 20
        assert report.single_case_swing == pytest.approx(0.05)

    def test_a_difference_one_case_could_explain_is_not_a_difference(self) -> None:
        """Twenty cases move Recall@k by whole points when one case changes."""
        baseline = CorpusReport.of(
            [_result(f"case-{n}", ("a",), ("a",)) for n in range(19)]
            + [_result("case-19", ("z",), ("a",))],
            k=10,
        )
        candidate = CorpusReport.of([_result(f"case-{n}", ("a",), ("a",)) for n in range(20)], k=10)
        comparison = compare(baseline, candidate)
        assert comparison.delta == pytest.approx(0.05)
        assert not comparison.distinguishable
        assert "one case" in comparison.verdict

    def test_a_difference_larger_than_any_single_case_is_reportable(self) -> None:
        baseline = CorpusReport.of([_result(f"case-{n}", ("z",), ("a",)) for n in range(20)], k=10)
        candidate = CorpusReport.of([_result(f"case-{n}", ("a",), ("a",)) for n in range(20)], k=10)
        comparison = compare(baseline, candidate)
        assert comparison.delta == pytest.approx(1.0)
        assert comparison.distinguishable

    def test_comparing_corpora_of_different_sizes_is_refused(self) -> None:
        baseline = CorpusReport.of([_result("a", ("a",), ("a",))], k=10)
        candidate = CorpusReport.of(
            [_result("a", ("a",), ("a",)), _result("b", ("b",), ("b",))], k=10
        )
        with pytest.raises(ValueError, match="same cases"):
            compare(baseline, candidate)

    def test_comparing_at_different_k_is_refused(self) -> None:
        results = [_result("a", ("a",), ("a",))]
        with pytest.raises(ValueError, match="same k"):
            compare(CorpusReport.of(results, k=5), CorpusReport.of(results, k=10))

    def test_an_empty_corpus_reports_nothing_rather_than_zero(self) -> None:
        with pytest.raises(ValueError, match="no ratified cases"):
            CorpusReport.of([], k=10)
