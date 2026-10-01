"""The workspace and context endpoints.

details/api-cli-mcp-contract.md section 1 requires every interface to be a thin
client over the same services, so these verify the surface and its shape rather
than re-testing resolution, which is covered where it lives. All six specified
endpoints are here, `/v1/context/deep` included.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    GitRepositoryLocator,
    WorkspaceMappingFile,
)
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.api import create_app
from never4ga.composition import FileSessionStore
from never4ga.domain.context import ContextPack
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.services import (
    ContentService,
    SessionFactory,
    VaultInitializer,
    VaultSession,
    WorkspaceService,
)
from never4ga.services.workspaces import ScopeResolution

CREDENTIAL = "test-credential-not-a-real-one"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "never4ga"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Test Vault")
    content = ContentService(files, documents)
    content.create_workspace("Never4gA", workspace_type="product")
    return root


@pytest.fixture
def workspaces(tmp_path: Path) -> WorkspaceService:
    return WorkspaceService(
        WorkspaceMappingFile(tmp_path / "state" / "workspaces.json"),
        GitRepositoryLocator(),
    )


@pytest.fixture
def sessions(vault: Path, tmp_path: Path) -> SessionFactory:
    database = tmp_path / "index.sqlite3"
    documents = FileSystemMarkdownStore(vault)
    files = FileSystemVaultFileStore(vault)
    vault_id = documents.get_by_path(SYSTEM_MANIFEST).concept_id  # type: ignore[union-attr]

    @contextmanager
    def open_session() -> Iterator[VaultSession]:
        with closing(open_index(database)) as connection:
            yield VaultSession(
                vault_id=vault_id,
                files=files,
                documents=documents,
                metadata=SQLiteMetadataIndex(connection),
                text=SQLiteFTS5Index(connection),
                graph=SQLiteGraphIndex(connection),
                state=SQLiteIndexState(connection),
            )

    return open_session


@pytest.fixture
def workspace_id(vault: Path) -> str:
    documents = FileSystemMarkdownStore(vault)
    for document in documents.iter_documents():
        if document.frontmatter.get("type") == "workspace":
            return str(document.concept_id)
    raise AssertionError("the fixture vault should hold a workspace")


@pytest.fixture
def client(sessions: SessionFactory, workspaces: WorkspaceService) -> Iterator[TestClient]:
    with sessions() as session:
        session.indexer.reconcile()
        vault_id = session.vault_id
    app = create_app(
        sessions=sessions,
        credential=CREDENTIAL,
        vault_id=vault_id,
        workspaces=workspaces,
        # Wired the way `service/server.py` wires it. Without a session store,
        # a startup answered by the running service would mint an id that
        # `checkpoint` then rejects as never opened.
        session_store=lambda session: FileSessionStore(
            PlatformPaths.resolve().sessions_database(session.vault_id)
        ),
    )
    with TestClient(app, headers={"authorization": f"Bearer {CREDENTIAL}"}) as test_client:
        yield test_client


@pytest.fixture
def mapped(
    client: TestClient,
    workspaces: WorkspaceService,
    workspace_id: str,
    repository: Path,
    vault: Path,
) -> str:
    from pathlib import PurePath

    documents = FileSystemMarkdownStore(vault)
    document = documents.get(ConceptId.parse(workspace_id))
    assert document is not None
    workspaces.map(
        workspace_id=document.concept_id,
        workspace_path=document.path,
        repository_root=PurePath(repository),
    )
    return workspace_id


class TestWorkspaceEndpoints:
    def test_listing_is_empty_before_anything_is_mapped(self, client: TestClient) -> None:
        assert client.get("/v1/workspaces").json()["mappings"] == []

    def test_a_mapping_is_listed(self, client: TestClient, mapped: str) -> None:
        listed = client.get("/v1/workspaces").json()["mappings"]
        assert [entry["workspace_id"] for entry in listed] == [mapped]

    def test_one_mapping_can_be_read(self, client: TestClient, mapped: str) -> None:
        response = client.get(f"/v1/workspaces/{mapped}")
        assert response.status_code == 200
        assert response.json()["workspace_id"] == mapped

    def test_an_unmapped_workspace_is_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/v1/workspaces/{ConceptId.new()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_mapped"

    def test_a_malformed_id_is_refused(self, client: TestClient) -> None:
        assert client.get("/v1/workspaces/not-a-uuid").status_code == 400

    def test_resolving_a_mapped_repository(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        response = client.post("/v1/workspaces/resolve", json={"cwd": str(repository / "src")})
        assert response.status_code == 200
        assert response.json()["workspace_id"] == mapped

    def test_resolution_reports_why_and_carries_no_conflict(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/workspaces/resolve", json={"cwd": str(repository)}).json()
        assert body["reason"]["code"]
        assert body["conflicts"] == []

    def test_an_unmapped_location_does_not_guess(self, client: TestClient, tmp_path: Path) -> None:
        response = client.post("/v1/workspaces/resolve", json={"cwd": str(tmp_path)})
        assert response.status_code >= 400
        assert response.json()["error"]["code"] == "scope_unresolved"


class TestContextEndpoints:
    def test_a_startup_pack_is_returned(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        response = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}})
        assert response.status_code == 200
        assert response.json()["depth"] == "startup"

    def test_a_startup_pack_carries_the_workspace(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        assert body["scope"]["workspace_id"] == mapped
        assert body["items"]

    def test_every_item_says_why_it_is_there(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        assert all(item["reason"]["code"] for item in body["items"])

    def test_no_llm_stage_is_reported(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        assert body["llm_stages"] == []

    def test_the_budget_is_reported_back(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        assert set(body["usage"]) == {
            "items",
            "characters",
            "estimated_tokens",
            "full_documents",
            "dropped_items",
            # The whole required reading, and its ceiling (core/07 section 10).
            "required_characters",
            "required_ceiling",
        }

    def test_a_focused_pack_takes_terms(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        response = client.post(
            "/v1/context/focus",
            json={"scope": {"cwd": str(repository)}, "terms": ["never4ga"]},
        )
        assert response.status_code == 200
        assert response.json()["depth"] == "focused"

    def test_a_request_cannot_carry_a_prompt(self, client: TestClient, repository: Path) -> None:
        # core/00 #27: Never4gA is not in the inference business, and a body
        # that accepted a prompt would be the first step into it.
        response = client.post(
            "/v1/context/startup",
            json={"scope": {"cwd": str(repository)}, "prompt": "summarise this"},
        )
        assert response.status_code == 422

    def test_the_client_that_asked_is_recorded(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post(
            "/v1/context/startup",
            json={"scope": {"cwd": str(repository)}, "client": "codex"},
        ).json()
        assert body["client"] == "codex"

    def test_a_task_is_recorded_as_given_and_not_as_said(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        response = client.post(
            "/v1/context/startup",
            json={
                "scope": {"cwd": str(repository)},
                "task": "Implement member search for the directory",
            },
        )
        assert response.json()["task_given"] is True
        assert "Implement member search" not in response.text

    def test_a_prompt_sized_task_is_refused(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        # `--task` is a short description. The length cap is core/04 section 16
        # read as a constraint: this field is not where a prompt goes.
        response = client.post(
            "/v1/context/startup",
            json={"scope": {"cwd": str(repository)}, "task": "x" * 2000},
        )
        assert response.status_code == 422

    def test_a_deep_pack_has_an_endpoint(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        response = client.post("/v1/context/deep", json={"scope": {"cwd": str(repository)}})
        assert response.status_code == 200, response.text
        assert response.json()["depth"] == "deep"

    def test_a_deep_pack_is_at_least_as_wide_as_a_startup_one(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        scope = {"scope": {"cwd": str(repository)}}
        startup = client.post("/v1/context/startup", json=scope).json()
        deep = client.post("/v1/context/deep", json=scope).json()
        assert len(deep["items"]) >= len(startup["items"])


class TestAPathTheServiceCannotSee:
    """A caller's path that does not exist where the service runs.

    Under systemd's `PrivateTmp=true` the caller's `/tmp` is not the service's,
    so "register the repository" is advice that cannot work. The refusal names
    what is actually wrong, and what does work.
    """

    def test_a_startup_pack_says_the_path_cannot_be_seen(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        unseen = tmp_path / "private-tmp" / "worktree"
        response = client.post("/v1/context/startup", json={"scope": {"cwd": str(unseen)}})
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "path_not_visible"
        assert str(unseen) in error["message"]
        assert "--local" in error["repair_hint"]

    def test_resolving_says_the_path_cannot_be_seen(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        unseen = tmp_path / "private-tmp" / "worktree"
        response = client.post("/v1/workspaces/resolve", json={"cwd": str(unseen)})
        assert response.json()["error"]["code"] == "path_not_visible"


class TestWithoutMachineLocalState:
    """An app built without workspace state says so rather than answering."""

    @pytest.fixture
    def bare(self, sessions: SessionFactory) -> Iterator[TestClient]:
        with sessions() as session:
            vault_id = session.vault_id
        app = create_app(sessions=sessions, credential=CREDENTIAL, vault_id=vault_id)
        with TestClient(app, headers={"authorization": f"Bearer {CREDENTIAL}"}) as test_client:
            yield test_client

    def test_listing_is_unavailable_rather_than_empty(self, bare: TestClient) -> None:
        # An empty list would be a lie: "nothing is mapped" is a different fact
        # from "this service cannot tell you".
        response = bare.get("/v1/workspaces")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "capability_unavailable"


class TestAuthentication:
    def test_the_new_endpoints_require_the_credential(
        self, sessions: SessionFactory, workspaces: WorkspaceService
    ) -> None:
        with sessions() as session:
            vault_id = session.vault_id
        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=vault_id,
            workspaces=workspaces,
        )
        with TestClient(app) as anonymous:
            assert anonymous.get("/v1/workspaces").status_code == 401
            for path in ("/v1/workspaces/resolve", "/v1/context/startup"):
                assert anonymous.post(path, json={}).status_code == 401, path


class TestTheApiAndCliAgreeOnShape:
    """details/api-cli-mcp-contract.md section 1: one shape, two interfaces.

    `cli._pack_payload` relies on this test to hold the two to one shape.
    Without it, a field such as `session_id` added to the CLI would silently be
    missing from the API.
    """

    def test_a_startup_response_carries_a_session_id(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        raw = body["session_id"]
        assert str(SessionId.parse(raw)) == raw

    def test_focused_context_opens_no_session(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post(
            "/v1/context/focus",
            json={"scope": {"cwd": str(repository)}, "terms": ["thing"]},
        ).json()
        # Present and null rather than absent: an absent key and a null key
        # are different shapes, and `client` is already handled this way.
        assert body["session_id"] is None

    def test_the_session_it_reports_is_one_a_checkpoint_can_join(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        # Which interface answered must not change what a session is.
        from never4ga.domain.identity import SessionId as _SessionId

        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        session = _SessionId.parse(body["session_id"])
        store = FileSessionStore(PlatformPaths.resolve().sessions_database(_vault_id_of(client)))
        assert store.get(session) is not None

    def test_both_interfaces_emit_the_same_keys(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        from never4ga import rendering

        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()

        pack = ContextPack(
            scope=ResolvedScope(
                workspace_id=ConceptId.new(),
                workspace_path=VaultPath.parse("10_Workspaces/Demo"),
                reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
            ),
            session=SessionId.new(),
        )
        payload = rendering.scope_pack("startup", pack, ScopeResolution(scope=pack.scope))
        assert set(payload) == set(body)


def _vault_id_of(client: TestClient) -> ConceptId:
    """The vault the app is serving, from its own health endpoint."""
    return ConceptId.parse(client.get("/v1/health").json()["vault_id"])


class TestTheSessionRecordsTheActor:
    """The actor a startup was given must reach the session.

    `core/02` section 5.2 makes the producer a MUST, and `wrap` resolves it
    from the session when a later command passes `--session` alone. If the API
    dropped it, every session opened through a running service would record
    `human:owner`, and every document written through one would claim a person
    wrote it.
    """

    def _recorded_actor(self, client: TestClient, body: dict[str, object]) -> str:
        session = SessionId.parse(str(body["session_id"]))
        store = FileSessionStore(PlatformPaths.resolve().sessions_database(_vault_id_of(client)))
        record = store.get(session)
        assert record is not None
        return record.actor

    def test_the_actor_given_is_the_actor_recorded(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        body = client.post(
            "/v1/context/startup",
            json={"scope": {"cwd": str(repository)}, "actor": "claude-code/claude-opus-5"},
        ).json()
        assert self._recorded_actor(client, body) == "claude-code/claude-opus-5"

    def test_no_actor_still_means_the_owner(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        # The default is a statement, not a gap: a person typing at their own
        # vault is the owner, and only `--actor` says otherwise.
        from never4ga.services.authoring import OWNER_ACTOR

        body = client.post("/v1/context/startup", json={"scope": {"cwd": str(repository)}}).json()
        assert self._recorded_actor(client, body) == OWNER_ACTOR

    def test_a_blank_actor_is_refused_rather_than_recorded(
        self, client: TestClient, mapped: str, repository: Path
    ) -> None:
        # The CLI refuses `--actor ""` with a structured error; a request body
        # must not be the way around that.
        response = client.post(
            "/v1/context/startup",
            json={"scope": {"cwd": str(repository)}, "actor": "   "},
        )
        assert response.status_code == 422


class TestContextTermsAreParsed:
    """Context terms, parsed through the endpoint the service-backed CLI calls."""

    def test_a_term_holding_several_words_is_searched_for_its_words(
        self,
        client: TestClient,
        mapped: str,
        repository: Path,
        vault: Path,
        sessions: SessionFactory,
    ) -> None:
        from never4ga.cli import main

        created = main(
            [
                "--vault",
                str(vault),
                "concept",
                "create",
                "standard",
                "Palette",
                "--workspace",
                mapped,
                "--in",
                "10_Workspaces/Never4gA/Brand",
            ]
        )
        assert created == 0
        with sessions() as session:
            session.indexer.reconcile()

        response = client.post(
            "/v1/context/focus",
            json={"scope": {"cwd": str(repository)}, "terms": ["palette ownership"]},
        )
        assert response.status_code == 200
        assert "Palette" in [item["title"] for item in response.json()["items"]]
