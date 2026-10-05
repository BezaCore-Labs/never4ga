"""Creating vault content (core/01, core/02 section 21, core/03).

Each service creates the smallest correct thing. core/03 section 29 is explicit
that workspace creation "MUST NOT create a giant tree of empty folders merely
because they exist logically", so only `workspace.md` and `index.md` are written
and the semantic directories appear on first use.

Everything created here is validated before it is written. A service that can
produce an invalid concept is a service that will.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Final

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import Never4gaError, VaultIntegrityError
from never4ga.indexing import written_title
from never4ga.layout import (
    AREA_MANIFEST,
    CHILD_WORKSPACE_DIRECTORY,
    DOMAIN_REGISTRY,
    RESERVED_INDEX,
    RESERVED_LOG,
    SYSTEM_MANIFEST,
    WORKSPACE_MANIFEST,
    DocumentRole,
    VaultRoot,
    is_foreign_note,
    life_area_directory_of,
    registered_foreign_material,
    role_of,
    workspace_directory_of,
)
from never4ga.layout.decision_numbers import (
    DecisionNumber,
    next_number,
    number_in_filename,
    number_in_title,
)
from never4ga.layout.structure import LOGS_DIRECTORY, archived_counterpart
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.vault_files import VaultFileStore
from never4ga.schema import (
    AREA_SCOPE_FIELD,
    BASE_WORKSPACE_PROFILE,
    LIST_VALUED_FIELDS,
    TYPE_REGISTRY,
    Location,
    LocationKind,
    TypeSpec,
    ValidationLevel,
    ValidationReport,
    coerce_field,
    domains_from_registry,
    type_spec,
    validate_document,
)
from never4ga.services import dashboard, scaffold
from never4ga.services.authoring import OWNER_ACTOR, Clock, build_concept, utc_now
from never4ga.services.navigation import lineage_of as navigation_lineage_of
from never4ga.services.navigation import refresh as navigation_refresh

__all__ = [
    "ConceptCreationError",
    "ContentService",
    "Created",
    "dated_filename_for",
    "directory_name_for",
    "filename_for",
]

#: Characters no filesystem should have to carry, plus the path separators that
#: would silently relocate a document.
_UNSAFE: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_COLLAPSE_SPACES: Final = re.compile(r"\s+")

#: Punctuation that is not part of a name. Word characters, whitespace, hyphens
#: and dots survive; everything else goes.
_PUNCTUATION: Final = re.compile(r"[^\w\s.-]", re.UNICODE)

#: `A -- B` leaves two hyphens once the dash is gone.
_COLLAPSE_HYPHENS: Final = re.compile(r"-{2,}")

#: A dash with space on both sides: an em dash, an en dash, or a typed `--`.
#: A filename's `_` field separator, written the way a person writes a title.
#: The spaces are required -- `well-known` is one idea and must stay one.
_FIELD_BOUNDARY: Final = re.compile(r"\s+(?:[\u2013\u2014]|-{2,})\s+")

#: Any run of separators containing a field boundary is one field boundary.
_COLLAPSE_FIELDS: Final = re.compile(r"[-_]*_[-_]*")

#: Names Windows refuses regardless of extension.
_RESERVED_STEMS: Final = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{n}" for n in range(1, 10)),
        *(f"lpt{n}" for n in range(1, 10)),
    }
)


class ConceptCreationError(Never4gaError):
    """A concept could not be created as asked.

    ``candidates`` carries the registered types the request was ambiguous
    between, when that is why it failed. The message states the fact and the
    *surface* phrases the ask: the CLI names `--type`, the companion offers
    the candidates as choices, and neither has to parse English to do it. A
    flag named in the message would mean nothing to a surface without flags.
    """

    def __init__(
        self,
        message: str,
        report: ValidationReport | None = None,
        candidates: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.report = report
        self.candidates = candidates


@dataclass(frozen=True, slots=True)
class Created:
    """What a creation service produced."""

    document: StoredDocument
    directories: tuple[VaultPath, ...] = ()
    files: tuple[VaultPath, ...] = ()
    #: Why the document went where it did, in one sentence. The generic verb
    #: infers a folder, so it says what it inferred and from what; the typed
    #: verbs have one answer each and leave this empty.
    placement_reason: str = ""
    #: Where the document was before, when writing it moved it. Only adoption
    #: out of foreign material does (core/01 section 1), so it reports both
    #: ends.
    moved_from: VaultPath | None = None
    #: The writer's keys adoption kept under `extensions.adopted` because the
    #: concept does not carry them unchanged (core/02 section 3.3). Only adoption out of
    #: foreign material sets anything aside.
    set_aside: tuple[str, ...] = ()
    #: The line that links a started walkthrough from its plan, relative to
    #: the plan's folder. The plan is hand-written, so Never4gA prints it for
    #: the session to add rather than editing the plan.
    plan_link: str = ""

    @property
    def concept_id(self) -> ConceptId:
        return self.document.concept_id

    @property
    def path(self) -> VaultPath:
        return self.document.path


def _sanitise(title: str) -> str:
    """Strip what a filesystem cannot carry, and keep the two separators.

    In a filename, `-` joins words inside one field and `_` separates fields.
    Whitespace becomes a hyphen because a name is one idea unless something says
    otherwise: `Hybrid Retrieval` is one thing, so `hybrid-retrieval`.
    Underscores the author typed are left alone -- they are how the author
    separated fields, and that is theirs to decide.

    **A removed character leaves a boundary behind.** Substituting the empty
    string would fuse the words either side of it and change the name:
    `Semantic/Vector` would become `semanticvector`, and `Ratio 3:1` would
    become `ratio-31`, which reads as thirty-one. A space is substituted
    instead and collapses with the rest.

    **A spaced dash is a field boundary, not a word join.** A title written
    `ADR-0001 — Use SQLite` is an identifier and then a description: two
    fields. Only a *spaced* dash counts, so `well-known` stays one idea.
    `dated_filename_for` joins its date to its subject with `_` for the same
    reason.
    """
    normalised = unicodedata.normalize("NFC", title)
    fielded = _FIELD_BOUNDARY.sub("_", normalised)
    cleaned = _PUNCTUATION.sub(" ", _UNSAFE.sub(" ", fielded))
    cleaned = _COLLAPSE_SPACES.sub("-", cleaned.strip())
    cleaned = _COLLAPSE_HYPHENS.sub("-", cleaned)
    # A boundary that picked up hyphens from the spaces around it is still one
    # boundary: `a-_-b` is `a_b`. Runs collapse to the stronger separator,
    # because a field boundary that degraded into a word join would put the
    # name back where it started.
    cleaned = _COLLAPSE_FIELDS.sub("_", cleaned).strip("-_.")
    if not cleaned:
        raise ConceptCreationError(f"{title!r} contains no characters usable in a filename")
    if cleaned.casefold() in _RESERVED_STEMS:
        # Windows refuses these whatever the extension.
        cleaned = f"{cleaned}-concept"
    return cleaned


def directory_name_for(title: str) -> str:
    """A directory name for a title.

    Case is preserved: directories may carry it, and `core/02` section 7.3
    makes the title the author's. A workspace called `HomeLab` gets
    `HomeLab/`; one called `Home Lab` gets `Home-Lab/`.
    """
    return _sanitise(title)


#: core/01 section 13 folders this type's records by year and keeps the full
#: date prefix on the filename, so a record names its own year and the path is
#: never the only thing carrying it. It is the one type whose placement depends on a
#: field rather than on the type alone.
DATED_LOG_TYPE: Final = "activity_log"

#: core/02 section 21.11 numbers this type's records within their folder. The
#: second type whose name depends on more than its title, after the log above.
NUMBERED_TYPE: Final = "decision"


def _log_date(fields: Mapping[str, Any] | None) -> date:
    """The day an activity_log is *about*, from its required `occurred_at`.

    Not today: a handoff written on Monday about Friday's session belongs under
    Friday. And not UTC-normalised -- `2026-01-01T00:30+05:00` is still 2025 in
    UTC, and filing it under 2025 would contradict the date the writer put in
    their own frontmatter.
    """
    raw = str((fields or {}).get("occurred_at", "")).strip()
    if not raw:
        raise ConceptCreationError(
            "an activity_log needs occurred_at to know which year it belongs in; pass it as a field"
        )
    try:
        # `date.fromisoformat` accepts a bare date; `datetime` handles the
        # offset form and keeps it, since `.date()` reads the local wall date.
        return date.fromisoformat(raw) if len(raw) == 10 else datetime.fromisoformat(raw).date()
    except ValueError as error:
        raise ConceptCreationError(
            f"occurred_at {raw!r} is not an ISO 8601 date or timestamp, so the year "
            "it belongs in cannot be determined"
        ) from error


def _is_log_root(directory: VaultPath) -> bool:
    """Whether a path is a workspace's `Logs/` itself, rather than a year in it."""
    segments = directory.segments
    return bool(segments) and segments[-1] == LOGS_DIRECTORY


