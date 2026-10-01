"""Run the storage contracts against the SQLite projections.

core/06 section 22: a backend becomes supported by passing the applicable
contract suite, not by restating its behaviour. Each class below is a subclass
and a fixture -- nothing else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMaintenanceFindings,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.index_state import IndexState
from never4ga.ports.maintenance_findings import MaintenanceFindings
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.text_index import TextIndex
from tests.contracts.graph_index_contract import GraphIndexContract
from tests.contracts.index_state_contract import IndexStateContract, PathRecordContract
from tests.contracts.maintenance_findings_contract import MaintenanceFindingsContract
from tests.contracts.metadata_index_contract import MetadataIndexContract
from tests.contracts.text_index_contract import TextIndexContract

pytestmark = pytest.mark.contract


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(open_index(tmp_path / "index.sqlite3")) as open_connection:
        yield open_connection


class TestSQLiteMetadataIndex(MetadataIndexContract):
    @pytest.fixture
    def index(self, connection: sqlite3.Connection) -> MetadataIndex:
        return SQLiteMetadataIndex(connection)


class TestSQLiteFTS5Index(TextIndexContract):
    @pytest.fixture
    def index(self, connection: sqlite3.Connection) -> TextIndex:
        return SQLiteFTS5Index(connection)


class TestSQLiteGraphIndex(GraphIndexContract):
    @pytest.fixture
    def index(self, connection: sqlite3.Connection) -> GraphIndex:
        return SQLiteGraphIndex(connection)


class TestSQLiteIndexState(IndexStateContract, PathRecordContract):
    @pytest.fixture
    def state(self, connection: sqlite3.Connection) -> IndexState:
        return SQLiteIndexState(connection)


class TestSQLiteMaintenanceFindings(MaintenanceFindingsContract):
    @pytest.fixture
    def findings(self, connection: sqlite3.Connection) -> MaintenanceFindings:
        return SQLiteMaintenanceFindings(connection)
