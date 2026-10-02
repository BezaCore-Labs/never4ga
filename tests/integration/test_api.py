"""The loopback HTTP API.

details/api-cli-mcp-contract.md section 1: three interfaces expose the same
application services, and none owns business logic of its own. These tests
drive the real app over a real vault and a real SQLite index, because the
question they answer -- does an HTTP client get the same answer the CLI gets --
cannot be answered against a mock.

What is checked hardest is what would fail silently: authentication being
required where it must be, and the credential never appearing in anything the
service emits (details/security-configuration.md sections 3 and 9).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path, PurePath
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.api import create_app
from never4ga.domain.identity import ConceptId
from never4ga.services import SessionFactory, VaultInitializer
from tests.integration.conftest import CREDENTIAL


class TestHealth:
    def test_it_answers_without_a_credential(self, anonymous: TestClient) -> None:
        # The CLI probes this to decide whether a service is running, before it
        # has read any credential. It must also be the one endpoint that keeps
        # answering when everything else is unhappy (core/05 section 19).
        response = anonymous.get("/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_it_reports_the_installed_version(self, anonymous: TestClient) -> None:
        assert anonymous.get("/v1/health").json()["version"] == version("never4ga")

    def test_it_ignores_a_wrong_credential_rather_than_refusing(
        self, anonymous: TestClient
    ) -> None:
        response = anonymous.get("/v1/health", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 200

    def test_it_names_the_vault_it_serves(self, client: TestClient, vault_id: str) -> None:
        # A client pointed at one vault must be able to tell that the service
        # answering on this port is serving that vault and not another.
        assert client.get("/v1/health").json()["vault_id"] == vault_id

    def test_it_reports_the_api_version(self, client: TestClient) -> None:
        assert client.get("/v1/health").json()["api_version"] == "v1"

    def test_it_works_before_anything_is_indexed(self, client: TestClient) -> None:
        assert client.get("/v1/health").status_code == 200


class TestAuthenticationIsRequired:
    """details/security-configuration.md section 3, core/05 section 12."""

    PROTECTED: ClassVar[list[tuple[str, str, dict[str, Any] | None]]] = [
        ("get", "/v1/doctor", None),
        ("get", "/v1/vault", None),
        ("post", "/v1/index/reconcile", {}),
        ("post", "/v1/index/rebuild", {}),
        ("get", "/v1/concepts/01920000-0000-7000-8000-000000000000", None),
        ("post", "/v1/concepts/search", {"query": "anything"}),
        ("post", "/v1/concepts/validate", {}),
        ("post", "/v1/concepts", {"type": "knowledge", "title": "X"}),
        ("post", "/v1/concepts/adopt", {"path": "30_Knowledge/Notes/x.md"}),
    ]

    @pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
    def test_no_credential_is_refused(
        self, anonymous: TestClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        response = anonymous.request(method, path, json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
    def test_a_wrong_credential_is_refused(
        self, client: TestClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        response = client.request(
            method, path, headers={"Authorization": "Bearer wrong"}, json=body
        )
        assert response.status_code == 401

    def test_a_malformed_authorization_header_is_refused(self, client: TestClient) -> None:
        response = client.get("/v1/vault", headers={"Authorization": CREDENTIAL})
        assert response.status_code == 401

    def test_the_wrong_scheme_is_refused(self, client: TestClient) -> None:
        response = client.get("/v1/vault", headers={"Authorization": f"Basic {CREDENTIAL}"})
        assert response.status_code == 401

    def test_refusal_is_a_structured_error(self, anonymous: TestClient) -> None:
        # details/api-cli-mcp-contract.md section 12: agents must not have to
        # parse English.
        error = anonymous.get("/v1/vault").json()["error"]
        assert error["code"] == "unauthenticated"
        assert error["retryable"] is False
        assert error["repair_hint"]

    def test_the_challenge_names_the_scheme(self, anonymous: TestClient) -> None:
        response = anonymous.get("/v1/vault")
        assert response.headers["WWW-Authenticate"].startswith("Bearer")


class TestTheCredentialNeverLeaks:
    """details/security-configuration.md sections 3 and 9."""

    def test_no_response_carries_it(self, indexed: TestClient) -> None:
        for path in ("/v1/health", "/v1/doctor", "/v1/vault"):
            assert CREDENTIAL not in indexed.get(path).text

    def test_a_refusal_does_not_echo_what_was_presented(self, client: TestClient) -> None:
        presented = "a-wrong-credential-that-must-not-be-echoed"
        response = client.get("/v1/vault", headers={"Authorization": f"Bearer {presented}"})
        assert presented not in response.text
        assert CREDENTIAL not in response.text

    def test_the_openapi_document_does_not_carry_it(self, client: TestClient) -> None:
        assert CREDENTIAL not in client.get("/openapi.json").text

    def test_an_unexpected_failure_does_not_leak_it(self, client: TestClient) -> None:
        # A traceback rendered into a response is the classic way a secret
        # escapes. The handler must answer with a structured error instead.
        response = client.get("/v1/concepts/not-a-uuid")
        assert response.status_code == 400
        assert CREDENTIAL not in response.text


class TestVault:
    def test_it_reports_the_vault(self, client: TestClient, vault_id: str) -> None:
        payload = client.get("/v1/vault").json()
        assert payload["vault_id"] == vault_id
        assert payload["title"] == "Test Vault"
        assert payload["concept_count"] >= 3

    def test_it_counts_concepts_by_type(self, client: TestClient) -> None:
        by_type = client.get("/v1/vault").json()["concepts_by_type"]
        assert by_type["knowledge"] == 1
        assert by_type["workspace"] == 1

    def test_it_reports_that_the_index_is_not_built(self, client: TestClient) -> None:
        assert client.get("/v1/vault").json()["index"]["stale"] is True

    def test_it_carries_no_filesystem_path(self, client: TestClient, vault: Path) -> None:
        # The vault's location is machine-local configuration the client
        # already has (core/05 section 9). Repeating it over the wire adds
        # nothing and widens what a response discloses.
        assert str(vault) not in client.get("/v1/vault").text


class TestIndexing:
    def test_reconcile_indexes_the_vault(self, client: TestClient) -> None:
        payload = client.post("/v1/index/reconcile", json={}).json()
        assert payload["indexed"] >= 3
        assert payload["removed"] == 0

    def test_reconcile_is_idempotent(self, client: TestClient) -> None:
        client.post("/v1/index/reconcile", json={})
        second = client.post("/v1/index/reconcile", json={"changed_only": True}).json()
        assert second["indexed"] == 0
        assert second["unchanged"] >= 3

    def test_a_vault_with_no_foreign_material_reports_none(self, client: TestClient) -> None:
        # One payload shape: the key is there, and zero, everywhere.
        payload = client.post("/v1/index/reconcile", json={}).json()
        assert payload["foreign"] == {"indexed": 0, "unchanged": 0, "removed": 0}

    def test_foreign_notes_are_counted_apart(self, client: TestClient, vault: Path) -> None:
        # A second `init` registers a directory that appeared since as foreign
        # material (core/01 section 1).
        (vault / "Projects").mkdir()
        (vault / "Projects" / "beds.md").write_text("# Raised beds\n", encoding="utf-8")
        VaultInitializer(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).initialize("Test Vault")
        payload = client.post("/v1/index/reconcile", json={}).json()
        assert payload["foreign"] == {"indexed": 1, "unchanged": 0, "removed": 0}
        status = client.get("/v1/index/status").json()
        assert (status["indexed_foreign"], status["stale"]) == (1, False)

    def test_rebuild_reindexes_everything(self, indexed: TestClient) -> None:
        payload = indexed.post("/v1/index/rebuild", json={}).json()
        assert payload["indexed"] >= 3

    def test_the_index_becomes_current(self, indexed: TestClient) -> None:
        assert indexed.get("/v1/vault").json()["index"]["stale"] is False

    def test_an_empty_body_is_accepted(self, client: TestClient) -> None:
        assert client.post("/v1/index/reconcile").status_code == 200


class TestSearch:
    def test_it_finds_a_concept(self, indexed: TestClient) -> None:
        payload = indexed.post("/v1/concepts/search", json={"query": "retrieval"}).json()
        assert payload["results"]
        assert any(result["title"] == "Hybrid Retrieval" for result in payload["results"])

    def test_every_result_carries_its_acquisition_reason(self, indexed: TestClient) -> None:
        # core/07: every Context Pack item carries provenance. A search result
        # is where that starts.
        payload = indexed.post("/v1/concepts/search", json={"query": "retrieval"}).json()
        assert all(result["reason"] for result in payload["results"])
        assert all(result["retriever"] for result in payload["results"])

    def test_it_filters_by_type(self, indexed: TestClient) -> None:
        payload = indexed.post(
            "/v1/concepts/search", json={"query": "retrieval", "types": ["workspace"]}
        ).json()
        assert payload["results"] == []

    def test_it_honours_the_limit(self, indexed: TestClient) -> None:
        payload = indexed.post("/v1/concepts/search", json={"query": "a", "limit": 1}).json()
        assert len(payload["results"]) <= 1

    def test_it_reports_a_stale_index_rather_than_hiding_it(self, client: TestClient) -> None:
        assert client.post("/v1/concepts/search", json={"query": "retrieval"}).json()[
            "index_is_stale"
        ]

    def test_a_missing_query_is_a_structured_error(self, indexed: TestClient) -> None:
        response = indexed.post("/v1/concepts/search", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


class TestConcepts:
    def _first_concept_id(self, client: TestClient) -> str:
        results = client.post("/v1/concepts/search", json={"query": "retrieval"}).json()["results"]
        return str(results[0]["id"])

    def test_it_reads_one_concept(self, indexed: TestClient) -> None:
        concept_id = self._first_concept_id(indexed)
        payload = indexed.get(f"/v1/concepts/{concept_id}").json()
        assert payload["id"] == concept_id
        assert payload["frontmatter"]["title"] == "Hybrid Retrieval"
        assert payload["body"]

    def test_it_preserves_unknown_frontmatter(self, indexed: TestClient, vault: Path) -> None:
        # core/02 section 31: unknown extension data survives a round trip, and
        # that has to remain true through an interface.
        concept_id = self._first_concept_id(indexed)
        path = Path(indexed.get(f"/v1/concepts/{concept_id}").json()["path"])
        text = (vault / path).read_text()
        (vault / path).write_text(text.replace("title:", "x_future_field: kept\ntitle:", 1))
        payload = indexed.get(f"/v1/concepts/{concept_id}").json()
        assert payload["frontmatter"]["x_future_field"] == "kept"

    def test_an_unknown_concept_is_a_structured_404(self, indexed: TestClient) -> None:
        response = indexed.get("/v1/concepts/01920000-0000-7000-8000-000000000000")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "concept_not_found"

    def test_a_malformed_id_is_refused_before_it_is_looked_up(self, client: TestClient) -> None:
        response = client.get("/v1/concepts/not-a-uuid")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_identity"

    def test_a_concept_is_readable_before_anything_is_indexed(self, client: TestClient) -> None:
        # Markdown is canonical (core/00 #1). The index only adds to it.
        client.post("/v1/index/reconcile", json={})
        concept_id = self._first_concept_id(client)
        assert client.get(f"/v1/concepts/{concept_id}").status_code == 200


class TestValidate:
    def test_it_validates_the_whole_vault(self, client: TestClient) -> None:
        payload = client.post("/v1/concepts/validate", json={}).json()
        assert payload["checked"] >= 3
        assert payload["valid"] == payload["checked"]

    def test_it_reports_rather_than_repairs(self, client: TestClient, vault: Path) -> None:
        # core/02 section 23: validation always reports, and repair is an
        # explicit act. The file is still exactly as it was afterwards.
        broken = vault / "30_Knowledge" / "Notes" / "incomplete.md"
        original = (
            "---\n"
            "type: knowledge\n"
            "id: 01920000-0000-7000-8000-0000000000aa\n"
            "schema: never4ga/0.1\n"
            "title: Incomplete\n"
            "---\n\nNo created_at.\n"
        )
        broken.write_text(original)
        payload = client.post("/v1/concepts/validate", json={"level": "core"}).json()
        assert payload["valid"] < payload["checked"]
        assert broken.read_text() == original

    def test_an_unreadable_document_is_reported_not_skipped(
        self, client: TestClient, vault: Path
    ) -> None:
        # `iter_documents` yields only what parses. A document with broken YAML
        # must still be counted, or validation would say "all valid".
        (vault / "30_Knowledge" / "Notes" / "unparseable.md").write_text(
            '---\ntype: knowledge\ndescription: "What "done" means"\n---\nbody\n'
        )
        payload = client.post("/v1/concepts/validate", json={}).json()
        assert payload["valid"] < payload["checked"]
        assert any(not document["ok"] for document in payload["documents"])

    def test_it_validates_named_paths_only(self, client: TestClient) -> None:
        payload = client.post(
            "/v1/concepts/validate", json={"paths": ["50_System/system.md"]}
        ).json()
        assert payload["checked"] == 1

    def test_an_unknown_level_is_a_structured_error(self, client: TestClient) -> None:
        response = client.post("/v1/concepts/validate", json={"level": "pedantic"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


class TestDoctor:
    def test_it_diagnoses_a_healthy_vault(self, indexed: TestClient) -> None:
        payload = indexed.get("/v1/doctor").json()
        assert payload["healthy"] is True
        assert payload["findings"] == []

    def test_it_reports_findings(self, indexed: TestClient, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "broken.md").write_text(
            "---\ntype: knowledge_note\ntitle: Broken\n---\n\nNo id.\n"
        )
        payload = indexed.get("/v1/doctor").json()
        assert payload["healthy"] is False
        assert payload["findings"]
        assert all(finding["severity"] in {"error", "warning"} for finding in payload["findings"])

    def test_the_level_is_selectable(self, indexed: TestClient) -> None:
        assert indexed.get("/v1/doctor", params={"level": "okf"}).status_code == 200

    def test_an_unknown_level_is_refused(self, indexed: TestClient) -> None:
        assert indexed.get("/v1/doctor", params={"level": "pedantic"}).status_code == 422


class TestTheContract:
    def test_every_route_is_under_the_version_prefix(self, client: TestClient) -> None:
        # details/api-cli-mcp-contract.md section 2: breaking changes require a
        # new major path.
        paths = client.get("/openapi.json").json()["paths"]
        assert paths
        assert all(path.startswith("/v1/") for path in paths)

    def test_the_documented_surface_is_exactly_what_the_milestone_scopes(
        self, client: TestClient
    ) -> None:
        # An endpoint that returns a stub is worse than one that does not
        # exist, because it invites a client to depend on it. The surface is
        # exactly what is implemented. Sessions, capture and adapters are not
        # yet. The work writes are update, create and comment, with no project
        # creation.
        #
        # The work reads, index status and the findings ledger are read-only.
        # There is no finding-dismissal endpoint: nothing reads a dismissal
        # yet, and an endpoint that writes a record the product ignores would
        # mislead.
        #
        # The two concept writes wrap the same creation and adoption services
        # the CLI calls (details/api-cli-mcp-contract.md section 3). The Schema
        # group publishes the Type Registry so a client can offer the types,
        # their optional fields and their lifecycle vocabularies. It is
        # read-only, and a property of the schema version rather than of a vault.
        paths = set(client.get("/openapi.json").json()["paths"])
        assert paths == {
            "/v1/concepts",
            "/v1/concepts/adopt",
            "/v1/schema/types",
            "/v1/health",
            "/v1/doctor",
            "/v1/vault",
            "/v1/index/reconcile",
            "/v1/index/rebuild",
            "/v1/index/status",
            "/v1/maintenance/findings",
            "/v1/work/items",
            "/v1/work/items/{work_item}",
            "/v1/concepts/{concept_id}",
            "/v1/concepts/search",
            "/v1/concepts/validate",
            "/v1/workspaces",
            "/v1/workspaces/{workspace_id}",
            "/v1/workspaces/resolve",
            "/v1/work/update",
            "/v1/work/create",
            "/v1/work/comment",
            "/v1/context/startup",
            "/v1/context/focus",
            "/v1/context/deep",
        }

    def test_an_unknown_route_is_a_structured_error(self, client: TestClient) -> None:
        response = client.get("/v1/sessions")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_every_error_body_has_the_section_12_shape(self, client: TestClient) -> None:
        response = client.get("/v1/concepts/not-a-uuid")
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details", "retryable", "repair_hint"}

    def test_responses_are_json(self, client: TestClient) -> None:
        response = client.get("/v1/health")
        assert response.headers["content-type"].startswith("application/json")
        json.loads(response.text)


class TestFailureDoesNotDestroyTheService:
    """core/05 section 19: a failed optional subsystem leaves the service up."""

    def test_one_unreadable_document_does_not_stop_the_rest(
        self, client: TestClient, vault: Path
    ) -> None:
        # core/05 section 19's own example: "one invalid Markdown file -> index
        # other files; report validation error".
        (vault / "30_Knowledge" / "Notes" / "unparsable.md").write_text(
            "---\nthis: [is not: valid yaml\n---\nbody\n"
        )
        payload = client.post("/v1/index/reconcile", json={}).json()
        assert payload["indexed"] >= 3

    def test_the_unreadable_document_is_reported(self, client: TestClient, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "unparsable.md").write_text(
            "---\nthis: [is not: valid yaml\n---\nbody\n"
        )
        client.post("/v1/index/reconcile", json={})
        findings = client.get("/v1/doctor").json()["findings"]
        assert any("unparsable.md" in finding["message"] for finding in findings)

    def test_the_service_still_answers_afterwards(self, client: TestClient, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "unparsable.md").write_text(
            "---\nthis: [is not: valid yaml\n---\nbody\n"
        )
        client.post("/v1/index/reconcile", json={})
        assert client.get("/v1/health").json()["status"] == "ok"
        assert client.get("/v1/vault").status_code == 200


class TestWorkWrites:
    """The `/v1/work` write endpoints.

    The point of these is that the HTTP surface and the CLI agree, because
    `core/05` section 13 makes both thin clients over one service. The gate
    logic is tested once, in `tests/unit/test_cli_work.py`; what is asserted
    here is the wiring, the status codes and the default.
    """

    @pytest.fixture
    def writing(
        self, sessions: SessionFactory, vault_id: str, tmp_path: Path
    ) -> Iterator[TestClient]:
        from never4ga.adapters.fakes import (
            FakeRepositoryLocator,
            FakeWorkManagementWriter,
            InMemoryWorkspaceMappingStore,
        )
        from never4ga.domain.identity import ExternalId
        from never4ga.ports.work_management import WorkItem
        from never4ga.services.work_writing import WorkWriteService
        from never4ga.services.workspaces import WorkspaceService

        item = WorkItem(
            ref=ExternalId(provider="fake-pm", value="838"), title="A ticket", status="New"
        )
        writer = FakeWorkManagementWriter([item])
        repository = PurePath(tmp_path / "repo")

        def build(session: Any) -> WorkWriteService:
            workspaces = WorkspaceService(
                InMemoryWorkspaceMappingStore(), FakeRepositoryLocator([repository])
            )
            manifest = next(
                document
                for document in session.documents.iter_documents()
                if document.frontmatter.get("type") == "workspace"
            )
            workspaces.map(
                workspace_id=manifest.concept_id,
                workspace_path=manifest.path,
                repository_root=repository,
            )
            return WorkWriteService(
                documents=session.documents, workspaces=workspaces, factory=lambda _: writer
            )

        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            work_writer=build,
        )
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            test_client.extra = {"repo": str(repository), "writer": writer}
            yield test_client

    def test_the_endpoints_require_the_credential(self, anonymous: TestClient) -> None:
        response = anonymous.post("/v1/work/update", json={"cwd": "/x", "work_item": "1"})
        assert response.status_code == 401

    def test_a_service_built_without_a_writer_says_so_rather_than_stubbing(
        self, client: TestClient
    ) -> None:
        # An endpoint that pretended to work would invite a client to depend on
        # it.
        response = client.post(
            "/v1/work/update", json={"cwd": "/x", "work_item": "1", "fields": {}}
        )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "work_writing_unavailable"

    def test_a_workspace_with_no_tracker_is_a_conflict_not_a_bad_request(
        self, writing: TestClient
    ) -> None:
        # The request was well formed; the vault does not permit it.
        response = writing.post(
            "/v1/work/update",
            json={"cwd": writing.extra["repo"], "work_item": "838", "fields": {"status": "x"}},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "work_management_undeclared"


class TestWorkWriteDefaults:
    def test_apply_defaults_to_false_in_the_schema(self) -> None:
        """The HTTP form of `--apply`, and its default is the safe one.

        A client that forgets the field gets a description rather than a change
        to somebody's system of record.
        """
        from never4ga.api.models import WorkUpdateRequest

        assert WorkUpdateRequest(cwd="/x", work_item="1").apply is False


class _AppliedWriteService:
    """A write service whose every write went through, as a tracker would reply.

    What is under test is the API's bookkeeping after a write, not the tracker
    round trip or the gates, which `TestWorkWrites` and the unit tests cover.
    """

    @staticmethod
    def _reply(action: str, **extra: Any) -> dict[str, Any]:
        return {
            "workspace": "w",
            "connection": "c",
            "action": action,
            "proposal": action,
            "applied": True,
            **extra,
        }

    def bind(self, where: PurePath, *, applying: bool) -> object:
        return object()

    def update(self, bound: object, work_item: str, fields: Any) -> dict[str, Any]:
        return self._reply("update")

    def create(self, bound: object, fields: Any) -> dict[str, Any]:
        return self._reply("create", item={"ref": "840", "title": "A new item"})

    def comment(
        self, bound: object, work_item: str, body: str, *, amends: str | None = None
    ) -> dict[str, Any]:
        return self._reply("comment")


class TestAnUnreachableTrackerIsWorthRetrying:
    """A tracker that does not answer is a retryable 503.

    It must not be reported as an unsupported operation with ``retryable:
    false``, which would tell a client not to retry the one failure it should.
    Every verb is driven through the real adapter with the network missing.
    """

    @pytest.fixture
    def down(self, sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
        from tests.openproject_fixtures import DownReader, DownTracker

        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            work_writer=lambda _: DownTracker(),  # type: ignore[arg-type,return-value]
            work_reader=lambda _: DownReader(),  # type: ignore[arg-type,return-value]
        )
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            yield test_client

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/v1/work/comment", {"work_item": "838", "body": "done"}),
            ("/v1/work/update", {"work_item": "838", "fields": {"status": "Closed"}}),
            ("/v1/work/create", {"title": "A new item"}),
        ],
    )
    def test_every_write_says_the_tracker_did_not_answer(
        self, down: TestClient, path: str, body: dict[str, Any]
    ) -> None:
        response = down.post(path, json={**body, "cwd": "/x", "apply": True})

        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "tracker_unreachable"
        assert error["retryable"] is True
        assert "does not support" not in error["message"]

    @pytest.mark.parametrize("path", ["/v1/work/items", "/v1/work/items/838"])
    def test_a_read_says_the_same_thing_with_the_same_status(
        self, down: TestClient, path: str
    ) -> None:
        # A read gets the same code and status. A 400 would tell a client its
        # request was malformed.
        response = down.get(path, params={"cwd": "/x"})

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "tracker_unreachable"
        assert response.json()["error"]["retryable"] is True


class TestAMissingItemIsNotFound:
    """An item the tracker does not have is a 404, not a 400.

    400 tells a client its request was malformed. The reference was legible;
    the tracker answered and does not have it, which is what 404 says.
    """

    @pytest.fixture
    def reachable(self, sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
        from tests.openproject_fixtures import TrackerReader, TrackerWriter

        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            work_writer=lambda _: TrackerWriter(),  # type: ignore[arg-type,return-value]
            work_reader=lambda _: TrackerReader(),  # type: ignore[arg-type,return-value]
        )
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            yield test_client

    def test_a_read_of_an_item_the_tracker_does_not_have(self, reachable: TestClient) -> None:
        response = reachable.get("/v1/work/items/999", params={"cwd": "/x"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "work_item_not_found"

    def test_a_read_of_one_it_has_still_answers(self, reachable: TestClient) -> None:
        assert reachable.get("/v1/work/items/838", params={"cwd": "/x"}).status_code == 200

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/v1/work/comment", {"work_item": "999", "body": "done"}),
            ("/v1/work/update", {"work_item": "999", "fields": {"status": "Closed"}}),
        ],
    )
    def test_a_write_to_an_item_the_tracker_does_not_have(
        self, reachable: TestClient, path: str, body: dict[str, Any]
    ) -> None:
        response = reachable.post(path, json={**body, "cwd": "/x", "apply": True})

        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "work_item_not_found"
        assert error["retryable"] is False


class _RefusingReadService:
    """A read service whose binding is refused with a given code."""

    def __init__(self, code: str) -> None:
        self._code = code

    def bind(self, where: PurePath) -> object:
        from never4ga.errors import StructuredError

        return StructuredError(self._code, f"refused: {self._code}", {})


class TestAReadRefusalIsNotABadRequest:
    """A refused read gets the status of its write-side twin, not a default 400.

    For the same state of the vault, the read says what the write says.
    """

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            # A workspace whose policy is `reference` knows the mapping and
            # does not query; the write side's policy refusal is 403.
            ("sync_policy_declines", 403),
            # No adapter or no token for the connection on this machine; the
            # write side's `connection_not_writable` is 409.
            ("connection_unreadable", 409),
        ],
    )
    def test_the_status_matches_the_write_sides(
        self, sessions: SessionFactory, vault_id: str, code: str, status: int
    ) -> None:
        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            work_reader=lambda _: _RefusingReadService(code),  # type: ignore[arg-type,return-value]
        )
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            response = client.get("/v1/work/items", params={"cwd": "/x"})

        assert response.status_code == status
        assert response.json()["error"]["code"] == code


class TestAWriteThroughTheApiIsRecordedAgainstItsSession:
    """A `/v1/work` write that names a ``session_id`` is recorded against it.

    The contract's operation metadata asks every mutating operation to accept
    one (details/api-cli-mcp-contract.md section 9). Without it a client writing
    through the service could never satisfy the check `wrap` makes.
    """

    @pytest.fixture
    def store(self, tmp_path: Path) -> Any:
        # The store the service composes: a connection per operation, so the
        # request thread and the test thread never share one.
        from never4ga.composition import FileSessionStore

        return FileSessionStore(tmp_path / "sessions.sqlite3")

    @pytest.fixture
    def recording(
        self, sessions: SessionFactory, vault_id: str, store: Any
    ) -> Iterator[TestClient]:
        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            session_store=lambda _: store,
            work_writer=lambda _: _AppliedWriteService(),  # type: ignore[arg-type,return-value]
        )
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            yield test_client

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/v1/work/comment", {"work_item": "840", "body": "done"}),
            ("/v1/work/update", {"work_item": "840", "fields": {"status": "Closed"}}),
            ("/v1/work/create", {"title": "A new item"}),
        ],
    )
    def test_a_declared_item_written_through_the_api_is_not_outstanding(
        self, recording: TestClient, store: Any, vault_id: str, path: str, body: dict[str, Any]
    ) -> None:
        from never4ga.services.sessions import SessionService

        sessions = SessionService(store)
        session = sessions.open(workspace=ConceptId.parse(vault_id), actor="a-client").id
        sessions.checkpoint(session, note="acting", work=("840: ready",))

        response = recording.post(
            path, json={**body, "cwd": "/x", "apply": True, "session_id": str(session)}
        )

        assert response.status_code == 200
        assert sessions.outstanding_work(session) == ()

    def test_a_write_that_names_no_session_records_nothing(
        self, recording: TestClient, store: Any, vault_id: str
    ) -> None:
        from never4ga.services.sessions import SessionService

        sessions = SessionService(store)
        session = sessions.open(workspace=ConceptId.parse(vault_id), actor="a-client").id
        sessions.checkpoint(session, note="acting", work=("840: ready",))

        recording.post(
            "/v1/work/comment",
            json={"work_item": "840", "body": "done", "cwd": "/x", "apply": True},
        )

        assert sessions.outstanding_work(session) == ("840",)

    def test_an_ordinary_write_says_nothing_about_a_record(
        self, recording: TestClient, store: Any, vault_id: str
    ) -> None:
        from never4ga.services.sessions import SessionService

        session = (
            SessionService(store).open(workspace=ConceptId.parse(vault_id), actor="a-client").id
        )

        response = recording.post(
            "/v1/work/comment",
            json={
                "work_item": "840",
                "body": "done",
                "cwd": "/x",
                "apply": True,
                "session_id": str(session),
            },
        )

        assert response.json()["record_lost"] is None


class TestAWriteIsNotFailedBecauseItsRecordWas:
    """A write the tracker accepted succeeds even when recording it fails.

    ``record_tracker_write`` runs after the write has landed. A locked or
    corrupt ``sessions.sqlite3`` must not turn that into a failed write.
    """

    @pytest.fixture
    def broken(self, sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
        from never4ga.errors import SessionStoreError

        class _Unusable:
            def record_work_action(self, action: Any) -> None:
                raise SessionStoreError("database is locked")

        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            session_store=lambda _: _Unusable(),  # type: ignore[arg-type,return-value]
            work_writer=lambda _: _AppliedWriteService(),  # type: ignore[arg-type,return-value]
        )
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
            yield test_client

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/v1/work/comment", {"work_item": "840", "body": "done"}),
            ("/v1/work/update", {"work_item": "840", "fields": {"status": "Closed"}}),
            ("/v1/work/create", {"title": "A new item"}),
        ],
    )
    def test_the_write_is_reported_as_the_success_it_was(
        self, broken: TestClient, path: str, body: dict[str, Any]
    ) -> None:
        response = broken.post(
            path,
            json={
                **body,
                "cwd": "/x",
                "apply": True,
                "session_id": "01a0c1be-f64a-76b0-8c96-bff5b4a95382",
            },
        )

        assert response.status_code == 200
        assert response.json()["applied"] is True

    def test_the_lost_record_is_named_in_the_answer(self, broken: TestClient) -> None:
        # Not silent: a client that retried a create because it read a 500
        # would file the item twice, and one told nothing at all would let
        # `wrap` call the item outstanding with no explanation.
        response = broken.post(
            "/v1/work/comment",
            json={
                "work_item": "840",
                "body": "done",
                "cwd": "/x",
                "apply": True,
                "session_id": "01a0c1be-f64a-76b0-8c96-bff5b4a95382",
            },
        )

        assert "database is locked" in response.json()["record_lost"]
