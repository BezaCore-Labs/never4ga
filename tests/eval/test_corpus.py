"""Eval cases, and what makes one count.

A case may be drafted by an agent, but it counts only once a person has
confirmed or struck every expectation line in it. So a case carries its own
ratification, and the ratification is over the expectation lines rather than
over the case's name: editing what was approved must un-approve it, or the
signature means nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.corpus import (
    CaseFormatError,
    EvalCase,
    Ratification,
    dangling_references,
    load_cases,
    ratified,
)

RATIFIED_CASE = """
[[case]]
id = "scope-resolution"
task = "how does the scope resolver find a workspace"
workspace = "Never4gA"
depth = "focused"
requires = ["30_Knowledge/Notes/scope-resolver.md"]
forbids = ["30_Knowledge/Notes/coffee.md"]
deprioritises = ["Decisions/adr-0001-superseded.md"]
why = "the resolver note is the only document that describes the walk-up"
ratified_by = "maintainer"
ratified_at = "2026-08-28"
ratified_hash = "PLACEHOLDER"
"""

DRAFT_CASE = """
[[case]]
id = "draft-case"
task = "something nobody has approved"
workspace = "Never4gA"
depth = "focused"
requires = ["30_Knowledge/Notes/whatever.md"]
why = "drafted by an agent, not yet read by anyone"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cases.toml"
    path.write_text(text)
    return path


def _ratify(text: str) -> str:
    """Stamp the case with the hash of what it currently says."""
    case = load_cases_ignoring_ratification(text)
    return text.replace("PLACEHOLDER", case.expectation_hash)


def load_cases_ignoring_ratification(text: str, tmp: Path | None = None) -> EvalCase:
    import tempfile

    directory = tmp or Path(tempfile.mkdtemp())
    path = directory / "cases.toml"
    path.write_text(text)
    return load_cases(path)[0]


class TestACaseCountsOnlyWhenItWasRatified:
    def test_a_drafted_case_loads_and_does_not_count(self, tmp_path: Path) -> None:
        cases = load_cases(_write(tmp_path, DRAFT_CASE))
        assert cases[0].ratification is Ratification.DRAFT
        assert ratified(cases) == ()

    def test_a_ratified_case_counts(self, tmp_path: Path) -> None:
        cases = load_cases(_write(tmp_path, _ratify(RATIFIED_CASE)))
        assert cases[0].ratification is Ratification.RATIFIED
        assert len(ratified(cases)) == 1

    def test_a_case_records_who_ratified_it(self, tmp_path: Path) -> None:
        """The synthetic corpus is true by construction; the real one is a judgement.

        Both carry the same hash guard, and only the name says which is which,
        so a recorded baseline cannot quietly present one as the other.
        """
        case = load_cases(_write(tmp_path, _ratify(RATIFIED_CASE)))[0]
        assert case.ratified_by == "maintainer"

    def test_editing_an_expectation_revokes_the_ratification(self, tmp_path: Path) -> None:
        """The whole point. A signature over a document that changed is a lie."""
        text = _ratify(RATIFIED_CASE).replace(
            '"30_Knowledge/Notes/scope-resolver.md"',
            '"30_Knowledge/Notes/scope-resolver.md", "30_Knowledge/Notes/added.md"',
        )
        cases = load_cases(_write(tmp_path, text))
        assert cases[0].ratification is Ratification.STALE
        assert ratified(cases) == ()

    def test_editing_the_prose_does_not_revoke_it(self, tmp_path: Path) -> None:
        """`why` is a note for the reader, not an expectation the harness runs."""
        text = _ratify(RATIFIED_CASE).replace(
            "the resolver note is the only document that describes the walk-up",
            "reworded entirely",
        )
        assert load_cases(_write(tmp_path, text))[0].ratification is Ratification.RATIFIED

    def test_a_stale_ratification_names_what_to_do(self, tmp_path: Path) -> None:
        text = _ratify(RATIFIED_CASE).replace("scope-resolver.md", "renamed.md")
        case = load_cases(_write(tmp_path, text))[0]
        assert case.expectation_hash in case.ratification_note
        assert "re-read" in case.ratification_note


