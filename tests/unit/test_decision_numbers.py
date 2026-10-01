"""How an ADR number is read from a filename and a title (`core/02` §21.11).

The number lives in the filename, `adr-[<series>-]NNNN_<slug>.md`, in every
numbered `Decisions/` folder in the vault. The title usually repeats it but
need not, so the filename is what allocation and `doctor` read.
"""

from __future__ import annotations

import pytest

from never4ga.layout.decision_numbers import (
    DecisionNumber,
    next_number,
    number_in_filename,
    number_in_title,
)


class TestReadingAFilename:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("adr-0043_use-sqlite-first.md", DecisionNumber("", 43)),
            ("adr-0001_x.md", DecisionNumber("", 1)),
            ("adr-ops-0007_control-node.md", DecisionNumber("ops", 7)),
            ("adr-web-0002_pointer-repo.md", DecisionNumber("web", 2)),
            ("ADR-0012_upper.md", DecisionNumber("", 12)),
            ("adr-12345_wide.md", DecisionNumber("", 12345)),
        ],
    )
    def test_a_numbered_record_names_its_series_and_number(
        self, filename: str, expected: DecisionNumber
    ) -> None:
        assert number_in_filename(filename) == expected

    @pytest.mark.parametrize(
        "filename",
        [
            "index.md",
            "phase-a-f-design-decisions.md",
            "education-is-a-life-area.md",
            "adr-guidance.md",
            "adr-12_too-short.md",
        ],
    )
    def test_an_unnumbered_record_has_none(self, filename: str) -> None:
        assert number_in_filename(filename) is None


class TestReadingATitle:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("ADR-0044 — Use SQLite First", DecisionNumber("", 44)),
            ("ADR 0001 — GNU Stow manages the dotfiles", DecisionNumber("", 1)),
            ("ADR-OPS-0009 — Something", DecisionNumber("ops", 9)),
            ("adr-0003: lower case", DecisionNumber("", 3)),
            ("ADR-0044", DecisionNumber("", 44)),
        ],
    )
    def test_a_title_that_states_a_number(self, title: str, expected: DecisionNumber) -> None:
        assert number_in_title(title) == expected

    @pytest.mark.parametrize(
        "title",
        ["Use SQLite First", "ADRs are allocated", "A course unit is its own type"],
    )
    def test_a_title_that_does_not(self, title: str) -> None:
        assert number_in_title(title) is None


class TestAllocating:
    def test_the_next_is_the_highest_plus_one_and_gaps_stay_gaps(self) -> None:
        existing = [DecisionNumber("", n) for n in (11, 12, 16)]
        assert next_number(existing, "") == DecisionNumber("", 17)

    def test_series_are_counted_apart(self) -> None:
        existing = [DecisionNumber("ops", 8), DecisionNumber("web", 7)]
        assert next_number(existing, "web") == DecisionNumber("web", 8)
        assert next_number(existing, "ops") == DecisionNumber("ops", 9)

    def test_an_empty_series_starts_at_one(self) -> None:
        assert next_number([], "") == DecisionNumber("", 1)


class TestWriting:
    def test_the_title_prefix_matches_the_vault_s_convention(self) -> None:
        assert DecisionNumber("", 44).title_prefix == "ADR-0044"
        assert DecisionNumber("ops", 9).title_prefix == "ADR-OPS-0009"
