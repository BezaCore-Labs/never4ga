"""The CLI as a client of a running service.

Two modes, one contract. What is tested here is that the mode is invisible:
the same command produces the same JSON whether the work happened in this
process or in the service's.

A real service runs on a real loopback socket. The only thing patched is the
port the CLI reads out of its config, because the service is given port 0 and
the machine decides which one it gets -- everything else, including the probe,
the vault-id check and the credential lookup from the shared secret store, is
the code that runs for a user.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import never4ga.cli
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.config import LocalConfig, ServiceEndpoint
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.service_manager import ServiceState, ServiceStatus
from never4ga.service import LocalService, ServiceSettings
from never4ga.services import ContentService, VaultInitializer


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)

    @property
    def error(self) -> Any:
        # The service logs to stderr in the same process, as a foreground
        # daemon should, so the structured error is found rather than assumed
        # to be the whole stream.
        return json.loads(self.err[self.err.index("{") :])["error"]


Run = Callable[..., Result]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Client Vault")
    ContentService(files, documents).create_knowledge(
        "Hybrid Retrieval", description="Lexical and vector retrieval combined."
    )
    return root


@pytest.fixture
def nothing_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at an endpoint no service can be answering on.

    Without this the test reads the real machine-local config, and a developer
    who has actually installed `never4ga.service` -- which the operations
    documentation tells them to -- has their own daemon answer on the default
    port and the assertion fails. A test must not depend on the product being
    switched off.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    endpoint = ServiceEndpoint(port=closed)

    def configured(cls: type[LocalConfig], path: Path | None = None) -> LocalConfig:
        return LocalConfig(service=endpoint)

    monkeypatch.setattr(LocalConfig, "load", classmethod(configured))


@pytest.fixture
def serving(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalService]:
    """A service for this vault, findable through the CLI's own config."""
    service = LocalService(
        ServiceSettings(
            vault=vault,
            endpoint=ServiceEndpoint(port=0),
            quiet_period=0.2,
            reconcile_interval=3600.0,
        ),
        paths=PlatformPaths.resolve(),
    )
    service.start()
    # Spend the one-shot full pass startup defers before any test body runs.
    # Left pending, it fires on the maintenance pump a moment later and
    # reconciles underneath a test that deliberately made the index stale, so
    # the result would depend on timing.
    service.runtime.tick()
    host, port = service.bound
    endpoint = ServiceEndpoint(host=host, port=port)

    def configured(cls: type[LocalConfig], path: Path | None = None) -> LocalConfig:
        return LocalConfig(service=endpoint)

    monkeypatch.setattr(LocalConfig, "load", classmethod(configured))
    try:
        yield service
    finally:
        service.stop()


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        argv = ["--vault", str(vault), *(["--json"] if as_json else []), *arguments]
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


class TestWithNoServiceRunning:
    """Every command still works in process when no service is running.

    core/05 section 19's corollary and the whole point of local-first: the
    vault stays usable with Never4gA not running.
    """

    def test_index_runs_in_process(self, run: Run) -> None:
        assert run("index", as_json=True).json["indexed"] >= 2

    def test_search_runs_in_process(self, run: Run) -> None:
        run("index")
        assert run("search", "retrieval", as_json=True).json["results"]

    def test_doctor_runs_in_process(self, run: Run) -> None:
        assert run("doctor", "--level", "core").code == EXIT_OK

    def test_a_local_write_is_allowed(self, run: Run) -> None:
        assert run("--local", "index", as_json=True).code == EXIT_OK

    def test_the_probe_does_not_slow_a_command_down(self, run: Run) -> None:
        # A refused connection is immediate. This is a guard against the probe
        # ever growing a retry: every command pays for it.
        import time

        started = time.monotonic()
        run("status")
        assert time.monotonic() - started < 2.0


