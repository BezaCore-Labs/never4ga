"""A settled reconcile pass must not read the vault.

`IndexService.reconcile(changed_only=True)` is what the service runs after
every settled watcher burst and on the periodic timer. Parsing every document
to conclude that none changed would make each pass cost as much as a full
index.

details/data-indexing-maintenance.md section 13 allows the cheap ``path +
size + mtime`` comparison, and :meth:`IndexService.health` trusts it: the stat
answers the common case, and anything it flags is read once and judged on its
hash. These tests hold the write path to the same standard: the stat decides
what to *read*; the content hash still decides what to *reindex*; and a store
that cannot stat its files keeps the read-everything behaviour, which
`tests/unit/test_index_service.py` pins.

The full pass (`changed_only=False`) still reads and hashes every document,
at startup and on `never4ga index`, so a stat that lies (same size, same
mtime, different bytes) is caught by the next startup rather than never.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from never4ga.adapters.fakes import (
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.adapters.filesystem import FileSystemMarkdownStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.graph_index import Direction
from never4ga.ports.text_index import TextQuery
from never4ga.services.indexing import IndexService


def frozen_clock() -> datetime:
    return datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class CountingStore(FileSystemMarkdownStore):
    """The real filesystem store, counting every document it parses."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.reads = 0

    def _read(self, path: VaultPath) -> StoredDocument | None:
        self.reads += 1
        return super()._read(path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def documents(root: Path) -> CountingStore:
    return CountingStore(root)


@pytest.fixture
def graph() -> InMemoryGraphIndex:
    return InMemoryGraphIndex()


@pytest.fixture
def text() -> InMemoryTextIndex:
    return InMemoryTextIndex()


@pytest.fixture
def state() -> InMemoryIndexState:
    return InMemoryIndexState()


@pytest.fixture
def service(
    documents: CountingStore,
    graph: InMemoryGraphIndex,
    text: InMemoryTextIndex,
    state: InMemoryIndexState,
) -> IndexService:
    return IndexService(
        documents=documents,
        metadata=InMemoryMetadataIndex(),
        text=text,
        graph=graph,
        state=state,
        clock=frozen_clock,
    )


def write(
    documents: CountingStore,
    *,
    path: str,
    body: str = "# One\nbody\n",
    concept_id: ConceptId | None = None,
    **frontmatter: Any,
) -> StoredDocument:
    concept_id = concept_id or ConceptId.new()
    base: dict[str, Any] = {
        "type": "knowledge",
        "id": str(concept_id),
        "schema": "never4ga/0.1",
        "title": "Thing",
        "created_at": "2026-08-31T12:00:00Z",
    }
    base.update(frontmatter)
    document = StoredDocument(
        concept_id=concept_id, path=VaultPath.parse(path), frontmatter=base, body=body
    )
    documents.put(document)
    return document


def corpus(documents: CountingStore, count: int = 5) -> list[StoredDocument]:
    return [
        write(documents, path=f"30_Knowledge/Notes/note-{i}.md", body=f"# Note {i}\nprose {i}\n")
        for i in range(count)
    ]


class TestASettledPassIsCheap:
    def test_it_reads_no_document(self, service: IndexService, documents: CountingStore) -> None:
        corpus(documents)
        service.reconcile()
        before = documents.reads
        run = service.reconcile(changed_only=True)
        assert documents.reads == before
        assert (run.indexed, run.unchanged, run.removed) == (0, 5, 0)

    def test_an_edited_document_is_the_only_one_read(
        self, service: IndexService, documents: CountingStore, root: Path, text: InMemoryTextIndex
    ) -> None:
        docs = corpus(documents)
        service.reconcile()
        target = root / str(docs[0].path)
        target.write_text(
            target.read_text(encoding="utf-8").replace("prose 0", "rewritten wording entirely"),
            encoding="utf-8",
        )
        before = documents.reads
        run = service.reconcile(changed_only=True)
        assert documents.reads == before + 1
        assert (run.indexed, run.unchanged) == (1, 4)
        assert text.search(TextQuery(terms=("rewritten",)))
        assert text.search(TextQuery(terms=("prose",)))  # the others are untouched

    def test_a_touched_but_identical_document_settles_without_reindexing(
        self, service: IndexService, documents: CountingStore, root: Path
    ) -> None:
        # `git checkout`, rsync and a formatter all move mtimes without
        # changing bytes. Read once, judged on the hash, and the stat is
        # recorded so the next pass does not read it again.
        docs = corpus(documents)
        service.reconcile()
        target = root / str(docs[0].path)
        os.utime(target, (target.stat().st_atime + 5, target.stat().st_mtime + 5))
        before = documents.reads
        run = service.reconcile(changed_only=True)
        assert documents.reads == before + 1
        assert (run.indexed, run.unchanged) == (0, 5)
        settled = documents.reads
        rerun = service.reconcile(changed_only=True)
        assert documents.reads == settled
        assert (rerun.indexed, rerun.unchanged) == (0, 5)

    def test_a_deletion_is_noticed_without_reading_anything(
        self, service: IndexService, documents: CountingStore, root: Path
    ) -> None:
        docs = corpus(documents)
        service.reconcile()
        (root / str(docs[0].path)).unlink()
        before = documents.reads
        run = service.reconcile(changed_only=True)
        assert documents.reads == before
        assert (run.removed, run.unchanged) == (1, 4)

    def test_a_move_keeps_identity(
        self, service: IndexService, documents: CountingStore, root: Path
    ) -> None:
        docs = corpus(documents)
        service.reconcile()
        source = root / str(docs[0].path)
        source.rename(root / "30_Knowledge" / "Notes" / "renamed.md")
        run = service.reconcile(changed_only=True)
        assert (run.indexed, run.removed) == (1, 0)

    def test_a_new_file_claiming_a_used_identity_is_reported(
        self, service: IndexService, documents: CountingStore, root: Path
    ) -> None:
        docs = corpus(documents)
        service.reconcile()
        original = root / str(docs[0].path)
        (root / "30_Knowledge" / "Notes" / "impostor.md").write_text(
            original.read_text(encoding="utf-8"), encoding="utf-8"
        )
        run = service.reconcile(changed_only=True)
        assert run.indexed == 0
        assert [issue.code for issue in run.issues] == ["duplicate_id"]


class TestLinksStillResolveAgainstWhatWasNotRead:
    def test_a_new_document_links_an_unread_one_by_stem(
        self, service: IndexService, documents: CountingStore, graph: InMemoryGraphIndex
    ) -> None:
        anchor = write(documents, path="30_Knowledge/Notes/anchor.md")
        service.reconcile()
        newcomer = write(
            documents,
            path="30_Knowledge/Notes/newcomer.md",
            body="# New\nSee [[anchor]].\n",
        )
        run = service.reconcile(changed_only=True)
        assert run.indexed == 1
        targets = {
            neighbor.concept_id
            for neighbor in graph.neighbors(newcomer.concept_id, direction=Direction.OUTGOING)
        }
        assert anchor.concept_id in targets

    def test_a_new_document_links_an_unread_one_by_alias(
        self, service: IndexService, documents: CountingStore, graph: InMemoryGraphIndex
    ) -> None:
        anchor = write(
            documents,
            path="30_Knowledge/Notes/anchor.md",
            aliases=["Old Faithful"],
        )
        service.reconcile()
        newcomer = write(
            documents,
            path="30_Knowledge/Notes/newcomer.md",
            body="# New\nSee [[Old Faithful]].\n",
        )
        run = service.reconcile(changed_only=True)
        assert run.indexed == 1
        targets = {
            neighbor.concept_id
            for neighbor in graph.neighbors(newcomer.concept_id, direction=Direction.OUTGOING)
        }
        assert anchor.concept_id in targets

    def test_a_new_document_links_an_unread_one_by_path(
        self, service: IndexService, documents: CountingStore, graph: InMemoryGraphIndex
    ) -> None:
        anchor = write(documents, path="30_Knowledge/Notes/anchor.md")
        service.reconcile()
        newcomer = write(
            documents,
            path="30_Knowledge/Notes/newcomer.md",
            body="# New\n[the anchor](anchor.md)\n",
        )
        run = service.reconcile(changed_only=True)
        assert run.indexed == 1
        targets = {
            neighbor.concept_id
            for neighbor in graph.neighbors(newcomer.concept_id, direction=Direction.OUTGOING)
        }
        assert anchor.concept_id in targets


def _link_set(state: InMemoryIndexState) -> set[tuple[Any, ...]]:
    return {
        (link.source, link.target_path, link.target_name, link.target_id) for link in state.links()
    }


def _what_a_full_pass_says(root: Path) -> set[tuple[Any, ...]]:
    """The links a fresh index of the same vault holds: the answer to agree with."""
    state = InMemoryIndexState()
    IndexService(
        documents=FileSystemMarkdownStore(root),
        metadata=InMemoryMetadataIndex(),
        text=InMemoryTextIndex(),
        graph=InMemoryGraphIndex(),
        state=state,
        clock=frozen_clock,
    ).reconcile()
    return _link_set(state)


def _outgoing(graph: InMemoryGraphIndex, source: StoredDocument) -> set[ConceptId]:
    return {
        neighbor.concept_id
        for neighbor in graph.neighbors(source.concept_id, direction=Direction.OUTGOING)
    }


class TestALinkFollowsItsTargetWithoutItsReferrerChanging:
    """A link's resolution depends on documents other than the one it is in.

    The settled pass reads only files whose stat moved. A document holding a
    link may not move while its target arrives, leaves or changes name, so
    the pass must re-resolve the links that target answers. A target that
    arrives after its referrer is normal whenever a vault is replicated.

    Every test here ends by asking the full pass the same question, because the
    settled pass is only allowed to be cheaper, never to disagree.
    """

    def test_a_target_that_arrives_late_clears_the_finding(
        self,
        service: IndexService,
        documents: CountingStore,
        graph: InMemoryGraphIndex,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        referrer = write(
            documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[latecomer]].\n"
        )
        service.reconcile()
        assert [link.target for link in service.health().broken_links] == ["latecomer"]

        latecomer = write(documents, path="30_Knowledge/Notes/latecomer.md")
        service.reconcile(changed_only=True)

        assert service.health().broken_links == ()
        assert latecomer.concept_id in _outgoing(graph, referrer)
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_so_does_one_addressed_by_path(
        self,
        service: IndexService,
        documents: CountingStore,
        graph: InMemoryGraphIndex,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        referrer = write(
            documents,
            path="30_Knowledge/Notes/referrer.md",
            body="# R\n[the brand](../../10_Workspaces/Brand/tokens.md)\n",
        )
        service.reconcile()
        assert len(service.health().broken_links) == 1

        tokens = write(documents, path="10_Workspaces/Brand/tokens.md")
        service.reconcile(changed_only=True)

        assert service.health().broken_links == ()
        assert tokens.concept_id in _outgoing(graph, referrer)
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_a_target_that_is_deleted_breaks_the_link(
        self,
        service: IndexService,
        documents: CountingStore,
        graph: InMemoryGraphIndex,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        referrer = write(
            documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[doomed]].\n"
        )
        doomed = write(documents, path="30_Knowledge/Notes/doomed.md")
        service.reconcile()
        assert service.health().broken_links == ()

        (root / str(doomed.path)).unlink()
        service.reconcile(changed_only=True)

        assert [link.target for link in service.health().broken_links] == ["doomed"]
        assert doomed.concept_id not in _outgoing(graph, referrer)
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_a_target_that_moves_breaks_a_link_to_where_it_was(
        self,
        service: IndexService,
        documents: CountingStore,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        write(documents, path="30_Knowledge/Notes/referrer.md", body="# R\n[it](wanderer.md)\n")
        wanderer = write(documents, path="30_Knowledge/Notes/wanderer.md")
        service.reconcile()

        (root / str(wanderer.path)).rename(root / "30_Knowledge" / "Notes" / "settled.md")
        service.reconcile(changed_only=True)

        assert [str(link.target_path) for link in service.health().broken_links] == [
            "30_Knowledge/Notes/wanderer.md"
        ]
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_a_target_that_loses_the_name_breaks_a_link_by_that_name(
        self,
        service: IndexService,
        documents: CountingStore,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        write(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[Old Name]].\n")
        renamed = write(documents, path="30_Knowledge/Notes/renamed.md", aliases=["Old Name"])
        service.reconcile()
        assert service.health().broken_links == ()

        write(
            documents,
            path=str(renamed.path),
            concept_id=renamed.concept_id,
            aliases=["New Name"],
        )
        service.reconcile(changed_only=True)

        assert [link.target for link in service.health().broken_links] == ["Old Name"]
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_a_second_document_answering_to_a_name_makes_the_link_ambiguous(
        self,
        service: IndexService,
        documents: CountingStore,
        state: InMemoryIndexState,
        root: Path,
    ) -> None:
        write(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[anchor]].\n")
        write(documents, path="30_Knowledge/Notes/anchor.md")
        service.reconcile()
        assert service.health().broken_links == ()

        write(documents, path="10_Workspaces/Elsewhere/anchor.md")
        run = service.reconcile(changed_only=True)

        assert [issue.code for issue in run.issues] == ["ambiguous_link"]
        assert _link_set(state) == _what_a_full_pass_says(root)

    def test_only_the_documents_that_link_to_it_are_read(
        self, service: IndexService, documents: CountingStore
    ) -> None:
        # The settled pass exists to be cheap. An arrival costs its own read
        # and one for each document whose link it answers -- not the vault.
        corpus(documents)
        write(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[latecomer]].\n")
        service.reconcile()
        write(documents, path="30_Knowledge/Notes/latecomer.md")
        before = documents.reads

        run = service.reconcile(changed_only=True)

        assert documents.reads == before + 2
        assert (run.indexed, run.unchanged) == (2, 5)

    def test_an_arrival_nothing_links_to_reads_nothing_else(
        self, service: IndexService, documents: CountingStore
    ) -> None:
        corpus(documents)
        write(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[note-0]].\n")
        service.reconcile()
        write(documents, path="30_Knowledge/Notes/unrelated.md")
        before = documents.reads

        service.reconcile(changed_only=True)

        assert documents.reads == before + 1

    def test_editing_a_target_in_place_does_not_reread_what_links_to_it(
        self, service: IndexService, documents: CountingStore, root: Path
    ) -> None:
        # A workspace page is linked from everywhere and edited often. An edit
        # that keeps its path and its names changes no link's resolution.
        docs = corpus(documents)
        for i in range(3):
            write(documents, path=f"30_Knowledge/Notes/fan-{i}.md", body="# F\nSee [[note-0]].\n")
        service.reconcile()
        target = root / str(docs[0].path)
        target.write_text(
            target.read_text(encoding="utf-8").replace("prose 0", "new words"), encoding="utf-8"
        )
        before = documents.reads

        service.reconcile(changed_only=True)

        assert documents.reads == before + 1
