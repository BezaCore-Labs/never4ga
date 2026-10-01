"""Identity invariants.

Specification:
- core/02 section 5.1 `id` — stable UUIDv7, canonical lowercase hyphenated.
- core/06 section 3 — canonical identity is never a backend-native row/node/point ID.
"""

from __future__ import annotations

import uuid

import pytest

from never4ga.domain.identity import ConceptId, ExternalId, SessionId
from never4ga.errors import IdentityError


class TestConceptId:
    def test_new_mints_a_uuid7(self) -> None:
        concept_id = ConceptId.new()
        assert concept_id.value.version == 7

    def test_new_ids_are_unique(self) -> None:
        assert ConceptId.new() != ConceptId.new()

    def test_parses_canonical_lowercase_hyphenated_form(self) -> None:
        raw = "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"
        assert str(ConceptId.parse(raw)) == raw

    def test_rejects_non_uuid7(self) -> None:
        uuid4 = str(uuid.uuid4())
        with pytest.raises(IdentityError):
            ConceptId.parse(uuid4)

    def test_rejects_backend_integer_ids(self) -> None:
        # core/06 section 3: SQLite INTEGER PRIMARY KEY, Postgres serial,
        # LanceDB row position and friends are forbidden as canonical identity.
        for rowid in (1, 42, "1", ""):
            with pytest.raises(IdentityError):
                ConceptId.parse(rowid)  # type: ignore[arg-type]

    def test_rejects_uppercase_and_braced_forms(self) -> None:
        canonical = "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"
        for variant in (canonical.upper(), "{" + canonical + "}", canonical.replace("-", "")):
            with pytest.raises(IdentityError):
                ConceptId.parse(variant)

    def test_is_hashable_and_value_compared(self) -> None:
        raw = "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"
        assert {ConceptId.parse(raw), ConceptId.parse(raw)} == {ConceptId.parse(raw)}

    def test_is_immutable(self) -> None:
        concept_id = ConceptId.new()
        with pytest.raises(AttributeError):
            concept_id.value = uuid.uuid7()  # type: ignore[misc]

    def test_timestamps_are_monotonic_enough_to_sort(self) -> None:
        # UUIDv7 is time-ordered; this is why it was chosen over v4.
        ids = [ConceptId.new() for _ in range(50)]
        assert [str(i) for i in ids] == sorted(str(i) for i in ids)


class TestSessionId:
    """core/04 section 17: a startup response carries a `session_id`.

    Never4gA mints it rather than accepting one from a client. Identity is
    Never4gA's job (core/06 section 3), three clients on one machine cannot be
    trusted not to collide, and UUIDv7 sorts by time, which is what "the most
    recent session in this workspace" needs.
    """

    def test_new_mints_a_uuid7(self) -> None:
        assert SessionId.new().value.version == 7

    def test_new_ids_are_unique(self) -> None:
        assert len({SessionId.new() for _ in range(100)}) == 100

    def test_it_sorts_by_time(self) -> None:
        minted = [SessionId.new() for _ in range(20)]
        assert sorted(minted) == minted

    def test_it_round_trips_through_its_canonical_string(self) -> None:
        session = SessionId.new()
        assert SessionId.parse(str(session)) == session

    def test_it_rejects_anything_that_is_not_a_uuid7(self) -> None:
        with pytest.raises(IdentityError):
            SessionId.parse("not-a-session")
        with pytest.raises(IdentityError):
            SessionId.parse(str(uuid.uuid4()))

    def test_it_is_not_a_concept_id(self) -> None:
        # A session is runtime state, never a canonical concept. Keeping the
        # types apart stops a session id reaching frontmatter, and stops a
        # concept id being mistaken for a session.
        assert not isinstance(SessionId.new(), ConceptId)
        # The two are distinct types rather than one wearing the other's name,
        # so nothing can pass a session where a concept is expected. They read
        # the same canonical spelling; that is all they share.
        raw = str(ConceptId.new())
        assert str(SessionId.parse(raw)) == raw


class TestExternalId:
    def test_carries_provider_namespace_and_is_not_a_concept_id(self) -> None:
        # core/08 section 15: provider IDs never replace Never4gA UUIDs.
        external = ExternalId(provider="mem0", value="mem_01H8XYZ")
        assert not issubclass(ExternalId, ConceptId)
        assert external.provider == "mem0"
        assert external.value == "mem_01H8XYZ"

    def test_rejects_empty_provider_or_value(self) -> None:
        for provider, value in (("", "x"), ("mem0", ""), ("  ", "x")):
            with pytest.raises(IdentityError):
                ExternalId(provider=provider, value=value)

    def test_is_hashable(self) -> None:
        one = ExternalId(provider="zep", value="e1")
        assert {one, ExternalId(provider="zep", value="e1")} == {one}

    def test_render_is_namespaced(self) -> None:
        assert str(ExternalId(provider="graphiti", value="n42")) == "graphiti:n42"
