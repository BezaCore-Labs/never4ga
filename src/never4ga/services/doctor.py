"""Basic vault health checks.

``doctor`` reports what can be established mechanically from the canonical
Markdown alone: structure, identity, schema and placement. Nothing here uses a
model, and nothing here repairs anything -- core/02 section 23 requires repair
to be explicit, and section 32 that canonical data is never discarded because
maintenance found a problem.

core/02 section 32 (*Anti-Drift Rules*) and
`details/data-indexing-maintenance.md` section 21 list what a maintaining
runtime should detect, and the two overlap almost exactly. This module covers
duplicate UUIDs, missing ids, path/type mismatches, invalid lifecycle values,
broken relation targets, stale content, missing workspace manifests,
superseded decisions with no replacement, parent/child mismatch, orphan and
misfiled workspace content, deprecated content still used as current,
near-duplicate tags, and the paths a mapped repository's agent files name.

**What is not covered**, named here so the gap stays legible rather than
assumed covered:

- **duplicate entity candidates.** The `entity` type is retired (core/02
  section 21.6), so there is nothing for the rule to be about.
- **unmapped external repositories, and unavailable PM connections.** Both reach
  outside the vault for machine-local or network state; the second would make
  `doctor` do I/O against a tracker.
- **proposing a `stale_after`** rather than only honouring one (section 15.1),
  and the section 22 scheduler.
- **a repair of any kind.** Detection writes nothing.

Section 20's `maintenance_findings` table has a store, and every root records a
diagnosis into it (:class:`~never4ga.services.maintenance.MaintenanceLedger`).
Nothing here does the recording: detection stays a pure reading of the vault
and the caller decides whether the run was complete enough to be remembered.
That is what :attr:`Diagnosis.complete` is for.

One rule cannot be checked mechanically: **an agent file asserting a fact that
belongs elsewhere**, such as the current milestone. Telling a quotation from an
assertion is reading English, which core/07 section 1 keeps out of the
deterministic path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Final

from never4ga.context.budget import REQUIRED_READING_CEILING
from never4ga.context.required import StandardFacts, excepted_targets, required_standards
from never4ga.domain.configuration import RetiredSetting
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.relations import CLOSES, NON_CORROBORATING, SUPERSEDED_BY
from never4ga.domain.scope import WorkspaceMapping
from never4ga.domain.vendoring import Drift
from never4ga.errors import IdentityError
from never4ga.layout import (
    CHILD_WORKSPACE_DIRECTORY,
    HOME,
    INBOX_SCRATCHPAD_NAME,
    KNOWLEDGE_DIRECTORIES,
    RESERVED_INDEX,
    ROOT_INDEX,
    SYSTEM_MANIFEST,
    WORKSPACE_MANIFEST,
    VaultRoot,
    life_area_directory_of,
    registered_foreign_material,
    workspace_directory_of,
)
from never4ga.layout.decision_numbers import DecisionNumber, number_in_filename
from never4ga.ports.document_store import DocumentStore, IntegrityReportingStore
from never4ga.ports.repository_locator import RepositoryLocator
from never4ga.ports.vault_files import VaultFileStore
from never4ga.ports.work_management import ProviderHealth
from never4ga.schema import (
    Severity,
    ValidationLevel,
    ValidationReport,
    registered_domains,
    stale_instant,
    validate_document,
)
from never4ga.services.adapters import AdapterFinding
from never4ga.services.authoring import Clock, utc_now
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.dashboard import missing as views_missing
from never4ga.services.dashboard import outdated as views_outdated
from never4ga.services.indexing import IndexHealth
from never4ga.services.navigation import missing_indexes as navigation_missing
from never4ga.services.navigation import outdated as navigation_outdated
from never4ga.services.repository_pointers import (
    CANONICAL_FILENAME,
    GITIGNORE_BEGIN,
    documentation_in,
    is_generated,
    pointer_body,
)
from never4ga.services.skill_library import SkillLibrary, SkillState
from never4ga.services.vendoring import VendoringService, provenance_path

__all__ = ["Diagnosis", "Doctor", "Finding"]

#: The roots under which a workspace directory may sit. A bounded pursuit
#: belonging to a responsibility rather than to a project lives at
#: `20_Life/<Area>/Workspaces/<Name>/` (core/03 section 30).
_WORKSPACE_ROOTS: Final = (VaultRoot.WORKSPACES, VaultRoot.LIFE)


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth telling the user about.

    Shaped like details/api-cli-mcp-contract.md section 12: a stable code, a
    message, and a hint at what would fix it.
    """

    code: str
    message: str
    severity: Severity
    #: Where the problem is -- the document at fault, never the thing it points
    #: at. Naming the target would make three documents linking to one missing
    #: file indistinguishable, and tell a reader nothing about where to look.
    path: VaultPath | None = None
    repair_hint: str | None = None
    #: The same document, by the identity that survives a rename (`core/06`
    #: section 3). `details/data-indexing-maintenance.md` section 20 gives a
    #: persisted finding a ``document_id``, and this is where it comes from.
    #: ``None`` where the finding is about the vault rather than a document --
    #: a missing directory, a stale index -- or where the index names a source
    #: the vault no longer holds.
    document_id: ConceptId | None = None
    #: Whether this finding may be remembered.
    #:
    #: Almost everything here is a claim about the vault, which does not change
    #: while nobody is looking, so a ledger row about it stays true. A claim
    #: about *somebody else's server* does not: a stored "unavailable" still
    #: reads as true tomorrow: a stale answer passing as a current one, with a
    #: `detected_at` making it look authoritative.
    #:
    #: :class:`~never4ga.services.maintenance.MaintenanceLedger` drops these, so
    #: reachability is re-established by asking rather than by remembering.
    ephemeral: bool = False


@dataclass(frozen=True, slots=True)
class Diagnosis:
    vault_id: ConceptId | None
    concept_count: int
    findings: tuple[Finding, ...]
    #: Whether every check this vault has was actually run.
    #:
    #: A diagnosis is *partial* when the level asked for fewer schema rules than
    #: ``strict``, when there was no index to check against, or when no
    #: repository locator was supplied. In each case a rule produced no findings
    #: because nobody ran it, which is indistinguishable from producing none
    #: because there is nothing wrong -- and
    #: :class:`~never4ga.services.maintenance.MaintenanceLedger` resolves a
    #: finding precisely by not seeing it again. So the diagnosis carries the
    #: answer rather than every caller re-deriving it from the arguments it
    #: happened to pass.
    #:
    #: Defaults to ``False`` because the safe assumption about a hand-built
    #: diagnosis is that it left something out. :meth:`Doctor.diagnose` computes
    #: the real answer.
    complete: bool = False

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def healthy(self) -> bool:
        return not self.errors


#: What `init` creates and what the vault contract depends on (core/01).
_REQUIRED_DIRECTORIES: Final = (
    *(str(root) for root in VaultRoot),
    *(f"{VaultRoot.KNOWLEDGE}/{name}" for name in KNOWLEDGE_DIRECTORIES),
)