class TestWhenAServiceIsRunning:
    def test_the_work_goes_to_the_service(self, run: Run, serving: LocalService) -> None:
        # Two facts together are the proof: an in-process write is refused
        # while the service owns the index, and `index` succeeds anyway. It
        # cannot have run here.
        assert run("--local", "index", as_json=True).code == EXIT_FAILED
        assert run("index", as_json=True).json["indexed"] >= 0

    def test_search_is_answered(self, run: Run, serving: LocalService) -> None:
        results = run("search", "retrieval", as_json=True).json["results"]
        assert any(result["title"] == "Hybrid Retrieval" for result in results)

    def test_doctor_is_answered(self, run: Run, serving: LocalService) -> None:
        payload = run("doctor", "--level", "core", as_json=True).json
        assert payload["healthy"] is True
        # The CLI adds what it knows and the wire deliberately does not carry.
        assert payload["vault"]

    def test_concept_get_is_answered(self, run: Run, serving: LocalService) -> None:
        concept_id = run("search", "retrieval", as_json=True).json["results"][0]["id"]
        payload = run("concept", "get", concept_id, as_json=True).json
        assert payload["id"] == concept_id
        assert payload["frontmatter"]["title"] == "Hybrid Retrieval"

    def test_rebuild_is_answered(self, run: Run, serving: LocalService) -> None:
        assert run("rebuild", as_json=True).json["indexed"] >= 2

    def test_index_reports_what_the_service_did(self, run: Run, serving: LocalService) -> None:
        # The service reconciled at startup, so a second run has nothing new.
        payload = run("index", "--changed", as_json=True).json
        assert payload["indexed"] == 0
        assert payload["unchanged"] >= 2

    def test_a_service_error_keeps_its_shape(self, run: Run, serving: LocalService) -> None:
        # details/api-cli-mcp-contract.md section 12: one error vocabulary, not
        # one per interface.
        result = run("concept", "get", "01920000-0000-7000-8000-000000000000", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.error["code"] == "concept_not_found"
        assert result.error["repair_hint"]

    def test_creating_content_still_happens_here(self, run: Run, serving: LocalService) -> None:
        # Canonical writes are Markdown, not derived state, and the service
        # owns only the latter. The watcher picks the file up afterwards.
        result = run("knowledge", "create", "Written Locally", as_json=True)
        assert result.code == EXIT_OK
        assert (Path(run("status", as_json=True).json["vault"]) / result.json["path"]).is_file()


class TestTheModeIsInvisible:
    def test_search_returns_the_same_payload_either_way(
        self, run: Run, serving: LocalService
    ) -> None:
        # Both modes read the same derived database -- which is exactly why a
        # second *writer* is refused, and why a second reader is not.
        through_service = run("search", "retrieval", as_json=True).json
        in_process = run("--local", "search", "retrieval", as_json=True).json
        assert through_service == in_process

    def test_concept_get_returns_the_same_payload_either_way(
        self, run: Run, serving: LocalService
    ) -> None:
        concept_id = run("search", "retrieval", as_json=True).json["results"][0]["id"]
        assert (
            run("concept", "get", concept_id, as_json=True).json
            == run("--local", "concept", "get", concept_id, as_json=True).json
        )

    def test_human_output_is_a_rendering_of_the_json(self, run: Run, serving: LocalService) -> None:
        human = run("search", "retrieval").out
        payload = run("search", "retrieval", as_json=True).json
        assert payload["results"][0]["title"] in human
        assert payload["results"][0]["path"] in human

    def test_doctor_human_output_names_every_finding(
        self, run: Run, serving: LocalService, vault: Path
    ) -> None:
        (vault / "30_Knowledge" / "Notes" / "unparsable.md").write_text(
            "---\nthis: [is not: valid yaml\n---\nbody\n"
        )
        human = run("doctor").out
        payload = run("doctor", as_json=True).json
        assert ("problems found" in human) is not payload["healthy"]
        for finding in payload["findings"]:
            assert finding["code"] in human


class TestLocalRefusesToBeASecondWriter:
    """While a service owns the index, an in-process write to it is refused."""

    def test_a_local_index_is_refused(self, run: Run, serving: LocalService) -> None:
        result = run("--local", "index", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.error["code"] == "service_owns_the_index"
        assert "second writer" in result.error["message"]

    def test_the_refusal_says_what_to_do_instead(self, run: Run, serving: LocalService) -> None:
        hint = run("--local", "index", as_json=True).error["repair_hint"]
        assert "without --local" in hint

    def test_a_local_rebuild_is_refused(self, run: Run, serving: LocalService) -> None:
        result = run("--local", "rebuild", as_json=True)
        assert result.code == EXIT_FAILED
        assert "second writer" in result.error["message"]

    def test_a_local_read_is_not_refused(self, run: Run, serving: LocalService) -> None:
        # "Reads are unaffected -- concurrent readers are what WAL is for."
        assert run("--local", "search", "retrieval", as_json=True).code == EXIT_OK
        assert run("--local", "index", "--status", as_json=True).code == EXIT_OK
        assert run("--local", "doctor", "--level", "core").code == EXIT_OK

    def test_a_local_creation_is_not_refused(self, run: Run, serving: LocalService) -> None:
        assert run("--local", "knowledge", "create", "Still Fine").code == EXIT_OK


class TestItOnlyTrustsAServiceForThisVault:
    def test_a_service_for_another_vault_is_ignored(
        self,
        run: Run,
        serving: LocalService,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A machine can hold several vaults and one service. Sending a command
        # about this vault to a service that owns another would answer
        # confidently and wrongly -- so the CLI checks the vault id the health
        # endpoint reports before it hands anything over.
        other = tmp_path / "other"
        other.mkdir()
        assert main(["--vault", str(other), "init"]) == EXIT_OK
        capsys.readouterr()

        # Work on `other` happens in process, and is allowed to, because no
        # service owns that vault's index.
        assert main(["--vault", str(other), "--json", "index"]) == EXIT_OK
        capsys.readouterr()
        assert main(["--vault", str(other), "--json", "--local", "index"]) == EXIT_OK


class TestServiceStatus:
    def test_it_reports_a_running_service(
        self, run: Run, serving: LocalService, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = run("service", "status", as_json=True).json
        assert payload["listening"] is True
        assert payload["serves_this_vault"] is True
        assert payload["endpoint"].startswith("http://127.0.0.1:")

    def test_it_reports_when_nothing_is_listening(self, run: Run, nothing_listening: None) -> None:
        payload = run("service", "status", as_json=True).json
        assert payload["listening"] is False
        assert payload["serves_this_vault"] is False

    def test_it_answers_even_where_there_is_no_service_manager(self, run: Run) -> None:
        # core/05 section 20: reporting always answers. The unit may be
        # missing, systemd may be absent, and `status` still says something.
        assert run("service", "status").code == EXIT_OK

    def test_it_never_prints_the_credential(self, run: Run, serving: LocalService) -> None:
        result = run("service", "status")
        assert serving.runtime.credential not in result.out
        assert serving.runtime.credential not in result.err


class TestServe:
    def test_it_refuses_a_directory_that_is_not_a_vault(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No system manifest means no vault identity, so derived state has
        # nowhere to live (core/05 sections 6 and 18).
        empty = tmp_path / "empty"
        empty.mkdir()
        code = main(["--vault", str(empty), "--json", "serve"])
        assert code == EXIT_FAILED
        error = json.loads(capsys.readouterr().err)["error"]
        assert error["code"] == "service_not_started"
        assert "init" in error["message"]

    def test_the_port_can_be_overridden_on_the_command_line(self, vault: Path) -> None:
        # Parsed, not run: starting a server inside a unit test would block.
        from never4ga.cli import _build_parser

        arguments = _build_parser().parse_args(["--vault", str(vault), "serve", "--port", "9999"])
        assert arguments.port == 9999


class TestAStartingServiceStillOwnsTheIndex:
    """A service that is starting still owns the index.

    `core/05` section 18 reconciles before uvicorn accepts requests, so for the
    first minutes after a restart the endpoint may be listening and answer
    nothing. That silence must not read as permission: a `--local` write would
    land on a database the starting service already holds.

    The moment a service is least able to say it owns the index is the moment
    it most certainly does.
    """

    @staticmethod
    @contextmanager
    def _listening_but_silent() -> Iterator[int]:
        """A socket that completes a connection and never answers.

        What a service looks like between binding its port and finishing the
        startup reconcile: the connection succeeds, the request does not.
        """
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        try:
            yield int(sock.getsockname()[1])
        finally:
            sock.close()

    def test_presence_separates_silence_from_absence(self) -> None:
        from never4ga.service_client import ServicePresence, presence

        with self._listening_but_silent() as port:
            state, health = presence(f"http://127.0.0.1:{port}", timeout=0.3)
            assert state is ServicePresence.UNREACHABLE
            assert health is None

        # Nothing bound at all is the other answer, and must stay distinct or
        # every machine without a service would refuse to index.
        absent, _ = presence("http://127.0.0.1:1", timeout=0.3)
        assert absent is ServicePresence.ABSENT

    def test_a_local_index_is_refused_while_the_service_starts(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with self._listening_but_silent() as port:
            endpoint = ServiceEndpoint(host="127.0.0.1", port=port)
            monkeypatch.setattr(
                LocalConfig,
                "load",
                classmethod(lambda cls, path=None: LocalConfig(service=endpoint)),
            )
            result = run("--local", "index", as_json=True)

        assert result.code == EXIT_FAILED
        assert result.error["code"] == "service_owns_the_index"
        assert "has not answered" in result.error["message"]

    def test_nothing_listening_still_permits_a_local_index(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure this must not cause.

        Refusing whenever the probe comes back empty would stop every machine
        that never runs the service from indexing at all.
        """
        endpoint = ServiceEndpoint(host="127.0.0.1", port=1)
        monkeypatch.setattr(
            LocalConfig,
            "load",
            classmethod(lambda cls, path=None: LocalConfig(service=endpoint)),
        )
        assert run("--local", "index").code == EXIT_OK

    def test_a_running_unit_that_is_not_listening_yet_is_refused(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe alone cannot see a service that is starting.

        `core/05` section 18 reconciles *before* uvicorn binds the port, so for
        the minutes that takes the connection is refused and the probe reports
        ABSENT. Permitting a write then would contend with the index the
        starting service already holds, and fail as "database is locked".

        The unit's own state is the signal the probe cannot carry. A running
        unit with nothing listening is a service on its way up.
        """
        endpoint = ServiceEndpoint(host="127.0.0.1", port=1)
        monkeypatch.setattr(
            LocalConfig,
            "load",
            classmethod(lambda cls, path=None: LocalConfig(service=endpoint, vault=vault)),
        )
        monkeypatch.setattr(
            never4ga.cli,
            "_service_manager",
            lambda: _ManagerReporting(ServiceState.RUNNING),
        )

        result = run("--local", "index", as_json=True)

        assert result.code == EXIT_FAILED
        assert result.error["code"] == "service_owns_the_index"
        assert "starting" in result.error["message"]

    def test_a_stopped_unit_still_permits_a_local_index(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other half, and the one that matters more: a machine that never
        # runs the service must keep indexing.
        endpoint = ServiceEndpoint(host="127.0.0.1", port=1)
        monkeypatch.setattr(
            LocalConfig,
            "load",
            classmethod(lambda cls, path=None: LocalConfig(service=endpoint, vault=vault)),
        )
        monkeypatch.setattr(
            never4ga.cli,
            "_service_manager",
            lambda: _ManagerReporting(ServiceState.STOPPED),
        )

        assert run("--local", "index").code == EXIT_OK

    def test_a_machine_with_no_service_manager_still_permits_one(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # UNSUPPORTED is most machines. Reading it as "maybe starting" would
        # refuse every local index on any platform without systemd.
        endpoint = ServiceEndpoint(host="127.0.0.1", port=1)
        monkeypatch.setattr(
            LocalConfig,
            "load",
            classmethod(lambda cls, path=None: LocalConfig(service=endpoint, vault=vault)),
        )
        monkeypatch.setattr(
            never4ga.cli,
            "_service_manager",
            lambda: _ManagerReporting(ServiceState.UNSUPPORTED),
        )

        assert run("--local", "index").code == EXIT_OK

    def test_a_unit_running_for_another_vault_does_not_refuse_this_one(
        self, run: Run, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A running unit refuses only a local index of its own vault.

        A service that has not bound its port cannot report a `vault_id`, so
        the machine's configured vault stands in for it. Refusing on unit state
        alone would block a local index of every other vault on the machine.
        """
        endpoint = ServiceEndpoint(host="127.0.0.1", port=1)
        monkeypatch.setattr(
            LocalConfig,
            "load",
            classmethod(
                lambda cls, path=None: LocalConfig(
                    service=endpoint, vault=vault.parent / "some-other-vault"
                )
            ),
        )
        monkeypatch.setattr(
            never4ga.cli,
            "_service_manager",
            lambda: _ManagerReporting(ServiceState.RUNNING),
        )

        assert run("--local", "index").code == EXIT_OK


class _ManagerReporting:
    """A service manager that reports one state and controls nothing."""

    def __init__(self, state: ServiceState) -> None:
        self._state = state

    def can_control(self) -> bool:
        return self._state is not ServiceState.UNSUPPORTED

    def status(self) -> ServiceStatus:
        return ServiceStatus(state=self._state)

    def start(self) -> ServiceStatus:
        return self.status()

    def stop(self) -> ServiceStatus:
        return self.status()

    def restart(self) -> ServiceStatus:
        return self.status()


class TestStartupThroughTheServiceKeepsTheActor:
    """`--actor` must survive the trip through a running service.

    If the forwarded request drops the actor, the session records
    `human:owner`, and a `wrap` against it writes a log whose `generated.by`
    claims a person wrote it. The in-process path cannot show this, so these
    tests go through a real service.
    """

    @pytest.fixture
    def repository(self, tmp_path: Path) -> Path:
        root = tmp_path / "Projects" / "client-repo"
        (root / ".git").mkdir(parents=True)
        return root

    def _startup_actor(self, run: Run, repository: Path, *flags: str) -> str:
        from never4ga.composition import FileSessionStore
        from never4ga.domain.identity import ConceptId, SessionId

        created = run("workspace", "create", "Client Workspace", "--type", "product", as_json=True)
        run("workspace", "map", str(created.json["id"]), "--repo", str(repository))
        assert run("index", as_json=True).code == EXIT_OK
        payload = run(*flags, "context", "startup", "--path", str(repository), as_json=True).json
        vault_id = ConceptId.parse(run("status", as_json=True).json["vault_id"])
        store = FileSessionStore(PlatformPaths.resolve().sessions_database(vault_id))
        record = store.get(SessionId.parse(str(payload["session_id"])))
        assert record is not None
        return record.actor

    def test_the_session_records_the_actor_the_cli_was_given(
        self, run: Run, serving: LocalService, repository: Path
    ) -> None:
        actor = self._startup_actor(run, repository, "--actor", "codex/gpt-5")
        assert actor == "codex/gpt-5"

    def test_the_mcp_toolbox_forwards_its_actor_too(
        self, run: Run, serving: LocalService, repository: Path, vault: Path
    ) -> None:
        # The third composition root builds the same request, so it must
        # carry the actor too (see test_composition_parity.py).
        from never4ga.composition import FileSessionStore
        from never4ga.domain.identity import ConceptId, SessionId
        from never4ga.mcp.toolbox import Toolbox

        created = run("workspace", "create", "Client Workspace", "--type", "product", as_json=True)
        run("workspace", "map", str(created.json["id"]), "--repo", str(repository))
        assert run("index", as_json=True).code == EXIT_OK
        payload = Toolbox(vault, actor="codex/gpt-5").context_startup({"cwd": str(repository)})
        vault_id = ConceptId.parse(run("status", as_json=True).json["vault_id"])
        store = FileSessionStore(PlatformPaths.resolve().sessions_database(vault_id))
        record = store.get(SessionId.parse(str(payload["session_id"])))
        assert record is not None
        assert record.actor == "codex/gpt-5"


class TestATimeoutIsNotAFailedReconcile:
    """The client gave up; the work did not.

    The service reconciles synchronously inside the request, so a client that
    stops waiting learns nothing about whether the reconcile finished.
    Reporting failure would be a confident wrong answer. No timeout is long
    enough for every vault, so the CLI reads the index back before reporting,
    the same read-back discipline the OpenProject write path applies.
    """

    @pytest.fixture
    def serving_still(self, vault: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalService]:
        """A service whose watcher will not reconcile during the test.

        The staleness these tests need is real but transient: a running
        watcher would index the new file within its quiet period and the
        assertion would pass or fail on timing. A long quiet period makes the
        state hold still without pretending the watcher is absent.
        """
        service = LocalService(
            ServiceSettings(
                vault=vault,
                endpoint=ServiceEndpoint(port=0),
                quiet_period=3600.0,
                reconcile_interval=3600.0,
            ),
            paths=PlatformPaths.resolve(),
        )
        service.start()
        # Spend the deferred full pass now, so it cannot reconcile
        # underneath the test later.
        service.runtime.tick()
        host, port = service.bound
        endpoint = ServiceEndpoint(host=host, port=port)
        monkeypatch.setattr(
            LocalConfig, "load", classmethod(lambda cls, path=None: LocalConfig(service=endpoint))
        )
        try:
            yield service
        finally:
            service.stop()

    @staticmethod
    def _write_unindexed(vault: Path, suffix: str) -> None:
        notes = vault / "30_Knowledge" / "Notes"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / f"late-{suffix}.md").write_text(
            f"---\ntype: knowledge\nid: 01a05d0b-0000-7000-8000-00000000000{suffix}\n"
            'schema: never4ga/0.1\ntitle: "Late"\ncreated_at: "2026-09-01T12:00:00Z"\n---\n'
            "# Late\nnot indexed yet\n",
            encoding="utf-8",
        )

    @pytest.fixture
    def timing_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from never4ga.service_client import ServiceClient, ServiceTimeoutError

        def slow(self: ServiceClient, *, changed_only: bool) -> dict[str, Any]:
            raise ServiceTimeoutError(f"the service at {self.base_url} did not answer: timed out")

        monkeypatch.setattr(ServiceClient, "reconcile", slow)

    def test_a_current_index_is_reported_as_done(
        self, run: Run, serving: LocalService, timing_out: None
    ) -> None:
        # The service reconciled at startup, so the index is current: the
        # honest answer is that the work is done, whoever heard about it.
        result = run("index", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["answered"] is False
        assert result.json["stale"] is False
        assert result.json["indexed_documents"] >= 2

    def test_it_never_invents_the_counts_it_did_not_receive(
        self, run: Run, serving: LocalService, timing_out: None
    ) -> None:
        # `indexed`/`unchanged`/`removed` belong to a reply that never
        # arrived. Reporting zeroes for them would be a fabricated run.
        payload = run("index", as_json=True).json
        assert "indexed" not in payload
        assert "unchanged" not in payload

    def test_a_stale_index_still_fails(
        self, run: Run, serving_still: LocalService, timing_out: None, vault: Path
    ) -> None:
        # Work outstanding and no answer: this one really is unresolved, and
        # saying so is the point of reading back rather than assuming either way.
        self._write_unindexed(vault, "1")
        result = run("index", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.error["code"] == "reconcile_unconfirmed"
        assert result.error["details"]["stale"] is True
        assert result.error["retryable"] is True

    def test_the_hint_does_not_send_the_user_to_become_a_second_writer(
        self, run: Run, serving_still: LocalService, timing_out: None, vault: Path
    ) -> None:
        # A hint to pass --local would, on a timeout, point at the one thing
        # that must not happen while a service owns the index.
        self._write_unindexed(vault, "2")
        result = run("index", as_json=True)
        assert "--local" not in result.error["repair_hint"]
        assert "index --status" in result.error["repair_hint"]


class TestRepairDoesNotFightTheService:
    """`repair --apply` asks the service to reindex instead of contending with it.

    Repair is the one thing that may change the vault, so it runs in process,
    and its last action reindexes. While a service holds the index, an
    in-process reconcile fails with "database is locked". The reindex goes to
    the service, and a failure is reported as a structured result, never a
    traceback.

    A small idle vault never reproduces the contention, so these tests force
    it: the in-process indexer is made to fail the way SQLite fails. The vault
    half still happens here. Only the reindex moves, the same rule `index`
    follows.
    """

    @pytest.fixture
    def finding(self, run: Run, vault: Path) -> None:
        """Record an `index_is_stale` finding, which is what plans a REINDEX.

        Order matters: index, then write a document, then diagnose. That
        leaves the index genuinely behind the vault, which is the finding
        whose repair action is the one this class is about.
        """
        run("index")
        (vault / "30_Knowledge" / "Notes").mkdir(parents=True, exist_ok=True)
        (vault / "30_Knowledge" / "Notes" / "orphan.md").write_text(
            "---\ntype: knowledge\nid: 01a05fb0-0000-7000-8000-000000000001\n"
            'schema: never4ga/0.1\ntitle: "Orphan"\ncreated_at: "2026-09-01T12:00:00Z"\n---\n'
            "# Orphan\nbody\n",
            encoding="utf-8",
        )
        run("doctor")

    @pytest.fixture
    def index_is_locked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The in-process indexer fails exactly as SQLite does under contention."""
        import sqlite3

        from never4ga.services import IndexService

        def locked(self: IndexService, **_: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(IndexService, "reconcile", locked)

    def test_the_reindex_goes_to_the_service_that_owns_the_index(
        self,
        run: Run,
        serving: LocalService,
        vault: Path,
        finding: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proved by the answer, because the failure cannot be simulated here.

        Making the in-process indexer raise does not distinguish the two
        paths: the test's service runs in this same process, so a patch on
        `IndexService` breaks the service's reconcile too. What does
        distinguish them is where the numbers come from, so the client is
        given numbers nothing in process could produce.
        """
        from never4ga.service_client import ServiceClient

        asked: list[bool] = []

        def answered(self: ServiceClient, *, changed_only: bool) -> dict[str, Any]:
            asked.append(changed_only)
            return {"indexed": 4242, "removed": 7}

        monkeypatch.setattr(ServiceClient, "reconcile", answered)
        result = run("repair", "--apply", as_json=True)
        assert result.code == EXIT_OK
        assert asked == [False], "the repair reindex must be a full pass, asked of the service"
        assert any("4242" in str(step) for step in result.json["performed"])

    def test_no_traceback_reaches_the_user(
        self, run: Run, serving: LocalService, vault: Path, finding: None, index_is_locked: None
    ) -> None:
        result = run("repair", "--apply", as_json=True)
        assert "Traceback" not in result.err
        assert "OperationalError" not in result.err

    def test_the_vault_half_still_happens_here(
        self, run: Run, serving: LocalService, vault: Path, finding: None
    ) -> None:
        # Repair is the one thing that changes the vault; only the derived
        # half is the service's to do.
        payload = run("repair", "--apply", as_json=True).json
        assert payload["applied"] is True
        assert any("regenerated" in str(step) for step in payload["performed"])

    def test_a_lock_with_no_service_is_reported_not_raised(
        self, run: Run, nothing_listening: None, vault: Path, finding: None, index_is_locked: None
    ) -> None:
        """Nothing to delegate to, and the index is still unavailable.

        The repair happened and the reindex did not. Saying so beats both a
        traceback and silence.
        """
        result = run("repair", "--apply", as_json=True)
        assert "Traceback" not in result.err
        assert result.json["applied"] is True
        assert any("could not" in str(step).lower() for step in result.json["performed"])

    def test_with_no_service_a_healthy_index_is_still_rebuilt_here(
        self, run: Run, nothing_listening: None, vault: Path, finding: None
    ) -> None:
        performed = run("repair", "--apply", as_json=True).json["performed"]
        assert any("indexed" in str(step) for step in performed), performed

    def test_the_plan_really_does_ask_for_a_reindex(
        self, run: Run, nothing_listening: None, vault: Path, finding: None
    ) -> None:
        # Guards the fixture, not the product: without an `index_is_stale`
        # finding, every assertion above would pass without reaching the code
        # it is about.
        actions = [a["action"] for a in run("repair", as_json=True).json["actions"]]
        assert "reindex" in actions, actions
