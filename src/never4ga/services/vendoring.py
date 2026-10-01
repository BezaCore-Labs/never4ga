"""Recording where vendored capability material came from (core/02 section 21.21).

The third hop. `core/04` section 25 governs vault to client, and the seed hash
governs shipped to vault; both assume Never4gA wrote the content. Material
vendored from a third party travels neither, so without a record the vault
holds directories it cannot account for.

What this module does is narrow on purpose: hash a subject directory as it
stands, write the record, read it back, and compare. Asking a remote whether it
has moved needs a network and belongs behind a port; this stays offline, which
means local drift is always answerable even when nothing else is.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Final

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.text_hazards import scan_text
from never4ga.domain.vendoring import (
    Drift,
    Integrity,
    Origin,
    OriginKind,
    SkillProvenance,
    Upstream,
    UpstreamStatus,
    integrity_of,
)
from never4ga.errors import Never4gaError
from never4ga.layout import VaultRoot
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.vault_files import VaultFileStore
from never4ga.schema.vocabulary import SCHEMA_VERSION
from never4ga.services.adapters import AGENTS_DIRECTORY, SKILLS_DIRECTORY
from never4ga.services.authoring import Clock, format_timestamp, utc_now

__all__ = [
    "AGENTS_DIRECTORY",
    "SUBJECT_DIRECTORIES",
    "ProvenanceError",
    "VendoringService",
    "provenance_path",
]

#: Where vendored material may live. Both are declared foreign-format
#: (`core/02` section 3.3), which is exactly why their provenance cannot be
#: kept inside them.
SUBJECT_DIRECTORIES: Final = (SKILLS_DIRECTORY, AGENTS_DIRECTORY)

_TYPE: Final = "skill_provenance"

#: What Never4gA writes into a Skill it seeded. Its presence is what
#: makes a directory first-party, and therefore already accounted for.
_SEED_MARKER: Final = "never4ga-seed-hash"


class ProvenanceError(Never4gaError):
    """A provenance record could not be built or read."""


def provenance_path(subject: str) -> VaultPath:
    """Where the record for a subject directory lives.

    Beside `50_System/system.md`, not inside the island. A sidecar within
    `50_System/Skills/<name>/` would be invisible to `doctor` and the index,
    and whole-directory deployment would copy it into every client tree.
    """
    trimmed = subject.rstrip("/")
    name = trimmed.rsplit("/", 1)[-1].removesuffix(".md")
    # The class is part of the filename, not decoration. A Skill and an agent
    # may legitimately share a name -- the deployment manifest already had to
    # namespace them for the same reason -- and without this their records
    # would collide on one path and the second would overwrite the first.
    kind = "agent" if trimmed.startswith(AGENTS_DIRECTORY) else "skill"
    return VaultPath.parse(f"{VaultRoot.SYSTEM}/{kind}-provenance_{name}.md")


@dataclass(frozen=True, slots=True)
class SubjectState:
    """One subject directory, as it is now against what was recorded."""

    subject: str
    recorded: SkillProvenance | None
    #: None when the directory no longer exists -- a record pointing at nothing.
    present: Integrity | None

    @property
    def missing_record(self) -> bool:
        return self.recorded is not None and self.present is None

    def record_is_incomplete(self) -> bool:
        """A record that kept no tree digest, so there is nothing to compare.

        `integrity.tree_digest` is parsed with a default of `""`, which compares
        unequal to every real digest. Treated as drift, such a record would be
        permanently LOCAL against a subject nobody had touched, and
        `changed_files` would find no file to name: a warning with no action in
        it.

        It is not drift. It is a record that cannot answer the question, and
        the two need opposite responses -- re-record versus investigate.

        `skills provenance record` always computes the digest, so this only
        appears where frontmatter was written by hand or assembled by an agent.
        """
        return (
            self.recorded is not None
            and self.present is not None
            and not self.recorded.integrity.tree_digest
        )

    def drift(self) -> Drift:
        if self.recorded is None or self.present is None:
            return Drift.NONE
        if self.record_is_incomplete():
            return Drift.NONE
        moved = self.present.tree_digest != self.recorded.integrity.tree_digest
        return replace(self.recorded, modified=moved).drift()

    def changed_files(self) -> tuple[str, ...]:
        """Which files differ, when the record kept a per-file breakdown.

        Without it a mismatch says only that something changed, which is the
        difference between a finding somebody can act on and one they cannot.
        """
        if self.recorded is None or self.present is None:
            return ()
        before, after = dict(self.recorded.integrity.files), dict(self.present.files)
        if not before:
            return ()
        return tuple(
            sorted(set(before) ^ set(after) | {k for k in before if before[k] != after.get(k)})
        )


class VendoringService:
    """Builds, writes and re-checks provenance records."""

    def __init__(
        self,
        files: VaultFileStore,
        documents: DocumentStore,
        *,
        now: Clock = utc_now,
    ) -> None:
        self._files = files
        self._documents = documents
        self._now = now
        #: Set only inside :meth:`one_pass`. See there for why it is not a
        #: cache that lives longer.
        self._read: dict[VaultPath, str] | None = None

    # -- reading the world -------------------------------------------------

    @contextmanager
    def one_pass(self) -> Iterator[None]:
        """Read the vault once for everything asked inside this block.

        `subject_files` walks the whole vault, and every question about a
        subject goes through it: is it first-party, does it hash to its record,
        does it carry a hazard. Without this a diagnosis walks the vault once
        per subject per question, which is quadratic in the vault's size.

        **The scope is one pass on purpose.** A cache living longer would
        answer from a vault that had since changed, and the daemon holds this
        service across runs. Entering again inside a pass is a no-op rather
        than a second read.
        """
        if self._read is not None:
            yield
            return
        self._read = dict(self._read_subject_directories())
        try:
            yield
        finally:
            self._read = None

    def _read_subject_directories(self) -> Iterable[tuple[VaultPath, str]]:
        """Every readable file that could belong to a subject.

        Only under :data:`SUBJECT_DIRECTORIES`, which is all `subject_files`
        and `subjects` ever look at. Reading the whole vault instead would be
        slower and would reach files that are not text at all.

        An undecodable file is skipped rather than fatal. Material vendored
        from somewhere else can carry anything, and a diagnosis that dies on a
        binary in a Skill directory reports nothing about the whole vault.
        """
        prefixes = tuple(tuple(root.split("/")) for root in SUBJECT_DIRECTORIES)
        for path in self._files.iter_paths():
            if not any(path.segments[: len(prefix)] == prefix for prefix in prefixes):
                continue
            try:
                text = self._files.read_text(path)
            except UnicodeDecodeError, ValueError:
                continue
            if text is not None:
                yield path, text

    def subject_files(self, subject: str) -> dict[str, str]:
        """Everything a subject is made of, keyed by relative path.

        A subject is a directory or a single file. A Skill is a directory --
        `SKILL.md` plus optional scripts and references -- and an agent is one
        Markdown file, which is the client's layout rather than a choice here.
        Both have to be hashable by the same code, or the second class would
        need a second digest and the two records would stop being comparable.

        A file subject yields one entry keyed by its own name, so its tree
        digest is the digest of a one-file tree. That is the same construction,
        not a special case.
        """
        prefix = tuple(subject.split("/"))
        contents: dict[str, str] = {}
        for path, text in self._paths():
            segments = path.segments
            if segments == prefix:
                contents[segments[-1]] = text
                continue
            if segments[: len(prefix)] != prefix or len(segments) <= len(prefix):
                continue
            contents["/".join(segments[len(prefix) :])] = text
        return contents

    def _paths(self) -> Iterable[tuple[VaultPath, str]]:
        """Every readable file, from this pass's read when there is one."""
        if self._read is not None:
            return tuple(self._read.items())
        return tuple(self._read_subject_directories())

    def subjects(self) -> tuple[str, ...]:
        """Every directory that could carry a provenance record.

        First-party material is excluded, and that is the whole distinction
        this module rests on. A Skill Never4gA ships already has provenance: the
        seed hash in its frontmatter, travelling the shipped-to-vault hop.
        Asking it for a vendored record as well would report a finding per
        shipped Skill on a freshly initialised vault and mean nothing by any
        of them.
        """
        found: set[str] = set()
        walked = self._paths()
        for root in SUBJECT_DIRECTORIES:
            prefix = tuple(root.split("/"))
            for path, _ in walked:
                segments = path.segments
                if segments[: len(prefix)] != prefix or len(segments) <= len(prefix):
                    continue
                if len(segments) == len(prefix) + 1:
                    # A file sitting directly in the root. Under `Agents/` that
                    # *is* the capability; under `Skills/` a Skill is a
                    # directory and a loose file beside them is somebody's
                    # note. The two roots hold different shapes and saying so
                    # here is cheaper than pretending they do not.
                    if root == AGENTS_DIRECTORY and segments[-1].endswith(".md"):
                        found.add("/".join(segments))
                    continue
                found.add("/".join((*prefix, segments[len(prefix)])))
        return tuple(sorted(s for s in found if not self._is_first_party(s)))

    def _is_first_party(self, subject: str) -> bool:
        """Whether Never4gA seeded this directory itself."""
        return any(_SEED_MARKER in text for text in self.subject_files(subject).values())

    # -- the gate ---------------------------------------------------------

    def hazards(self, subject: str, *, forbidden: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """Why this material should not be vendored as it stands.

        Rendered as strings rather than returned as values, because the caller
        is either refusing an act or writing a finding and neither needs the
        structure. Each names the file, the position and the reason: an
        invisible character cannot be found by reading, so a report without a
        position leaves a reader no better off than none.
        """
        found: list[str] = []
        for name, text in sorted(self.subject_files(subject).items()):
            found.extend(f"{name}:{hazard}" for hazard in scan_text(text, forbidden=forbidden))
        return tuple(found)

    def refuse_if_hazardous(self, subject: str, *, forbidden: frozenset[str] = frozenset()) -> None:
        """The gate itself.

        A `doctor` finding would arrive after the material is already in the
        vault. Text that hides instructions from a reviewer is worth stopping
        at the door rather than noticing later.
        """
        hazards = self.hazards(subject, forbidden=forbidden)
        if hazards:
            listed = "\n  ".join(hazards[:10])
            more = f"\n  ... and {len(hazards) - 10} more" if len(hazards) > 10 else ""
            raise ProvenanceError(
                f"{subject} carries text that must not be vendored as it stands:\n  {listed}{more}"
            )

    # -- the record --------------------------------------------------------

    def record(self, subject: str, origin: Origin, *, per_file: bool = True) -> SkillProvenance:
        """Hash the subject as it stands and build its record."""
        files = self.subject_files(subject)
        if not files:
            raise ProvenanceError(f"{subject} holds no files to record")
        return SkillProvenance(
            skill_path=subject,
            origin=origin,
            integrity=integrity_of(files, per_file=per_file),
        )

    def read(self, subject: str) -> SkillProvenance | None:
        """The record for a subject, or None when there is not one."""
        document = self._documents.get_by_path(provenance_path(subject))
        if document is None:
            return None
        return _from_frontmatter(document.frontmatter)

    def state_of(self, subject: str) -> SubjectState:
        files = self.subject_files(subject)
        return SubjectState(
            subject=subject,
            recorded=self.read(subject),
            present=integrity_of(files) if files else None,
        )

    def frontmatter(self, record: SkillProvenance, *, base: Mapping[str, Any]) -> dict[str, Any]:
        """The record as frontmatter, on top of the base every concept carries."""
        origin: dict[str, Any] = {
            "kind": str(record.origin.kind),
            "fetched_at": record.origin.fetched_at,
        }
        for key in ("url", "ref", "commit", "subpath", "license", "author"):
            value = getattr(record.origin, key)
            if value is not None:
                origin[key] = value
        integrity: dict[str, Any] = {
            "algorithm": record.integrity.algorithm,
            "tree_digest": record.integrity.tree_digest,
        }
        if record.integrity.files:
            integrity["files"] = dict(record.integrity.files)
        upstream: dict[str, Any] = {"status": str(record.upstream.status)}
        if record.upstream.last_checked_at:
            upstream["last_checked_at"] = record.upstream.last_checked_at
        if record.upstream.last_seen_commit:
            upstream["last_seen_commit"] = record.upstream.last_seen_commit
        # `core/02` section 5 orders the required five; a reader opening the
        # file should see what it is and which concept it is before anything
        # else, and the store needs `id` to recognise the file at all.
        return {
            "type": _TYPE,
            **dict(base),
            "skill_path": record.skill_path,
            "origin": origin,
            "integrity": integrity,
            "upstream": upstream,
            "local": {"modified": record.modified},
        }

    def base_frontmatter(
        self, record: SkillProvenance, *, concept_id: ConceptId, actor: str
    ) -> dict[str, Any]:
        """The five fields every concept carries, plus who wrote this one.

        Built here rather than by the caller so that the one type nobody
        hand-authors has exactly one place that knows its shape. `generated.at`
        is the clock this service was given and is never fabricated
        (`core/02` section 5.2).
        """
        stamp = format_timestamp(self._now())
        return {
            "id": str(concept_id),
            "schema": SCHEMA_VERSION,
            "title": f"Vendored: {record.skill_path.rsplit('/', 1)[-1]}",
            "created_at": stamp,
            "generated": {"by": actor, "at": stamp},
            "authority": "informational",
        }

    def write(
        self,
        record: SkillProvenance,
        *,
        concept_id: ConceptId,
        base: Mapping[str, Any],
        body: str = "",
    ) -> VaultPath:
        path = provenance_path(record.skill_path)
        frontmatter = self.frontmatter(record, base={"id": str(concept_id), **dict(base)})
        self._documents.put(
            StoredDocument(
                concept_id=concept_id,
                path=path,
                frontmatter=frontmatter,
                body=body,
            )
        )
        return path


def _from_frontmatter(frontmatter: Mapping[str, Any]) -> SkillProvenance:
    """Parse a record back, tolerating anything extra.

    `core/02` requires unknown fields to survive a round trip, so this reads
    what it knows and ignores the rest rather than refusing a record written by
    a later version.
    """
    origin = dict(frontmatter.get("origin") or {})
    integrity = dict(frontmatter.get("integrity") or {})
    upstream = dict(frontmatter.get("upstream") or {})
    local = dict(frontmatter.get("local") or {})
    try:
        kind = OriginKind(str(origin.get("kind", OriginKind.LOCAL)))
    except ValueError:
        kind = OriginKind.LOCAL
    try:
        status = UpstreamStatus(str(upstream.get("status", UpstreamStatus.UNKNOWN)))
    except ValueError:
        status = UpstreamStatus.UNKNOWN
    return SkillProvenance(
        skill_path=str(frontmatter.get("skill_path", "")),
        origin=Origin(
            kind=kind,
            fetched_at=str(origin.get("fetched_at", "")),
            url=origin.get("url"),
            ref=origin.get("ref"),
            commit=origin.get("commit"),
            subpath=origin.get("subpath"),
            license=origin.get("license"),
            author=origin.get("author"),
        ),
        integrity=Integrity(
            tree_digest=str(integrity.get("tree_digest", "")),
            algorithm=str(integrity.get("algorithm", "sha256")),
            files=dict(integrity.get("files") or {}),
        ),
        upstream=Upstream(
            status=status,
            last_checked_at=upstream.get("last_checked_at"),
            last_seen_commit=upstream.get("last_seen_commit"),
        ),
        modified=bool(local.get("modified", False)),
    )
