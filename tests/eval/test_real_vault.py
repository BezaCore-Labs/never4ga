"""A corpus built from a real vault, which can measure relevance. Opt-in, never in CI.

The synthetic corpus gates pull requests; a corpus built from a real vault is an
opt-in local run. Its material belongs to the vault's owner rather than the
repository, and its cases carry that person's relevance judgements, which
cannot be checked into a repository that anybody clones.

So the cases live in the vault, and this points at them:

```bash
NEVER4GA_EVAL_VAULT=~/path/to/vault \\
NEVER4GA_EVAL_CASES=~/path/to/eval-cases.toml \\
    .venv/bin/pytest tests/eval/test_real_vault.py -m eval_vault -s
```

Nothing here writes to the vault, and the index is built in a temporary
directory rather than against the one the running service owns.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.eval.corpus import Ratification, dangling_references, load_cases
from tests.eval.harness import render, run_corpus
from tests.eval.retrieval import vault_retriever
from tests.eval.session import indexed_vault

pytestmark = [
    pytest.mark.eval_vault,
    pytest.mark.skipif(
        not os.environ.get("NEVER4GA_EVAL_VAULT"),
        reason="set NEVER4GA_EVAL_VAULT and NEVER4GA_EVAL_CASES to measure a real vault",
    ),
]


@pytest.fixture(scope="module")
def cases_path() -> Path:
    raw = os.environ.get("NEVER4GA_EVAL_CASES")
    if not raw:
        pytest.skip("set NEVER4GA_EVAL_CASES to the ratified case file")
    return Path(raw).expanduser()


def test_report_the_corpus(cases_path: Path, tmp_path: Path) -> None:
    """Prints the report. It asserts almost nothing, on purpose.

    A real vault's numbers are a measurement to record, not a threshold to
    pass. A test that failed when Recall@k dipped would be a threshold nobody
    chose deliberately. What it does assert
    is the two things that would make the measurement meaningless: that
    somebody ratified the cases it counted, and that those cases are about
    documents the vault actually has. A corpus can be perfectly ratified and
    still measure nothing, if what it forbids no longer exists.
    """
    vault = Path(os.environ["NEVER4GA_EVAL_VAULT"]).expanduser()
    cases = load_cases(cases_path)

    # Before measuring, not after. An expectation about a document the vault
    # does not have cannot be wrong, so the cases carrying one report green
    # while testing nothing -- and the run's numbers are then a mean over
    # assertions that could not have failed.
    dangling = dangling_references(cases, vault)
    assert not dangling, "expectations name documents the vault does not have:\n  " + "\n  ".join(
        dangling
    )

    with indexed_vault(vault, tmp_path / "index.sqlite3") as session:
        retrievers = {
            depth: vault_retriever(session.metadata, session.text, session.graph, depth)
            for depth in {case.depth for case in cases}
        }
        run = run_corpus(cases, lambda case: retrievers[case.depth](case), k=10)

    print(f"\ncorpus  {cases_path}\nvault   {vault}\n")
    print(render(run))

    stale = [case.case_id for case in cases if case.ratification is Ratification.STALE]
    assert not stale, f"expectations changed since ratification: {', '.join(stale)}"
    assert run.results, "no ratified cases; nothing was measured"
