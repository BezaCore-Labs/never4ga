"""SecretRef is a reference, never a value (core/05 section 9)."""

from __future__ import annotations

import pytest

from never4ga.ports.secret_store import SecretRef


def test_is_a_reference_not_a_value() -> None:
    ref = SecretRef("openproject.api_token")
    assert ref.name == "openproject.api_token"
    assert not hasattr(ref, "value")


def test_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        SecretRef("  ")


def test_renders_as_its_name() -> None:
    assert str(SecretRef("local.bearer")) == "local.bearer"


def test_is_hashable_and_orderable_for_stable_listings() -> None:
    refs = {SecretRef("b"), SecretRef("a"), SecretRef("a")}
    assert [ref.name for ref in sorted(refs)] == ["a", "b"]
