"""Milestone boundary: what must exist and what must not exist yet.

``AGENTS.md`` ("Current task boundary") points here. These tests fail if a
deferred implementation appears early, and if a required seam disappears. The
boundary moves as work lands: an entry comes off a list when the thing it
guards is built.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
import tomllib
import typing
from pathlib import Path
from typing import Any

import pytest

import never4ga.ports
from never4ga.domain.identity import ConceptId

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "never4ga"
PYPROJECT = ROOT / "pyproject.toml"

#: Every port module, and the Protocol each one is named for.
#:
#: core/05 section 20's rule is that each port arrives with the milestone that
#: first requires it. `test_every_port_module_is_covered` makes the table's
#: completeness the assertion rather than its length, so a new port cannot
#: arrive without being named here.
#:
#: A module with more than one Protocol names its principal one. The optional
#: companions -- `IntegrityReportingStore` beside `DocumentStore`, say -- are a
#: capability a backend may also offer, not a port of their own.
REQUIRED_PORTS = {
    "never4ga.ports.document_store": "DocumentStore",
    "never4ga.ports.metadata_index": "MetadataIndex",
    "never4ga.ports.text_index": "TextIndex",
    "never4ga.ports.vector_index": "VectorIndex",
    "never4ga.ports.graph_index": "GraphIndex",
    "never4ga.ports.embedding_provider": "EmbeddingProvider",
    "never4ga.ports.work_management": "WorkManagementProvider",
    # Registering the MCP server runs each client's own `mcp add` verb, which
    # means running a process, so a service asks a port and an adapter owns
    # the subprocess.
    "never4ga.ports.client_registry": "ClientRegistry",
    "never4ga.ports.secret_store": "SecretStore",
    "never4ga.ports.agent_adapter": "AgentAdapter",
    "never4ga.ports.context_signal_provider": "ContextSignalProvider",
    "never4ga.ports.memory_augmentor": "MemoryAugmentor",
    "never4ga.ports.temporal_graph": "TemporalGraphProvider",
    "never4ga.ports.extension_registry": "ExtensionRegistry",
    # Vault content that is not a canonical concept: reserved OKF navigation,
    # templates, Obsidian views, and the directories themselves.
    "never4ga.ports.vault_files": "VaultFileStore",
    # What the index knows about its own currency, and where links
    # point. Not a way of finding things, which is why it is its own port.
    "never4ga.ports.index_state": "IndexState",
    # The background service's lifecycle. core/05 section 20 lists it as a
    # port.
    "never4ga.ports.service_manager": "ServiceManager",
    # Resolving a working directory to a workspace needs to know
    # which repository this is, and which workspace that repository was mapped
    # to. The mapping is machine-local and nothing in the vault can rebuild it.
    "never4ga.ports.repository_locator": "RepositoryLocator",
    "never4ga.ports.workspace_mappings": "WorkspaceMappingStore",
    # An adapter is a descriptor, not a class. One sync engine
    # writes client files through a port and records what it deployed, so that
    # what Never4gA owns stays distinguishable from what a user wrote.
    "never4ga.ports.client_files": "ClientFileStore",
    "never4ga.ports.deployments": "DeploymentStore",
    # What a tracker last said, derived and disposable, in its own database
    # beside the index.
    "never4ga.ports.tracker_cache": "TrackerCache",
    # Working session state and its checkpoints (core/08 section 1), in its
    # own database beside the index. Unlike every other
    # store here it is durable rather than derived -- nothing in the vault ever
    # held a checkpoint, so `rebuild` must leave it alone.
    "never4ga.ports.session_store": "SessionStore",
    # A finding's history
    # (`details/data-indexing-maintenance.md` section 20). Derived, and in the
    # index's own database rather than beside it -- unlike the tracker cache
    # above, a finding *is* re-derivable from Markdown by running detection
    # again, so `rebuild` discarding it is correct rather than a data loss.
    "never4ga.ports.maintenance_findings": "MaintenanceFindings",
}


class TestRequiredPortsExist:
    @pytest.mark.parametrize(("module_name", "port_name"), sorted(REQUIRED_PORTS.items()))
    def test_port_imports(self, module_name: str, port_name: str) -> None:
        module = importlib.import_module(module_name)
        assert hasattr(module, port_name)

    @pytest.mark.parametrize(("module_name", "port_name"), sorted(REQUIRED_PORTS.items()))
    def test_port_is_a_protocol(self, module_name: str, port_name: str) -> None:
        port = getattr(importlib.import_module(module_name), port_name)
        assert issubclass(port, typing.Protocol)  # type: ignore[arg-type]

    def test_every_port_module_is_covered(self) -> None:
        """The table names every port module.

        Asserting a *count* would let a port arrive without being named here,
        and a table nobody has to update stops describing anything.
        """
        modules = {
            f"never4ga.ports.{found.name}"
            for found in pkgutil.iter_modules(never4ga.ports.__path__)
        }
        assert set(REQUIRED_PORTS) == modules


class TestDeferredSystemsAreAbsent:
    #: Systems deferred for now. The seams exist; the implementations must not.
    #:
    #: OpenProject reads and writes are built; writes are gated by core/03
    #: section 22's policy rather than by this list. `webhook` stays:
    #: details/openproject-adapter.md section 10 makes polling the default and
    #: section 11 puts webhooks later, behind a separately exposed surface,
    #: while the local API stays loopback-only.
    DEFERRED = (
        "qdrant",
        "neo4j",
        "postgres",
        "pgvector",
        "lancedb",
        "mem0",
        "zep",
        "graphiti",
        "marketplace",
        "webhook",
        # The vector lane is retired. Measured on the evaluation corpus it
        # gave no Recall@10 change, an MRR gain below the corpus's own
        # resolution, 9x the median latency, and needed a 9.7 GB model.
        # `ollama` is here as the implementation that was removed, not as a
        # judgement about the provider: `core/00` #27 bans *requiring* a paid
        # API, and nothing bans a local one.
        "ollama",
    )

    #: Ports kept as seams, with every implementation removed. `core/07` §7
    #: still defines optional enrichment interfaces, and the layering rules
    #: still govern where an inference SDK may be imported, so the seams stay.
    UNIMPLEMENTED_PORTS = ("embedding_provider.py", "vector_index.py")

    @pytest.mark.parametrize("port", UNIMPLEMENTED_PORTS)
    def test_a_retired_port_keeps_its_seam(self, port: str) -> None:
        assert (SRC / "ports" / port).exists()

    @pytest.mark.parametrize("port", UNIMPLEMENTED_PORTS)
    def test_nothing_implements_a_retired_port(self, port: str) -> None:
        """The seam exists; an implementation of it does not.

        An optional vector extra has to be wired by every composition root, and
        the roots drift apart on it. If the lane returns it should return
        through a decision, not through a file added because the Protocol was
        sitting there.
        """
        module = f"never4ga.ports.{port.removesuffix('.py')}"
        importers = [
            str(path.relative_to(SRC))
            for path in sorted(SRC.rglob("*.py"))
            if path.parent.name != "ports"
            and any(
                imported == module or imported.startswith(f"{module}.")
                for imported in _imported_modules(ast.parse(path.read_text()))
            )
        ]
        assert importers == []

    @pytest.mark.parametrize("system", DEFERRED)
    def test_no_module_implements_a_deferred_system(self, system: str) -> None:
        matches = [
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if system in path.name.casefold()
        ]
        assert matches == []

    def test_the_mcp_package_carries_its_own_protocol(self) -> None:
        # The MCP package exists and implements the protocol itself rather
        # than through an SDK.
        assert (SRC / "mcp" / "jsonrpc.py").exists()

    def test_nothing_imports_an_mcp_sdk(self) -> None:
        # The MCP SDK is declined, not deferred: seventeen required
        # dependencies against six, a second HTTP client, and four of them
        # serving a socket stdio never opens. This enforces that choice.
        violations = [
            f"{path.name} -> {imported}"
            for path in sorted(SRC.rglob("*.py"))
            for imported in _imported_modules(ast.parse(path.read_text()))
            if imported.split(".")[0] == "mcp"
        ]
        assert violations == []

    def test_the_api_exposes_no_later_milestones_capability_group(self) -> None:
        # details/api-cli-mcp-contract.md section 3 lists nine capability
        # groups. The ones not built yet stay away: an endpoint that returns a
        # stub is worse than none, because it invites a client to depend on it.
        #
        # `capture` and `adapters` are CLI verbs and are absent here
        # deliberately. Deploying Skills into a user's home directory across
        # every installed client is not something a loopback endpoint should be
        # able to trigger, and `core/04` section 7 names a future "sync from the
        # service" as needing a decision rather than a drift into existence.
        from never4ga.api import API_PREFIX

        deferred = ("sessions", "capture", "decisions", "adapters")
        routes = _api_route_paths()
        assert routes, "the API should expose its capability groups"
        violations = [
            path for path in routes for group in deferred if _in_group(path, API_PREFIX, group)
        ]
        assert violations == []

    def test_work_writes_are_exposed_and_project_creation_is_not(self) -> None:
        # The three work item verbs have HTTP twins. `create_project` does
        # not: `/v1/workspaces` reads and resolves and does not create, so
        # there is no lifecycle surface for it to belong to, and an endpoint
        # would mean inventing a group to hold one verb.
        from never4ga.api import API_PREFIX

        routes = _api_route_paths()
        assert f"{API_PREFIX}/work/update" in routes
        assert f"{API_PREFIX}/work/create" in routes
        assert f"{API_PREFIX}/work/comment" in routes
        assert not [path for path in routes if "project" in path]
        # And the workspace surface still creates nothing, which is the reason.
        assert not [
            path
            for path in routes
            if path.startswith(f"{API_PREFIX}/workspaces") and "create" in path
        ]

    def test_a_group_name_that_prefixes_another_does_not_match_it(self) -> None:
        # "work" is a prefix of "workspaces". Matching on raw string prefixes
        # would forbid a group it is not meant to, and would let a real
        # /v1/work endpoint through under a different name.
        assert _in_group("/v1/work/items", "/v1", "work")
        assert _in_group("/v1/work", "/v1", "work")
        assert not _in_group("/v1/workspaces", "/v1", "work")
        # Worth asserting with `/v1/work` real: the two groups differ by one
        # character and one of them is still deferred.
        assert _in_group("/v1/adapters/sync", "/v1", "adapters")

    def test_all_three_context_depths_are_exposed(self) -> None:
        # Progressive context is complete only with all three depths; two of
        # three modes is not progressive.
        from never4ga.api import API_PREFIX

        routes = _api_route_paths()
        for depth in ("startup", "focus", "deep"):
            assert f"{API_PREFIX}/context/{depth}" in routes

    def test_the_cli_speaks_one_transport_and_serves_none(self) -> None:
        # The CLI switches to a service client when a service is available. It
        # stays narrow: exactly one HTTP client library, `httpx`, and nothing
        # that would let the CLI serve. `never4ga serve` launches
        # `never4ga.service`; it does not become the server.
        forbidden = {
            "requests",
            "aiohttp",
            "urllib3",
            "http",
            "socket",
            "socketserver",
            "uvicorn",
            "fastapi",
            "starlette",
            "hypercorn",
            "wsgiref",
        }
        roots = {
            module.split(".")[0]
            for path in sorted((SRC / "cli").rglob("*.py"))
            for module in _imported_modules(ast.parse(path.read_text()))
        }
        assert not (roots & forbidden), sorted(roots & forbidden)
        # The CLI reaches a running service through `never4ga.service_client`,
        # which lives outside this package so MCP can make the same choice
        # without importing another root. What must hold is that
        # something in this repository can talk to the service and that it is
        # a client rather than a server.
        client = (SRC / "service_client.py").read_text()
        assert "httpx" in _imported_modules(ast.parse(client))

    def test_the_console_scripts_are_the_two_interfaces_that_are_processes(self) -> None:
        # `never4ga-mcp` is the MCP server. The API is not here because it is
        # not a process a person starts: the daemon is, and the daemon is
        # `never4ga serve`.
        config = tomllib.loads(PYPROJECT.read_text())
        assert config["project"]["scripts"] == {
            "never4ga": "never4ga.cli:main",
            "never4ga-mcp": "never4ga.mcp.__main__:main",
        }

    def test_no_work_capability_is_claimed_that_no_code_can_exercise(self) -> None:
        # details/openproject-adapter.md section 12 makes capability a function
        # of the instance, its modules and the token's permissions, so a
        # capability nothing can discover or act on is a claim rather than a
        # capability. Every member must be reachable by the one adapter that
        # reports them.
        from never4ga.domain.capabilities import WorkManagementCapability

        adapter = (SRC / "adapters" / "openproject" / "provider.py").read_text()
        unreferenced = [
            capability.name
            for capability in WorkManagementCapability
            if capability.name not in adapter
        ]
        assert unreferenced == []

    def test_work_capabilities_beyond_the_milestone_stay_absent(self) -> None:
        # Every work package advertises `delete`, `logTime`, `addWatcher` and
        # `addAttachment`, and the adapter must not turn an available link into
        # a capability merely because it is there. Webhooks are later still:
        # details/openproject-adapter.md section 11 puts them behind a
        # separately exposed surface while the local API stays loopback-only.
        from never4ga.domain.capabilities import WorkManagementCapability

        assert not any(
            token in capability.value
            for capability in WorkManagementCapability
            for token in ("delete", "webhook", "time", "watch", "attach")
        )

    def test_no_llm_enrichment_is_implemented(self) -> None:
        # core/07 section 7 names QueryEnricher, CandidateReranker and friends.
        # They are optional interfaces for later work, and none is implemented.
        names = {path.stem for path in SRC.rglob("*.py")}
        assert not names & {"enricher", "reranker", "summarizer", "llm"}

    def test_no_module_defines_a_model_prompt(self) -> None:
        # Mechanical-first, checked in the source: a prompt constant anywhere
        # would mean the core had opinions about a model.
        #
        # Enum bodies are skipped: capability vocabularies legitimately contain
        # names like ``prompt_context`` (core/08 section 17), which describe what
        # a provider offers rather than anything Never4gA sends to a model.
        violations = [
            f"{path.name}:{name}"
            for path in sorted(SRC.rglob("*.py"))
            for name in _assigned_names_outside_enums(ast.parse(path.read_text()))
            if any(
                token in name.casefold() for token in ("prompt", "system_message", "instructions")
            )
        ]
        assert violations == []


def _in_group(path: str, prefix: str, group: str) -> bool:
    """Whether a route belongs to a capability group, by path segment.

    Segment-wise rather than by string prefix: capability group names are not
    prefix-free, and `/v1/workspaces` is not part of `work`.
    """
    base = f"{prefix}/{group}"
    return path == base or path.startswith(f"{base}/")


def _api_route_paths() -> set[str]:
    """Every path the app actually serves, read from the app itself."""
    from never4ga.api import create_app

    def sessions() -> Any:  # pragma: no cover - never entered; no request is made
        raise AssertionError("the boundary test inspects routes, it does not call them")

    app = create_app(
        sessions=sessions,
        credential="unused",
        vault_id=ConceptId.new(),
    )
    paths = {str(getattr(route, "path", "")) for route in app.routes}
    return {path for path in paths if path.startswith("/v1")}


def _imported_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _is_enum(node: ast.ClassDef) -> bool:
    return any("enum" in ast.unparse(base).casefold() for base in node.bases)


def _assigned_names_outside_enums(tree: ast.Module) -> list[str]:
    """Every assigned name in the module, ignoring enum member definitions."""
    inside_enum: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_enum(node):
            inside_enum.update(id(child) for child in ast.walk(node))

    names: list[str] = []
    for node in ast.walk(tree):
        if id(node) in inside_enum:
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names.extend(target.id for target in targets if isinstance(target, ast.Name))
    return names


class TestNativeContextNeedsNoExternalMemory:
    """Native context needs no external memory provider to work."""

    def test_the_startup_assembler_takes_no_memory_provider(self) -> None:
        from never4ga.context.assembler import StartupContextAssembler

        parameters = set(inspect.signature(StartupContextAssembler.__init__).parameters)
        assert not any(
            token in parameter
            for parameter in parameters
            for token in ("memory", "augmentor", "temporal")
        )

    def test_the_null_providers_are_complete_implementations(self) -> None:
        from never4ga.adapters.null import (
            NullExternalMemoryProvider,
            NullTemporalGraphProvider,
        )
        from never4ga.ports.memory_augmentor import MemoryAugmentor
        from never4ga.ports.temporal_graph import TemporalGraphProvider

        assert isinstance(NullExternalMemoryProvider(), MemoryAugmentor)
        assert isinstance(NullTemporalGraphProvider(), TemporalGraphProvider)

    def test_removing_an_augmentor_does_not_invalidate_domain_models(self) -> None:
        from never4ga.domain.identity import ConceptId
        from never4ga.domain.memory import MemoryCandidate

        promoted = ConceptId.new()
        del MemoryCandidate  # the provider integration goes away entirely
        assert ConceptId.parse(str(promoted)) == promoted


class TestBootstrapSkillsArePresent:
    """AGENTS.md mandatory startup reading."""

    @pytest.mark.parametrize("skill", ["architecture-review", "spec-compliance", "tdd"])
    def test_skill_exists(self, skill: str) -> None:
        assert (ROOT / "bootstrap" / "skills" / skill / "SKILL.md").is_file()


class TestSkillsDescribeVerbsThatExist:
    """A canonical Skill must not name a verb the CLI does not have.

    `core/04` section 16 makes the CLI the universal bootstrap fallback, and
    every canonical Skill tells the agent to run one rather than describing
    what it does. A Skill that names a missing verb misleads the agent, and
    one that still calls its verb unbuilt goes stale silently once the verb
    ships.
    """

    def _invocations(self, body: str) -> set[str]:
        """Every `never4ga <verb>` a Skill tells an agent to run."""
        return {
            match.group(1)
            for match in re.finditer(r"never4ga (?:--[\w-]+ [^\s]+ )*([a-z][a-z-]*)", body)
        }

    def test_every_verb_a_skill_names_is_a_real_command(self) -> None:
        from never4ga.services.scaffold import SKILLS

        commands = _cli_commands()
        for skill, body in SKILLS.items():
            for verb in self._invocations(body):
                assert verb in commands, f"{skill} names `never4ga {verb}`, which does not exist"

    def test_no_skill_still_says_it_is_unbuilt(self) -> None:
        # Phrasing that marks a Skill as a stub. A Skill that says this while its
        # verb works is worse than one that says nothing: an agent believes it.
        from never4ga.services.scaffold import SKILLS

        for skill, body in SKILLS.items():
            lowered = body.casefold()
            assert "not built yet" not in lowered, skill
            assert "not yet implemented" not in lowered, skill
            assert "there is no `never4ga" not in lowered, skill


def _cli_commands() -> set[str]:
    """Every subcommand the CLI offers, read from its own usage line.

    Through the parser's public output rather than its internals: argparse's
    `_subparsers` is private, and a test that reaches into it breaks on a
    Python upgrade rather than on a real change.
    """
    from never4ga.cli import _build_parser

    usage = _build_parser().format_usage()
    listed = re.search(r"\{([a-z,-]+)\}", usage)
    assert listed is not None, f"no subcommand list in usage: {usage}"
    return set(listed.group(1).split(","))
