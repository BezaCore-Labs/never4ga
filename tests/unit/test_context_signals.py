"""ContextSignalProvider behaviour (core/07 section 4).

A ContextSignalProvider contributes structured signals, never prose.
"""

from __future__ import annotations

import pytest

from never4ga.domain.provenance import AcquisitionReason, AcquisitionStage, ReasonCode
from never4ga.domain.signals import MAX_SIGNAL_TEXT_LENGTH, ContextSignal


def make_signal(value: object, **overrides: object) -> ContextSignal:
    defaults: dict[str, object] = {
        "provider_id": "git",
        "kind": "git.branch",
        "value": value,
        "reason": AcquisitionReason.of(ReasonCode.GIT_CHANGED_FILE),
    }
    defaults.update(overrides)
    return ContextSignal(**defaults)  # type: ignore[arg-type]


class TestStructuredValues:
    @pytest.mark.parametrize(
        "value",
        [
            "milestone-0",
            7,
            3.5,
            True,
            None,
            ("a.py", "b.py"),
            {"branch": "milestone-0", "dirty": True},
        ],
    )
    def test_structured_values_are_accepted(self, value: object) -> None:
        assert make_signal(value).value == value

    def test_prose_is_rejected(self) -> None:
        prose = (
            "The workspace appears to be focused on implementing the runtime, "
            "and the developer seems to be working through the port definitions "
            "while considering how the context assembler should behave overall."
        )
        with pytest.raises(ValueError, match="structured"):
            make_signal(prose)

    def test_multi_line_text_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="structured"):
            make_signal("line one\nline two")

    def test_the_length_limit_is_explicit(self) -> None:
        assert make_signal("a" * MAX_SIGNAL_TEXT_LENGTH).value is not None
        with pytest.raises(ValueError, match="structured"):
            make_signal("a" * (MAX_SIGNAL_TEXT_LENGTH + 1))

    def test_nested_prose_inside_a_collection_is_also_rejected(self) -> None:
        with pytest.raises(ValueError, match="structured"):
            make_signal({"summary": "x" * (MAX_SIGNAL_TEXT_LENGTH + 1)})

    def test_arbitrary_objects_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="structured"):
            make_signal(object())

    def test_there_is_no_narrative_field_to_fill(self) -> None:
        signal = make_signal("milestone-0")
        for forbidden in ("summary", "narrative", "prose", "explanation", "completion"):
            assert not hasattr(signal, forbidden)


class TestSignalProvenance:
    def test_a_signal_is_always_mechanical(self) -> None:
        # A provider that needs a model belongs behind an enrichment interface
        # (core/07 section 7), not behind ContextSignalProvider.
        with pytest.raises(ValueError, match="mechanical"):
            make_signal("x", reason=AcquisitionReason.of(ReasonCode.LLM_RERANK))

    def test_semantic_stages_are_also_refused(self) -> None:
        with pytest.raises(ValueError, match="mechanical"):
            make_signal("x", reason=AcquisitionReason.of(ReasonCode.VECTOR_RANK))

    def test_a_signal_names_its_provider_and_kind(self) -> None:
        signal = make_signal("milestone-0")
        assert signal.provider_id == "git"
        assert signal.kind == "git.branch"
        assert signal.reason.stage is AcquisitionStage.EXTERNAL_STATE

    def test_provider_id_and_kind_are_required(self) -> None:
        for field in ("provider_id", "kind"):
            with pytest.raises(ValueError, match=field):
                make_signal("x", **{field: "  "})
