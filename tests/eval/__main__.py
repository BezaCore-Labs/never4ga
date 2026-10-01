"""Run a corpus, or print the stamp a case needs.

    .venv/bin/python -m tests.eval report                      # the fixture corpus
    .venv/bin/python -m tests.eval report --vault ~/Vaults/x --cases <file>
    .venv/bin/python -m tests.eval stamp  --cases <file>

`stamp` prints; it never rewrites the file. The cases are hand-edited TOML with
comments in it, and rewriting a hand-edited TOML file loses them -- the same
reason `config.toml` is read and never written. Ratifying is a deliberate act,
so pasting the hash in is the deliberate act.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from tests.eval.corpus import load_cases
from tests.eval.fixture_vault import build_fixture_vault
from tests.eval.harness import render, run_corpus
from tests.eval.retrieval import vault_retriever
from tests.eval.session import indexed_vault

FIXTURE_CASES = Path(__file__).parent / "cases" / "fixture.toml"


def _report(
    vault: Path | None,
    cases_path: Path,
    k: int,
    include_drafts: bool,
    database: Path | None,
) -> int:
    cases = load_cases(cases_path)
    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        if vault is None:
            vault = scratch_path / "vault"
            vault.mkdir()
            build_fixture_vault(vault)
        index = scratch_path / "index.sqlite3"
        if database is not None:
            # Copied rather than opened. The measurement must not write to an
            # index a service owns, and a copy is also what makes the run
            # repeatable -- the same bytes measured twice.
            shutil.copy(database, index)
        with indexed_vault(vault, index) as session:
            by_depth = {
                depth: vault_retriever(
                    session.metadata,
                    session.text,
                    session.graph,
                    depth,
                )
                for depth in {case.depth for case in cases}
            }

            def retrieve(case, /):  # type: ignore[no-untyped-def]
                return by_depth[case.depth](case)

            run = run_corpus(cases, retrieve, k=k, include_drafts=include_drafts)
    print(f"corpus  {cases_path}")
    print(f"vault   {vault}")
    print()
    print(render(run))
    return 0


def _stamp(cases_path: Path) -> int:
    for case in load_cases(cases_path):
        stamp = f'ratified_hash = "{case.expectation_hash}"'
        print(f"{case.case_id:36s} {stamp}  ({case.ratification.value})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.eval")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="run a corpus and print the result")
    report.add_argument("--vault", type=Path, default=None, help="default: the fixture vault")
    report.add_argument("--cases", type=Path, default=FIXTURE_CASES)
    report.add_argument("-k", type=int, default=10)
    report.add_argument(
        "--include-drafts",
        action="store_true",
        help="score unratified cases too, to read them while ratifying; never a baseline",
    )
    report.add_argument(
        "--database",
        type=Path,
        default=None,
        help=("copy this existing index instead of building one"),
    )

    stamp = sub.add_parser("stamp", help="print the hash each case needs to be ratified")
    stamp.add_argument("--cases", type=Path, default=FIXTURE_CASES)

    arguments = parser.parse_args(argv)
    if arguments.command == "stamp":
        return _stamp(arguments.cases)
    return _report(
        arguments.vault,
        arguments.cases,
        arguments.k,
        arguments.include_drafts,
        arguments.database,
    )


if __name__ == "__main__":
    sys.exit(main())
