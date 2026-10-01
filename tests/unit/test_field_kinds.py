"""What a registered field *is*, and what `--field` may safely coerce.

`--field unit=1` must write the integer `1`, as the same field typed by hand
in Obsidian would be, or two notes of one type disagree on the type of `unit`
depending on how each was made. `unit` is exactly the property a Base sorts by.

Nothing is guessed: a verb that guessed would have to decide whether `1.0` is a
number and `no` is a boolean, which is the Norway problem, and is why core/02
quotes its timestamps. **The vocabulary declares a kind for a field, and only a
declared field is converted**; everything else stays the string it arrived as.
"""

from __future__ import annotations

import pytest

from never4ga.schema import (
    BOOLEAN_FIELDS,
    DATE_FIELDS,
    INTEGER_FIELDS,
    TIMESTAMP_FIELDS,
    coerce_field,
    field_kind,
)


class TestTheVocabularyDeclaresTheKind:
    def test_unit_is_an_integer(self) -> None:
        assert "unit" in INTEGER_FIELDS
        assert field_kind("unit") == "integer"

    def test_the_two_course_dates_are_dates(self) -> None:
        # core/02 sections 21.24 and 21.25 register `opens` and `due`. Without
        # a declared kind they would be treated as prose.
        assert "opens" in DATE_FIELDS
        assert "due" in DATE_FIELDS

    def test_a_timestamp_field_is_still_a_timestamp(self) -> None:
        assert field_kind("occurred_at") == "timestamp"
        assert "occurred_at" in TIMESTAMP_FIELDS

    def test_required_reading_is_a_boolean(self) -> None:
        # core/02 section 21.15: whether every startup reads a standard in full.
        assert "required_reading" in BOOLEAN_FIELDS
        assert field_kind("required_reading") == "boolean"

    def test_a_list_field_is_a_list(self) -> None:
        assert field_kind("tags") == "list"

    def test_an_undeclared_field_has_no_kind(self) -> None:
        assert field_kind("grade") is None
        assert field_kind("x_whatever") is None


class TestOnlyADeclaredFieldIsConverted:
    def test_an_integer_field_becomes_an_integer(self) -> None:
        assert coerce_field("unit", "3") == 3

    def test_a_negative_and_a_zero_survive(self) -> None:
        assert coerce_field("unit", "0") == 0
        assert coerce_field("unit", "-1") == -1

    def test_an_integer_field_that_is_not_one_stays_a_string(self) -> None:
        """`unit: <number-or-label>` -- core/02 allows "Final Exam"."""
        assert coerce_field("unit", "Final") == "Final"
        assert coerce_field("unit", "3.5") == "3.5"
        assert coerce_field("unit", "") == ""

    def test_grade_stays_a_string_because_it_is_not_declared(self) -> None:
        # An A, a 95, a 95/100 and a Pass are all grades. Declaring it numeric
        # would be asserting something about a vocabulary that has none.
        assert coerce_field("grade", "95") == "95"
        assert coerce_field("grade", "A") == "A"

    def test_a_date_field_keeps_its_string_form(self) -> None:
        # core/02 section 7.4 quotes dates on purpose: a bare YYYY-MM-DD is a
        # date object to YAML, and the vault stores the text.
        assert coerce_field("due", "2026-09-15") == "2026-09-15"

    def test_a_date_field_that_is_not_a_date_is_left_alone(self) -> None:
        assert coerce_field("due", "next friday") == "next friday"

    def test_a_boolean_field_takes_only_true_and_false(self) -> None:
        assert coerce_field("required_reading", "true") is True
        assert coerce_field("required_reading", "false") is False

    def test_a_boolean_field_does_not_guess(self) -> None:
        # `yes`, `no`, `on` and `True` are booleans to one YAML version and not
        # another. Left as strings, validation names them.
        for raw in ("yes", "no", "on", "True", "1", ""):
            assert coerce_field("required_reading", raw) == raw

    def test_nothing_else_is_touched(self) -> None:
        for raw in ("1.0", "no", "yes", "true", "null", "on", "NO"):
            assert coerce_field("description", raw) == raw

    def test_a_value_that_is_already_typed_is_returned_unchanged(self) -> None:
        assert coerce_field("unit", 3) == 3
        assert coerce_field("tags", ["a"]) == ["a"]


class TestTheNorwayProblemStaysSolved:
    """Undeclared fields are never guessed at.

    `no` is Norway's country code and YAML 1.1 reads it as False. None of
    these is a declared field, so none of them is converted -- the guard is
    that conversion is opt-in per field, not that these strings are special.
    """

    @pytest.mark.parametrize("raw", ["no", "NO", "yes", "off", "true", "1.0"])
    def test_an_undeclared_field_keeps_every_ambiguous_string(self, raw: str) -> None:
        assert coerce_field("some_extension_field", raw) == raw