def _named_reason(spec: TypeSpec, directory: VaultPath, dated: bool) -> str:
    """Why a document went where it did, when the caller named the folder.

    An ``activity_log`` in a `Logs/` root is a year roll-up (core/01 section 13) rather
    than a dated record, and the two are told apart by the folder alone. Saying which
    one happened is the difference between a caller seeing a deliberate choice and a
    caller seeing a filename that lost its date.
    """
    if spec.name != DATED_LOG_TYPE:
        return f"{directory} was named"
    if dated:
        return f"{directory} was named, and a dated record keeps its date (core/01 section 13)"
    if _is_log_root(directory):
        return (
            f"{directory} was named, so this is the year roll-up core/01 section 13 keeps there "
            f"rather than a dated record; name {directory}/<YYYY> for one of those"
        )
    return f"{directory} was named"


def _is_year_directory(directory: VaultPath) -> bool:
    """Whether a path ends in a four-digit year, as `Logs/<YYYY>` does."""
    segments = directory.segments
    return bool(segments) and len(segments[-1]) == 4 and segments[-1].isdigit()


def dated_filename_for(title: str, day: date) -> str:
    """The filename of an activity_log: its date, then what it is about.

    core/01 section 13 says "filenames keep a full date prefix, so a record
    names its own year". A title routinely carries that
    date already -- "Session Handoff -- 2026-08-25" is how a person writes one
    -- and prefixing it verbatim states the day twice. So an occurrence of
    *this* date is dropped from the slug. A different date is content and stays:
    a log written on one day about another names both on purpose.
    """
    stamp = day.isoformat()
    remainder = filename_for(title).removesuffix(".md")
    if stamp in remainder:
        # Removing the date from the middle leaves the separators that sat
        # either side of it back to back. Re-collapsing is the same pass
        # `_sanitise` ends with, and it has to run here too: without it a title
        # written `Handoff -- <date> -- Review` yields `handoff_-_review`.
        remainder = remainder.replace(stamp, "-")
    remainder = _COLLAPSE_HYPHENS.sub("-", remainder)
    remainder = _COLLAPSE_FIELDS.sub("_", remainder).strip("-_.")
    return f"{stamp}_{remainder}.md" if remainder else f"{stamp}.md"


def filename_for(title: str) -> str:
    """A Markdown filename for a concept title.

    Lowercase, because file names are. The title itself keeps its spacing
    and capitals in frontmatter; only the path is transformed.

    Refuses the two names OKF reserves. A *directory* may be called `Index`; a
    concept file may not, because `index.md` is navigation wherever it sits
    (core/01 sections 4 and 13).
    """
    filename = f"{_sanitise(title).casefold()}.md"
    if filename in {RESERVED_INDEX, RESERVED_LOG}:
        raise ConceptCreationError(
            f"{filename!r} is a reserved OKF filename; a concept may not use it"
        )
    return filename


