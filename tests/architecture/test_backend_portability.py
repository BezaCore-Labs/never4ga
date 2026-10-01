"""Backend-portability invariants.

core/06 section 25 defines seven tests that must always be possible. These are
the ones that apply today. B and C need a vector index implementation, and none
exists yet:

- A: replace a TextIndex with a fake without modifying higher-level code;
- D: project relations through a GraphIndex while canonical relations are unchanged;
- F: no canonical document contains a backend-native primary key;
- G: no public API requires FTS5, Cypher, Qdrant filter or SQL syntax.

Test E (delete derived data and rebuild from Markdown) needs a real projection,
so it is not tested here; the ``clear`` half of it is in every index contract.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from never4ga.adapters.fakes import (
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.domain import context as context_types
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports import (
    embedding_provider,
    graph_index,
    metadata_index,
    text_index,
    vector_index,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.text_index import TextIndex

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "never4ga"


class TestFakesSatisfyTheirPorts:
    """Tests A and D: a fake is a complete substitute for a real backend."""

    @pytest.mark.parametrize(
        ("implementation", "port"),
        [
            (InMemoryDocumentStore(), DocumentStore),
            (InMemoryMetadataIndex(), MetadataIndex),
            (InMemoryTextIndex(), TextIndex),
            (InMemoryGraphIndex(), GraphIndex),
        ],
    )
    def test_implementation_satisfies_its_port(self, implementation: object, port: type) -> None:
        assert isinstance(implementation, port)

    def test_no_fake_inherits_from_its_port(self) -> None:
        # Ports are Protocols: an adapter wrapping a third-party client must be
        # able to satisfy one without inheriting from Never4gA.
        for implementation in (InMemoryTextIndex(), InMemoryGraphIndex()):
            assert type(implementation).__mro__[1:] == (object,)


class TestNoSqliteRequired:
    """The mechanical context stack runs without SQLite."""

    def test_using_the_stack_never_imports_sqlite(self) -> None:
        # Run in a subprocess so that nothing else in the test session can have
        # imported sqlite3 first.
        script = """
import sys
from pathlib import PurePosixPath

from never4ga.adapters.fakes import InMemoryMetadataIndex, InMemoryTextIndex
from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import TextQuery

workspace_id = ConceptId.new()
registry = WorkspaceRegistry()
registry.register(
    WorkspaceMapping(
        workspace_id=workspace_id,
        workspace_path=VaultPath.parse("10_Workspaces/w/workspace.md"),
        repository_root=PurePosixPath("/repo"),
    )
)
index = InMemoryMetadataIndex()
index.upsert(
    MetadataRecord(
        concept_id=workspace_id,
        concept_type="workspace",
        path=VaultPath.parse("10_Workspaces/w/workspace.md"),
        title="W",
    )
)
assembler = StartupContextAssembler(
    resolver=MechanicalScopeResolver(registry), metadata_index=index
)
pack = assembler.assemble(
    ContextRequest(
        scope=ScopeRequest(cwd=PurePosixPath("/repo/src")),
        depth=ContextDepth.STARTUP,
        budget=ContextBudget(max_items=5, max_characters=1000),
    )
)
assert pack.items
InMemoryTextIndex().search(TextQuery(terms=("anything",)))
assert "sqlite3" not in sys.modules, "sqlite3 was imported"
print("OK")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK"


class TestNoBackendSyntaxInPublicRequests:
    """core/06 section 25 Test G."""

    FORBIDDEN_FRAGMENTS = (
        "select ",
        "insert into",
        "create table",
        "create virtual table",
        "fts5",
        "match (",
        "cypher",
        "tsvector",
        "tsquery",
        "pgvector",
        "qdrant",
        "neo4j",
        "->>",
    )

    def _non_docstring_strings(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(), filename=str(path))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_query_syntax_appears_in_domain_or_port_code(self) -> None:
        violations: list[str] = []
        for path in sorted((SRC / "domain").rglob("*.py")) + sorted((SRC / "ports").rglob("*.py")):
            for literal in self._non_docstring_strings(path):
                lowered = literal.casefold()
                violations.extend(
                    f"{path.name}: {fragment!r} in {literal!r}"
                    for fragment in self.FORBIDDEN_FRAGMENTS
                    if fragment in lowered
                )
        assert violations == []

    @pytest.mark.parametrize(
        "request_type",
        [
            metadata_index.MetadataQuery,
            text_index.TextQuery,
            vector_index.VectorQuery,
            context_types.ContextRequest,
        ],
    )
    def test_requests_are_structured_not_raw_query_strings(self, request_type: type) -> None:
        field_names = {field.name for field in dataclasses.fields(request_type)}
        assert not field_names & {"sql", "query", "expression", "filter", "where", "cypher"}

    def test_a_text_query_separates_terms_from_identifiers(self) -> None:
        query = text_index.TextQuery(terms=("workspace",), exact_identifiers=("ADR-0002",))
        assert query.terms == ("workspace",)
        assert query.exact_identifiers == ("ADR-0002",)


class TestIdentityIsNeverABackendKey:
    """core/06 sections 3 and 25 Test F: identity is never a backend key."""

    RECORD_TYPES = (
        StoredDocument,
        metadata_index.MetadataRecord,
        text_index.IndexedChunk,
        vector_index.VectorPoint,
        graph_index.GraphEdge,
        embedding_provider.EmbeddingNamespace,
        context_types.ContextItem,
    )

    @pytest.mark.parametrize("record_type", RECORD_TYPES)
    def test_no_record_carries_a_backend_primary_key(self, record_type: type) -> None:
        field_names = {field.name for field in dataclasses.fields(record_type)}
        assert not field_names & {"rowid", "row_id", "pk", "primary_key", "oid", "point_id"}

    @pytest.mark.parametrize("record_type", RECORD_TYPES)
    def test_no_identity_field_is_an_integer(self, record_type: type) -> None:
        for field in dataclasses.fields(record_type):
            if field.name.endswith("_id") or field.name == "id":
                assert "int" not in str(field.type).replace("point", ""), field.name

    def test_concept_identity_is_a_uuid(self) -> None:
        assert ConceptId.new().value.version == 7


class TestCanonicalRelationsAreIndependentOfTheGraphBackend:
    """core/06 section 25 Test D."""

    def test_clearing_the_graph_projection_does_not_touch_documents(self) -> None:
        store = InMemoryDocumentStore()
        graph = InMemoryGraphIndex()
        from never4ga.ports.graph_index import Direction, GraphEdge

        source, target = ConceptId.new(), ConceptId.new()
        for concept_id, name in ((source, "a"), (target, "b")):
            store.put(
                StoredDocument(
                    concept_id=concept_id,
                    path=VaultPath.parse(f"30_Knowledge/Notes/{name}.md"),
                    frontmatter={"relations": [{"depends_on": str(target)}]},
                    body="",
                )
            )
        graph.add_edge(GraphEdge(source=source, target=target, relation_type="depends_on"))

        graph.clear()

        assert graph.neighbors(source, direction=Direction.BOTH) == ()
        document = store.get(source)
        assert document is not None
        assert document.frontmatter["relations"] == [{"depends_on": str(target)}]
