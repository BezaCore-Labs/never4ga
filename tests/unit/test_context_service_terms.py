"""Context terms are parsed the way search parses a query.

One argument holding several words becomes several terms, a quoted phrase stays
a phrase, and an identifier is tried both exactly and as a term. Without this,
`context focus "indexing scope owner"` would search for a three-word phrase no
document contains and return an empty pack where `search` finds results.

The parsing lives in `ContextService`, the one service every interface
assembles a pack through (core/05 section 15), so the CLI, the API and the MCP
server all get it.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from never4ga.adapters.fakes import (
    FakeRepositoryLocator,
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
    InMemoryWorkspaceMappingStore,
)
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import IndexedChunk
from never4ga.services.context import ContextService
from never4ga.services.workspaces import WorkspaceService

WORKSPACE = ConceptId.new()
NOTE = ConceptId.new()


class Recording:
    """A signal provider that keeps the request the assembler was given."""

    provider_id = "recording"

    def __init__(self) -> None:
        self.seen: list[ContextRequest] = []

    def supports(self, request: ContextRequest) -> bool:
        return True

    def collect(self, request: ContextRequest, scope: object) -> tuple[()]:
        self.seen.append(request)
        return ()


@pytest.fixture
def recording() -> Recording:
    return Recording()


@pytest.fixture
def service(recording: Recording) -> ContextService:
    workspaces = WorkspaceService(InMemoryWorkspaceMappingStore(), FakeRepositoryLocator())
    workspaces.map(
        workspace_id=WORKSPACE,
        workspace_path=VaultPath.parse("10_Workspaces/Example/workspace.md"),
        repository_root=PurePath("/repo"),
    )
    metadata = InMemoryMetadataIndex()
    metadata.upsert(
        MetadataRecord(
            concept_id=NOTE,
            # A research note, because deep context admits a `context` document
            # structurally whatever the terms say, and this one must arrive only
            # if its words were searched for.
            concept_type="research_note",
            path=VaultPath.parse("10_Workspaces/Example/Research/indexing.md"),
            title="Indexing",
            workspace_id=WORKSPACE,
        )
    )
    text = InMemoryTextIndex()
    text.index_chunk(
        IndexedChunk(
            chunk=ChunkIdentity(
                concept_id=NOTE,
                heading_path=(),
                ordinal=0,
                content_hash="abc123",
                policy_version="v1",
            ),
            path=VaultPath.parse("10_Workspaces/Example/Research/indexing.md"),
            text="the indexing service is the only owner of the index",
            line_range=LineRange(start=1, end=1),
        )
    )
    return ContextService(
        workspaces=workspaces,
        metadata=metadata,
        text=text,
        graph=InMemoryGraphIndex(),
        documents=InMemoryDocumentStore(),
        signal_providers=(recording,),
    )


def _focused(
    *terms: str, exact_identifiers: tuple[str, ...] = (), depth: ContextDepth = ContextDepth.FOCUSED
) -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(workspace_id=WORKSPACE),
        depth=depth,
        budget=ContextBudget(max_items=5, max_characters=5_000),
        terms=terms,
        exact_identifiers=exact_identifiers,
    )


class TestTermsAreParsed:
    def test_one_term_holding_several_words_becomes_several_terms(
        self, service: ContextService, recording: Recording
    ) -> None:
        service.assemble(_focused("indexing scope owner"))
        assert recording.seen[0].terms == ("indexing", "scope", "owner")

    def test_words_that_were_already_words_are_unchanged(
        self, service: ContextService, recording: Recording
    ) -> None:
        service.assemble(_focused("indexing", "owner"))
        assert recording.seen[0].terms == ("indexing", "owner")
        assert recording.seen[0].phrases == ()

    def test_a_quoted_phrase_stays_a_phrase(
        self, service: ContextService, recording: Recording
    ) -> None:
        service.assemble(_focused('"only owner" indexing'))
        assert recording.seen[0].phrases == ("only owner",)
        assert recording.seen[0].terms == ("indexing",)

    def test_an_identifier_is_searched_exactly_and_as_a_term(
        self, service: ContextService, recording: Recording
    ) -> None:
        # `search`'s rule: a guess about syntax must never narrow a search to
        # nothing, so the identifier stays a term as well.
        service.assemble(_focused("ADR-0035 adoption"))
        assert recording.seen[0].exact_identifiers == ("ADR-0035",)
        assert "ADR-0035" in recording.seen[0].terms

    def test_identifiers_the_caller_named_are_kept_first(
        self, service: ContextService, recording: Recording
    ) -> None:
        service.assemble(_focused("ADR-0035 ABC-1", exact_identifiers=("ABC-1",)))
        assert recording.seen[0].exact_identifiers == ("ABC-1", "ADR-0035")


class TestTheReportedDefect:
    """Several words in one argument find what the same words find apart."""

    @pytest.mark.parametrize("depth", [ContextDepth.FOCUSED, ContextDepth.DEEP])
    def test_a_quoted_argument_finds_what_its_words_find(
        self, service: ContextService, depth: ContextDepth
    ) -> None:
        separate, _ = service.assemble(_focused("indexing", "scope", "owner", depth=depth))
        together, _ = service.assemble(_focused("indexing scope owner", depth=depth))
        assert NOTE in {item.concept_id for item in separate.items}
        assert [item.concept_id for item in together.items] == [
            item.concept_id for item in separate.items
        ]

    def test_a_quoted_phrase_the_document_holds_is_found(self, service: ContextService) -> None:
        pack, _ = service.assemble(_focused('"only owner"'))
        assert [item.concept_id for item in pack.items] == [NOTE]