class ContentService:
    """Creates workspaces, life areas, knowledge notes, maps and entities."""

    def __init__(
        self,
        files: VaultFileStore,
        documents: DocumentStore,
        *,
        now: Clock = utc_now,
        actor: str = OWNER_ACTOR,
    ) -> None:
        self._files = files
        self._documents = documents
        self._now = now
        self._actor = actor

    @property
    def actor(self) -> str:
        """Who anything this service writes records in ``generated.by``.

        Exposed because `WrapService` rewrites a document this service created
        and must record the same producer. A separate copy would let a first
        and a second wrap of one session disagree.
        """
        return self._actor

    # -- workspaces -------------------------------------------------------

    def create_workspace(
        self,
        title: str,
        *,
        workspace_type: str,
        parent: ConceptId | None = None,
        description: str | None = None,
        lifecycle: str = "active",
        profiles: list[str] | None = None,
        repositories: list[str] | None = None,
    ) -> Created:
        """Create a workspace, optionally beneath an existing one.

        core/01 section 6: a child workspace physically lives under its parent's
        `Workspaces/`. The parent is located by identity, so the caller never
        has to know where the parent sits on disk.

        ``profiles`` are *additional* to the base profile, which every workspace
        carries (core/03 section 8); naming the base again is harmless.
        ``repositories`` are names, not paths -- where a repository sits on a
        machine is the mapping registry's business, not the vault's.
        """
        directory = self._workspace_directory(title, parent)
        manifest_path = _child(directory, WORKSPACE_MANIFEST)
        self._refuse_if_occupied(manifest_path, f"a workspace already exists at {directory}")

        document = build_concept(
            concept_type="workspace",
            title=title,
            path=manifest_path,
            actor=self._actor,
            now=self._now,
            description=description,
            body=scaffold.workspace_body(title),
            workspace_type=workspace_type,
            lifecycle=lifecycle,
            parent=str(parent) if parent is not None else None,
            profiles=_with_base_profile(profiles),
            repositories=list(repositories) if repositories else None,
        )
        self._validate(document)

        self._files.ensure_directory(directory)
        index_path = _child(directory, RESERVED_INDEX)
        self._files.write_text(index_path, scaffold.WORKSPACE_INDEX.format(title=title))
        self._documents.put(document)
        self.refresh_generated(document.path)
        return Created(document, directories=(directory,), files=(index_path,))

    def refresh_generated(self, document: VaultPath) -> None:
        """Fill in the generated block on the indexes a written document sits under.

        Creating a directory changes what its parent navigates as well as what
        the new one does, so this is the new directory *and its ancestors*
        rather than the one file. Without it, a new directory's index would
        have no generated block and `doctor` would report it at once.

        Public because creation is not the only verb that moves a file into or
        out of a listing: a `wrap` that retitles renames the log, and a rename
        the navigation did not follow is a `broken_link` finding.

        **It covers the lineage, not the vault.** Creating one document is not
        a licence to rewrite the parts of the vault it did not touch;
        `repair --apply` is the verb that asks for a sweep. Every creating verb
        calls this.

        The cost is a load of the corpus on every creation. What it buys is a
        directory whose index is right when the document lands, instead of
        when something unrelated next sweeps past it.
        """
        corpus = list(self._documents.iter_documents())
        lineage = navigation_lineage_of(document)
        navigation_refresh(self._files, corpus, within=lineage)
        # The workspace dashboard is the other derivable region a write can
        # stale: the first goal a workspace gains is what makes its
        # Goals view appear, and the lineage already names every workspace
        # this document can have changed.
        dashboard.refresh(self._files, corpus, within=lineage)

    def _workspace_directory(self, title: str, parent: ConceptId | None) -> VaultPath:
        name = directory_name_for(title)
        if parent is None:
            return VaultPath.parse(f"{VaultRoot.WORKSPACES}/{name}")

        manifest = self._documents.get(parent)
        if manifest is None:
            raise ConceptCreationError(f"parent workspace {parent} does not exist")
        parent_directory = self._parent_container(manifest)
        if parent_directory is None:
            raise ConceptCreationError(f"{parent} is not a workspace or life area")
        return _child(_child(parent_directory, CHILD_WORKSPACE_DIRECTORY), name)

    @staticmethod
    def _parent_container(manifest: StoredDocument) -> VaultPath | None:
        """The directory whose ``Workspaces/`` a child of ``manifest`` belongs in.

        A workspace's parent may be a life area (core/03 section 30): a course
        or a certification belongs to a responsibility rather than to a
        project, lives under ``20_Life/<Area>/Workspaces/`` and names the area
        as ``parent``. `doctor` accepts that arrangement -- its parent map is
        areas and workspaces together -- so the creator must too.

        An area is still not a workspace. It is reached through a different
        helper, carries `area` rather than `workspace` as its scope field
        (core/02 section 16.3), and is never a `workspace:` target; all this decides is
        where a child physically lands.
        """
        match manifest.frontmatter.get("type"):
            case "workspace":
                return workspace_directory_of(manifest.path)
            case "life_area":
                return life_area_directory_of(manifest.path)
            case _:
                return None

    def ensure_section(self, workspace: ConceptId, section: str) -> VaultPath:
        """Create a workspace's semantic directory on first use (core/03 section 2)."""
        manifest = self._documents.get(workspace)
        if manifest is None:
            raise ConceptCreationError(f"workspace {workspace} does not exist")
        directory = workspace_directory_of(manifest.path)
        if directory is None:
            raise ConceptCreationError(f"{workspace} is not a workspace")
        target = _child(directory, section)
        self._files.ensure_directory(target)
        return target

    # -- life areas -------------------------------------------------------

    def create_life_area(
        self,
        title: str,
        *,
        description: str | None = None,
        lifecycle: str = "active",
    ) -> Created:
        name = directory_name_for(title)
        directory = VaultPath.parse(f"{VaultRoot.LIFE}/{name}")
        manifest_path = _child(directory, AREA_MANIFEST)
        self._refuse_if_occupied(manifest_path, f"a life area already exists at {directory}")

        document = build_concept(
            concept_type="life_area",
            title=title,
            path=manifest_path,
            actor=self._actor,
            now=self._now,
            description=description,
            body=scaffold.template_body("life_area", title),
            lifecycle=lifecycle,
        )
        self._validate(document)

        self._files.ensure_directory(directory)
        index_path = _child(directory, RESERVED_INDEX)
        self._files.write_text(index_path, scaffold.AREA_INDEX.format(title=title))
        self._documents.put(document)
        self.refresh_generated(document.path)
        return Created(document, directories=(directory,), files=(index_path,))

    # -- flat areas -------------------------------------------------------

    def create_knowledge(
        self,
        title: str,
        *,
        description: str | None = None,
        domains: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Created:
        """A durable note in `30_Knowledge/Notes/`, which stays flat."""
        return self._create_flat(
            concept_type="knowledge",
            directory=f"{VaultRoot.KNOWLEDGE}/Notes",
            title=title,
            description=description,
            domains=domains,
            tags=tags,
        )

    def create_map(self, title: str, *, description: str | None = None) -> Created:
        return self._create_flat(
            concept_type="map",
            directory=f"{VaultRoot.KNOWLEDGE}/Maps",
            title=title,
            description=description,
            body=scaffold.template_body("map", title),
        )

    def _create_flat(
        self,
        *,
        concept_type: str,
        directory: str,
        title: str,
        description: str | None,
        body: str | None = None,
        **fields: Any,
    ) -> Created:
        path = VaultPath.parse(f"{directory}/{filename_for(title)}")
        self._refuse_if_occupied(path, f"a document already exists at {path}")

        document = build_concept(
            concept_type=concept_type,
            title=title,
            path=path,
            actor=self._actor,
            now=self._now,
            description=description,
            body=body if body is not None else f"# {title}\n",
            **fields,
        )
        self._validate(document)
        self._files.ensure_directory(VaultPath.parse(directory))
        self._documents.put(document)
        self.refresh_generated(document.path)
        return Created(document)

    # -- any registered type ----------------------------------------------

    def create_concept(
        self,
        concept_type: str,
        title: str,
        *,
        workspace: ConceptId | None = None,
        in_directory: VaultPath | None = None,
        description: str | None = None,
        fields: Mapping[str, Any] | None = None,
        actor: str | None = None,
        numbered: bool = False,
        series: str | None = None,
    ) -> Created:
        """Create a document of any registered type (core/02 section 21).

        The five typed verbs above stay as the front doors they are; this is the
        one that covers the rest, so that adding a type to the registry is
        enough to be able to create one.

        Placement is worked out from the registry and reported back. The rule is
        that a type's *first* registered location is its home, after the
        arguments have narrowed the candidates: naming a workspace means the
        workspace's own sections were meant. Where core/02 section 21 gives a
        type several equal homes and none of them is the first, nothing is guessed --
        ``in_directory`` names one.

        ``actor`` records someone other than this service's own actor in
        ``generated.by``, for a caller that writes on another's behalf -- `wrap`,
        whose log credits the producer the session recorded. It is the one way
        in for provenance: a ``generated`` field is refused.

        A decision is numbered as its folder numbers them (core/02 section 21.11):
        ``numbered`` asks for a number where the folder has none yet, and
        ``series`` names which of a folder's series it belongs to. Both are
        refused for any other type.
        """
        fields = _declared_kinds(fields)
        spec = type_spec(concept_type)
        if spec is None:
            registered = ", ".join(sorted(TYPE_REGISTRY))
            raise ConceptCreationError(
                f"{concept_type!r} is not a registered type; core/02 section 21 "
                f"registers {registered}"
            )
        _refuse_what_another_verb_owns(spec)
        _refuse_owned_fields(fields, "the type is creation's first argument")
        if (numbered or series is not None) and spec.name != NUMBERED_TYPE:
            raise ConceptCreationError(
                "only a decision carries an ADR number (core/02 section 21.11); "
                f"{_a(spec.name)} does not"
            )
        # The title and the description are arguments of creation, so a
        # field naming either again contradicts them rather than refining
        # them. Refused here, rather than failing later inside build_concept.
        repeated = [name for name in ("title", "description") if name in (fields or {})]
        if repeated:
            raise ConceptCreationError(
                f"{' and '.join(repeated)} {'is' if len(repeated) == 1 else 'are'} "
                "given to creation directly, not as a field; give each once"
            )

        workspace_directory = self._workspace_directory_of(workspace)
        placement = (
            self._named_placement(spec, title, in_directory, workspace_directory, fields)
            if in_directory is not None
            else self._inferred_placement(spec, title, workspace_directory, fields)
        )
        if spec.name == NUMBERED_TYPE:
            title, placement = self._numbered(title, placement, numbered=numbered, series=series)

        self._refuse_if_occupied(placement.path, f"a document already exists at {placement.path}")
        frontmatter = self._concept_fields(
            spec,
            fields,
            workspace or self._workspace_at(placement.directory),
            placement.path,
        )
        body = (
            scaffold.template_body(concept_type, title)
            if concept_type in scaffold.shaped_types()
            else f"# {title}\n"
        )

        document = build_concept(
            concept_type=concept_type,
            title=title,
            path=placement.path,
            actor=actor or self._actor,
            now=self._now,
            description=description,
            body=body,
            extra_fields=frontmatter,
        )
        self._validate(document)

        self._files.ensure_directory(placement.directory)
        self._documents.put(document)
        self.refresh_generated(document.path)
        return Created(document, placement_reason=placement.reason)

    def start_walkthrough(
        self, plan: ConceptId | VaultPath, phase: str, *, backfill: bool = False
    ) -> Created:
        """Start the walkthrough for one phase of ``plan`` (core/02 section 21.27).

        The walkthrough is created from its template beside the plan's
        workspace, names the phase, and links the plan with `implements`. A
        started phase is `in_progress`. ``backfill`` records a phase that is
        already done: `complete`, and saying it was reconstructed after the
        fact, so a reader knows its steps were pieced together rather than
        written as they happened.

        The plan is not edited. It is hand-written, so the result carries the
        line that links the walkthrough from it, for the session to add under
        the phase.
        """
        document = (
            self._documents.get(plan)
            if isinstance(plan, ConceptId)
            else self._documents.get_by_path(plan)
        )
        if document is None:
            raise ConceptCreationError(f"there is no plan {plan} in this vault")
        concept_type = str(document.frontmatter.get("type", ""))
        if concept_type != "plan":
            raise ConceptCreationError(
                f"{document.path} is {_a(concept_type or 'untyped document')}, not a plan; "
                "a walkthrough records one phase of a plan"
            )
        phase = phase.strip()
        if not phase:
            raise ConceptCreationError("a walkthrough needs the phase it records")
        plan_title = document.frontmatter.get("title", document.path.name.removesuffix(".md"))
        title = f"{plan_title} — {phase}"
        plan_directory = VaultPath(document.path.segments[:-1])
        fields: dict[str, Any] = {
            "lifecycle": "complete" if backfill else "in_progress",
            "phase": phase,
            "relations": [{"type": "implements", "target": str(document.concept_id)}],
        }
        workspace = document.frontmatter.get("workspace")
        created = self.create_concept(
            "walkthrough",
            title,
            workspace=ConceptId.parse(str(workspace)) if workspace else None,
            # A plan in a life area has no workspace; its walkthrough sits
            # beside it, in the same area.
            in_directory=None if workspace else plan_directory,
            fields=fields,
        )
        if backfill:
            heading, _, rest = created.document.body.partition("\n")
            reconstructed = replace(
                created.document,
                body=f"{heading}\n\n{_RECONSTRUCTED}\n{rest}",
            )
            self._documents.put(reconstructed)
            created = replace(created, document=reconstructed)
        link = posixpath.relpath(str(created.path), str(plan_directory))
        return replace(created, plan_link=f"Walkthrough: [{title}]({link})")

    def adopt_concept(
        self,
        path: VaultPath,
        *,
        concept_type: str | None = None,
        fields: Mapping[str, Any] | None = None,
        in_directory: VaultPath | None = None,
    ) -> Created:
        """Make the Markdown already at ``path`` a tracked concept.

        The reverse of :meth:`create_concept`: the file exists and stays
        exactly where it is, and the *type* is what gets worked out -- from the
        frontmatter the writer already began, from ``concept_type``, or from
        the folder, in that order. The body is preserved verbatim; identity is
        minted, because an ``id`` is the one thing a person cannot write.

        **A note in registered foreign material moves** (core/01 section 1).
        It has no home to stay in -- no type is at home in somebody's
        `Projects/`, and a binding location is binding -- so it goes to its
        type's first registered location, or to ``in_directory``, and the
        result names where it came from. Its own name comes with it, through
        the rule every concept path follows; a pile's `index.md` or `log.md`
        takes its folder's name, because those two are reserved here. With no
        type named, ``in_directory`` decides it the
        way the folder does for a file already home. ``in_directory`` is
        refused for such a file: it stays where it sits.

        The discipline is creation's. A folder more than one type is at home
        in is refused with the candidates named, never guessed -- and a field
        that is a fact about the content (`occurred_at`) is refused rather
        than invented, exactly as `create` refuses it.
        """
        fields = _declared_kinds(fields)
        foreign = self._is_foreign_note(path)
        if not foreign:
            self._refuse_what_adoption_does_not_cover(path)
            if in_directory is not None:
                raise ConceptCreationError(
                    f"{path} is already inside the vault's structure, and adoption "
                    "leaves such a file where it sits; move it first if it belongs "
                    "somewhere else"
                )
        _refuse_owned_fields(fields, "name the type with --type")
        try:
            untracked = self._documents.get_untracked(path)
        except VaultIntegrityError as error:
            raise ConceptCreationError(str(error)) from error
        if untracked is None:
            raise ConceptCreationError(f"no file at {path}; adoption starts from one")

        writer = dict(untracked.frontmatter)
        if foreign:
            _refuse_an_extensions_clash(path, writer)
        existing = dict(writer)
        declared_title = existing.pop("title", None)
        description = existing.pop("description", None)
        # What the file itself says is replaced rather than refused: it was
        # written before adoption, and adoption is what makes it a concept.
        for owned in OWNED_FIELDS:
            existing.pop(owned, None)

        # A supplied field wins over what the file declares, the title and
        # the description as much as any other. Both leave
        # the mapping because build_concept places them itself.
        supplied = dict(fields or {})
        declared_title = supplied.pop("title", declared_title)
        description = supplied.pop("description", description)
        merged: dict[str, Any] = {**existing, **supplied}

        if foreign:
            # A type the registry does not know is the writer's word, not a
            # claim in Never4gA's vocabulary: it is set aside with the rest,
            # and the note declares nothing (core/02 section 3.3).
            declared = {
                key: value
                for key, value in untracked.frontmatter.items()
                if key != "type" or type_spec(str(value)) is not None
            }
            spec, placement = self._foreign_placement(
                path, declared, concept_type, in_directory, merged
            )
            self._refuse_if_occupied(
                placement.path,
                f"{placement.path} already exists; adopting {path} will not write over it",
            )
        else:
            spec = self._adopted_type(path, untracked.frontmatter, concept_type, inferred_from=path)
            _refuse_what_another_verb_owns(spec)
            placement = _Placement(path, VaultPath(path.segments[:-1]), "adopted where it sits")

        workspace = self._workspace_at(placement.directory)

        def compose(fields: Mapping[str, Any]) -> StoredDocument:
            return build_concept(
                concept_type=spec.name,
                title=str(declared_title)
                if declared_title is not None
                else written_title(placement.path, untracked.body),
                path=placement.path,
                actor=self._actor,
                now=self._now,
                description=str(description) if description is not None else None,
                body=untracked.body,
                extra_fields=self._concept_fields(spec, fields, workspace, placement.path),
            )

        document = compose(merged)
        set_aside: dict[str, Any] = {}
        if foreign:
            # core/02 section 3.3: a writer's value that does not fit is set aside
            # rather than refused. Only the writer's: a value the caller supplied is
            # theirs, and is refused.
            removable = set(existing) - set(supplied)
            while misfits := self._misfits(document, removable):
                for key in misfits:
                    merged.pop(key, None)
                    removable.discard(key)
                document = compose(merged)
            set_aside = _not_carried(writer, document.frontmatter)
            if set_aside:
                extensions = dict(writer.get(EXTENSIONS_FIELD) or {})
                extensions[ADOPTED_NAMESPACE] = set_aside
                document = compose({**merged, EXTENSIONS_FIELD: extensions})
        self._validate(document)
        if foreign:
            # Written before the source goes, so a failure between the two
            # leaves the note twice rather than nowhere.
            self._files.ensure_directory(placement.directory)
            self._documents.put(document)
            self._files.remove(path)
        else:
            self._documents.put(document)
        self.refresh_generated(placement.path)

        reason = f"{placement.reason}; " + (
            f"you named {_a(spec.name)}"
            if concept_type is not None
            else f"the frontmatter declares {_a(spec.name)}"
            if untracked.frontmatter.get("type") is not None
            else f"the folder says {_a(spec.name)}"
        )
        if not spec.accepts(placement.path) and not spec.location_is_binding:
            reason += (
                f"; {_a(spec.name)} typically belongs in "
                f"{_describe(spec, workspace_directory_of(placement.path))}"
            )
        return Created(
            document,
            placement_reason=reason,
            moved_from=path if foreign else None,
            set_aside=tuple(set_aside),
        )

    def _is_foreign_note(self, path: VaultPath) -> bool:
        """Whether ``path`` is a note in material `init` registered (core/01 section 1)."""
        manifest = self._documents.get_by_path(SYSTEM_MANIFEST)
        if manifest is None:
            return False
        return is_foreign_note(path, registered_foreign_material(manifest.frontmatter))

    def _foreign_placement(
        self,
        path: VaultPath,
        frontmatter: Mapping[str, Any],
        concept_type: str | None,
        in_directory: VaultPath | None,
        fields: Mapping[str, Any],
    ) -> tuple[TypeSpec, _Placement]:
        """Which type a foreign note becomes, and where it moves to.

        The placement is creation's, with the note's own name standing in for
        a title: a named folder is checked the way `--in` is checked on
        `create`, and without one the type's first registered home is used.
        A type whose every home is inside a workspace or a life area has no
        such folder to infer, so the refusal asks for one.
        """
        name = _adopted_name(path)
        if in_directory is not None:
            inferred_from: VaultPath | None = _child(in_directory, filename_for(name))
        else:
            inferred_from = None
        spec = self._adopted_type(path, frontmatter, concept_type, inferred_from=inferred_from)
        _refuse_what_another_verb_owns(spec)

        if in_directory is not None:
            placement = self._named_placement(spec, name, in_directory, None, fields)
        elif any(location.kind is LocationKind.DIRECTORY for location in spec.locations):
            placement = self._inferred_placement(spec, name, None, fields)
        else:
            raise ConceptCreationError(
                f"{_a(spec.name)} belongs in {_describe(spec, None)}; name the folder "
                f"{path} should move to"
            )
        reason = f"moved out of foreign material into {placement.directory}"
        return spec, _Placement(placement.path, placement.directory, reason)

    def _adopted_type(
        self,
        path: VaultPath,
        frontmatter: Mapping[str, Any],
        concept_type: str | None,
        *,
        inferred_from: VaultPath | None,
    ) -> TypeSpec:
        """Which type ``path`` is adopted as: declared, named, or inferred.

        ``inferred_from`` is where a folder can decide it: the file itself when
        it is already home, the destination when a foreign note is given one,
        and nothing when a foreign note is not -- a pile says nothing about
        what its notes are, so the candidates are the types whose home in
        `30_Knowledge/` needs no workspace.
        """
        declared = frontmatter.get("type")
        if concept_type is not None and declared is not None and concept_type != declared:
            raise ConceptCreationError(
                f"{path} declares type {declared!r}; adopt it as that, or edit "
                "the file if the declaration is wrong"
            )
        chosen = concept_type or (str(declared) if declared is not None else None)
        if chosen is not None:
            spec = type_spec(chosen)
            if spec is None:
                registered = ", ".join(sorted(TYPE_REGISTRY))
                raise ConceptCreationError(
                    f"{chosen!r} is not a registered type; core/02 section 21 "
                    f"registers {registered}"
                )
            return spec

        if inferred_from is None:
            homes = _homes_needing_no_workspace()
            names = " or ".join(_a(spec.name) for spec in homes)
            raise ConceptCreationError(
                f"nothing says what {path} is: it is foreign material, so no folder "
                f"decides it. It could be {names}, or a type a workspace's folder "
                "decides once that folder is named",
                candidates=tuple(spec.name for spec in homes),
            )
        candidates = [spec for spec in TYPE_REGISTRY.values() if spec.accepts(inferred_from)]
        if not candidates:
            raise ConceptCreationError(
                f"no registered type is at home at {inferred_from}; name what it is"
            )
        if len(candidates) > 1:
            ordered = sorted(candidates, key=lambda spec: spec.name)
            names = " or ".join(_a(spec.name) for spec in ordered)
            raise ConceptCreationError(
                f"the folder does not decide what {inferred_from} is -- it could be "
                f"{names}; name which one it is",
                candidates=tuple(spec.name for spec in ordered),
            )
        return candidates[0]

    @staticmethod
    def _refuse_what_adoption_does_not_cover(path: VaultPath) -> None:
        """Files with their own flow, or with no concept role to adopt into."""
        if path.segments[0] == VaultRoot.INBOX:
            raise ConceptCreationError(
                f"{path} is a capture, and the Inbox has its own accounting: "
                "file it where it belongs, then run `never4ga inbox resolve`"
            )
        match role_of(path):
            case DocumentRole.CONCEPT:
                return
            case DocumentRole.FOREIGN_FORMAT:
                raise ConceptCreationError(
                    f"{path} is foreign-format material governed by another tool "
                    "(core/02 section 3.3); it is never adopted as a concept"
                )
            case DocumentRole.DIRECTORY_LOG:
                raise ConceptCreationError(
                    f"{path} is reserved OKF directory history, not a concept; "
                    "workspace activity belongs in Logs/ as an activity_log"
                )
            case _:
                raise ConceptCreationError(
                    f"{path} is reserved navigation and carries no concept "
                    "frontmatter (core/01 section 4)"
                )

    def _workspace_directory_of(self, workspace: ConceptId | None) -> VaultPath | None:
        if workspace is None:
            return None
        manifest = self._documents.get(workspace)
        if manifest is None:
            raise ConceptCreationError(f"workspace {workspace} does not exist")
        directory = workspace_directory_of(manifest.path)
        if directory is None or manifest.frontmatter.get("type") != "workspace":
            raise ConceptCreationError(f"{workspace} is not a workspace")
        return directory

    def _area_at(self, path: VaultPath) -> ConceptId | None:
        """The life area a path sits in, read from its `area.md` (core/02 section 16.3).

        The mirror of :meth:`_workspace_at`: naming the folder already says
        which responsibility the document belongs to.
        """
        area = life_area_directory_of(path)
        if area is None:
            return None
        manifest = self._documents.get_by_path(_child(area, AREA_MANIFEST))
        return manifest.concept_id if manifest is not None else None

    def _workspace_at(self, directory: VaultPath) -> ConceptId | None:
        """The workspace a directory sits in, if it sits in one.

        So that naming a folder is enough: `--in .../MyProject/Decisions` already
        says which workspace the decision belongs to, and asking for it twice
        would be asking the caller to repeat itself.
        """
        enclosing = workspace_directory_of(directory)
        if enclosing is None:
            return None
        manifest = self._documents.get_by_path(_child(enclosing, WORKSPACE_MANIFEST))
        return manifest.concept_id if manifest is not None else None

    def _named_placement(
        self,
        spec: TypeSpec,
        title: str,
        directory: VaultPath,
        workspace_directory: VaultPath | None,
        fields: Mapping[str, Any] | None = None,
    ) -> _Placement:
        # `--in` chooses the folder, and for an activity_log the folder says
        # which kind of record this is. core/01 section 13 puts dated records in
        # `Logs/<YYYY>/` and the year roll-up directly in `Logs/` as
        # `<YYYY>_summary.md`, so naming the year folder -- the obvious thing to
        # do once one exists -- must keep the date prefix, while naming `Logs/`
        # leaves the caller's name alone.
        dated = spec.name == DATED_LOG_TYPE and _is_year_directory(directory)
        name = dated_filename_for(title, _log_date(fields)) if dated else filename_for(title)
        path = _child(directory, name)
        advisory = not spec.accepts(path)
        if advisory and spec.location_is_binding:
            raise ConceptCreationError(
                f"{_a(spec.name)} does not belong in {directory}; "
                f"it belongs in {_describe(spec, workspace_directory)}"
            )
        if workspace_directory is not None:
            enclosing = workspace_directory_of(path)
            if enclosing != workspace_directory:
                raise ConceptCreationError(
                    f"{directory} is not inside the workspace at {workspace_directory}; "
                    "a document may not claim one workspace and sit in another"
                )
        if advisory:
            # core/02 spells this type's location "Typical location", and
            # `validate` honors that with a warning at every level. The verb
            # matches the validator: the folder the caller named wins, and the
            # advice travels in the reason instead of becoming a refusal.
            return _Placement(
                path,
                directory,
                f"{directory} was named; {_a(spec.name)} typically belongs in "
                f"{_describe(spec, workspace_directory)}",
            )
        return _Placement(path, directory, _named_reason(spec, directory, dated))

    def _inferred_placement(
        self,
        spec: TypeSpec,
        title: str,
        workspace_directory: VaultPath | None,
        fields: Mapping[str, Any] | None = None,
    ) -> _Placement:
        candidates = [
            location
            for location in spec.locations
            if (
                location.kind is not LocationKind.WORKSPACE_SECTION
                or workspace_directory is not None
            )
            # An area location is a subtree, not a folder: grouping inside one
            # is allowed (core/01 section 7), so there is no single directory to
            # infer and no basis for choosing between responsibilities. The
            # caller names it with `--in`.
            and location.kind is not LocationKind.LIFE_AREA_CONTENT
        ]
        if not candidates:
            raise ConceptCreationError(
                f"{_a(spec.name)} belongs in {_describe(spec, None)}; "
                "name the workspace it belongs to"
            )
        if workspace_directory is not None:
            sections = [
                location
                for location in candidates
                if location.kind is LocationKind.WORKSPACE_SECTION
            ]
            candidates = sections or candidates

        chosen = candidates[0]
        if len(candidates) > 1 and chosen != spec.locations[0]:
            legal = ", ".join(_location_names(candidates, workspace_directory))
            raise ConceptCreationError(
                f"a {spec.name} is equally at home in {legal}; name the one you mean with --in"
            )

        directory = _directory_of(chosen, workspace_directory)

        if spec.name == DATED_LOG_TYPE:
            day = _log_date(fields)
            directory = _child(directory, str(day.year))
            path = _child(directory, dated_filename_for(title, day))
            return _Placement(
                path,
                directory,
                f"an activity_log belongs in {directory}, foldered by the year it is about",
            )

        path = _child(directory, filename_for(title))
        return _Placement(path, directory, f"{_a(spec.name)} belongs in {directory}")

    def _numbered(
        self, title: str, placement: _Placement, *, numbered: bool, series: str | None
    ) -> tuple[str, _Placement]:
        """Give a decision the next number in its folder, or refuse the one it states.

        core/02 section 21.11. The folder is the scope, not the workspace
        family. Its archived counterpart is read with it, because archiving
        moves a record out of the folder and its number is still taken:
        without that, archiving the highest record would hand its number to
        the next decision. A folder with no numbered records, live or
        archived, is left unnumbered unless a number is asked for, because
        some folders never number their decisions at all.

        Two sessions can still both take the same number at the same moment.
        That is ``decision_number_duplicate``, which `doctor` reports.
        """
        directory = placement.directory
        archive = archived_counterpart(directory)
        folders = {directory.segments: False}
        if archive is not None:
            folders[archive.segments] = True
        existing: list[DecisionNumber] = []
        archived: list[DecisionNumber] = []
        for path in self._files.iter_paths():
            is_archived = folders.get(path.segments[:-1])
            if is_archived is None or (number := number_in_filename(path.name)) is None:
                continue
            existing.append(number)
            if is_archived:
                archived.append(number)
        stated = number_in_title(title)
        named = series.strip().casefold() if series is not None else None
        if stated is not None and named is not None and stated.series != named:
            raise ConceptCreationError(
                f"{title!r} states {stated.title_prefix}, which is not in the series "
                f"{named!r} that was named; give the series once"
            )
        chosen = named if named is not None else (stated.series if stated is not None else None)
        if chosen is None:
            present = sorted({number.series for number in existing})
            if len(present) > 1:
                listed = ", ".join(one or "(unprefixed)" for one in present)
                raise ConceptCreationError(
                    f"{directory} numbers its decisions in more than one series ({listed}); "
                    "name the one this belongs to with --series"
                )
            chosen = present[0] if present else ""
        if not (existing or numbered or named is not None or stated is not None):
            return title, placement

        allocated = next_number(existing, chosen)
        if stated is not None and stated != allocated:
            raise ConceptCreationError(
                f"{title!r} states {stated.title_prefix}, but the next number in {directory} "
                f"is {allocated.title_prefix} (core/02 section 21.11); state that one, or "
                "leave the number out and it is allocated"
            )
        numbered_title = title if stated is not None else f"{allocated.title_prefix} — {title}"
        reason = f"{placement.reason}; numbered {allocated.title_prefix}, the next in that folder"
        if any(one.series == chosen for one in archived):
            # Said because the folder alone no longer explains the number.
            reason += f", counting the records archived to {archive}"
        return numbered_title, _Placement(
            _child(directory, filename_for(numbered_title)), directory, reason
        )

    def _concept_fields(
        self,
        spec: TypeSpec,
        fields: Mapping[str, Any] | None,
        workspace: ConceptId | None,
        path: VaultPath,
    ) -> dict[str, Any]:
        """What goes in the frontmatter beyond what every concept carries.

        Two required fields Never4gA can supply, because both are structure
        rather than content: the workspace, which the caller named, and the
        lifecycle, whose registered vocabulary starts at the value a new
        document is at. Everything else it refuses to invent -- when a logged
        thing happened, or what kind of thing an entity is, are facts about the
        content, and the same rule that forbids fabricating ``generated.at``
        (core/02 section 5.2) applies to them.
        """
        supplied: dict[str, Any] = {
            key: ([value] if key in LIST_VALUED_FIELDS and isinstance(value, str) else value)
            for key, value in (fields or {}).items()
        }
        required = spec.required_fields_for(path)
        if AREA_SCOPE_FIELD in required:
            # The area is derivable from the path and its id lives in the
            # manifest beside it, so asking the caller to type a UUID they
            # named a folder for would be asking them to repeat themselves --
            # the same reason `--in` already implies the workspace.
            area = self._area_at(path)
            if area is not None and AREA_SCOPE_FIELD not in supplied:
                supplied[AREA_SCOPE_FIELD] = str(area)
        elif workspace is not None and "workspace" not in supplied:
            supplied["workspace"] = str(workspace)
        if "lifecycle" in required and "lifecycle" not in supplied and spec.lifecycle_values:
            supplied["lifecycle"] = spec.lifecycle_values[0]

        missing = [name for name in required if supplied.get(name) is None]
        if missing:
            named = ", ".join(missing)
            raise ConceptCreationError(
                f"{_a(spec.name)} requires {named}; supply each with --field name=value"
            )
        return supplied

    # -- helpers ----------------------------------------------------------

    def _refuse_if_occupied(self, path: VaultPath, message: str) -> None:
        if self._files.exists(path) or self._documents.get_by_path(path) is not None:
            raise ConceptCreationError(message)

    def _misfits(self, document: StoredDocument, removable: set[str]) -> set[str]:
        """The writer's keys that make ``document`` invalid, and nothing else."""
        registry = self._documents.get_by_path(DOMAIN_REGISTRY)
        report = validate_document(
            document.path,
            document.frontmatter,
            level=ValidationLevel.STRICT,
            domains=domains_from_registry(registry.frontmatter if registry else None),
        )
        fields = {issue.field.split(".")[0] for issue in report.errors if issue.field}
        return fields & removable

    def _validate(self, document: StoredDocument) -> None:
        registry = self._documents.get_by_path(DOMAIN_REGISTRY)
        report = validate_document(
            document.path,
            document.frontmatter,
            level=ValidationLevel.STRICT,
            domains=domains_from_registry(registry.frontmatter if registry else None),
        )
        if report.errors:
            reasons = "; ".join(issue.message for issue in report.errors)
            raise ConceptCreationError(
                f"refusing to write an invalid {document.frontmatter['type']}: {reasons}",
                report,
            )


#: Where adoption keeps what the writer wrote and the concept does not carry
#: (core/02 section 3.3), under `core/02` section 20's namespaced extension data.
EXTENSIONS_FIELD: Final = "extensions"
ADOPTED_NAMESPACE: Final = "adopted"


def _refuse_an_extensions_clash(path: VaultPath, writer: Mapping[str, Any]) -> None:
    """Refuse to merge into an `extensions` Never4gA did not write."""
    extensions = writer.get(EXTENSIONS_FIELD)
    if extensions is None:
        return
    if not isinstance(extensions, Mapping):
        raise ConceptCreationError(
            f"{path} has an extensions field that is not a mapping; adoption keeps "
            "what it sets aside under extensions.adopted, so edit the file first"
        )
    if ADOPTED_NAMESPACE in extensions:
        raise ConceptCreationError(
            f"{path} already has extensions.adopted; adoption will not merge into "
            "a namespace it did not write, so rename it in the file first"
        )


def _not_carried(writer: Mapping[str, Any], adopted: Mapping[str, Any]) -> dict[str, Any]:
    """Every key the writer wrote whose value the concept does not carry unchanged.

    A single string made a one-item list is carried: that is the list-valued
    field's own shape (`tags: garden` is `tags: [garden]`), not a loss.
    """
    kept: dict[str, Any] = {}
    for key, value in writer.items():
        if key == EXTENSIONS_FIELD:
            continue
        carried = adopted.get(key)
        if carried == value:
            continue
        if key in LIST_VALUED_FIELDS and isinstance(value, str) and carried == [value]:
            continue
        kept[key] = value
    return kept


def _with_base_profile(profiles: list[str] | None) -> list[str]:
    """The base profile first, then whatever else was named, without repeats."""
    ordered = [BASE_WORKSPACE_PROFILE]
    for profile in profiles or ():
        if profile not in ordered:
            ordered.append(profile)
    return ordered


def _child(directory: VaultPath, name: str) -> VaultPath:
    return VaultPath((*directory.segments, name))


@dataclass(frozen=True, slots=True)
class _Placement:
    """Where a document goes, and why."""

    path: VaultPath
    directory: VaultPath
    reason: str


#: Frontmatter Never4gA writes on every concept, and no caller supplies. `id` is
#: identity, minted because a person cannot write one; `generated` is
#: provenance core/02 section 5.2 says is never fabricated; `type` and `schema`
#: are what Never4gA resolved and reports. `created_at` is deliberately absent:
#: when a document was written is a fact about its content.
OWNED_FIELDS: Final = ("type", "id", "schema", "generated")


def _declared_kinds(fields: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Supplied fields, each converted to the kind the vocabulary declares.

    Every interface hands creation what its caller typed: a `--field` string,
    or a JSON value from a form. Converting here rather than in an interface
    means the CLI, the API and MCP write the same frontmatter for the same
    request (`details/api-cli-mcp-contract.md` section 3), so `unit: "3"`
    becomes `unit: 3` and `required_reading: "true"` the boolean core/02
    section 21.15 requires. An undeclared field, and a value that is not
    really of its declared kind, stay what they were; a list converts each
    element.
    """
    if not fields:
        return fields
    return {
        name: (
            [coerce_field(name, one) for one in value]
            if isinstance(value, list)
            else coerce_field(name, value)
        )
        for name, value in fields.items()
    }


def _refuse_owned_fields(fields: Mapping[str, Any] | None, type_hint: str) -> None:
    """Refuse a supplied field Never4gA writes itself.

    Refused, not ignored: ignoring the field would hide the caller's mistake,
    which is also why creation refuses a title given twice.
    """
    supplied = [name for name in OWNED_FIELDS if name in (fields or {})]
    if not supplied:
        return
    named = supplied[0] if len(supplied) == 1 else f"{', '.join(supplied[:-1])} and {supplied[-1]}"
    verb, noun = ("is", "a field") if len(supplied) == 1 else ("are", "fields")
    hint = f"; {type_hint}" if "type" in supplied else ""
    raise ConceptCreationError(
        f"{named} {verb} written by Never4gA and cannot be supplied as {noun}{hint}"
    )


def _refuse_what_another_verb_owns(spec: TypeSpec) -> None:
    """Four types the generic verb does not create, for two different reasons.

    The two *manifest* types make a directory and an `index.md` beside the
    manifest (core/01 section 6), and `workspace create` also takes a parent,
    profiles and repositories. A generic verb cannot express any of that without
    becoming a soup of type-specific flags.

    The two *singletons* -- the types pinned to one exact path -- are the vault
    itself. A vault has exactly one `system_manifest` and its `id` **is** the
    vault identity (core/02 section 21.20, core/05 section 6), so a verb that
    could write one could bring a vault into existence sideways: pointed at an
    empty directory it would produce `50_System/system.md`, a fresh identity and
    a vault with no roots, no `home.md` and no templates. `init`
    creates a vault, and it is the only thing that does.
    """
    for location in spec.locations:
        match location.kind:
            case LocationKind.WORKSPACE_MANIFEST:
                raise ConceptCreationError(
                    f"use `never4ga workspace create` to create {_a(spec.name)}"
                )
            case LocationKind.LIFE_AREA_MANIFEST:
                raise ConceptCreationError(
                    f"use `never4ga life-area create` to create {_a(spec.name)}"
                )
            case LocationKind.EXACT_PATH:
                raise ConceptCreationError(
                    f"a vault has one {spec.name}, at {location.value}, and "
                    "`never4ga init` writes it"
                )
            case _:
                continue


def _adopted_name(path: VaultPath) -> str:
    """The name a foreign note keeps when it moves home.

    Its own, through the rule every concept path follows, so `Seed
    Catalogue.md` arrives as `seed-catalogue.md`. A pile's `index.md` or
    `log.md` was the writer's note about its folder, and is named for the
    folder instead: both names are reserved in Never4gA's structure, and
    `Projects/index.md` is what the writer wrote about `Projects`.
    """
    stem = path.name.removesuffix(".md")
    if f"{_sanitise(stem).casefold()}.md" in {RESERVED_INDEX, RESERVED_LOG}:
        return path.segments[-2]
    return stem


def _homes_needing_no_workspace() -> list[TypeSpec]:
    """The types a person's own writing can become with nothing but the type named.

    Their first home is a folder under `30_Knowledge/`: knowledge and maps.
    The `50_System/` types need no workspace either, but they are the vault's
    own records, and offering them for a note out of somebody's pile would be
    offering a category error.
    """
    knowledge = f"{VaultRoot.KNOWLEDGE}/"
    return sorted(
        (
            spec
            for spec in TYPE_REGISTRY.values()
            if spec.locations
            and spec.locations[0].kind is LocationKind.DIRECTORY
            and spec.locations[0].value.startswith(knowledge)
        ),
        key=lambda spec: spec.name,
    )


#: What a backfilled walkthrough says first, so a reader knows its steps were
#: pieced together after the phase rather than written as it ran.
_RECONSTRUCTED: Final = (
    "> Reconstructed after the fact, from the plan, the activity logs and the "
    "repository's history. A detail nothing confirms is marked [inferred], and "
    "one that could not be checked is marked [unverified]."
)


def _a(type_name: str) -> str:
    """`an activity_log`, `a decision`. Messages are read by people."""
    return f"{'an' if type_name[0] in 'aeiou' else 'a'} {type_name}"


def _directory_of(location: Location, workspace_directory: VaultPath | None) -> VaultPath:
    match location.kind:
        case LocationKind.WORKSPACE_SECTION:
            assert workspace_directory is not None  # narrowed before we get here
            return _child(workspace_directory, location.value)
        case _:
            # EXACT_PATH never arrives: `_refuse_what_another_verb_owns` sends
            # the only two types that use it to `init` before placement runs.
            return VaultPath.parse(location.value)


def _location_names(locations: list[Location], workspace_directory: VaultPath | None) -> list[str]:
    """Name each location the way a caller would have to type it.

    With a workspace in hand a section is named as the folder it actually is,
    because that is what `--in` takes; without one it can only be described.
    """
    names = []
    for location in locations:
        if location.kind is LocationKind.LIFE_AREA_CONTENT:
            names.append(f"a life area under {VaultRoot.LIFE}/")
        elif location.kind is LocationKind.WORKSPACE_SECTION and workspace_directory is None:
            names.append(f"a workspace's {location.value}/")
        else:
            names.append(str(_directory_of(location, workspace_directory)))
    return names


def _describe(spec: TypeSpec, workspace_directory: VaultPath | None) -> str:
    return " or ".join(_location_names(list(spec.locations), workspace_directory))