class Doctor:
    """Mechanical vault diagnostics."""

    def __init__(
        self,
        files: VaultFileStore,
        documents: DocumentStore,
        *,
        level: ValidationLevel = ValidationLevel.STRICT,
        index: IndexHealth | None = None,
        now: Clock = utc_now,
        repositories: RepositoryLocator | None = None,
        mappings: Sequence[WorkspaceMapping] = (),
        adapters: Sequence[AdapterFinding] | None = None,
        connection_health: Mapping[str, ProviderHealth] | None = None,
        configuration: Sequence[RetiredSetting] = (),
    ) -> None:
        self._files = files
        self._documents = documents
        self._level = level
        self._index = index
        self._now = now
        self._repositories = repositories
        self._mappings = tuple(mappings)
        self._adapters = None if adapters is None else tuple(adapters)
        self._connection_health = connection_health
        self._configuration = tuple(configuration)

    def diagnose(self) -> Diagnosis:
        findings: list[Finding] = []
        # One walk, shared by everything below it. Loading the corpus is most of
        # what a diagnosis costs; the checks themselves are cheap by comparison.
        # `tests/unit/test_diagnosis_reads_the_vault_once.py` holds this to one
        # pass, because a second walk would add cost silently: every finding
        # would still be right.
        concepts = list(self._documents.iter_documents())
        # Which top-level directories are the user's foreign material (core/01
        # section 1). One read of the manifest, not a walk, and the four checks
        # that need it share it.
        foreign = self._foreign_material()
        findings.extend(self._check_structure(foreign))
        vault_id = self._check_identity(findings, concepts)
        findings.extend(self._check_duplicates())
        findings.extend(self._check_unreadable(foreign))
        findings.extend(self._check_untracked(foreign))
        findings.extend(self._check_schema(concepts))
        findings.extend(self._check_index(concepts))
        findings.extend(self._check_connections(concepts))
        findings.extend(self._check_skills())
        findings.extend(self._check_closed_plans(concepts))
        findings.extend(self._check_staleness(concepts))
        findings.extend(self._check_required_reading(concepts))
        findings.extend(self._check_workspace_manifests())
        findings.extend(self._check_supersession(concepts))
        findings.extend(self._check_decision_numbers(concepts))
        findings.extend(self._check_workspace_scope(concepts))
        findings.extend(self._check_deprecated_in_use(concepts))
        findings.extend(self._check_tag_hygiene(concepts))
        findings.extend(self._check_repositories())
        findings.extend(self._check_adapters())
        findings.extend(self._check_navigation(concepts))
        findings.extend(self._check_workspace_views(concepts))
        findings.extend(self._check_configuration())
        findings.extend(self._check_connection_health())
        findings.extend(self._check_vendored_material(concepts))
        return Diagnosis(
            vault_id=vault_id,
            concept_count=len(concepts),
            findings=tuple(findings),
            complete=self._is_complete(),
        )

    def _check_vendored_material(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """What the vault holds that it cannot account for (core/02 section 21.21).

        Three findings, and the split matters. A subject with no record is a
        gap in the bookkeeping. A subject whose files no longer hash to what
        was recorded has been changed here. A record whose subject directory is
        gone is bookkeeping outliving its subject. None of them is repairable:
        where a vendored artifact came from is not derivable from its contents,
        which is why every one of these reports rather than fixes.

        Upstream drift is deliberately not checked here. It needs a network,
        `doctor` runs on a schedule, and a stored "unavailable" wearing a
        `detected_at` reads as authoritative when it is merely old.
        """
        service = VendoringService(self._files, self._documents)
        with service.one_pass():
            return self._vendored_findings(service, concepts)

    def _vendored_findings(
        self, service: VendoringService, concepts: Sequence[StoredDocument]
    ) -> list[Finding]:
        subjects = service.subjects()
        # No early return on an empty subject list. A vault with no vendored
        # material can still hold a record whose subject was deleted, and that
        # is exactly the case where the last subject going away takes the
        # finding with it.
        findings: list[Finding] = []
        recorded_paths = {
            str(document.frontmatter.get("skill_path", ""))
            for document in concepts
            if document.frontmatter.get("type") == "skill_provenance"
        }
        for subject in subjects:
            state = service.state_of(subject)
            # Before the record check, not after it. Material that arrived by
            # hand has no record *and* skipped the gate, which makes it the
            # most likely thing to be carrying a hazard and the least likely to
            # be scanned if this sits below a `continue`.
            for hazard in service.hazards(subject):
                findings.append(
                    Finding(
                        code="vendored_text_hazard",
                        message=f"{subject}/{hazard}",
                        severity=Severity.WARNING,
                        path=VaultPath.parse(subject + "/"),
                        repair_hint=(
                            "an invisible character or a personal path in text a model reads; "
                            "remove it at the source and re-record, rather than editing the "
                            "vendored copy and losing the match with its origin"
                        ),
                    )
                )
            if state.recorded is None:
                findings.append(
                    Finding(
                        code="skill_provenance_missing",
                        message=f"{subject} has no record of where it came from",
                        severity=Severity.WARNING,
                        path=VaultPath.parse(subject + "/"),
                        repair_hint=(
                            "record it with `never4ga skills provenance record <subject>`; "
                            "where a vendored artifact came from cannot be recovered from "
                            "its contents, so this is not repairable automatically"
                        ),
                    )
                )
                continue
            if state.record_is_incomplete():
                findings.append(
                    Finding(
                        code="skill_provenance_incomplete",
                        message=(
                            f"{subject} has a provenance record with no tree digest, "
                            "so nothing can say whether it still matches"
                        ),
                        severity=Severity.WARNING,
                        path=provenance_path(subject),
                        repair_hint=(
                            "re-record it with `never4ga skills provenance record <subject>`, "
                            "which computes the digest the record is missing. This is not "
                            "drift: the material may be untouched, and until the digest "
                            "exists nothing can tell"
                        ),
                    )
                )
                continue
            if state.drift() is Drift.LOCAL:
                changed = state.changed_files()
                named = f": {', '.join(changed[:3])}" if changed else ""
                findings.append(
                    Finding(
                        code="skill_local_drift",
                        message=f"{subject} no longer matches what was recorded{named}",
                        severity=Severity.WARNING,
                        path=VaultPath.parse(subject + "/"),
                        repair_hint=(
                            "an intended edit means re-recording it; an unintended one means "
                            "restoring from the origin the record names. Reinstalling destroys "
                            "local edits, so decide which this is first"
                        ),
                    )
                )

        for subject in recorded_paths - set(subjects):
            if not subject:
                continue
            findings.append(
                Finding(
                    code="skill_provenance_orphaned",
                    message=f"a provenance record names {subject}, which is not in the vault",
                    severity=Severity.WARNING,
                    path=provenance_path(subject),
                    repair_hint=(
                        "the material was removed and its record was not; delete the record, "
                        "or restore the material if the removal was accidental"
                    ),
                )
            )
        return findings

    def _is_complete(self) -> bool:
        """Whether this run asked every question, in the sense Diagnosis means.

        An empty ``_mappings`` is deliberately not a partial run: no repository
        being mapped is an answer to the question, not a refusal to ask it.
        """
        return (
            self._level is ValidationLevel.STRICT
            and self._index is not None
            and self._repositories is not None
            and self._adapters is not None
        )

    # -- checks -----------------------------------------------------------

    def _check_closed_plans(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """A plan something `closes` that still calls itself open (core/02 section 17.2).

        A plan can still say `draft` long after a completion report finished
        it, and nothing can detect that while the report names its plan only in
        prose. `closes` is the machine-readable form, and this reads it.

        Unknown lifecycle values are tolerated rather than reported. core/02
        section 20 requires that, and a vault with its own vocabulary must not
        be told its plans are open because Never4gA does not know the word.
        """
        lifecycles: dict[ConceptId, tuple[str, str]] = {}
        closed: set[ConceptId] = set()
        for document in concepts:
            lifecycle = document.frontmatter.get("lifecycle")
            title = document.frontmatter.get("title")
            if isinstance(lifecycle, str):
                lifecycles[document.concept_id] = (
                    lifecycle,
                    title if isinstance(title, str) else str(document.path),
                )
            closed.update(_closes(document))

        findings: list[Finding] = []
        for concept_id in sorted(closed, key=str):
            entry = lifecycles.get(concept_id)
            if entry is None:
                continue
            lifecycle, title = entry
            if lifecycle not in _OPEN_LIFECYCLES:
                continue
            findings.append(
                Finding(
                    code="closed_plan_is_open",
                    message=(
                        f"{title!r} is closed by another document but still says "
                        f"lifecycle: {lifecycle}"
                    ),
                    severity=Severity.WARNING,
                    repair_hint=(
                        "set its lifecycle to completed, or to abandoned if the work "
                        "stopped rather than finished; if it is genuinely still open, "
                        "the `closes` relation is the wrong one"
                    ),
                )
            )
        return findings

    def _check_staleness(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """Content that has passed the freshness instant it set for itself.

        core/02 section 32 lists "stale content" among what the runtime should
        detect, and section 15 gives the rule: a concept is stale when
        ``now >= stale_after``. Without this check a document could pass its own
        expiry while `doctor` called the vault healthy.

        A WARNING, and reported rather than repaired. Section 15.2 keeps stale
        content available and visibly flagged, and the field is a claim its
        author made about their own content -- whether it is still true is a
        person's judgement, not a timestamp's.

        Absence is not staleness. Section 15 is explicit that no `stale_after`
        means no instant was asserted, and that this does not mean the content
        can never go stale. Guessing one from age would replace the author's
        claim with Never4gA's, which section 15.1 rules out to avoid two
        competing freshness sources of truth.
        """
        now = self._now()
        findings: list[Finding] = []
        for document in concepts:
            moment = stale_instant(document.frontmatter.get("stale_after"))
            if moment is None or moment > now:
                continue
            title = document.frontmatter.get("title")
            name = title if isinstance(title, str) else str(document.path)
            findings.append(
                Finding(
                    code="content_is_stale",
                    message=(
                        f"{name!r} passed the stale_after it set for itself, {_stamp(moment)}"
                    ),
                    severity=Severity.WARNING,
                    path=document.path,
                    repair_hint=(
                        "review it: confirm it is still true and push stale_after "
                        "forward, or correct the content, or drop the field if the "
                        "claim no longer expires (core/02 section 15.2)"
                    ),
                )
            )
        return findings

    def _check_required_reading(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """A workspace whose required reading is past its ceiling (core/07 section 10).

        `core/02` section 32's item. Required reading is what every startup in
        a workspace delivers in full: its page, its parents' pages, its live
        context documents, its newest handoff and its required standards.
        Nothing cuts it, so this is what keeps it from drifting toward 100k
        tokens -- a WARNING that names the largest contributors and repairs
        nothing, because what moves out is a person's call.

        The same definition `StructuralContext` marks at startup;
        `tests/integration/test_required_reading.py` holds the two to one total.
        """
        live = [document for document in concepts if document.path.root != VaultRoot.ARCHIVE]
        workspaces = {
            document.concept_id: document
            for document in live
            if document.frontmatter.get("type") == "workspace"
        }
        by_workspace: dict[ConceptId, list[StoredDocument]] = {}
        handoffs: dict[ConceptId, StoredDocument] = {}
        standards = [
            document for document in live if document.frontmatter.get("type") == "standard"
        ]
        for document in live:
            owner = _workspace_of(document)
            if owner is None:
                continue
            kind = document.frontmatter.get("type")
            if kind == "context":
                by_workspace.setdefault(owner, []).append(document)
            elif kind == "activity_log" and not document.frontmatter.get("covers"):
                current = handoffs.get(owner)
                if current is None or _occurred(document) > _occurred(current):
                    handoffs[owner] = document

        findings: list[Finding] = []
        for workspace_id, page in workspaces.items():
            parents = _parent_pages(page, workspaces)
            reading = [page, *parents]
            reading.extend(by_workspace.get(workspace_id, ()))
            if workspace_id in handoffs:
                reading.append(handoffs[workspace_id])
            lineage = {workspace_id, *(parent.concept_id for parent in parents)}
            reading.extend(_required_standards_for(lineage, standards))
            total = sum(len(document.body) for document in reading)
            if total <= REQUIRED_READING_CEILING:
                continue
            largest = sorted(reading, key=lambda document: len(document.body), reverse=True)[:3]
            findings.append(
                Finding(
                    code="required_reading_too_large",
                    message=(
                        f"{_title_of(page)!r} requires {total:,} characters of reading at "
                        f"startup, over the {REQUIRED_READING_CEILING:,} ceiling; largest: "
                        + ", ".join(
                            f"{_title_of(document)} ({len(document.body):,})"
                            for document in largest
                        )
                    ),
                    severity=Severity.WARNING,
                    path=page.path,
                    repair_hint=(
                        "move settled history to a Logs roll-up and how-to material to "
                        "Runbooks/; nothing is cut for you (core/07 section 10)"
                    ),
                )
            )
        return findings

    def _check_workspace_manifests(self) -> list[Finding]:
        """A workspace directory that carries no `workspace.md` (section 21).

        core/01 section 4 keeps the semantic manifest separate from reserved OKF
        navigation, so a workspace is `workspace.md` and never `index.md`.

        An ERROR rather than a warning. A workspace without its manifest has no
        identity: nothing can declare `workspace:` against it, `workspace
        resolve` cannot reach it from a repository, and the id that would have
        been its stable name does not exist. That is broken rather than untidy.

        `workspace_directory_of` decides what counts, which keeps the nesting
        rule in one place: `Acme/Workspaces/` holds workspaces and is not one
        (core/01 section 6).

        **`90_Archive/Workspaces/` is deliberately excluded.** That tree often
        holds material *detached from* a live workspace, not an archived one.
        core/01 section 13 makes that the specified use: when a year closes its
        logs move intact to `90_Archive/Workspaces/<Name>/Logs/<YYYY>/`, and the
        workspace carries on.

        So a manifest there would be a second document claiming to be the same
        workspace. The two cases -- an archived workspace and a live workspace's
        archived material -- are indistinguishable from the path, and the only
        thing that would tell them apart is the manifest whose absence is the
        question. `workspace_directory_of` still resolves inside the archive,
        because a rolled-up log must still resolve to the workspace whose
        history it is; that is a different question from who owes a manifest.
        """
        findings: list[Finding] = []
        for directory in sorted(self._files.iter_directories(), key=str):
            if directory.root not in _WORKSPACE_ROOTS:
                continue
            if workspace_directory_of(directory) != directory:
                continue
            manifest = VaultPath((*directory.segments, WORKSPACE_MANIFEST))
            if self._files.exists(manifest):
                continue
            findings.append(
                Finding(
                    code="missing_workspace_manifest",
                    message=f"{directory} is a workspace directory with no {WORKSPACE_MANIFEST}",
                    severity=Severity.ERROR,
                    path=directory,
                    repair_hint=(
                        "create the manifest with `never4ga workspace create`, or move the "
                        "directory out of 10_Workspaces/ if it is not a workspace "
                        "(core/01 section 4)"
                    ),
                )
            )
        return findings

    def _check_supersession(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """A superseded decision that does not say what replaced it.

        core/02 section 21.11 makes the replacement link a MUST.

        A WARNING, not an error: the decision record is still true history, and
        what is missing is the pointer forward rather than the content.

        Whether the named replacement *exists* is deliberately not this check's
        business. A decision naming one has satisfied section 21.11; a target
        that resolves to nothing is `unresolved_relation`, which already owns
        it. Reporting both would make one problem look like two.
        """
        findings: list[Finding] = []
        for document in concepts:
            if document.frontmatter.get("lifecycle") != "superseded":
                continue
            if _relation_targets(document, SUPERSEDED_BY):
                continue
            title = document.frontmatter.get("title")
            name = title if isinstance(title, str) else str(document.path)
            findings.append(
                Finding(
                    code="superseded_decision_without_replacement",
                    message=f"{name!r} is superseded but names no replacement",
                    severity=Severity.WARNING,
                    path=document.path,
                    repair_hint=(
                        "add a `superseded_by` relation naming the decision that "
                        "replaced it (core/02 section 21.11); if the replacement was "
                        "itself deleted, or the decision lapsed rather than being "
                        "replaced, that is a judgement for the decision's owner and "
                        "this finding stands until they make it"
                    ),
                )
            )
        return findings

    def _check_decision_numbers(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """Two decisions in one folder sharing an ADR number (core/02 section 21.11).

        Creation allocates the next number, but two sessions can take the same
        one at the same moment and a hand-written file never asked. Reported
        on every record carrying the number, because which of them keeps it is
        the folder owner's call and neither is more wrong than the other.

        Only duplicates: a gap is legitimate, and so is the same number in two
        folders, since every workspace numbers its own. Read from the concepts
        already loaded, so this costs no walk of its own.
        """
        holders: dict[tuple[tuple[str, ...], DecisionNumber], list[StoredDocument]] = {}
        for document in concepts:
            if document.frontmatter.get("type") != "decision":
                continue
            number = number_in_filename(document.path.name)
            if number is not None:
                holders.setdefault((document.path.segments[:-1], number), []).append(document)
        findings: list[Finding] = []
        for (folder, number), documents in holders.items():
            if len(documents) < 2:
                continue
            for document in sorted(documents, key=lambda one: str(one.path)):
                others = ", ".join(
                    other.path.name for other in documents if other.path != document.path
                )
                findings.append(
                    Finding(
                        code="decision_number_duplicate",
                        message=(
                            f"{document.path.name} is {number.title_prefix}, and so is "
                            f"{others} in {'/'.join(folder)}"
                        ),
                        severity=Severity.WARNING,
                        path=document.path,
                        document_id=document.concept_id,
                        repair_hint=(
                            "renumber all but one to the next free number in the folder, "
                            "and correct the links that name it (core/02 section 21.11); "
                            "which keeps the number is the decision owner's call"
                        ),
                    )
                )
        return findings

    def _check_workspace_scope(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """Whether a document's declared scope agrees with where it sits.

        Three findings from one pass over the workspace manifests, because all
        three need the same map of id to directory:

        - `workspace_parent_mismatch` -- section 21's *parent/child workspace
          mismatch*, and one of the four contradictions section 23 is willing to
          call deterministic. A child's `parent` must be the workspace whose
          `Workspaces/` it sits in (core/01 section 6), and a top-level
          workspace must not claim one.
        - `orphan_workspace_content` -- section 32's *orphan workspace content*.
          A document naming a workspace that does not exist.
        - `misfiled_workspace_content` -- the same rule's other half: a document
          inside one workspace declaring a different one. This is what a bad
          move or a wrong `--workspace` produces, and the frontmatter alone
          looks valid.

        `schema/validation.py` checks both fields for *shape* only, so an id
        that resolves to nothing passes validation.

        **The misfiled half reads `10_Workspaces/` only.** Archived material
        keeps `workspace:` pointing at the workspace whose history it is while
        sitting under `90_Archive/Workspaces/<Name>/`, which core/01 section 13
        makes the specified arrangement for a rolled-up year. The orphan half
        has no such exemption: a workspace that does not exist is missing from
        wherever the document sits.
        """
        directories: dict[str, VaultPath] = {}
        titles: dict[str, str] = {}
        areas: dict[str, VaultPath] = {}
        for document in concepts:
            kind = document.frontmatter.get("type")
            if kind == "life_area":
                area = life_area_directory_of(document.path)
                if area is not None:
                    areas[str(document.concept_id)] = area
                continue
            if kind != "workspace":
                continue
            directory = workspace_directory_of(document.path)
            if directory is None:
                continue
            directories[str(document.concept_id)] = directory
            title = document.frontmatter.get("title")
            titles[str(document.concept_id)] = (
                title if isinstance(title, str) else str(document.path)
            )

        # A workspace under `20_Life/<Area>/Workspaces/` names the *area* as its
        # parent (core/03 section 30), and an area is not a workspace -- so the
        # parent rule resolves against both maps while the two `workspace:`
        # rules keep resolving against workspaces alone. Merging them there
        # would make an area id a legal `workspace:` target, which is the
        # distinction the separate `area` field exists to keep.
        parents = {**directories, **areas}

        findings: list[Finding] = []
        for document in concepts:
            findings.extend(self._parent_findings(document, parents))
            findings.extend(self._scope_findings(document, directories, titles))
        return findings

    def _parent_findings(
        self, document: StoredDocument, parents: Mapping[str, VaultPath]
    ) -> list[Finding]:
        if document.frontmatter.get("type") != "workspace":
            return []
        directory = workspace_directory_of(document.path)
        if directory is None or directory.root not in _WORKSPACE_ROOTS:
            return []

        segments = directory.segments
        nested = len(segments) > 2 and segments[-2] == CHILD_WORKSPACE_DIRECTORY
        expected = VaultPath(segments[:-2]) if nested else None

        declared = document.frontmatter.get("parent")
        declared_id = declared if isinstance(declared, str) else None
        actual = parents.get(declared_id) if declared_id else None

        if expected is None:
            if declared_id is None:
                return []
            problem = f"sits at the top of {VaultRoot.WORKSPACES} but declares a parent"
        elif declared_id is None:
            problem = f"sits inside {expected} but declares no parent"
        elif actual == expected:
            return []
        elif actual is None:
            problem = (
                f"sits inside {expected} but its parent is not a workspace "
                "or life area in this vault"
            )
        else:
            problem = f"sits inside {expected} but names {actual} as its parent"

        title = document.frontmatter.get("title")
        name = title if isinstance(title, str) else str(document.path)
        return [
            Finding(
                code="workspace_parent_mismatch",
                message=f"{name!r} {problem}",
                severity=Severity.WARNING,
                path=document.path,
                repair_hint=(
                    "a child workspace lives under its parent's Workspaces/ and names "
                    "that parent's id; correct the field or move the directory "
                    "(core/01 section 6)"
                ),
            )
        ]

    def _scope_findings(
        self,
        document: StoredDocument,
        directories: Mapping[str, VaultPath],
        titles: Mapping[str, str],
    ) -> list[Finding]:
        declared = document.frontmatter.get("workspace")
        if not isinstance(declared, str):
            return []
        title = document.frontmatter.get("title")
        name = title if isinstance(title, str) else str(document.path)

        if declared not in directories:
            return [
                Finding(
                    code="orphan_workspace_content",
                    message=f"{name!r} names a workspace that is not in this vault",
                    severity=Severity.WARNING,
                    path=document.path,
                    repair_hint=(
                        "point `workspace` at an existing workspace's id, or remove the "
                        "field if the document belongs to no workspace"
                    ),
                )
            ]

        directory = workspace_directory_of(document.path)
        if directory is None or directory.root not in _WORKSPACE_ROOTS:
            return []
        if directory == directories[declared]:
            return []
        return [
            Finding(
                code="misfiled_workspace_content",
                message=(f"{name!r} sits in {directory} but declares {titles[declared]!r}"),
                severity=Severity.WARNING,
                path=document.path,
                repair_hint=(
                    "move the document into the workspace it names, or correct "
                    "`workspace` to the one it sits in"
                ),
            )
        ]

    def _check_deprecated_in_use(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """A live document that depends on a retired one (section 32).

        Two definitions decide the rule, and both reuse something that already
        exists rather than inventing one.

        *Deprecated* is a lifecycle value meaning retired, across whichever
        vocabulary the type registers -- `_RETIRED_LIFECYCLES`. A *completed*
        plan is not retired; it finished, and depending on finished work is
        ordinary. An unrecognised value is never treated as retired, because
        core/02 section 20 requires unknown vocabularies to be tolerated.

        *Used as current* is a **subject** relation from a live document.
        `domain/relations.py` already draws that line: `NON_CORROBORATING` is
        the derived edges plus the lifecycle edges, because a status edge is
        not a claim about subject matter.

        Excluding it is what keeps this check honest. `superseded_by` and
        `closes` exist to point at retired material, and a Markdown link is a
        derived edge that prose uses constantly to explain how a decision was
        reached. Reading either would report every history section in the vault
        as a defect.
        """
        lifecycles: dict[str, str] = {}
        titles: dict[str, str] = {}
        for document in concepts:
            key = str(document.concept_id)
            lifecycle = document.frontmatter.get("lifecycle")
            if isinstance(lifecycle, str):
                lifecycles[key] = lifecycle
            title = document.frontmatter.get("title")
            titles[key] = title if isinstance(title, str) else str(document.path)

        findings: list[Finding] = []
        for document in concepts:
            if lifecycles.get(str(document.concept_id)) in _RETIRED_LIFECYCLES:
                # Old material may cite old material; only live use is a problem.
                continue
            title = document.frontmatter.get("title")
            name = title if isinstance(title, str) else str(document.path)
            for relation_type, target in _subject_relations(document):
                retired = lifecycles.get(str(target))
                if retired not in _RETIRED_LIFECYCLES:
                    continue
                findings.append(
                    Finding(
                        code="deprecated_content_in_use",
                        message=(
                            f"{name!r} declares {relation_type} -> "
                            f"{titles[str(target)]!r}, which is {retired}"
                        ),
                        severity=Severity.WARNING,
                        path=document.path,
                        repair_hint=(
                            "point the relation at whatever replaced it, or drop it if "
                            "the dependency ended with the document"
                        ),
                    )
                )
        return findings

    def _check_tag_hygiene(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """One tag spelled two ways (section 32's *near-duplicate tags*).

        Normalisation only: case folded, punctuation dropped, nothing else. Two
        tags collide when they are the same word written differently, and never
        otherwise.

        **Edit distance and similarity ratios are deliberately not used.**
        `milestone-1` and `milestone-10` are one edit apart and both correct,
        because a numbered series looks like a typo to any fuzzy measure, and
        short distinct tags such as `adr` and `api` are close too. A fuzzy rule
        produces many candidates and almost no real ones; normalisation has no
        false positives by construction.
        """
        spellings: dict[str, set[str]] = {}
        for document in concepts:
            for tag in _tags(document):
                key = _normalise_tag(tag)
                if key:
                    spellings.setdefault(key, set()).add(tag)

        return [
            Finding(
                code="near_duplicate_tags",
                message=(
                    "the same tag is spelled more than one way: "
                    + ", ".join(repr(tag) for tag in sorted(variants))
                ),
                severity=Severity.WARNING,
                repair_hint=(
                    "settle on one spelling and update the documents using the others; "
                    "two spellings split the same tag into two facets"
                ),
            )
            for _, variants in sorted(spellings.items())
            if len(variants) > 1
        ]

    def _check_adapters(self) -> list[Finding]:
        """Skills deployed to clients that no longer match the vault (section 21).

        Section 24 lists "Agent adapters" and "Skill sync" among what `doctor`
        reports. The detection lives in :meth:`AdapterService.diagnose`, behind
        `adapters doctor`, and is reported here as well: a person asking what is
        wrong should get one answer, and the section 22 scheduler can only
        surface what `doctor` reports.

        `adapters doctor` keeps the detailed per-client view and `adapters sync`
        keeps being the verb that fixes it. Only the reporting is shared.

        **The two codes stay apart because the responses differ.** A Skill that
        is stale or not deployed is fixed by `sync --apply`. A *collision* is a
        Never4gA name already held by something Never4gA does not own, and
        core/09 section 11 forbids overwriting it -- so its hint deliberately names no
        command, which is also what keeps `repair` from treating it as
        mechanical.

        **A Skill that was never deployed is not drift.** The rule is *stale
        adapter deployment*: something deployed that no longer matches. Never
        having deployed is the state every new install starts in, and
        `adapters sync` is how a person leaves it. Reporting it would open a
        fresh vault with a warning per Skill per client, and a warning nobody
        can clear teaches you to stop reading the ones you can.
        `adapters doctor` still lists what is not deployed, because saying so
        is its whole job.

        A client that is not installed is not reported either. Not having Codex
        on this machine is not a problem with the vault.
        """
        if self._adapters is None:
            return []
        findings: list[Finding] = []
        for finding in self._adapters:
            where = f"{finding.name!r} on {finding.client_id}"
            if finding.code == "collision":
                findings.append(
                    Finding(
                        "adapter_collision",
                        f"{where} collides with something Never4gA does not own: {finding.detail}",
                        Severity.WARNING,
                        None,
                        "decide who owns that name; core/09 section 11 forbids overwriting "
                        "unmanaged capability, so nothing will resolve this for you",
                    )
                )
            elif finding.code in {"stale", "to_remove"}:
                findings.append(
                    Finding(
                        "stale_adapter_deployment",
                        f"{where} does not match the vault: {finding.detail}",
                        Severity.WARNING,
                        None,
                        "run `never4ga adapters sync --apply`",
                    )
                )
        return findings

    def _check_connection_health(self) -> list[Finding]:
        """A configured tracker that does not answer (section 21).

        **Off unless asked.** `doctor` runs on the section 22 schedule and in
        CI, and a check that reaches the network would make a diagnosis depend
        on somebody else's server being up. A tracker being
        down is not a problem with the vault.

        So the composition root probes when `--check-connections` says to, and
        passes what it found. Absence here means *nobody asked*, and unlike the
        index or the adapters it does **not** make the run partial -- treating
        an opt-in check's absence as partial would stop every ordinary `doctor`
        recording anything.

        What makes that safe is :attr:`Finding.ephemeral`: nothing about
        reachability reaches the ledger, so no later run can wrongly resolve it.

        **Why the default is silence rather than a cached answer.** There is no
        "last reachable" fact to report. The cache records ``fetched_at`` on
        work items, which says *we cached something then*, not *the tracker
        answered then*, and offering one as the other would be a stale answer
        passing as a current one.
        """
        if self._connection_health is None:
            return []
        return [
            Finding(
                "connection_unavailable",
                f"the connection {name!r} did not answer"
                + (f": {health.detail}" if health.detail else ""),
                Severity.WARNING,
                None,
                "check the tracker and the stored token; nothing in the vault "
                "depends on it being reachable (core/05 section 19)",
                None,
                ephemeral=True,
            )
            for name, health in sorted(self._connection_health.items())
            if not health.available
        ]

    def _check_repositories(self) -> list[Finding]:
        """Paths the mapped repository's agent files name, that are gone.

        `AGENTS.md` and `CLAUDE.md` are the two documents every session is
        required to read first, and they live in the repository rather than
        the vault. The check runs inside `doctor` rather than behind its own
        verb, because a check nobody has to remember to run is the point.

        **What it may conclude is narrow**: whether a linked path exists. It
        makes no judgement about what the file says.

        Three roots, because agent files legitimately name paths in all three:
        the repository, the workspace, and the vault root (for example
        `50_System/system.md`). Fewer roots would report correct paths as
        broken.

        A mapped repository that is not on disk is one finding rather than a
        finding per line, so a machine where the repository is not checked out
        is reported rather than silently checked less.
        """
        if self._repositories is None:
            return []
        findings: list[Finding] = []
        for mapping in self._mappings:
            root = mapping.repository_root
            if root is None:
                continue
            if not self._repositories.exists(root, "."):
                findings.append(
                    Finding(
                        code="repository_absent",
                        message=(
                            f"{mapping.workspace_path} is mapped to {root}, "
                            "which is not on this machine"
                        ),
                        severity=Severity.WARNING,
                        path=mapping.workspace_path,
                        repair_hint=(
                            "check the repository out there, or run "
                            "`never4ga workspace unmap` if it has moved"
                        ),
                    )
                )
                continue
            for name in AGENT_INSTRUCTION_FILES:
                text = self._repositories.read_text(root, name)
                if text is None:
                    continue
                for reference in _path_references(text):
                    if self._resolves(reference, root, mapping.workspace_path):
                        continue
                    findings.append(
                        Finding(
                            code="repository_reference_broken",
                            message=f"{name} names {reference!r}, which does not exist",
                            severity=Severity.WARNING,
                            path=mapping.workspace_path,
                            repair_hint=(
                                "correct the path, or remove the reference if what it "
                                "named is gone; an agent reads this file first"
                            ),
                        )
                    )
            findings.extend(self._check_pointer(mapping, root))
        return findings

    def _check_pointer(self, mapping: WorkspaceMapping, root: PurePath) -> list[Finding]:
        """Whether the generated repository pointer is where it should be, and current.

        Three checks, and what they can and cannot prove is worth stating.

        **What they prove.** A mapped repository that is not excepted
        (`details/agent-instruction-layering.md` section 8) holds a pointer;
        that pointer still matches what `adapters sync` would write, byte for
        byte (section 7); and the repository's `.gitignore` carries the block
        that keeps agent files out of the index.

        **What they cannot prove.** That no agent file is *tracked*. Section
        5.3 makes a committed agent file a reportable condition, and
        answering it needs `git ls-files` -- a subprocess this locator
        deliberately does not run, because `repository_root` is a loop over
        parent directories precisely so it works on a machine with no `git`
        installed. So the ignore block stands as the mechanical proxy: it
        catches every repository that *would* start tracking one, and misses a
        file already tracked before the block arrived. Closing that gap needs a
        decision about running Git rather than a wider rule here.

        Only `AGENTS.md` is regenerated for comparison. Which client-specific
        names should also be occupied depends on the descriptors installed on
        this machine, which is a composition-root fact rather than a vault one;
        `adapters sync` reports those.
        """
        if self._repositories is None or mapping.ships_agent_contract:
            return []
        findings: list[Finding] = []
        current = self._repositories.read_text(root, CANONICAL_FILENAME)
        if current is None:
            findings.append(
                Finding(
                    code="agent_pointer_missing",
                    message=(
                        f"{root} is mapped and has no {CANONICAL_FILENAME}; "
                        "a session starting there gets no pointer to its workspace"
                    ),
                    severity=Severity.WARNING,
                    path=mapping.workspace_path,
                    repair_hint="run `never4ga adapters sync --apply --repositories`",
                )
            )
        else:
            expected = pointer_body(mapping, documentation_in(self._repositories, root))
            if current != expected:
                findings.append(
                    Finding(
                        code=(
                            "agent_pointer_modified"
                            if is_generated(current)
                            else "agent_file_unmanaged"
                        ),
                        # Naming both causes is the whole point. A generated
                        # pointer that differs from the template has two
                        # possible histories -- somebody edited the file, or
                        # the template moved and nothing regenerated it -- and
                        # they call for opposite reactions. Reported as bare
                        # drift, it would send a reader looking for an edit
                        # that may never have happened. The remedy is the same
                        # either way, which is why saying both costs nothing.
                        message=(
                            f"{root}/{CANONICAL_FILENAME} differs from what `adapters sync` "
                            "would write -- either it was edited here, or the generated text "
                            "moved on and this copy was never refreshed"
                            if is_generated(current)
                            else f"{root}/{CANONICAL_FILENAME} was not written by Never4gA"
                        ),
                        severity=Severity.WARNING,
                        path=mapping.workspace_path,
                        repair_hint=(
                            "run `never4ga adapters sync --apply --repositories` to regenerate it, "
                            "or `--adopt` as well if it was written by hand"
                        ),
                    )
                )
        ignored = self._repositories.read_text(root, ".gitignore")
        if ignored is None or GITIGNORE_BEGIN not in ignored:
            findings.append(
                Finding(
                    code="agent_files_not_ignored",
                    message=(
                        f"{root}/.gitignore does not ignore agent instruction files, "
                        "so a generated pointer can be committed"
                    ),
                    severity=Severity.WARNING,
                    path=mapping.workspace_path,
                    repair_hint="run `never4ga adapters sync --apply --repositories`",
                )
            )
        return findings

    def _resolves(self, reference: str, root: PurePath, workspace: VaultPath) -> bool:
        """Against the repository, the workspace, then the vault root.

        In that order because it is the order of likelihood, not because the
        roots mean different things -- a path that resolves anywhere is fine.

        ``workspace`` is a mapping's ``workspace_path``, which names the
        *manifest* rather than the directory holding it. Joining a reference
        onto it directly would produce `.../workspace.md/Context/start-here.md`,
        which resolves to nothing, so the directory is taken first.
        """
        if reference.startswith("~"):
            # Machine-wide agent conventions live at `~/.claude/CLAUDE.md`, and
            # an agent file is right to name them. None of the three roots below
            # is home, so without this every such reference would be reported
            # broken while the file sits on disk.
            #
            # Expansion is a pure path operation; the question of whether the
            # file is *there* still goes through the locator, so this service
            # reads the filesystem through a port like everything else.
            if self._repositories is None:
                return False
            expanded = Path(reference).expanduser()
            return self._repositories.exists(expanded.parent, expanded.name)
        if self._repositories is not None and self._repositories.exists(root, reference):
            return True
        segments = tuple(part for part in reference.split("/") if part)
        directory = workspace_directory_of(workspace)
        if directory is not None and self._files.exists(
            VaultPath((*directory.segments, *segments))
        ):
            return True
        return self._files.exists(VaultPath(segments))

    def _check_skills(self) -> list[Finding]:
        """Canonical Skills the vault seeded and the product has since improved.

        `adapters doctor` compares clients against the vault and is right to
        call a uniformly stale set "in sync" -- every copy matches its source.
        The drift is one level up, between the vault and the build, and this is
        where it becomes visible.

        Reported, never repaired, like everything else here: refreshing a Skill
        rewrites a file in a Git-backed vault the user may have opinions about.
        """
        findings: list[Finding] = []
        for status in SkillLibrary(self._files).status():
            if status.state is not SkillState.OUTDATED:
                continue
            findings.append(
                Finding(
                    code="skill_is_outdated",
                    # Version-to-version reads as a no-op when the versions
                    # match, which is a real case: the body is what is compared,
                    # not the version, and a finding phrased as "at 0.3.0; this
                    # build ships 0.3.0" argues against itself.
                    #
                    # But `body_moved_on` alone is not that case. A deliberate
                    # edit moves the body *and* bumps the version, and then the
                    # two versions differ. Only say "both" when both is true.
                    message=(
                        f"the canonical Skill '{status.name}' no longer matches the text "
                        f"this build ships (both at {status.shipped_version}; the version "
                        "is not what changed)"
                        if status.body_moved_on and status.vault_version == status.shipped_version
                        else (
                            f"the canonical Skill '{status.name}' is at "
                            f"{status.vault_version}; this build ships "
                            f"{status.shipped_version}"
                        )
                    ),
                    severity=Severity.WARNING,
                    path=VaultPath.parse(f"50_System/Skills/{status.name}/SKILL.md"),
                    repair_hint="run `never4ga skills refresh --apply`",
                )
            )
        findings.extend(
            Finding(
                code="template_is_outdated",
                message=(f"the template '{status.name}' is not the one this build ships"),
                severity=Severity.WARNING,
                path=VaultPath.parse(f"50_System/Templates/{status.name}"),
                repair_hint="run `never4ga skills refresh --apply`",
            )
            for status in SkillLibrary(self._files).template_status()
            if status.state is SkillState.OUTDATED
        )
        return findings

    def _check_connections(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """Secrets that have reached the vault (core/03 section 16).

        An ERROR rather than a warning: a token in Git-backed Markdown has
        already been committed and pushed by the time anybody reads this, so
        there is no degree of it that is merely untidy. Reported and never
        repaired -- rewriting the user's file would destroy the evidence of
        what leaked, and core/02 makes validation always report.
        """
        return [
            Finding(
                finding.code,
                finding.detail,
                Severity.ERROR,
                path=finding.path,
                repair_hint=(
                    "remove the key from the document, rotate the credential at the "
                    "provider, and store the new value in the secret store"
                ),
            )
            for finding in ConnectionRegistry(self._documents).findings(concepts)
        ]

    def _check_index(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """What the derived index knows, if there is one.

        details/data-indexing-maintenance.md section 24 lists Index, FTS, and
        broken links/relations among what `doctor` reports. All of it is a
        *warning*: a stale or absent index is a derived-state problem, and the
        vault itself is no less correct for it (core/06 section 2).

        ``concepts`` is here so a link or a relation can be reported against the
        document that holds it. The index records a source by id; a reader needs
        a path, and there is no cheaper way to turn one into the other.
        """
        if self._index is None:
            return []

        where = {concept.concept_id: concept.path for concept in concepts}

        findings: list[Finding] = []
        if self._index.is_stale:
            findings.append(
                Finding(
                    "index_is_stale",
                    f"the index is behind the vault: {len(self._index.new)} new, "
                    f"{len(self._index.changed)} changed, {len(self._index.missing)} missing",
                    Severity.WARNING,
                    None,
                    "run `never4ga index` (reads report staleness, they never repair it)",
                )
            )
        findings.extend(
            Finding(
                "broken_link",
                (
                    f"{link.target_path} does not exist"
                    if link.target_path is not None
                    else f"no document is named {link.target_name!r}"
                ),
                Severity.WARNING,
                where.get(link.source),
                "fix the link, or create the document it points at",
                link.source,
            )
            for link in self._index.broken_links
        )
        findings.extend(
            Finding(
                "unresolved_relation",
                f"{relation.source} declares {relation.relation_type} -> {relation.target}, "
                "which is not a document in this vault",
                Severity.WARNING,
                where.get(relation.source),
                "correct the relation target (core/02 section 17.1)",
                relation.source,
            )
            for relation in self._index.unresolved_relations
        )
        return findings

    def _foreign_material(self) -> frozenset[str]:
        """The top-level directories `init` registered as foreign material.

        The registration is the exemption, never the location (core/01
        section 1). A
        directory nobody registered is judged like anywhere else, and
        :meth:`_check_structure` says so.
        """
        manifest = self._documents.get_by_path(SYSTEM_MANIFEST)
        if manifest is None:
            return frozenset()
        return registered_foreign_material(manifest.frontmatter)

    @staticmethod
    def _is_foreign(path: VaultPath, foreign: frozenset[str]) -> bool:
        return bool(path.segments) and path.segments[0] in foreign

    def _check_structure(self, foreign: frozenset[str]) -> list[Finding]:
        findings: list[Finding] = []
        for raw in _REQUIRED_DIRECTORIES:
            path = VaultPath.parse(raw)
            if not self._files.is_directory(path):
                findings.append(
                    Finding(
                        "missing_directory",
                        f"{raw} does not exist",
                        Severity.ERROR,
                        path,
                        "run `never4ga init` to restore the canonical structure "
                        "(core/01 section 1)",
                    )
                )

        for path in (ROOT_INDEX, HOME):
            if not self._files.exists(path):
                findings.append(
                    Finding(
                        "missing_root_document",
                        f"{path} does not exist",
                        Severity.WARNING,
                        path,
                        "run `never4ga init`; it will not touch anything already there",
                    )
                )

        # The scratchpad `init` writes. A vault that lacks one would otherwise
        # never say so, and the person it exists for -- jotting a line in
        # Obsidian -- would never see it. Reported here rather than created at
        # startup: a context read that writes to the vault is a write nobody
        # asked for.
        scratchpad = VaultPath.parse(f"{VaultRoot.INBOX.value}/{INBOX_SCRATCHPAD_NAME}")
        if not self._files.exists(scratchpad):
            findings.append(
                Finding(
                    "inbox_scratchpad_missing",
                    f"{scratchpad} does not exist, so there is nowhere to jot a thought "
                    "down for a later session to file",
                    Severity.WARNING,
                    scratchpad,
                    "run `never4ga repair --apply`; it creates the scratchpad and leaves "
                    "anything already there alone",
                )
            )

        # core/01 section 1: a top-level directory that is neither a root nor registered
        # was added by hand after `init`. Reported rather than quietly treated
        # as foreign, so a later step never indexes somebody's directory on the
        # strength of where it sits. `init` (and so `repair --apply`) registers
        # it and touches nothing inside.
        roots = {str(root) for root in VaultRoot}
        for directory in sorted(self._files.iter_directories()):
            if len(directory.segments) != 1:
                continue
            name = directory.segments[0]
            if name in roots or name in foreign:
                continue
            findings.append(
                Finding(
                    "foreign_material_unregistered",
                    f"{name}/ is neither a root nor registered as foreign material, so "
                    "nothing can tell whether it is yours or a topical root somebody "
                    "invented (core/01 section 1)",
                    Severity.WARNING,
                    directory,
                    "run `never4ga init`; it registers the directory as foreign material "
                    "and touches nothing inside it",
                )
            )

        findings.extend(self._check_directory_indexes())
        return findings

    def _check_navigation(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """An `index.md` whose navigation no longer matches its directory.

        `core/01` section 4 reserves `index.md` for progressive disclosure and
        section 6 keeps concept frontmatter out of it, which is what makes the
        navigation derivable: no identity, no relations, no judgement.

        A hand-maintained index drifts: it names directories that no longer
        exist and misses ones that do, and nothing notices unless something
        compares the index to the directory under it.

        Only the block between the markers is compared. The prose around it is a
        person's orientation rather than navigation, and this rule has no opinion
        about it -- see :mod:`never4ga.services.navigation`.

        **Only directories that already have an index are checked.** Inventing
        one everywhere would report a finding per directory on every vault,
        a warning nobody asked for. `missing_index`
        already covers the roots that ought to have one.
        """
        findings = [
            Finding(
                "navigation_is_outdated",
                f"{index} does not navigate what is in its directory",
                Severity.WARNING,
                index,
                "run `never4ga repair --apply`",
            )
            for index in navigation_outdated(self._files, concepts)
        ]
        # One finding with a count, never one per directory. A vault that has
        # never had per-directory navigation is a single fact about the vault;
        # said once per directory it buries everything else `doctor` reports.
        missing = navigation_missing(self._files, concepts)
        if missing:
            shown = ", ".join(str(path) for path in missing[:3])
            more = f", and {len(missing) - 3} more" if len(missing) > 3 else ""
            findings.append(
                Finding(
                    "navigation_is_missing",
                    f"{len(missing)} directories hold documents and have no index.md, "
                    f"so a link to them resolves to nothing: {shown}{more}",
                    Severity.WARNING,
                    None,
                    "run `never4ga repair --apply`",
                )
            )
        return findings

    def _check_configuration(self) -> list[Finding]:
        """A machine-local config key that no longer does anything.

        Never4gA reads that file and never writes it (core/05 section 9), so
        it cannot delete a dead block -- but a block that *looks* live is worse
        than none: it misleads whoever reads the file into thinking the setting
        still has an effect.

        The settings arrive as an argument: reading the file belongs to a
        composition root, and a service that opened its own config would stop
        being a function of what it was given.

        No path, deliberately. The config sits outside the vault, and a finding
        that pointed into it would send `repair` after a document that is not
        there -- which is also why the hint names a person rather than a verb.
        """
        return [
            Finding(
                "config_names_a_retired_setting",
                f"machine config still sets `{setting.key}`: {setting.reason}",
                Severity.WARNING,
                None,
                "edit the config yourself; Never4gA never writes it",
            )
            for setting in self._configuration
        ]

    def _check_workspace_views(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        """A workspace dashboard whose views no longer match what it holds.

        The views are derivable -- one per kind of content the
        workspace actually has -- so like navigation they live in a managed
        block, and a block that disagrees with the corpus is drift `doctor`
        can see. Prose outside the markers, including a hand-written view
        somebody chose to keep, is not compared and not touched.

        A manifest with no block at all is one fact about the vault, reported
        once with a count. A vault whose workspaces predate the block has many
        in that state, and one warning per workspace would bury the rest of the
        report, as with `navigation_is_missing`.
        """
        findings = [
            Finding(
                "workspace_views_outdated",
                f"{manifest} does not show what its workspace holds",
                Severity.WARNING,
                manifest,
                "run `never4ga repair --apply`",
            )
            for manifest in views_outdated(self._files, concepts)
        ]
        missing = views_missing(self._files, concepts)
        if missing:
            shown = ", ".join(str(path) for path in missing[:3])
            more = f", and {len(missing) - 3} more" if len(missing) > 3 else ""
            findings.append(
                Finding(
                    "workspace_views_missing",
                    f"{len(missing)} workspace dashboards have no generated views, "
                    f"so their content is reachable only through the folder tree: "
                    f"{shown}{more}",
                    Severity.WARNING,
                    None,
                    "run `never4ga repair --apply`",
                )
            )
        return findings

    def _check_directory_indexes(self) -> list[Finding]:
        """core/01 section 4: each root offers progressive disclosure."""
        return [
            Finding(
                "missing_index",
                f"{root} has no {RESERVED_INDEX}",
                Severity.WARNING,
                VaultPath.parse(f"{root}/{RESERVED_INDEX}"),
                "an index.md gives humans and agents a first hop into the "
                "directory (core/01 section 4)",
            )
            for root in VaultRoot
            if self._files.is_directory(VaultPath.parse(str(root)))
            and not self._files.exists(VaultPath.parse(f"{root}/{RESERVED_INDEX}"))
        ]

    def _check_identity(
        self, findings: list[Finding], concepts: Sequence[StoredDocument]
    ) -> ConceptId | None:
        """core/05 section 6: exactly one document carries the vault identity.

        The manifest is still fetched by path rather than found in ``concepts``,
        and deliberately: one read is free, and deriving it from the walk would
        tie "does this vault have an identity" to whether the walk's eligibility
        rules happened to admit that one file. The strays come from ``concepts``.
        """
        manifest = self._documents.get_by_path(SYSTEM_MANIFEST)
        if manifest is None:
            findings.append(
                Finding(
                    "no_vault_identity",
                    f"{SYSTEM_MANIFEST} is missing; this vault has no stable identity",
                    Severity.ERROR,
                    SYSTEM_MANIFEST,
                    "run `never4ga init` (core/05 section 6)",
                )
            )
            return None

        strays = [
            document.path
            for document in concepts
            if document.frontmatter.get("type") == "system_manifest"
            and document.path != SYSTEM_MANIFEST
        ]
        findings.extend(
            Finding(
                "duplicate_vault_identity",
                f"{path} also claims to be the vault manifest",
                Severity.ERROR,
                path,
                "a vault has exactly one system_manifest, at "
                f"{SYSTEM_MANIFEST} (core/02 section 21.20)",
            )
            for path in strays
        )
        return manifest.concept_id

    def _check_duplicates(self) -> list[Finding]:
        """core/02 section 32: duplicate UUIDs are a first-class concern."""
        if not isinstance(self._documents, IntegrityReportingStore):
            return []
        findings: list[Finding] = []
        for concept_id, paths in self._documents.duplicate_ids().items():
            findings.extend(
                Finding(
                    "duplicate_id",
                    f"{path} shares id {concept_id} with "
                    f"{', '.join(str(other) for other in paths if other != path)}",
                    Severity.ERROR,
                    path,
                    "an id is unique within a vault; give one document a fresh "
                    "UUIDv7 (core/02 section 5.1)",
                )
                for path in paths
            )
        return findings

    def _check_untracked(self, foreign: frozenset[str]) -> list[Finding]:
        """Hand-written Markdown that is waiting to be adopted.

        A warning, because prose is legitimate vault content and whether it
        should become a concept is the writer's call -- which is also exactly
        why this is excluded from `repair --apply`: choosing a type is a
        judgement, and two people reading the finding would not write the
        same fix. The Inbox is left out because a capture is unprocessed by
        definition and has its own accounting; the store already leaves out
        reserved navigation and the foreign-format islands.
        """
        if not isinstance(self._documents, IntegrityReportingStore):
            return []
        lost = self._index.missing_identities if self._index is not None else {}
        findings: list[Finding] = []
        for path in self._documents.untracked():
            if path.segments[0] == VaultRoot.INBOX:
                continue
            identity = lost.get(path)
            if identity is not None:
                # The index recorded a concept here and the file is still
                # here, so this is not a note nobody adopted -- it is one
                # whose frontmatter something removed. An external tool that
                # rewrites a document wholesale does exactly that. The
                # id cannot be re-minted: a new one is a different concept,
                # and every link by identity would stay broken.
                findings.append(
                    Finding(
                        "identity_lost",
                        f"{path} was concept {identity} and now carries no "
                        "frontmatter; something rewrote the file and dropped it",
                        Severity.ERROR,
                        path,
                        "recover the frontmatter block from git history -- a fresh "
                        "id would be a different concept and would not mend a "
                        "single link (core/02 section 5.1)",
                    )
                )
                continue
            if self._is_foreign(path, foreign):
                findings.append(_untracked_foreign(path, "carries no frontmatter"))
                continue
            findings.append(
                Finding(
                    "untracked_document",
                    f"{path} carries no frontmatter, so it is invisible to index, "
                    "search and every Context Pack",
                    Severity.WARNING,
                    path,
                    "run `never4ga adopt` on it to make it a tracked concept where "
                    "it sits, or leave it: prose is legitimate vault content",
                )
            )
        return findings

    def _check_unreadable(self, foreign: frozenset[str]) -> list[Finding]:
        """Files that look like concepts and cannot be read as one.

        Inside foreign material there is no concept to be broken (core/02
        section 3.3): a note whose frontmatter declares no `id`, or an `id` that
        is Jekyll's rather than ours, is the writer's and is simply not adopted
        yet. Reporting it as an error would make a freshly registered pile
        unhealthy before its owner had decided anything.
        """
        if not isinstance(self._documents, IntegrityReportingStore):
            return []
        findings: list[Finding] = []
        for problem in self._documents.problems():
            if self._is_foreign(problem.path, foreign):
                findings.append(
                    _untracked_foreign(
                        problem.path, "carries frontmatter of its own but no Never4gA id"
                    )
                )
                continue
            findings.append(
                Finding(
                    problem.code,
                    f"{problem.path}: {problem.detail}",
                    Severity.ERROR,
                    problem.path,
                    "the file is left exactly as it is; repair it explicitly (core/02 section 23)",
                )
            )
        return findings

    def _check_schema(self, concepts: Sequence[StoredDocument]) -> list[Finding]:
        known = {document.concept_id for document in concepts}
        domains = registered_domains(concepts)
        findings: list[Finding] = []
        for document in concepts:
            report: ValidationReport = validate_document(
                document.path,
                document.frontmatter,
                level=self._level,
                known_ids=known,
                domains=domains,
            )
            findings.extend(
                Finding(
                    issue.code,
                    f"{report.path}: {issue.message}",
                    issue.severity,
                    report.path,
                    issue.repair_hint,
                )
                for issue in report.issues
            )
        return findings


#: Lifecycle values that mean the work is not finished. core/02 section 21.9
#: gives a plan `draft | active | completed | abandoned`; the first two are open
#: and `proposed` is here because a decision uses it the same way. Anything
#: unrecognised is left alone: section 20 requires unknown values to be
#: tolerated, and guessing would report a vault for having its own vocabulary.
_OPEN_LIFECYCLES: Final = frozenset({"draft", "active", "proposed"})

#: Lifecycle values meaning the document has been retired, across every
#: vocabulary in the type registry. `completed`, `complete`, `done` and
#: `achieved` are deliberately absent: finished is not retired, and depending on
#: finished work is ordinary.
#:
#: `deprecated` is not in any registered vocabulary today. It is here because it
#: is the word core/02 section 32 uses for the rule, and a vault that adopts it
#: should not have to be told twice.
_RETIRED_LIFECYCLES: Final = frozenset(
    {"superseded", "abandoned", "cancelled", "rejected", "retired", "deprecated"}
)


def _closes(document: StoredDocument) -> list[ConceptId]:
    """Every plan this document declares finished."""
    return _relation_targets(document, CLOSES)


#: The two files every session is required to read first. `AGENTS.md` is the
#: authoritative one and `CLAUDE.md` is subordinate to it
#: (`details/agent-instruction-layering.md` section 8); both are checked,
#: because both are read.
AGENT_INSTRUCTION_FILES: Final = ("AGENTS.md", "CLAUDE.md")

#: A path is worth checking when it is a Markdown link target, and only then.
#:
#: A backticked path is often named *in order to say it is gone*, and telling
#: that from a pointer is reading English, which core/07 section 1 keeps out of
#: the deterministic path. `[text](path)` is somewhere to go; a backticked path
#: may be prose about one. The cost is that a genuine pointer written as a code
#: span is not checked. An author who wants a path checked links it.
_LINK_TARGET: Final = re.compile(r"\]\(([^)\s]+)\)")

#: Extensions that make a token a file rather than a specification reference.
#: `core/05` names a document in the workspace's Architecture and is not a path;
#: `Context/start-here.md` is.
_PATH_SUFFIXES: Final = (
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".sh",
    ".cfg",
    ".ini",
)

#: Glob metacharacters. `Logs/*.md` and `tests/contracts/*_contract.py` are
#: patterns rather than paths.
#:
#: Angle brackets join them for the same reason:
#: `inventory/host_vars/<hostname>.yml` tells a reader where to put a file whose
#: name depends on the host. It is a
#: template wearing different punctuation.
_GLOB_CHARACTERS: Final = frozenset("*?[]<>")


def _path_references(text: str) -> list[str]:
    """Every path-shaped token the text names, deduplicated and ordered.

    Conservative by construction: a token must be a Markdown link target,
    contain a directory separator, end in a known file suffix, carry no
    whitespace and no glob character. Link syntax is the author saying "this is
    a place to go" rather than "this is a path I am talking about" -- see the
    note on :data:`_LINK_TARGET` for why the backticked form came out.
    """
    found: list[str] = []
    seen: set[str] = set()
    candidates = [match.group(1).strip() for match in _LINK_TARGET.finditer(text)]
    for candidate in candidates:
        if candidate in seen:
            continue
        if "://" in candidate or candidate.startswith("#"):
            continue
        if "/" not in candidate or " " in candidate:
            continue
        if not candidate.endswith(_PATH_SUFFIXES):
            continue
        if _GLOB_CHARACTERS & set(candidate):
            continue
        seen.add(candidate)
        found.append(candidate)
    return found


def _tags(document: StoredDocument) -> list[str]:
    """Every tag on this document, ignoring a malformed list."""
    tags = document.frontmatter.get("tags")
    if not isinstance(tags, Sequence) or isinstance(tags, str):
        return []
    return [tag for tag in tags if isinstance(tag, str)]


def _normalise_tag(tag: str) -> str:
    """Case and punctuation removed, so one word spelled two ways collides.

    Deliberately not a similarity measure. See `_check_tag_hygiene`.
    """
    return "".join(character for character in tag.lower() if character.isalnum())


def _subject_relations(document: StoredDocument) -> list[tuple[str, ConceptId]]:
    """Every relation this document asserts *about subject matter*.

    `NON_CORROBORATING` is the exclusion: the derived edges, because nobody
    wrote them, and the lifecycle edges, because what they assert is a status
    rather than a dependency.
    """
    relations = document.frontmatter.get("relations")
    if not isinstance(relations, Sequence) or isinstance(relations, str):
        return []
    found: list[tuple[str, ConceptId]] = []
    for relation in relations:
        if not isinstance(relation, Mapping):
            continue
        relation_type = relation.get("type")
        if not isinstance(relation_type, str) or relation_type in NON_CORROBORATING:
            continue
        try:
            found.append((relation_type, ConceptId.parse(str(relation.get("target")))))
        except IdentityError:
            continue
    return found


def _relation_targets(document: StoredDocument, relation_type: str) -> list[ConceptId]:
    """Every target this document names under one relation type (core/02 section 17).

    Relations are a sequence of ``{type, target}`` mappings. A malformed entry
    is skipped rather than raised on: `validate` already reports it, and a
    health check that raised on one would report nothing else about the vault.
    """
    relations = document.frontmatter.get("relations")
    if not isinstance(relations, Sequence) or isinstance(relations, str):
        return []
    targets: list[ConceptId] = []
    for relation in relations:
        if not isinstance(relation, Mapping) or relation.get("type") != relation_type:
            continue
        try:
            targets.append(ConceptId.parse(str(relation.get("target"))))
        except IdentityError:
            continue
    return targets


def _stamp(moment: datetime) -> str:
    """The instant, to the minute. Seconds add nothing to a staleness report."""
    return moment.strftime("%Y-%m-%d %H:%M %Z").strip()


def _untracked_foreign(path: VaultPath, what: str) -> Finding:
    """A note in foreign material, waiting to be adopted (core/02 section 3.3).

    Not invisible: search and a focused pack reach it by its path. What it
    lacks is everything an identity brings -- a type, relations, a place in a
    startup pack -- and `adopt` is how it gets them.
    """
    return Finding(
        "untracked_document",
        f"{path} {what}: search and a focused pack find it by its path, but it has "
        "no type, no relations and no place in a startup pack until it is adopted",
        Severity.WARNING,
        path,
        "run `never4ga adopt` on it with `--type` to make it a tracked concept in its "
        "type's home, or leave it: it is yours, and nothing in it is rewritten",
    )


def _workspace_of(document: StoredDocument) -> ConceptId | None:
    value = document.frontmatter.get("workspace")
    try:
        return ConceptId.parse(str(value)) if value else None
    except IdentityError:
        return None


def _required_standards_for(
    lineage: set[ConceptId], standards: list[StoredDocument]
) -> list[StoredDocument]:
    """The standards a startup in `lineage` reads in full, decided as the pack decides."""
    applicable = [
        document
        for document in standards
        if (owner := _workspace_of(document)) is None or owner in lineage
    ]
    required = required_standards(
        StandardFacts(
            concept_id=str(document.concept_id),
            vault_wide=document.path.root == VaultRoot.SYSTEM,
            required_reading=document.frontmatter.get("required_reading"),
            excepts=excepted_targets(document.frontmatter.get("relations")),
        )
        for document in applicable
    )
    return [document for document in applicable if str(document.concept_id) in required]


def _parent_pages(
    page: StoredDocument, workspaces: Mapping[ConceptId, StoredDocument]
) -> list[StoredDocument]:
    """The workspace pages of a workspace's parent chain, nearest first."""
    chain: list[StoredDocument] = []
    seen = {page.concept_id}
    current = page
    while True:
        parent = _parent_of(current)
        if parent is None or parent in seen or parent not in workspaces:
            return chain
        seen.add(parent)
        current = workspaces[parent]
        chain.append(current)


def _parent_of(page: StoredDocument) -> ConceptId | None:
    value = page.frontmatter.get("parent")
    try:
        return ConceptId.parse(str(value)) if value else None
    except IdentityError:
        return None


def _occurred(document: StoredDocument) -> str:
    """As `structural.occurred_at`: ISO strings with an offset sort unparsed."""
    for name in ("occurred_at", "created_at"):
        value = document.frontmatter.get(name)
        if value:
            return str(value)
    return ""


def _title_of(document: StoredDocument) -> str:
    title = document.frontmatter.get("title")
    return title if isinstance(title, str) else str(document.path)