class TestTheFileIsRefusedRatherThanGuessedAt:
    def test_a_case_with_no_requirement_is_refused(self, tmp_path: Path) -> None:
        text = DRAFT_CASE.replace('requires = ["30_Knowledge/Notes/whatever.md"]', "requires = []")
        with pytest.raises(CaseFormatError, match="requires"):
            load_cases(_write(tmp_path, text))

    def test_a_duplicate_id_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CaseFormatError, match="draft-case"):
            load_cases(_write(tmp_path, DRAFT_CASE + DRAFT_CASE))

    def test_an_unknown_depth_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CaseFormatError, match="startup"):
            load_cases(_write(tmp_path, DRAFT_CASE.replace('"focused"', '"startup"')))

    def test_a_document_required_and_forbidden_is_refused(self, tmp_path: Path) -> None:
        text = DRAFT_CASE.replace("why =", 'forbids = ["30_Knowledge/Notes/whatever.md"]\nwhy =')
        with pytest.raises(CaseFormatError, match="both required and forbidden"):
            load_cases(_write(tmp_path, text))


class TestAnExpectationAboutAMissingDocumentCannotPass:
    """A `forbids` on a document that does not exist can never fail.

    A missing required document costs recall, so it is noticed on the next run.
    A vacuous `forbids` costs nothing and *raises* the mean: when a vault
    deletes a forbidden document, the case goes inert and keeps reporting green.

    A real-vault case file depends on that vault's mutable content by
    construction. Nothing can stop that; what this stops is the dependency
    breaking quietly.
    """

    CASE = """
[[case]]
id = "probe"
task = "what governs retrieval"
workspace = "Never4gA"
depth = "focused"
requires = ["30_Knowledge/Notes/present.md"]
forbids = ["Brand/typography.md"]
deprioritises = ["Decisions/adr-0005-archived.md"]
why = "the typography note is the stopword-pollution probe"
"""

    @pytest.fixture
    def vault(self, tmp_path: Path) -> Path:
        root = tmp_path / "vault"
        (root / "30_Knowledge" / "Notes").mkdir(parents=True)
        (root / "30_Knowledge" / "Notes" / "present.md").write_text("# Present\n")
        return root

    def test_a_case_whose_documents_all_exist_reports_nothing(
        self, vault: Path, tmp_path: Path
    ) -> None:
        (vault / "Brand").mkdir()
        (vault / "Brand" / "typography.md").write_text("# Typography\n")
        (vault / "Decisions").mkdir()
        (vault / "Decisions" / "adr-0005-archived.md").write_text("# Archived\n")
        cases = load_cases(_write(tmp_path, self.CASE))

        assert dangling_references(cases, vault) == ()

    def test_a_forbidden_document_that_does_not_exist_is_reported(
        self, vault: Path, tmp_path: Path
    ) -> None:
        """The silent one, and the reason this exists."""
        cases = load_cases(_write(tmp_path, self.CASE))

        reported = dangling_references(cases, vault)
        assert any("Brand/typography.md" in line for line in reported)

    def test_it_says_which_case_and_which_expectation(self, vault: Path, tmp_path: Path) -> None:
        """Several dangling references across a corpus need to be told apart."""
        cases = load_cases(_write(tmp_path, self.CASE))

        reported = dangling_references(cases, vault)
        line = next(line for line in reported if "typography" in line)
        assert "probe" in line
        assert "forbids" in line

    def test_every_kind_of_expectation_is_checked(self, vault: Path, tmp_path: Path) -> None:
        """A `deprioritises` on a missing document is vacuous in the same way.

        It scores by rank, so a document that cannot be retrieved can never be
        ranked badly, and the case collects the credit for free.
        """
        cases = load_cases(_write(tmp_path, self.CASE))

        reported = dangling_references(cases, vault)
        assert any("adr-0005-archived.md" in line for line in reported)
        assert any("deprioritises" in line for line in reported)

    def test_a_missing_required_document_is_reported_too(self, vault: Path, tmp_path: Path) -> None:
        """It fails loudly on its own, but silently *first* -- as a zero.

        A recall of 0.0 reads as retrieval being bad at the question, not as
        the question naming a document nobody has. Saying which it is costs one
        line.
        """
        missing = self.CASE.replace("Notes/present.md", "Notes/gone.md")
        cases = load_cases(_write(tmp_path, missing))

        assert any(
            "gone.md" in line and "requires" in line for line in dangling_references(cases, vault)
        )
