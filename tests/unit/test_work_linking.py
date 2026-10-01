"""Linking a workspace to a tracker project.

Linking can create the tracker project itself, not only record one that exists.

Specification:
- core/03 section 15 -- the declaration and its `sync_policy` vocabulary.
- core/03 sections 23 and 27 step 3 -- both assume the project already exists.
"""

from __future__ import annotations

from typing import Any

import pytest

from never4ga.adapters.fakes import FakeWorkManagementWriter, InMemoryDocumentStore
from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import WriteRejectedError
from never4ga.ports.work_management import WorkManagementWriter
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.work_linking import WorkspaceLinkService, project_identifier_for

WORKSPACE = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")
PARENT = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb7")
CONNECTION = Connection(
    name="work_openproject",
    provider="fake-pm",
    base_url="https://pm.example.dev",
)


def manifest(
    identity: ConceptId,
    title: str,
    *,
    path: str = "10_Workspaces/Never4gA/workspace.md",
    **frontmatter: Any,
) -> StoredDocument:
    return StoredDocument(
        concept_id=identity,
        path=VaultPath.parse(path),
        frontmatter={
            "type": "workspace",
            "id": str(identity),
            "schema": "never4ga/0.1",
            "title": title,
            "created_at": "2026-08-26T00:00:00Z",
            **frontmatter,
        },
        body="# Never4gA\n",
    )


class _Connections(ConnectionRegistry):
    """A registry that answers with one connection and reads no vault."""

    def __init__(self) -> None: ...

    def resolve(self, name: str) -> Connection:
        return CONNECTION


def service(
    *documents: StoredDocument,
    writer: WorkManagementWriter | None = None,
) -> tuple[WorkspaceLinkService, InMemoryDocumentStore, FakeWorkManagementWriter]:
    store = InMemoryDocumentStore()
    for document in documents:
        store.put(document)
    built = writer or FakeWorkManagementWriter()
    assert isinstance(built, FakeWorkManagementWriter)
    return (
        WorkspaceLinkService(documents=store, connections=_Connections(), factory=lambda _: built),
        store,
        built,
    )


class TestIdentifier:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Never4gA", "never4ga"),
            ("BezaCore Labs", "bezacore-labs"),
            ("Beza_Core Labs", "beza-core-labs"),
            ("  Spaced  Out  ", "spaced-out"),
            ("Punctuation! & Symbols?", "punctuation-symbols"),
        ],
    )
    def test_a_title_slugs_to_a_lowercase_identifier(self, title: str, expected: str) -> None:
        # A project identifier appears in every URL and in the path work-package
        # creation addresses, so it is kept lowercase.
        assert project_identifier_for(title) == expected

    def test_never4gas_own_project_is_what_this_would_have_produced(self) -> None:
        assert project_identifier_for("Never4gA") == "never4ga"


