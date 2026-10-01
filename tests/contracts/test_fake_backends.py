"""Run every storage/retrieval contract against the in-memory fakes.

core/06 section 22: an implementation is only supported once it passes the
applicable contract suite. A future ``SQLiteFTS5Index`` adds a module like this
one rather than restating the behaviour.
"""

from __future__ import annotations

import pytest

from never4ga.adapters.fakes import (
    InMemoryDocumentStore,
    InMemoryIndexState,
    InMemoryMaintenanceFindings,
    InMemoryTextIndex,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.index_state import IndexState
from never4ga.ports.text_index import TextIndex
from tests.contracts.document_store_contract import DocumentStoreContract
from tests.contracts.index_state_contract import IndexStateContract, PathRecordContract
from tests.contracts.maintenance_findings_contract import MaintenanceFindingsContract
from tests.contracts.text_index_contract import TextIndexContract

pytestmark = pytest.mark.contract


class TestInMemoryDocumentStore(DocumentStoreContract):
    @pytest.fixture
    def store(self) -> DocumentStore:
        return InMemoryDocumentStore()


class TestInMemoryIndexState(IndexStateContract, PathRecordContract):
    @pytest.fixture
    def state(self) -> IndexState:
        return InMemoryIndexState()


class TestInMemoryMaintenanceFindings(MaintenanceFindingsContract):
    @pytest.fixture
    def findings(self) -> InMemoryMaintenanceFindings:
        return InMemoryMaintenanceFindings()


class TestInMemoryTextIndex(TextIndexContract):
    """The fake under the same suite as SQLite (core/06 section 22).

    This pins the suite's own guarantees -- exact identifiers never matching
    substrings, a scope of concepts excluding a path-owned chunk -- on both
    engines rather than one.
    """

    @pytest.fixture
    def index(self) -> TextIndex:
        return InMemoryTextIndex()
