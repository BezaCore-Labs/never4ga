"""A title becomes a filename without fusing words or losing a field boundary.

The vault's naming convention: a name is one or more *fields*, fields are
separated by `_`, and words inside a field are joined by `-`. Read `_` as "and
now a different kind of thing", `-` as "still the same thing".

A character that cannot appear in a filename is a separator, not something to
delete. Deleting it fuses the words either side: `Ratio 3:1` would become
`ratio-31`, which reads as thirty-one. That is a different name, and nothing
would report it because the result is a valid filename.

A spaced dash in a title, as in `Milestone 6 — Scope Questions`, separates an
identifier from a description. Those are two different kinds of thing, which is
what `_` is for.
"""

from __future__ import annotations

from datetime import date

import pytest

from never4ga.services.creation import (
    ConceptCreationError,
    dated_filename_for,
    directory_name_for,
    filename_for,
)


class TestASeparatorSeparates:
    """A removed character must not fuse the words either side of it."""

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Semantic/Vector Retrieval", "semantic-vector-retrieval.md"),
            ("Read/Write Policy", "read-write-policy.md"),
            ("Ratio 3:1", "ratio-3-1.md"),
            ("Either|Or", "either-or.md"),
            ("What? Why!", "what-why.md"),
        ],
    )
    def test_a_stripped_character_leaves_a_boundary(self, title: str, expected: str) -> None:
        assert filename_for(title) == expected

    def test_it_never_silently_joins_two_words(self) -> None:
        assert "semanticvector" not in filename_for("Semantic/Vector")


class TestAFieldBoundaryBecomesAnUnderscore:
    """`_` separates fields, `-` joins words within one."""

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            (
                "ADR-0020 — Use Postgres for Reports",
                "adr-0020_use-postgres-for-reports.md",
            ),
            ("Milestone 6 — Scope Questions", "milestone-6_scope-questions.md"),
            ("Milestone 6 -- Scope Questions", "milestone-6_scope-questions.md"),
            # An en dash, written as an escape: ruff RUF001 rightly objects to one
            # sitting unannounced in source, and it is the character a person
            # actually types, so it has to be covered.
            ("Milestone 6 \u2013 Scope Questions", "milestone-6_scope-questions.md"),
        ],
    )
    def test_a_spaced_dash_marks_a_field(self, title: str, expected: str) -> None:
        assert filename_for(title) == expected

    def test_a_hyphen_inside_a_word_is_not_a_field_boundary(self) -> None:
        """`well-known` is one idea; only a *spaced* dash divides fields."""
        assert filename_for("A well-known problem") == "a-well-known-problem.md"

    def test_an_identifier_keeps_its_internal_hyphen(self) -> None:
        assert filename_for("ADR-0020 — Something").startswith("adr-0020_")

    def test_more_than_one_field_is_kept(self) -> None:
        assert (
            filename_for("Green Eggs and Ham — Dr Seuss — 1985")
            == "green-eggs-and-ham_dr-seuss_1985.md"
        )

    def test_an_underscore_the_author_typed_still_survives(self) -> None:
        assert filename_for("already_fielded name") == "already_fielded-name.md"


class TestItStaysAValidName:
    def test_no_doubled_or_dangling_separators(self) -> None:
        for title in ("A — — B", "-- Leading", "Trailing --", "A / — / B"):
            name = filename_for(title).removesuffix(".md")
            assert "__" not in name
            assert "--" not in name
            assert "-_" not in name and "_-" not in name
            assert not name.startswith(("-", "_"))
            assert not name.endswith(("-", "_"))

    def test_a_title_of_only_punctuation_is_still_refused(self) -> None:
        with pytest.raises(ConceptCreationError):
            filename_for("///")

    def test_a_directory_keeps_its_case_and_gains_the_same_boundaries(self) -> None:
        assert directory_name_for("BezaCore Labs — Marketing") == "BezaCore-Labs_Marketing"


class TestTheDatePrefixIsUnaffected:
    """`dated_filename_for` uses `_` for its own field boundary.

    A log is `<date>_<what it was about>`, the same convention.
    """

    def test_a_log_keeps_one_date_field_and_one_subject_field(self) -> None:
        assert (
            dated_filename_for("Milestone 8 scoped", date(2026, 8, 27))
            == "2026-08-27_milestone-8-scoped.md"
        )

    def test_a_dash_in_the_title_does_not_produce_a_third_separator_run(self) -> None:
        name = dated_filename_for("Session Handoff — Milestone 8", date(2026, 8, 27))
        assert name == "2026-08-27_session-handoff_milestone-8.md"
        assert "__" not in name