class TestProposing:
    def test_a_proposal_derives_the_identifier_and_creates_nothing(self) -> None:
        linking, _, writer = service(manifest(WORKSPACE, "Never4gA"))
        proposal = linking.propose(WORKSPACE, "work_openproject")
        assert proposal.identifier == "never4ga"
        assert proposal.creates_project is True
        assert writer.find_project("never4ga") is None

    def test_the_declaration_is_read_never_read_write(self) -> None:
        # `read_write` is the first gate on tracker writes. If this verb wrote
        # it, the tool would be granting that permission to itself.
        linking, _, _ = service(manifest(WORKSPACE, "Never4gA"))
        assert linking.propose(WORKSPACE, "work_openproject").declaration == {
            "mode": "external",
            "connection": "work_openproject",
            "provider": "fake-pm",
            "project_ref": "never4ga",
            "sync_policy": "read",
        }

    def test_an_existing_project_is_used_rather_than_remade(self) -> None:
        linking, _, writer = service(manifest(WORKSPACE, "Never4gA"))
        writer.create_project("never4ga", "Never4gA")
        assert linking.propose(WORKSPACE, "work_openproject").creates_project is False

    def test_an_identifier_may_be_named_rather_than_derived(self) -> None:
        linking, _, _ = service(manifest(WORKSPACE, "Never4gA"))
        proposal = linking.propose(WORKSPACE, "work_openproject", identifier="n4ga")
        assert proposal.identifier == "n4ga"

    def test_the_parent_workspaces_project_becomes_the_parent(self) -> None:
        linking, _, _ = service(
            manifest(
                PARENT,
                "BezaCore Labs",
                path="10_Workspaces/BezaCore-Labs/workspace.md",
                work_management={"mode": "external", "project_ref": "bezacore-labs"},
            ),
            manifest(WORKSPACE, "Never4gA", parent=str(PARENT)),
        )
        assert linking.propose(WORKSPACE, "work_openproject").parent == "bezacore-labs"

    def test_a_parent_without_a_tracker_project_leaves_it_unparented(self) -> None:
        linking, _, _ = service(
            manifest(PARENT, "BezaCore Labs", path="10_Workspaces/BezaCore-Labs/workspace.md"),
            manifest(WORKSPACE, "Never4gA", parent=str(PARENT)),
        )
        assert linking.propose(WORKSPACE, "work_openproject").parent is None

    def test_a_workspace_that_already_declares_one_is_refused(self) -> None:
        linking, _, _ = service(
            manifest(
                WORKSPACE,
                "Never4gA",
                work_management={"mode": "external", "connection": "other", "project_ref": "x"},
            )
        )
        with pytest.raises(WriteRejectedError, match="already declares"):
            linking.propose(WORKSPACE, "work_openproject")

    def test_something_that_is_not_a_workspace_is_refused(self) -> None:
        note = manifest(WORKSPACE, "A Note")
        linking, _, _ = service(
            StoredDocument(
                concept_id=note.concept_id,
                path=note.path,
                frontmatter={**note.frontmatter, "type": "knowledge"},
                body=note.body,
            )
        )
        with pytest.raises(WriteRejectedError, match="not a workspace"):
            linking.propose(WORKSPACE, "work_openproject")

    def test_a_title_that_slugs_to_nothing_asks_for_a_name(self) -> None:
        linking, _, _ = service(manifest(WORKSPACE, "!!!"))
        with pytest.raises(WriteRejectedError, match="name one"):
            linking.propose(WORKSPACE, "work_openproject")

    def test_a_proposal_renders_what_it_would_do(self) -> None:
        linking, _, _ = service(manifest(WORKSPACE, "Never4gA"))
        rendered = linking.propose(WORKSPACE, "work_openproject").render()
        assert "create project" in rendered
        assert "never4ga" in rendered
        assert "sync_policy: read" in rendered


