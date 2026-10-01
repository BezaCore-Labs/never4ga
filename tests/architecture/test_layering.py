"""Layering and dependency invariants.

core/05 section 5: "core concepts do not depend on provider APIs; provider-specific
code stays isolated; interfaces are replaceable; tests can use fake adapters."

These tests read the source, not the runtime, so a violation fails even if the
offending import is never executed.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

pytestmark = pytest.mark.architecture

SRC = Path(__file__).resolve().parents[2] / "src" / "never4ga"
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def module_name(path: Path) -> str:
    relative = path.relative_to(SRC.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_modules() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield module_name(path), ast.parse(path.read_text(), filename=str(path))


def imported_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


#: Which packages each layer may import from. Lower layers know nothing of higher
#: ones. This is a DAG, not a chain: ``services`` and ``adapters`` sit at the same
#: height and must not see each other. Services are written against ports so that
#: any adapter satisfies them; ``cli`` is the composition root that picks one.
_PURE = {
    "never4ga.errors",
    "never4ga.domain",
    "never4ga.layout",
    "never4ga.schema",
}
_CORE = _PURE | {"never4ga.ports", "never4ga.context"}

#: core/05 section 7's platform abstraction. A leaf: it computes where derived
#: state belongs and touches nothing. The layers that write derived state may
#: ask it; the pure layers and `context` have no business knowing the machine
#: has directories at all.
_PLATFORM = {"never4ga.platform_paths"}

#: core/05 section 9's machine-local configuration, a second leaf beside it.
#: Only the composition root reads it: a service that took its own settings
#: from a file would stop being a function of the arguments it was given, and
#: the CLI, the API and the daemon would each be able to configure it
#: differently.
#: A third leaf, and one for the same reason: client descriptors are adapter
#: data (core/04 section 9), read once by the composition root and handed to the
#: sync engine as arguments. An engine that loaded its own descriptors would be
#: an engine that knew which clients exist, which is the thing a descriptor
#: model exists to prevent.
#: A fourth leaf: which build of the package this process is running. It reads
#: the installed tree's own file stats and nothing else, and every layer above
#: may ask -- `api` answers it on `/v1/health`, `cli` compares it before
#: delegating to a service. It imports nothing of Never4gA's at all, which is
#: what lets both a composition root and an interface use it without either
#: seeing the other.
_MACHINE = _PLATFORM | {
    "never4ga.config",
    "never4ga.client_descriptors",
    "never4ga.build",
}

#: Domain and service values as the one payload shape every interface returns.
#: A leaf above `services`: it reads nothing, opens nothing and picks nothing,
#: and every root may see it. Keeping the shape here stops each interface from
#: growing its own copy.
_RENDERING = {"never4ga.rendering"}

#: The loopback client every interface uses to reach a running service. It
#: lives outside `cli/` because MCP makes the same route decision per tool call
#: and may not import another composition root, so the choice lives once rather
#: than twice.
_SERVICE_CLIENT = {"never4ga.service_client"}

ALLOWED_INTERNAL_IMPORTS = {
    # The bottom layer. It may see its own submodules and nothing else.
    "never4ga.errors": {"never4ga.errors"},
    "never4ga.domain": {"never4ga.errors", "never4ga.domain"},
    "never4ga.layout": {"never4ga.errors", "never4ga.domain", "never4ga.layout"},
    "never4ga.schema": _PURE,
    "never4ga.platform_paths": {"never4ga.errors", "never4ga.domain"},
    "never4ga.config": {"never4ga.errors", "never4ga.domain"} | _PLATFORM,
    "never4ga.client_descriptors": {"never4ga.errors", "never4ga.domain"},
    # Stdlib only. A process identifying itself must depend on nothing that
    # could fail to import.
    "never4ga.build": set(),
    "never4ga.ports": _PURE | {"never4ga.ports"},
    "never4ga.context": _CORE,
    # Pure functions of a document, expressed in port types. It knows nothing
    # about any backend, which is what keeps projections rebuildable.
    "never4ga.indexing": _PURE | {"never4ga.ports", "never4ga.indexing"},
    "never4ga.services": _CORE | _PLATFORM | {"never4ga.indexing", "never4ga.services"},
    "never4ga.adapters": _CORE | _PLATFORM | {"never4ga.adapters"},
    # An interface, exactly like `cli`: it renders services over HTTP and picks
    # no adapter of its own (core/05 section 15).
    "never4ga.rendering": _CORE | {"never4ga.services"},
    "never4ga.service_client": {"never4ga.errors"},
    "never4ga.api": _CORE | _MACHINE | _RENDERING | {"never4ga.services", "never4ga.api"},
    # The daemon: a composition root, like `cli`. It picks adapters, builds the
    # app, and owns the process (core/05 sections 10 and 18).
    "never4ga.service": (
        _CORE
        | _MACHINE
        | {
            "never4ga.services",
            "never4ga.adapters",
            "never4ga.api",
            "never4ga.service",
            "never4ga.composition",
        }
    ),
    # Wiring shared by the two composition roots (core/05 section 15). The one
    # module below them allowed to see `adapters` and `services` at once, so
    # that anything both roots must assemble identically is assembled once.
    # Nothing below it may import it -- a service that reached in here would be
    # a service that had picked its own adapter.
    # It may see `_SERVICE_CLIENT` because deciding whether to delegate to a
    # running service needs config, the document store, the secret store and
    # the build id at once, which is composition's own job. Written out per
    # root, those conditions drift apart. `service_client` imports nothing but
    # `errors`, so nothing below gains any reach from this.
    "never4ga.composition": (
        _CORE
        | _MACHINE
        | _SERVICE_CLIENT
        | {"never4ga.services", "never4ga.adapters", "never4ga.composition"}
    ),
    # An interface, like `api` and `cli`, and a composition root because it must
    # pick adapters to answer in process when the daemon is not running.
    # It may not see `api` or `cli`: a root that could build the app
    # would be a second server, and one root importing another is how two
    # assemblies drift (core/05 section 15).
    "never4ga.mcp": (
        _CORE
        | _MACHINE
        | _RENDERING
        | _SERVICE_CLIENT
        | {
            "never4ga.services",
            "never4ga.adapters",
            "never4ga.composition",
            "never4ga.mcp",
        }
    ),
    # The CLI is both an interface and a composition root, and it can also
    # launch the daemon, so it may see `service`.
    # It may not see `api`: a client that could build the app would be a
    # second server.
    "never4ga.cli": (
        _CORE
        | _MACHINE
        | _RENDERING
        | _SERVICE_CLIENT
        | {
            "never4ga.services",
            "never4ga.adapters",
            "never4ga.service",
            "never4ga.cli",
            "never4ga.composition",
        }
    ),
}


def layer_of(name: str) -> str | None:
    for layer in ALLOWED_INTERNAL_IMPORTS:
        if name == layer or name.startswith(f"{layer}."):
            return layer
    return None


class TestLayering:
    def test_every_module_belongs_to_a_declared_layer(self) -> None:
        unplaced = [
            name for name, _ in source_modules() if name != "never4ga" and layer_of(name) is None
        ]
        assert unplaced == []

    def test_no_module_imports_a_higher_layer(self) -> None:
        violations: list[str] = []
        for name, tree in source_modules():
            layer = layer_of(name)
            if layer is None:
                continue
            allowed = ALLOWED_INTERNAL_IMPORTS[layer]
            for imported in imported_modules(tree):
                imported_layer = layer_of(imported)
                if imported_layer is not None and imported_layer not in allowed:
                    violations.append(f"{name} -> {imported}")
        assert violations == []

    def test_domain_depends_on_nothing_but_itself_and_errors(self) -> None:
        # The domain model must be usable with no backend, transport or provider
        # anywhere in sight.
        for name, tree in source_modules():
            if layer_of(name) != "never4ga.domain":
                continue
            internal = {i for i in imported_modules(tree) if i.startswith("never4ga")}
            assert internal <= {"never4ga.errors"} | {
                i for i in internal if i.startswith("never4ga.domain")
            }, name

    def test_ports_never_import_adapters(self) -> None:
        for name, tree in source_modules():
            if layer_of(name) != "never4ga.ports":
                continue
            assert not any(i.startswith("never4ga.adapters") for i in imported_modules(tree)), name

    def test_services_never_import_adapters(self) -> None:
        # Application services depend on ports only (core/05 section 5). Choosing a
        # concrete adapter is a composition root's job.
        for name, tree in source_modules():
            if layer_of(name) != "never4ga.services":
                continue
            assert not any(i.startswith("never4ga.adapters") for i in imported_modules(tree)), name

    def test_the_cli_never_builds_the_app_itself(self) -> None:
        # It reaches the service over HTTP. A CLI that
        # imported `never4ga.api` could construct a second app with different
        # settings, and the two would answer differently.
        for name, tree in source_modules():
            if layer_of(name) != "never4ga.cli":
                continue
            assert not any(i.startswith("never4ga.api") for i in imported_modules(tree)), name

    def test_the_interfaces_never_import_adapters(self) -> None:
        # `cli` is the composition root and may; `api` is not and may not. An
        # API that reached for SQLite directly would be a second place where
        # backends are chosen, and the first place they diverge.
        for name, tree in source_modules():
            if layer_of(name) != "never4ga.api":
                continue
            assert not any(i.startswith("never4ga.adapters") for i in imported_modules(tree)), name

    def test_the_pure_layers_import_no_third_party_package(self) -> None:
        # Frontmatter crosses a port boundary as a plain Mapping, so the
        # domain, layout and schema rules must be usable with no YAML library
        # installed at all.
        stdlib = set(sys.stdlib_module_names)
        violations: list[str] = []
        for name, tree in source_modules():
            if layer_of(name) not in _PURE:
                continue
            for imported in imported_modules(tree):
                root = imported.split(".")[0]
                if root not in stdlib and not imported.startswith("never4ga"):
                    violations.append(f"{name} -> {imported}")
        assert violations == []


class TestDependencyDiscipline:
    """Where a dependency may live and when it may arrive."""

    #: Inference SDKs, hosted and local alike. One of these may only be
    #: imported by an adapter implementing an optional *capability* interface.
    #: A port qualifies when its absence degrades Never4gA rather than breaking
    #: it, no core layer imports it, and it is off the startup path. core/07
    #: section 7's enrichment interfaces and core/06 section 10's
    #: EmbeddingProvider are examples of such ports, not the complete list.
    #:
    #: This is not a ban on any provider. core/00 #27 forbids *requiring*
    #: per-token API use to reach capability the user already subscribes to, and
    #: forbids Never4gA acting as an inference gateway. It does not forbid a
    #: user configuring their own provider. The rule here is about where
    #: inference lives, not whether it exists.
    INFERENCE_SDKS = frozenset(
        {
            # hosted
            "openai",
            "anthropic",
            "cohere",
            "mistralai",
            "groq",
            "together",
            "google",  # google.generativeai / google-genai
            # local
            "ollama",
            "llama_cpp",
            "sentence_transformers",
            "transformers",
        }
    )

    #: Packages permitted to import an inference SDK. Empty until an optional
    #: capability adapter is built, so the rule bars every module. Reaching
    #: Ollama over plain `httpx` imports no SDK and needs no entry here.
    INFERENCE_ADAPTER_PACKAGES: ClassVar[tuple[str, ...]] = ()

    def test_no_module_outside_an_enrichment_adapter_imports_inference(self) -> None:
        violations = [
            f"{name} -> {imported}"
            for name, tree in source_modules()
            if not name.startswith(self.INFERENCE_ADAPTER_PACKAGES or ("\0",))
            for imported in imported_modules(tree)
            if imported.split(".")[0] in self.INFERENCE_SDKS
        ]
        assert violations == [], (
            "Inference belongs in an adapter "
            "behind an optional capability interface -- one whose absence "
            "degrades rather than breaks, that no core layer imports, and that "
            "is off the startup path. Never4gA must stay useful with every "
            "provider disabled."
        )

    def test_no_inference_sdk_is_a_dependency_yet(self) -> None:
        # Every provider is still on the schedule gate, so the next one to
        # arrive does so through a recorded decision rather than by drifting in.
        assert set(self.NOT_YET_REQUIRED) >= self.INFERENCE_SDKS

    #: Packages that are fine in principle but not needed yet.
    #:
    #: AGENTS.md "Dependencies": being on the core/05 section 4 vetted list is
    #: permission to consider a package, never to install it. Each value says
    #: when a package would plausibly be needed, not an approval. It still has
    #: to justify itself with a recorded decision.
    NOT_YET_REQUIRED: ClassVar[dict[str, str]] = {
        # platform_paths.py resolves XDG in stdlib, and core/05 section 11
        # makes macOS and Windows later extensions.
        "platformdirs": "when a second platform is real",
        # Declined rather than deferred. The SDK needs seventeen required
        # dependencies against this project's six, including a second HTTP
        # client beside `httpx`, and four serving a socket stdio never opens.
        # `never4ga/mcp/jsonrpc.py` implements the protocol instead. The entry
        # enforces that choice rather than a schedule.
        "mcp": "declined: the protocol is implemented in stdlib",
        "sentence_transformers": "a local embedding lane, if retrieval ever needs one",
        "transformers": "a local embedding lane, if retrieval ever needs one",
        "qdrant_client": "a vector store, if retrieval ever needs one",
        "lancedb": "a vector store, if retrieval ever needs one",
        "sqlite_vec": "a vector store, if retrieval ever needs one",
        "ollama": "whenever a local enricher is built (core/07 section 7)",
        "neo4j": "post-v0.1",
        "psycopg": "post-v0.1",
        "psycopg2": "post-v0.1",
        "asyncpg": "post-v0.1",
        "mem0": "an external memory provider (core/08)",
        "zep_python": "an external memory provider (core/08)",
        "zep_cloud": "an external memory provider (core/08)",
        "graphiti_core": "an external memory provider (core/08)",
        "markdown_it": "may never be needed",
        # Hosted inference. Not barred, simply not a dependency,
        # and only ever importable from an optional enrichment adapter.
        "openai": "optional enrichment adapter, if one is ever built",
        "anthropic": "optional enrichment adapter, if one is ever built",
        "cohere": "optional enrichment adapter, if one is ever built",
        "mistralai": "optional enrichment adapter, if one is ever built",
        "groq": "optional enrichment adapter, if one is ever built",
        "together": "optional enrichment adapter, if one is ever built",
        "google": "optional enrichment adapter, if one is ever built",
        "llama_cpp": "optional enrichment adapter, if one is ever built",
    }

    def test_no_source_module_imports_a_not_yet_required_dependency(self) -> None:
        violations = [
            f"{name} -> {imported} (gated until {self.NOT_YET_REQUIRED[root]})"
            for name, tree in source_modules()
            for imported in imported_modules(tree)
            if (root := imported.split(".")[0]) in self.NOT_YET_REQUIRED
        ]
        assert violations == [], (
            "This is a schedule, not a prohibition. When a feature needs one of these, "
            "remove its entry and record an ADR justifying the dependency "
            "(AGENTS.md 'Dependencies')."
        )

    def test_runtime_dependencies_are_exactly_the_approved_set(self) -> None:
        # Every runtime dependency needs a recorded decision (AGENTS.md,
        # "Dependencies"). Nothing outside this set is approved. `pydantic` is
        # declared although fastapi installs it, because never4ga/api imports
        # it directly.
        config = tomllib.loads(PYPROJECT.read_text())
        roots = {
            requirement.split(">")[0].split("=")[0].split("[")[0].strip()
            for requirement in config["project"]["dependencies"]
        }
        assert roots == {
            "ruamel.yaml",
            "fastapi",
            "uvicorn",
            "pydantic",
            "watchdog",
            "httpx",
        }

    #: The HTTP stack, and the one package each part of it belongs to
    #: (core/05 section 5). Modelled on the ruamel.yaml rule below:
    #: the point of confinement is that replacing a library touches one place.
    #:
    #: pydantic models in `never4ga.api` are expected -- they are why FastAPI
    #: was chosen over Starlette alone. What this bars is core/05 section 15's
    #: failure: a model that leaks below the interface starts competing with
    #: the frozen dataclasses for the role of domain model, and the API's shape
    #: begins driving the domain rather than describing it.
    HTTP_STACK: ClassVar[dict[str, str]] = {
        "fastapi": "never4ga.api",
        "pydantic": "never4ga.api",
        "starlette": "never4ga.api",
        "uvicorn": "never4ga.service",
    }

    @pytest.mark.parametrize("package", sorted(HTTP_STACK))
    def test_the_http_stack_stays_in_the_interface_layer(self, package: str) -> None:
        allowed = self.HTTP_STACK[package]
        violations = [
            f"{name} -> {imported}"
            for name, tree in source_modules()
            for imported in imported_modules(tree)
            if imported.split(".")[0] == package and not name.startswith(allowed)
        ]
        assert violations == [], (
            f"{package} belongs to {allowed}. services, context, indexing, "
            "ports and everything below them must remain usable -- and "
            "testable -- with no HTTP stack installed at all."
        )

    def test_only_the_filesystem_adapter_imports_yaml(self) -> None:
        # The YAML codec is confined so that replacing it touches one module.
        allowed = "never4ga.adapters.filesystem"
        violations = [
            f"{name} -> {imported}"
            for name, tree in source_modules()
            for imported in imported_modules(tree)
            if imported.split(".")[0] == "ruamel" and not name.startswith(allowed)
        ]
        assert violations == []

    #: details/openproject-adapter.md section 1: "Never4gA core code MUST NOT
    #: depend on OpenProject-specific concepts outside the adapter boundary."
    #: The boundary is this package, and the rule is what makes section 14's
    #: second provider a package beside it rather than an edit everywhere.
    OPENPROJECT_ADAPTER: ClassVar[str] = "never4ga.adapters.openproject"

    #: The one module outside it allowed to know the adapter exists. core/03
    #: section 15 makes `provider` an adapter identifier, so *something* has to
    #: turn that string into a class; confining it to one registry is what keeps
    #: every other module from growing its own answer.
    PROVIDER_REGISTRY: ClassVar[str] = "never4ga.adapters.work_management"

    def test_only_the_openproject_adapter_knows_about_openproject(self) -> None:
        violations: list[str] = []
        for name, tree in source_modules():
            if name.startswith((self.OPENPROJECT_ADAPTER, self.PROVIDER_REGISTRY)):
                continue
            for imported in imported_modules(tree):
                if imported.startswith(self.OPENPROJECT_ADAPTER):
                    violations.append(f"{name} imports {imported}")
            violations.extend(
                f"{name} names {node.value!r}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                # An exact constant is what a dispatch branch is made of:
                # `provider == "openproject"` in a service is the shape section
                # 1 forbids. Prose that mentions the product -- a docstring, a
                # template's `<openproject|...>` placeholder -- is not.
                and node.value.strip().casefold() == "openproject"
            )
            violations.extend(
                f"{name} defines {node.id}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and "openproject" in node.id.casefold()
            )
        assert violations == [], (
            "details/openproject-adapter.md section 1: OpenProject vocabulary "
            "stops at the adapter, and `adapters/work_management.py` is the one "
            "registry that maps a connection's `provider` field to it. Nothing "
            "else -- no service, no interface, no composition root -- names a "
            "provider."
        )

    def test_development_extras_are_only_test_and_quality_tools(self) -> None:
        config = tomllib.loads(PYPROJECT.read_text())
        extras = config["project"]["optional-dependencies"]["dev"]
        roots = {requirement.split(">")[0].split("=")[0].strip() for requirement in extras}
        assert roots == {"pytest", "mypy", "ruff"}

    def test_python_314_is_required(self) -> None:
        # core/05 section 3: standard-library UUIDv7 support.
        config = tomllib.loads(PYPROJECT.read_text())
        assert config["project"]["requires-python"] == ">=3.14"
