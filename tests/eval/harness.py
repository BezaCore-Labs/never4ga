"""Running an eval corpus and reporting what happened.

The harness is deliberately ignorant of *how* retrieval works: it takes a
callable that answers a case with a ranking, so the same corpus runs against
any lane configuration, and two runs are comparable because nothing but the
callable changed.

:func:`render` always shows how far one case can move a number. A number
reported without that is an overclaim.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from tests.eval.corpus import EvalCase, ratified
from tests.eval.metrics import CaseResult, CorpusReport

__all__ = [
    "CorpusRun",
    "RankedResult",
    "Retriever",
    "render",
    "run_case",
    "run_corpus",
]


@dataclass(frozen=True, slots=True)
class RankedResult:
    """What one configuration answered a case with.

    ``lanes`` maps a retriever's name to the documents it voted for, which is
    what makes a failure diagnosable: a case can miss because the lexical lane
    never found the document, or because it found it and fusion buried it.
    """

    ranking: tuple[str, ...]
    lanes: Mapping[str, tuple[str, ...]]


Retriever = Callable[[EvalCase], RankedResult]


def run_case(case: EvalCase, retriever: Retriever, k: int) -> CaseResult:
    started = time.perf_counter()
    found = retriever(case)
    latency_ms = (time.perf_counter() - started) * 1000

    required = set(case.requires)
    return CaseResult(
        case_id=case.case_id,
        ranking=found.ranking,
        required=case.requires,
        forbidden=case.forbids,
        deprioritised=case.deprioritises,
        latency_ms=latency_ms,
        # A lane is credited for what the case asked for, not for how much it
        # returned. Counting everything would rank a lane by verbosity.
        lanes={
            lane: sum(1 for document in documents if document in required)
            for lane, documents in found.lanes.items()
        },
    )


@dataclass(frozen=True, slots=True)
class CorpusRun:
    """One configuration over one corpus, with the drafts it did not score."""

    results: tuple[CaseResult, ...]
    skipped: tuple[tuple[str, str], ...]
    k: int
    #: True when unratified cases were scored. Such a run exists to be read
    #: while ratifying, and may never be recorded as a baseline: its numbers
    #: rest on expectations nobody has agreed to yet.
    provisional: bool = False
    #: Which of the scored cases were actually ratified. Empty on a normal run,
    #: where every scored case was.
    ratified_ids: frozenset[str] = frozenset()

    @property
    def report(self) -> CorpusReport:
        """The numbers. Raises when nothing was ratified, rather than scoring zero."""
        return CorpusReport.of(self.results, self.k)


def run_corpus(
    cases: Sequence[EvalCase],
    retriever: Retriever,
    k: int,
    *,
    include_drafts: bool = False,
) -> CorpusRun:
    """Run every ratified case, and record the rest as a queue rather than a failure.

    ``include_drafts`` scores the queue too. It is for reading a case before
    ratifying it -- knowing what twenty cases do is most of the work of agreeing
    to them -- and it marks the whole run provisional, so its numbers cannot be
    mistaken for a measurement.
    """
    counted = cases if include_drafts else ratified(cases)
    drafts = tuple((case.case_id, case.ratification_note) for case in cases if not case.counts)
    return CorpusRun(
        results=tuple(run_case(case, retriever, k) for case in counted),
        skipped=() if include_drafts else drafts,
        k=k,
        provisional=include_drafts and bool(drafts),
        ratified_ids=frozenset(case.case_id for case in cases if case.counts),
    )


def render(run: CorpusRun) -> str:
    """The report, in the form it is recorded in as a baseline."""
    lines: list[str] = []
    if run.provisional:
        lines.append("PROVISIONAL -- unratified cases were scored, so this is not a baseline")
        lines.append("")
    if not run.results:
        lines.append(f"no ratified cases; {len(run.skipped)} awaiting ratification")
    else:
        report = run.report
        if run.provisional:
            counted = sum(1 for result in run.results if result.case_id in run.ratified_ids)
            lines.append(
                f"cases        {counted} ratified, "
                f"{report.case_count - counted} unratified, all scored"
            )
        else:
            lines.append(f"cases        {report.case_count} ratified, {len(run.skipped)} skipped")
        lines.append(f"Recall@{report.k}    {report.recall:.3f}")
        lines.append(f"MRR          {report.mrr:.3f}")
        lines.append(
            f"latency      {report.median_latency_ms:.1f} ms median, "
            f"{report.slowest_latency_ms:.1f} ms slowest"
        )
        if report.lanes:
            contribution = ", ".join(
                f"{lane} {count}" for lane, count in sorted(report.lanes.items())
            )
            lines.append(f"lanes        {contribution} (required documents found)")
        lines.append(
            f"resolution   {report.single_case_swing:.3f} -- one case flipping moves "
            f"Recall@{report.k} by that much, and a smaller difference than that "
            f"is not a difference this corpus can report"
        )
        if report.failures:
            lines.append("")
            lines.append("unmet expectations")
            for case_id, violations in report.failures:
                for violation in violations:
                    lines.append(f"  {case_id}: {violation}")

    if run.skipped:
        lines.append("")
        lines.append("not scored")
        for case_id, note in run.skipped:
            lines.append(f"  {case_id}: {note}")
    return "\n".join(lines)
