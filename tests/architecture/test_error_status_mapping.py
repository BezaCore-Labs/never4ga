"""Every domain error has a decided HTTP status, or is decided to be a fault.

`details/api-cli-mcp-contract.md` section 12 asks for a stable code. A
catch-all that turns every unmapped error into a 500 gets three things wrong:

- The status misleads. An incomplete request is the client's fault, and a 500
  tells the client the server broke. An agent that gets a 500 retries or
  alerts; it does not fix its call.
- A code taken from a Python class name changes whenever the class is renamed,
  silently changing the wire contract. Codes are stable snake_case.
- `retryable` must be true where retrying is right, as for
  `ProviderUnavailableError`.

The mapping itself can change. What this test guarantees is that no new error
class becomes a 500 without a decision.
"""

from __future__ import annotations

import pathlib

import pytest

from never4ga.api.errors import SERVER_FAULTS, STATUS_BY_ERROR, status_for
from never4ga.errors import (
    DuplicateConceptIdError,
    Never4gaError,
    ProviderUnavailableError,
    ScopeResolutionError,
    VaultIntegrityError,
    VectorIndexDisabledError,
)

#: Errors the API can never raise, because they belong to the *other*
#: composition root or to the client half of talking to one. `cli` and `api`
#: cannot see each other (core/05 section 15), `service_client` is what calls a
#: service rather than what runs inside it, and `service` raises its own only
#: while starting or controlling a daemon -- never while answering a request.
#:
#: Excluded rather than mapped: a table in `api/` that had to enumerate the
#: CLI's private exceptions would be describing something it cannot reach.
_UNREACHABLE = (
    "never4ga.cli",
    "never4ga.service.",
    "never4ga.service_client",
    # And adapters, for a different and stronger reason: core/05 section 15
    # forbids an interface importing one, so `api/errors.py` cannot name these
    # even to map them. An adapter error that escapes to a request is a fault
    # in how the service handled it -- a store reports an unreadable document
    # rather than raising past the caller -- so 500 is the honest answer.
    "never4ga.adapters",
    "never4ga.client_descriptors",
)


def descendants(cls: type) -> set[type]:
    """Every subclass the product defines that the API could actually raise.

    Scoped to `never4ga.*` deliberately. `__subclasses__` sees whatever has been
    imported, which in a full run includes error classes defined inside tests --
    including the throwaway one below. Those are not the product's hierarchy and
    holding them to it makes this test depend on execution order.
    """
    subclasses: list[type] = cls.__subclasses__()
    found = {
        child
        for child in subclasses
        if child.__module__.startswith("never4ga.")
        and not child.__module__.startswith(_UNREACHABLE)
    }
    for child in tuple(found):
        found |= descendants(child)
    return found


class TestNothingFallsThrough:
    def test_every_error_class_is_decided_one_way_or_the_other(self) -> None:
        undecided = sorted(
            error.__name__
            for error in descendants(Never4gaError)
            if error not in STATUS_BY_ERROR and error not in SERVER_FAULTS
        )
        assert not undecided, (
            "these errors would become a 500 with no decision behind it: "
            + ", ".join(undecided)
            + " -- add each to STATUS_BY_ERROR or to SERVER_FAULTS"
        )

    def test_an_error_is_not_in_both(self) -> None:
        assert not (set(STATUS_BY_ERROR) & SERVER_FAULTS)

    def test_no_code_is_a_python_class_name(self) -> None:
        """A code is a contract; a class name is an implementation detail."""
        for error, mapped in STATUS_BY_ERROR.items():
            assert mapped.code == mapped.code.lower()
            assert mapped.code != error.__name__.lower()
            assert " " not in mapped.code

    def test_every_code_is_unique(self) -> None:
        codes = [mapped.code for mapped in STATUS_BY_ERROR.values()]
        assert len(codes) == len(set(codes))


class TestTheStatusSaysWhoseFaultItIs:
    def test_an_incomplete_request_is_a_client_error(self) -> None:
        mapped = status_for(ScopeResolutionError("no workspace was supplied"))
        assert mapped.status == 400
        assert mapped.code == "scope_unresolved"
        assert "workspace" in mapped.repair_hint

    def test_an_unreachable_provider_is_not_the_client_s_fault_and_is_retryable(
        self,
    ) -> None:
        """An unreachable provider is a normal state, and the status agrees."""
        mapped = status_for(ProviderUnavailableError("the server is off"))
        assert mapped.status == 503
        assert mapped.retryable is True

    def test_a_stale_write_is_a_conflict_and_is_not_retryable(self) -> None:
        """No automatic retry: refetching the version and resending would
        silently overwrite the other writer's change."""
        from never4ga.errors import WriteConflictError

        mapped = status_for(WriteConflictError("lockVersion 2 is stale"))
        assert mapped.status == 409
        assert mapped.retryable is False


class TestTheMostSpecificEntryWins:
    def test_a_subclass_beats_its_parent(self) -> None:
        assert status_for(DuplicateConceptIdError("two documents")).code == "duplicate_concept_id"
        assert status_for(VaultIntegrityError("something")).code == "vault_integrity"

    def test_and_again_for_capabilities(self) -> None:
        assert status_for(VectorIndexDisabledError("off")).code == "vector_index_disabled"

    def test_an_unmapped_error_is_a_fault_with_a_stable_code(self) -> None:
        class SomethingNewError(Never4gaError):
            pass

        mapped = status_for(SomethingNewError("boom"))
        assert mapped.status == 500
        assert mapped.code == "internal_error"


@pytest.mark.architecture
class TestItIsTheApiThatDecides:
    def test_the_mapping_lives_in_the_interface_not_the_domain(self) -> None:
        """`errors` knows nothing about HTTP, and must not learn.

        A status code is one interface's opinion about a domain error. The CLI
        turns the same errors into exit codes and does not consult this.
        """
        import never4ga.errors as domain

        source = domain.__file__
        assert source is not None
        text = pathlib.Path(source).read_text(encoding="utf-8")
        assert "http" not in text.lower().replace("https://", "")
