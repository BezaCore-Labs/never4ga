"""The tools, bound to one vault.

This is a composition root, one of three. It picks adapters because the route
is chosen per tool call, and answering in process needs something to answer
with. `cli` and `service` cannot see each other and neither can see this. What
all three must assemble identically lives in `never4ga.composition`, and what
they render lives in `never4ga.rendering`.

**Each root does its own wiring.** None of them may carry behaviour: every tool
below resolves a route, calls a service, and renders the result through the
same functions the CLI uses. That is why the MCP and CLI payloads can be
compared for exact equality.

**The route is decided per call.** The daemon starts and stops, and a server
that chose at spawn would keep a dead route for as long as a client holds it
open. The probe is one loopback request, and `core/05` section 19 requires
that nothing depends on the service.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from never4ga import rendering
from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    GitRepositoryLocator,
    LocalSecretFileStore,
)
from never4ga.adapters.git import GitSignalProvider
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMaintenanceFindings,
    SQLiteMetadataIndex,
)
from never4ga.adapters.sqlite.connection import open_index
from never4ga.composition import (
    FileSessionStore,
    adapter_service,
    provider_factory,
    service_client,
    work_signal_provider,
    work_write_service,
    workspace_service,
)
from never4ga.config import retired_settings
from never4ga.context.budget import deep_budget, startup_budget
from never4ga.context.inbox_signals import InboxSignalProvider
from never4ga.context.session_signals import SessionSignalProvider
from never4ga.domain.context import ContextDepth, ContextRequest, terms_from_task
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.scope import ScopeRequest
from never4ga.errors import IdentityError, StructuredError
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.mcp.server import ToolFailureError
from never4ga.mcp.tools import Tool, optional_string, schema
from never4ga.platform_paths import PlatformPaths
from never4ga.schema import ValidationLevel
from never4ga.service_client import ServiceClient
from never4ga.services import (
    ContextService,
    Doctor,
    IndexService,
    SearchRequest,
    SearchService,
    WorkspaceService,
    foreign_note_at,
    not_a_concept,
)
from never4ga.services.authoring import OWNER_ACTOR
from never4ga.services.capture import CaptureService
from never4ga.services.creation import ContentService
from never4ga.services.maintenance import MaintenanceLedger
from never4ga.services.sessions import SessionService
from never4ga.services.work_reading import WorkReadService
from never4ga.services.work_recording import noting_a_lost_record, record_tracker_write
from never4ga.services.work_writing import WorkWriteService
from never4ga.services.wrap import WrapService

__all__ = ["Toolbox"]

#: What a write records as its author when nothing better is known. A tool
#: called by a model is not a person typing, and `core/02` section 5.2 requires
#: saying so. This names the server, not who is working: when a session recorded
#: an actual client, that is more specific and wins (:meth:`Toolbox.stated_actor`).
DEFAULT_ACTOR = "mcp/never4ga"


@dataclass(frozen=True, slots=True)
class _Indexes:
    metadata: SQLiteMetadataIndex
    text: SQLiteFTS5Index
    graph: SQLiteGraphIndex
    state: SQLiteIndexState
    findings: SQLiteMaintenanceFindings


class Toolbox:
    """Every tool this server offers, over one vault."""

    def __init__(self, root: Path, *, actor: str = DEFAULT_ACTOR, local: bool = False) -> None:
        self._root = root
        self._actor = actor
        self._local = local

    @property
    def stated_actor(self) -> str | None:
        """Who somebody said is writing, or `None` for the built-in default.

        A stated actor outranks whatever a session recorded. The default does
        not, because `mcp/never4ga` names the transport that carried a call,
        not the producer that did the work. A server started with no `--actor`,
        wrapping a session another client opened, must not relabel it.
        """
        return None if self._actor == DEFAULT_ACTOR else self._actor

    # -- the route --------------------------------------------------------

    def _client(self) -> ServiceClient | None:
        """The running service, if one answers for this vault and this build.

        The conditions live in `composition.service_client`, shared with the
        CLI, so the two roots cannot disagree about when to delegate.

        Nothing is passed for `on_stale_build`: a tool call returns a payload,
        not a transcript, so there is nowhere to print a note. What matters is
        declining to delegate, so the fallback answers in process with this
        build. `never4ga service status` reports a daemon that is behind.
        """
        return service_client(self._root, local=self._local)

    # -- vault wiring -----------------------------------------------------

    @property
    def _documents(self) -> FileSystemMarkdownStore:
        return FileSystemMarkdownStore(self._root)

    @property
    def _files(self) -> FileSystemVaultFileStore:
        return FileSystemVaultFileStore(self._root)

    @property
    def _workspaces(self) -> WorkspaceService:
        documents = self._documents
        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        return workspace_service(manifest.concept_id if manifest else None, documents)

    def _vault_id(self) -> ConceptId:
        manifest = self._documents.get_by_path(SYSTEM_MANIFEST)
        if manifest is None:
            raise ToolFailureError(
                StructuredError(
                    "not_a_vault",
                    f"{self._root} has no {SYSTEM_MANIFEST}, so it is not an initialised vault",
                    {"vault": str(self._root)},
                    repair_hint="run `never4ga init`",
                )
            )
        return manifest.concept_id

    def _index_exists(self) -> bool:
        return PlatformPaths.resolve().index_database(self._vault_id()).exists()

    def _require_index(self) -> None:
        if not self._index_exists():
            raise ToolFailureError(
                StructuredError(
                    "index_not_built",
                    "this vault has not been indexed yet",
                    {"vault": str(self._root)},
                    repair_hint="run `never4ga index`",
                )
            )

    @contextmanager
    def _indexes(self) -> Iterator[_Indexes]:
        """Open the derived database. Never creates one: `core/05` section 7."""
        database = PlatformPaths.resolve().index_database(self._vault_id())
        # This assembly must match the CLI's and the service's (core/05
        # section 15). `tests/integration/test_composition_parity.py` checks it.
        with closing(open_index(database)) as connection:
            yield _Indexes(
                metadata=SQLiteMetadataIndex(connection),
                text=SQLiteFTS5Index(connection),
                graph=SQLiteGraphIndex(connection),
                state=SQLiteIndexState(connection),
                findings=SQLiteMaintenanceFindings(connection),
            )

    def _indexer(self, indexes: _Indexes) -> IndexService:
        return IndexService(
            documents=self._documents,
            metadata=indexes.metadata,
            text=indexes.text,
            graph=indexes.graph,
            state=indexes.state,
        )

    def _searcher(self, indexes: _Indexes) -> SearchService:
        return SearchService(
            documents=self._documents,
            metadata=indexes.metadata,
            text=indexes.text,
            graph=indexes.graph,
            index=self._indexer(indexes),
        )

    # -- the tools --------------------------------------------------------

    def tools(self) -> Sequence[Tool]:
        return (
            Tool(
                "never4ga_workspace_resolve",
                "Which Never4gA workspace a directory belongs to, and why. "
                "Mechanical: it refuses to guess rather than picking one.",
                self.workspace_resolve,
                schema({"cwd": optional_string("An absolute path on this machine.")}),
            ),
            Tool(
                "never4ga_context_startup",
                "The smallest useful context for starting a session in a workspace: "
                "what it is, what binds it, what the last session did. Opens a session "
                "and returns its id.",
                self.context_startup,
                schema(
                    {
                        "cwd": optional_string("An absolute path on this machine."),
                        "task": optional_string(
                            "A short description of the task. Becomes retrieval terms; "
                            "never pass a whole prompt."
                        ),
                        "client": optional_string("Which agent client is asking."),
                    }
                ),
            ),
            Tool(
                "never4ga_context_focus",
                "More context about particular terms, at a stated depth. Use when the "
                "startup pack was not enough or work moved to an unfamiliar area.",
                self.context_focus,
                schema(
                    {
                        "terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "What the pack should be about.",
                        },
                        "cwd": optional_string("An absolute path on this machine."),
                        "depth": {
                            "type": "string",
                            "enum": ["startup", "focused", "deep"],
                            "description": "How far the pack reaches. Defaults to focused.",
                        },
                    },
                    required=("terms",),
                ),
            ),
            Tool(
                "never4ga_search",
                "Search the vault. Lexical always, and semantic as well when an embedder "
                "is configured; the lanes are fused by rank. Returns ranked results with "
                "the reason each was retrieved, and says when the index is stale.",
                self.search,
                schema(
                    {
                        "query": optional_string("What to search for."),
                        "limit": {"type": "integer", "description": "How many results."},
                        "explain": {
                            "type": "boolean",
                            "description": (
                                "Include the fused score and which lane found each "
                                "result. Unstable output."
                            ),
                        },
                    },
                    required=("query",),
                ),
            ),
            Tool(
                "never4ga_get_concept",
                "One concept by its Never4gA id: its frontmatter, its body, and the "
                "relations the projection knows about.",
                self.get_concept,
                schema({"id": optional_string("The concept's UUIDv7.")}, required=("id",)),
            ),
            Tool(
                "never4ga_doctor",
                "Check vault health. Reports findings with repair hints and never repairs "
                "anything itself.",
                self.doctor,
                schema(
                    {
                        "level": {
                            "type": "string",
                            "enum": [level.value for level in ValidationLevel],
                            "description": "Validation level. Defaults to strict.",
                        }
                    }
                ),
            ),
            *self._work_read_tools(),
            *self._write_tools(),
        )

    def _write_tools(self) -> Sequence[Tool]:
        """The three safe writes, then the four higher-impact ones.

        The higher-impact four are gated by the service, not by being absent:
        an MCP `work_update` is refused by the same code that refuses the CLI.
        Withholding the tool would only push agents to shell out, which adds no
        safety.
        """
        return (
            Tool(
                "never4ga_capture",
                "Put a thought, link or fragment in the Inbox without deciding where it "
                "belongs. Use when something is worth keeping and classifying it now "
                "would interrupt the work.",
                self.capture,
                schema(
                    {
                        "text": optional_string("What to keep."),
                        "title": optional_string("A title; derived from the text otherwise."),
                    },
                    required=("text",),
                ),
            ),
            Tool(
                "never4ga_checkpoint",
                "Record what this session has done so far. Runtime state only: it never "
                "touches the vault, so an interruption cannot leave half a log behind.",
                self.checkpoint,
                schema(
                    {
                        "session_id": optional_string("The id context_startup returned."),
                        "note": optional_string("What happened, briefly."),
                        "actions": _strings("A durable thing that happened."),
                        "decisions": _strings("Something decided, to be reported at wrap."),
                        "memories": _strings("Something worth remembering."),
                        "work": _strings(
                            "A work item that may need an update. Reported at wrap, "
                            "never acted on: act with never4ga_work_update or "
                            "never4ga_work_comment, passing this session_id so wrap "
                            "sees it was done."
                        ),
                        "context": _strings(
                            "A context document this session changed something in, by "
                            "id or vault path, as '<ref>: what changed'. Update the "
                            "document itself: wrap reports one whose content did not move."
                        ),
                        "walkthroughs": _strings(
                            "A phase walkthrough this session wrote a step into, by id or "
                            "vault path, as '<ref>: step N'. Write the step itself: wrap "
                            "reports one whose content did not move."
                        ),
                    },
                    required=("session_id", "note"),
                ),
            ),
            Tool(
                "never4ga_wrap",
                "Close a session: write the one activity log it leaves behind, and "
                "report what was declared along the way -- decisions, things worth "
                "remembering, work items that may need an update, and the context "
                "documents the session was given, with the question whether it changed "
                "anything they assert. It writes "
                "nothing but the log. Idempotent: wrapping twice updates one "
                "document rather than writing two, and a title given the "
                "second time replaces the first and renames the file.",
                self.wrap,
                schema(
                    {
                        "session_id": optional_string("The id context_startup returned."),
                        "title": optional_string(
                            "What the session was about. Passed again on a "
                            "later wrap, it retitles the log and renames it."
                        ),
                        "into": optional_string(
                            "Where the log belongs when the session's workspace no "
                            "longer exists: the id of the workspace it became, or of "
                            "the life area that now holds it. An archived workspace "
                            "needs nothing passed."
                        ),
                    },
                    required=("session_id",),
                ),
            ),
            Tool(
                "never4ga_decide",
                "Draft a decision record. It is written as PROPOSED and is not in "
                "effect: an agent never authors an accepted decision, and only the "
                "maintainer moves one to accepted.",
                self.decide,
                schema(
                    {
                        "title": optional_string(
                            "What the decision is about. Leave the ADR number out: "
                            "it is allocated from the folder."
                        ),
                        "workspace_id": optional_string("The workspace it belongs to."),
                        "description": optional_string("One line summarising it."),
                        "numbered": {
                            "type": "boolean",
                            "description": (
                                "Number it even though its folder has no numbered "
                                "decisions yet. A folder that numbers them always does."
                            ),
                        },
                        "series": optional_string(
                            "Which of the folder's ADR series it belongs to, when it has "
                            "more than one."
                        ),
                    },
                    required=("title",),
                ),
            ),
            Tool(
                "never4ga_work_create",
                "Create a work item on the workspace's tracker. Describes the change "
                "and sends nothing unless `apply` is true.",
                self.work_create,
                schema(
                    {
                        "title": optional_string("The item's title."),
                        "fields": {"type": "object", "description": "Other fields to set."},
                        "cwd": optional_string("An absolute path in the workspace."),
                        "session_id": _session_id(),
                        "apply": _apply(),
                    },
                    required=("title",),
                ),
            ),
            Tool(
                "never4ga_work_update",
                "Change fields on a work item. Describes the change and sends nothing "
                "unless `apply` is true; a change to nothing is refused either way.",
                self.work_update,
                schema(
                    {
                        "work_item": optional_string("The tracker's id for the item."),
                        "fields": {"type": "object", "description": "Fields to change."},
                        "cwd": optional_string("An absolute path in the workspace."),
                        "session_id": _session_id(),
                        "apply": _apply(),
                    },
                    required=("work_item", "fields"),
                ),
            ),
            Tool(
                "never4ga_work_comment",
                "Comment on a work item, or replace a comment already made. Describes "
                "the change and sends nothing unless `apply` is true.",
                self.work_comment,
                schema(
                    {
                        "work_item": optional_string("The tracker's id for the item."),
                        "body": optional_string("The comment's text."),
                        "amends": optional_string("An existing comment's id to replace."),
                        "cwd": optional_string("An absolute path in the workspace."),
                        "session_id": _session_id(),
                        "apply": _apply(),
                    },
                    required=("work_item", "body"),
                ),
            ),
        )

    def _work_read_tools(self) -> Sequence[Tool]:
        return (
            Tool(
                "never4ga_work_search",
                "Ask the workspace's tracker what is open. The tracker stays the system "
                "of record; this reads it rather than mirroring it.",
                self.work_search,
                schema(
                    {
                        "terms": _strings("A word to search for."),
                        "statuses": _strings("A status to filter by."),
                        "limit": {"type": "integer", "description": "How many items."},
                        "cwd": optional_string("An absolute path in the workspace."),
                    }
                ),
            ),
            Tool(
                "never4ga_work_get",
                "Read one work item from the workspace's tracker.",
                self.work_get,
                schema(
                    {
                        "work_item": optional_string("The tracker's id for the item."),
                        "cwd": optional_string("An absolute path in the workspace."),
                    },
                    required=("work_item",),
                ),
            ),
        )

    # -- implementations --------------------------------------------------

    def workspace_resolve(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        where = PurePath(Path(str(arguments.get("cwd") or ".")).expanduser().resolve())
        return rendering.resolved_scope(self._workspaces.resolve(ScopeRequest(cwd=where)))

    def context_startup(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._context(arguments, ContextDepth.STARTUP)

    def context_focus(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        depth = ContextDepth(str(arguments.get("depth") or ContextDepth.FOCUSED.value))
        return self._context(arguments, depth)

    def _context(self, arguments: Mapping[str, Any], depth: ContextDepth) -> Mapping[str, Any]:
        cwd = str(Path(str(arguments.get("cwd") or ".")).expanduser().resolve())
        request: dict[str, Any] = {
            "scope": {"cwd": cwd},
            "terms": [str(term) for term in arguments.get("terms") or ()],
            "client": arguments.get("client"),
            # The in-process branch below passes `self._actor`, so the
            # forwarded request must carry it too.
            "actor": self._actor,
            "task": arguments.get("task"),
            "max_items": None,
            "max_characters": None,
            "work_item": None,
            "refresh": False,
        }
        client = self._client()
        if client is not None:
            answered = client.context(_ROUTE_OF_DEPTH[depth], request)
            return rendering.with_disk_paths(answered, self._root)
        self._require_index()
        sessions = FileSessionStore(PlatformPaths.resolve().sessions_database(self._vault_id()))
        with self._indexes() as indexes:
            service = ContextService(
                workspaces=self._workspaces,
                metadata=indexes.metadata,
                text=indexes.text,
                graph=indexes.graph,
                documents=self._documents,
                signal_providers=(
                    GitSignalProvider(),
                    # The same Inbox signal the CLI shows; the parity tests
                    # hold the three assemblies to one answer.
                    InboxSignalProvider(self._files),
                    SessionSignalProvider(sessions),
                    work_signal_provider(
                        documents=self._documents,
                        secrets=LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
                        trackers_database=PlatformPaths.resolve().trackers_database(
                            self._vault_id()
                        ),
                    ),
                ),
                sessions=sessions,
            )
            budget = deep_budget() if depth is ContextDepth.DEEP else startup_budget()
            pack, resolution = service.assemble(
                ContextRequest(
                    scope=ScopeRequest(cwd=PurePath(cwd)),
                    depth=depth,
                    budget=budget,
                    terms=(
                        *request["terms"],
                        *terms_from_task(str(request["task"] or "")),
                    ),
                    client=request["client"],
                    actor=self._actor,
                    task_given=request["task"] is not None,
                )
            )
        return rendering.with_disk_paths(
            rendering.scope_pack(depth.value, pack, resolution), self._root
        )

    def search(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        request: dict[str, Any] = {
            "query": str(arguments.get("query", "")),
            "limit": arguments.get("limit"),
            "explain": bool(arguments.get("explain", False)),
        }
        client = self._client()
        if client is not None:
            return dict(client.search(request))
        self._require_index()
        limit = request["limit"]
        with self._indexes() as indexes:
            response = self._searcher(indexes).search(
                SearchRequest(query=request["query"], limit=int(limit) if limit else None)
            )
        return rendering.search_response(response, explain=request["explain"])

    def get_concept(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        identity = str(arguments.get("id", ""))
        client = self._client()
        if client is not None:
            return dict(client.concept(identity))
        try:
            concept_id = ConceptId.parse(identity)
        except IdentityError:
            note = foreign_note_at(self._documents, identity)
            if note is None:
                raise
            raise ToolFailureError(not_a_concept(note)) from None
        if self._index_exists():
            with self._indexes() as indexes:
                view = self._searcher(indexes).get(concept_id)
        else:
            # Markdown is canonical (`core/00` #1), so a concept is readable
            # before anything has ever been indexed.
            from never4ga.services.search import ConceptView

            document = self._documents.get(concept_id)
            view = None if document is None else ConceptView(document=document, index_is_stale=True)
        if view is None:
            raise ToolFailureError(
                StructuredError(
                    "concept_not_found",
                    f"no concept with id {identity}",
                    {"id": identity},
                    repair_hint="use never4ga_search to find it",
                )
            )
        return rendering.concept_view(view)

    def doctor(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        level = ValidationLevel(str(arguments.get("level") or ValidationLevel.STRICT.value))
        client = self._client()
        if client is not None:
            return {"vault": str(self._root), **client.doctor(level.value)}
        if not self._index_exists():
            found = Doctor(
                self._files,
                self._documents,
                level=level,
                repositories=GitRepositoryLocator(),
                mappings=self._workspaces.mappings(),
                adapters=adapter_service(self._root, self._files).diagnose(),
                configuration=retired_settings(),
            ).diagnose()
        else:
            with self._indexes() as indexes:
                found = Doctor(
                    self._files,
                    self._documents,
                    level=level,
                    index=self._indexer(indexes).health(),
                    repositories=GitRepositoryLocator(),
                    mappings=self._workspaces.mappings(),
                    adapters=adapter_service(self._root, self._files).diagnose(),
                    configuration=retired_settings(),
                ).diagnose()
                # The same act the CLI performs, in the third assembly
                # (`core/05` section 15). Section 20's `detected_at` is a
                # property of the run, so recording belongs where the run is.
                if found.complete:
                    MaintenanceLedger(indexes.findings).record(found)
        return rendering.diagnosis(str(self._root), found)

    # -- work, and the writes ---------------------------------------------

    def _reader(self) -> WorkReadService:
        return WorkReadService(
            documents=self._documents,
            workspaces=self._workspaces,
            factory=provider_factory(
                LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
                PlatformPaths.resolve().trackers_database(self._vault_id()),
            ),
        )

    def _writer(self) -> WorkWriteService:
        return work_write_service(
            self._documents,
            self._workspaces,
            LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
            PlatformPaths.resolve().trackers_database(self._vault_id()),
        )

    def _where(self, arguments: Mapping[str, Any]) -> PurePath:
        return PurePath(Path(str(arguments.get("cwd") or ".")).expanduser().resolve())

    def _bound_reader(self, arguments: Mapping[str, Any]) -> tuple[WorkReadService, Any]:
        service = self._reader()
        bound = service.bind(self._where(arguments))
        if isinstance(bound, StructuredError):
            raise ToolFailureError(bound)
        return service, bound

    def _bound_writer(self, arguments: Mapping[str, Any]) -> tuple[WorkWriteService, Any]:
        service = self._writer()
        bound = service.bind(self._where(arguments), applying=bool(arguments.get("apply")))
        if isinstance(bound, StructuredError):
            raise ToolFailureError(bound)
        return service, bound

    @staticmethod
    def _or_fail(result: dict[str, Any] | StructuredError) -> Mapping[str, Any]:
        if isinstance(result, StructuredError):
            raise ToolFailureError(result)
        return result

    def work_search(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        service, bound = self._bound_reader(arguments)
        limit = arguments.get("limit")
        return self._or_fail(
            service.search(
                bound,
                terms=[str(one) for one in arguments.get("terms") or ()],
                statuses=[str(one) for one in arguments.get("statuses") or ()],
                limit=int(limit) if limit else None,
            )
        )

    def work_get(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        service, bound = self._bound_reader(arguments)
        return self._or_fail(service.get(bound, str(arguments.get("work_item", ""))))

    def work_create(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        service, bound = self._bound_writer(arguments)
        fields = {"title": arguments.get("title"), **dict(arguments.get("fields") or {})}
        result = service.create(bound, fields)
        return self._or_fail(self._record_write(arguments, result, "create"))

    def work_update(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        service, bound = self._bound_writer(arguments)
        result = service.update(
            bound, str(arguments.get("work_item", "")), dict(arguments.get("fields") or {})
        )
        return self._or_fail(self._record_write(arguments, result, "update"))

    def work_comment(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        service, bound = self._bound_writer(arguments)
        amends = arguments.get("amends")
        result = service.comment(
            bound,
            str(arguments.get("work_item", "")),
            str(arguments.get("body", "")),
            amends=str(amends) if amends else None,
        )
        return self._or_fail(self._record_write(arguments, result, "comment"))

    def _record_write(self, arguments: Mapping[str, Any], result: Any, verb: str) -> Any:
        """Tell the session the write happened, as the CLI's `--session` does.

        This runs before the result is checked for failure, which is harmless:
        a structured error records nothing, and a failure to record never fails
        the write.

        Returns the payload with ``record_lost`` on it when the tracker changed
        and the session could not be told. Raising instead would tell the agent
        its write had failed, and the agent would retry, turning one comment or
        item into two. The field says the write landed and only the bookkeeping
        did not.
        """
        session = arguments.get("session_id")
        given = arguments.get("work_item")
        record = record_tracker_write(
            FileSessionStore(PlatformPaths.resolve().sessions_database(self._vault_id())),
            str(session) if session else None,
            verb=verb,
            result=result,
            given=str(given) if given else None,
            applied=bool(arguments.get("apply")),
        )
        return noting_a_lost_record(result, record)

    # -- authoring --------------------------------------------------------

    def _content(self, *, actor: str | None = None) -> ContentService:
        return ContentService(self._files, self._documents, actor=actor or self._actor)

    def _wrap_actor(self, sessions: FileSessionStore, session: SessionId) -> str:
        """Who a wrapped log records, in one place, most specific first.

        1. A **stated** actor. Somebody said who is writing, and saying so is
           the only way to beat an observation.
        2. The **session's** actor, when it recorded a real one. A session
           opened by an agent client and wrapped through an MCP server was done
           by that client; the server is the transport, not the producer.
        3. `mcp/never4ga`. Nothing better is known, and it is still true.
           Deferring to a session that recorded nothing would give
           `human:owner`, a false claim that a person wrote it.
        """
        stated = self.stated_actor
        if stated is not None:
            return stated
        recorded = sessions.get(session)
        if recorded is not None and recorded.actor != OWNER_ACTOR:
            return recorded.actor
        return DEFAULT_ACTOR

    def capture(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        note = CaptureService(self._files).capture(
            str(arguments.get("text", "")), title=arguments.get("title")
        )
        return rendering.captured(note)

    def decide(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Draft a decision. Proposed, never accepted.

        An agent never authors an accepted decision (`core/04`,
        `never4ga-decide`). The type's body shape carries `lifecycle: proposed`,
        so what this writes is a draft that says so on its face.
        """
        workspace = arguments.get("workspace_id")
        made = self._content().create_concept(
            "decision",
            str(arguments.get("title", "")),
            workspace=ConceptId.parse(str(workspace)) if workspace else None,
            description=arguments.get("description"),
            numbered=bool(arguments.get("numbered", False)),
            series=arguments.get("series"),
        )
        return rendering.created(made)

    def checkpoint(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        from never4ga.domain.identity import SessionId

        sessions = FileSessionStore(PlatformPaths.resolve().sessions_database(self._vault_id()))
        recorded = SessionService(sessions).checkpoint(
            SessionId.parse(str(arguments.get("session_id", ""))),
            str(arguments.get("note", "")),
            actions=tuple(str(one) for one in arguments.get("actions") or ()),
            decisions=tuple(str(one) for one in arguments.get("decisions") or ()),
            memories=tuple(str(one) for one in arguments.get("memories") or ()),
            work=tuple(str(one) for one in arguments.get("work") or ()),
            context=tuple(str(one) for one in arguments.get("context") or ()),
            walkthroughs=tuple(str(one) for one in arguments.get("walkthroughs") or ()),
        )
        return rendering.checkpoint(recorded)

    def wrap(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        sessions = FileSessionStore(PlatformPaths.resolve().sessions_database(self._vault_id()))
        session = SessionId.parse(str(arguments.get("session_id", "")))
        service = WrapService(
            sessions, self._content(actor=self._wrap_actor(sessions, session)), self._documents
        )
        title = arguments.get("title")
        into = arguments.get("into")
        done = service.wrap(
            session,
            title=str(title) if title else None,
            into=ConceptId.parse(str(into)) if into else None,
        )
        return rendering.wrapped(done)


_ROUTE_OF_DEPTH: Mapping[ContextDepth, str] = {
    ContextDepth.STARTUP: "startup",
    ContextDepth.FOCUSED: "focus",
    ContextDepth.DEEP: "deep",
}


def _strings(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def _session_id() -> dict[str, Any]:
    """The session a write belongs to, so `wrap` can see it happened.

    Optional, as the CLI's `--session` is: a write with none still goes
    through, and simply belongs to no session.
    """
    return optional_string(
        "The id context_startup returned. Pass it so wrap sees this write happened."
    )


def _apply() -> dict[str, Any]:
    """The explicit apply gate for tracker writes, as a tool argument.

    Defaults to false, so a model that omits it gets a description rather than
    a change to somebody's system of record.
    """
    return {
        "type": "boolean",
        "description": "Actually send it. Without this the change is only described.",
    }
