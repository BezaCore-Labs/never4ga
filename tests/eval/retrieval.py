"""Wiring the eval harness to the retrieval that actually ships.

The harness takes a callable; this builds the one under test. It deliberately
enters at :meth:`FocusedContextAssembler.retrieve` rather than at ``assemble``:
retrieve is Stages B, D and E plus fusion, which is exactly the surface a new
retrieval lane would join. Budget and item assembly sit after it and would only
add a truncation the measurement is not about.

`deep` is not a fourth retriever -- `context/deep.py` builds the same assembler
with a second graph hop and `30_Knowledge/` admitted -- so one builder covers
both depths, parameterised the way the product parameterises them.
"""

from __future__ import annotations

from collections import defaultdict

from never4ga.context.deep import DEEP_RELATION_DEPTH
from never4ga.context.focused import RELATION_DEPTH, FocusedContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest, terms_from_task
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery
from never4ga.ports.text_index import TextIndex
from tests.eval.corpus import EvalCase
from tests.eval.harness import RankedResult, Retriever

__all__ = ["find_workspace", "vault_retriever", "workspace_registry"]

#: Retrieval is measured before the budget, so the budget must not be the thing
#: that decides Recall@k. It is set well above any k the corpus asks for.
_UNBOUNDED = ContextBudget(max_items=1_000, max_characters=10_000_000)


class WorkspaceNotFoundError(Exception):
    """A case names a workspace this vault has not got, or has more than one of."""


def workspace_registry(metadata: MetadataIndex) -> WorkspaceRegistry:
    """Every workspace in the vault, with its lineage.

    Built from the index rather than from the machine-local mappings file: a
    case names a workspace, not a checkout, and the corpus has to mean the same
    thing on a machine that has never mapped a repository.
    """
    registry = WorkspaceRegistry()
    for record in metadata.query(MetadataQuery(types=("workspace",))):
        parent = record.extra.get("parent")
        registry.register(
            WorkspaceMapping(
                workspace_id=record.concept_id,
                workspace_path=record.path,
                parent_id=ConceptId.parse(parent) if isinstance(parent, str) else None,
            )
        )
    return registry


def find_workspace(metadata: MetadataIndex, title: str) -> ConceptId:
    """The workspace a case names, by title.

    A case is written by a person and ratified by a person, so it names the
    workspace the way they do. An ambiguous title is refused rather than
    resolved by picking one: two workspaces with one name would silently move
    the measurement between runs.
    """
    matches = [
        record
        for record in metadata.query(MetadataQuery(types=("workspace",)))
        if record.title == title
    ]
    if not matches:
        raise WorkspaceNotFoundError(f"no workspace titled {title!r} in this vault")
    if len(matches) > 1:
        where = ", ".join(str(record.path) for record in matches)
        raise WorkspaceNotFoundError(f"{title!r} names more than one workspace ({where})")
    return matches[0].concept_id


def vault_retriever(
    metadata: MetadataIndex,
    text: TextIndex,
    graph: GraphIndex,
    depth: str,
) -> Retriever:
    """The retrieval that actually ships, as the harness sees it.

    Three lanes: structural reduction, lexical, relational. A vector lane was
    measured against the corpus here and not kept.
    """
    registry = workspace_registry(metadata)
    resolver = MechanicalScopeResolver(registry)
    deep = depth == "deep"
    assembler = FocusedContextAssembler(
        resolver=resolver,
        metadata_index=metadata,
        text_index=text,
        graph_index=graph,
        relation_depth=DEEP_RELATION_DEPTH if deep else RELATION_DEPTH,
        include_knowledge=deep,
    )

    def retrieve(case: EvalCase) -> RankedResult:
        scope_request = ScopeRequest(workspace_id=find_workspace(metadata, case.workspace))
        scope = resolver.resolve(scope_request)
        request = ContextRequest(
            scope=scope_request,
            depth=ContextDepth.DEEP if deep else ContextDepth.FOCUSED,
            budget=_UNBOUNDED,
            terms=terms_from_task(case.task),
            task_given=True,
        )
        fused = assembler.retrieve(request, scope)

        ranking: list[str] = []
        lanes: defaultdict[str, list[str]] = defaultdict(list)
        for candidate in fused:
            if candidate.concept_id is None:
                # A note in foreign material has no identity a case could
                # name; the corpus is written against concepts.
                continue
            record = metadata.get(candidate.concept_id)
            if record is None:
                # An index that ranked a concept it cannot describe is a real
                # defect, but it is the indexer's, not the corpus's.
                continue
            path = str(record.path)
            ranking.append(path)
            for lane in candidate.contributions:
                lanes[lane].append(path)
        return RankedResult(
            ranking=tuple(ranking),
            lanes={lane: tuple(paths) for lane, paths in lanes.items()},
        )

    return retrieve