class TestApplying:
    def test_the_project_is_made_and_the_declaration_written(self) -> None:
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        outcome = linking.apply(linking.propose(WORKSPACE, "work_openproject"), creating=True)
        assert outcome.created_project is True
        assert writer.find_project("never4ga") is not None
        stored = store.get(WORKSPACE)
        assert stored is not None
        assert stored.frontmatter["work_management"]["project_ref"] == "never4ga"
        assert stored.frontmatter["work_management"]["sync_policy"] == "read"

    def test_an_existing_project_is_declared_without_being_remade(self) -> None:
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        writer.create_project("never4ga", "Never4gA")
        outcome = linking.apply(linking.propose(WORKSPACE, "work_openproject"))
        assert outcome.created_project is False
        stored = store.get(WORKSPACE)
        assert stored is not None
        assert stored.frontmatter["work_management"]["project_ref"] == "never4ga"

    def test_the_rest_of_the_manifest_survives_unchanged(self) -> None:
        # core/02: unknown and extension fields survive a round trip. A verb
        # that rewrote a workspace to add one field would be the reverse.
        linking, store, _ = service(
            manifest(WORKSPACE, "Never4gA", lifecycle="active", repositories=["never4ga"])
        )
        linking.apply(linking.propose(WORKSPACE, "work_openproject"), creating=True)
        stored = store.get(WORKSPACE)
        assert stored is not None
        assert stored.frontmatter["repositories"] == ["never4ga"]
        assert stored.frontmatter["lifecycle"] == "active"
        assert stored.body == "# Never4gA\n"

    def test_a_failed_declaration_names_the_project_nothing_references(self) -> None:
        """The ordering hazard, reported rather than rolled back.

        A project is not cheap to delete, and this service will not pretend to
        have undone one. What it can do is say exactly which one is orphaned.
        """
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        proposal = linking.propose(WORKSPACE, "work_openproject")

        def refuse(_: StoredDocument) -> None:
            raise WriteRejectedError("the vault is read-only today")

        store.put = refuse  # type: ignore[method-assign,assignment]
        with pytest.raises(WriteRejectedError) as raised:
            linking.apply(proposal, creating=True)
        message = str(raised.value)
        assert "never4ga" in message
        assert "delete it or link a workspace to it by hand" in message
        # The project really was made: the report is not speculative.
        assert writer.find_project("never4ga") is not None

    def test_a_failure_before_anything_was_created_says_so(self) -> None:
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        writer.create_project("never4ga", "Never4gA")
        proposal = linking.propose(WORKSPACE, "work_openproject")

        def refuse(_: StoredDocument) -> None:
            raise WriteRejectedError("the vault is read-only today")

        store.put = refuse  # type: ignore[method-assign,assignment]
        with pytest.raises(WriteRejectedError, match="Nothing was created"):
            linking.apply(proposal)

    def test_a_connection_with_no_writable_adapter_is_refused(self) -> None:
        store = InMemoryDocumentStore()
        store.put(manifest(WORKSPACE, "Never4gA"))
        linking = WorkspaceLinkService(
            documents=store, connections=_Connections(), factory=lambda _: None
        )
        with pytest.raises(WriteRejectedError, match="no writable adapter"):
            linking.propose(WORKSPACE, "work_openproject")


class TestTheCreatingGate:
    """Creating a tracker project needs its own permission.

    The verb does two different things depending on what is already there.
    Recording a mapping to a project that exists is nearly harmless; creating
    one reaches somebody's instance, and that is the half the gate is for.
    """

    def test_linking_to_a_project_that_exists_needs_no_permission_to_create(self) -> None:
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        writer.create_project("never4ga", "Never4gA")
        outcome = linking.apply(linking.propose(WORKSPACE, "work_openproject"))
        assert outcome.created_project is False
        stored = store.get(WORKSPACE)
        assert stored is not None
        assert stored.frontmatter["work_management"]["project_ref"] == "never4ga"

    def test_creating_one_without_permission_is_refused(self) -> None:
        linking, store, writer = service(manifest(WORKSPACE, "Never4gA"))
        proposal = linking.propose(WORKSPACE, "work_openproject")
        with pytest.raises(WriteRejectedError, match="separate permission"):
            linking.apply(proposal)
        # Nothing happened on either side: no project, no declaration.
        assert writer.find_project("never4ga") is None
        stored = store.get(WORKSPACE)
        assert stored is not None
        assert "work_management" not in stored.frontmatter

    def test_the_refusal_names_the_project_it_would_have_made(self) -> None:
        linking, _, _ = service(manifest(WORKSPACE, "Never4gA"))
        with pytest.raises(WriteRejectedError) as raised:
            linking.apply(linking.propose(WORKSPACE, "work_openproject"))
        assert "never4ga" in str(raised.value)

    def test_with_permission_it_creates(self) -> None:
        linking, _, writer = service(manifest(WORKSPACE, "Never4gA"))
        outcome = linking.apply(linking.propose(WORKSPACE, "work_openproject"), creating=True)
        assert outcome.created_project is True
        assert writer.find_project("never4ga") is not None

    def test_permission_to_create_does_not_force_a_creation(self) -> None:
        # Passing it when the project is already there is not an error and does
        # not make a second one: it permits, it does not instruct.
        linking, _, writer = service(manifest(WORKSPACE, "Never4gA"))
        writer.create_project("never4ga", "Never4gA")
        outcome = linking.apply(linking.propose(WORKSPACE, "work_openproject"), creating=True)
        assert outcome.created_project is False
