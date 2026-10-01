"""`wrap`: turn a session's accumulation into the one record that survives.

`core/08` section 1 puts episodic memory in ``Workspace/Logs/``. Everything a
session records before it wraps stays outside the vault; wrapping is when it
enters. One session becomes one ``activity_log``, dated from when the session
started rather than from when it was wrapped.

Wrapping is idempotent because a session records the document it produced. A
second wrap rewrites that document rather than leaving two accounts of one
session in the vault.

**It writes nothing to a tracker.** If a ticket needs something, somebody
should write that something; a note saying a session wrapped is only noise on
the ticket's audit trail, and a pointer to a vault path is no use to a reader
without the vault.

Instead it follows `core/04` section 37, which asks for "what PM work items
*may need* an update": a candidate. A checkpoint declares one, `wrap` reports
them beside the decisions and the memories, and `work update` and
`work comment` are how a person acts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Final, NamedTuple

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import (
    Checkpoint,
    GivenContext,
    Session,
    SessionStateError,
    body_digest,
)
from never4ga.errors import IdentityError, VaultPathError
from never4ga.layout import life_area_directory_of
from never4ga.layout.structure import LOGS_DIRECTORY
from never4ga.ports.document_store import DocumentStore, FileStatReportingStore
from never4ga.ports.session_store import SessionStore
from never4ga.services.authoring import OWNER_ACTOR, Clock, format_timestamp, utc_now
from never4ga.services.creation import ContentService, dated_filename_for, filename_for

__all__ = ["CONTEXT_QUESTION", "WorkspaceGoneError", "WrapService", "Wrapped"]

#: What every wrap asks about the context documents its session was given
#: (`core/04` section 37). Fixed wording, so every interface asks the same thing.
CONTEXT_QUESTION: Final = "Did this session change anything these assert?"

#: How much of a checkpoint note becomes a title when nobody supplies one.
_TITLE_LIMIT = 60

#: The `YYYY-MM-DD_` a dated log opens with, which a retitle keeps.
_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}[_.]")


class WorkspaceGoneError(SessionStateError):
    """The session's workspace is no longer in the vault, and nowhere else was named.

    This is not rare. A session that retires a workspace, merges two,
    or raises a directory into a workspace of its own is exactly the session
    that then cannot close, because the id it opened against resolves to
    nothing. The error names the way through rather than a uuid.
    """


@dataclass(frozen=True, slots=True)
class Wrapped:
    """What a wrap did."""

    session: SessionId
    document: StoredDocument
    checkpoints: int
    #: False the first time, true whenever an existing log was rewritten.
    updated: bool = False
    #: What the agent declared across the session, gathered in order. Handed
    #: back so a client can show them; nothing here has been written anywhere
    #: but the log, and turning one into an ADR is a person's step.
    decisions: tuple[str, ...] = ()
    memories: tuple[str, ...] = ()
    #: Work items the agent said may need an update. Reported, never acted on.
    work: tuple[str, ...] = ()
    #: Of those, the ones this session never wrote to. A declaration is a claim
    #: and a recorded action is an observation, so this is the gap between what
    #: an agent said a ticket needed and what Never4gA saw it do about it.
    #:
    #: `wrap` still writes its log -- refusing would strand the session's only
    #: durable record over a tracker it does not own. It does not pass over the
    #: gap in silence either: the CLI exits non-zero while one remains.
    outstanding_work: tuple[str, ...] = ()
    #: Where the log used to be, when a rewrite retitled it and the filename
    #: followed. ``None`` on a first wrap and on a rewrite that kept its title.
    #: Reported rather than assumed: a rename nobody is told about is how a
    #: link goes stale without anybody choosing that.
    renamed_from: VaultPath | None = None
    #: The workspace the session opened against, when the log was directed
    #: somewhere else because that workspace is gone. ``None`` when
    #: the log landed where the session opened, archived or not.
    redirected_from: ConceptId | None = None
    #: Context documents the agent said this session changed, verbatim (`core/04`
    #: section 37). One naming no context document stays here and nowhere else:
    #: reported, never held against the session.
    context: tuple[str, ...] = ()
    #: What the session's startup carried from `Context/`, in the order it was
    #: recorded. Listed on every wrap under :data:`CONTEXT_QUESTION`, declared
    #: or not, because the session that forgets to declare is the one this is
    #: for.
    context_given: tuple[GivenContext, ...] = ()
    #: Declared context documents that did not change: a given one whose body
    #: still has the digest it had at startup, or one not given that was last
    #: modified before the session opened. Their vault paths, in declaration
    #: order. Saying a fact changed is not the same as correcting the document
    #: that asserts it, and the CLI exits non-zero while one remains.
    outstanding_context: tuple[str, ...] = ()

    @property
    def concept_id(self) -> ConceptId:
        return self.document.concept_id


class WrapService:
    """Writes the episodic record a session leaves behind."""

    def __init__(
        self,
        sessions: SessionStore,
        content: ContentService,
        documents: DocumentStore,
        *,
        now: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._content = content
        self._documents = documents
        self._now = now

    def wrap(
        self,
        session_id: SessionId,
        *,
        title: str | None = None,
        into: ConceptId | None = None,
    ) -> Wrapped:
        """Write, or rewrite, the session's log.

        ``into`` names where the log belongs when the session's own workspace no longer
        exists: the workspace it became, or the life area that now holds what it held.
        An archived workspace needs nothing passed: its identity travels with it, so the
        session's id still resolves and the log lands beside the rest of that
        workspace's history. Only a workspace that is *gone* needs a person to say
        where. A rewrite ignores ``into``: one session leaves one record, and it stays
        where it was first written.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionStateError(
                f"session {session_id} was never opened; there is nothing to wrap"
            )
        checkpoints = tuple(self._sessions.checkpoints(session_id))
        if not checkpoints:
            # An empty log is worse than no log: it claims a session happened
            # and says nothing about it, and `doctor` would then have to have
            # an opinion about a document nobody meant to write.
            raise SessionStateError(
                f"session {session_id} has no checkpoints; "
                "record one with `never4ga checkpoint` before wrapping"
            )

        if session.wrapped_log is not None:
            return self._rewrite(session, checkpoints, title)
        return self._create(session, checkpoints, title or _title_from(checkpoints), into)

    # -- internals --------------------------------------------------------

    def _outstanding(self, session: SessionId, declared: Sequence[str]) -> tuple[str, ...]:
        """Declared items with no recorded write against them.

        Resolution is shallow on purpose: an item is the leading identifier of
        a declaration, and a declaration that names none is a candidate for a
        person rather than something to hold the session to. Never4gA reads no
        intent out of prose, and a `wrap` that objected to work nobody asked
        for would be trained out of a reader within a week.
        """
        acted = {action.item for action in self._sessions.work_actions(session)}
        outstanding: list[str] = []
        for declaration in declared:
            item = _declared_item(declaration)
            if item is not None and item not in acted and item not in outstanding:
                outstanding.append(item)
        return tuple(outstanding)

    def _outstanding_context(self, session: Session, declared: Sequence[str]) -> tuple[str, ...]:
        """Declared context documents that did not change (`core/04` section 37).

        A document the session was given is compared by the digest of its body
        at startup, so an edit to frontmatter alone is not a correction. One
        it was not given has no digest, and is held to its modification time
        instead: modified after the session opened means updated, whichever
        came first, the edit or the declaration. A store that cannot say when
        a file changed holds nothing -- a declaration is never called
        unactioned on evidence Never4gA does not have.
        """
        given = {one.concept_id: one.digest for one in self._sessions.given_context(session.id)}
        outstanding: list[str] = []
        for declaration in declared:
            document = self._declared_context(declaration)
            if document is None or str(document.path) in outstanding:
                continue
            baseline = given.get(document.concept_id)
            if baseline is not None:
                unchanged = body_digest(document.body) == baseline
            else:
                unchanged = self._untouched_since(document.path, session.started_at)
            if unchanged:
                outstanding.append(str(document.path))
        return tuple(outstanding)

    def _declared_context(self, declaration: str) -> StoredDocument | None:
        """The context document a declaration opens with, if it opens with one.

        The shape the checkpoint Skill teaches is ``"<id or path>: <what
        changed>"``, so the reference is the first token before a colon or
        whitespace, read as a concept id and then as a vault path. Anything
        else is prose, and prose is for a person to read.
        """
        head = declaration.strip().split(":", 1)[0].strip().split()
        if not head:
            return None
        reference = head[0]
        document: StoredDocument | None
        try:
            document = self._documents.get(ConceptId.parse(reference))
        except IdentityError:
            try:
                document = self._documents.get_by_path(VaultPath.parse(reference))
            except VaultPathError:
                return None
        if document is None or document.frontmatter.get("type") != "context":
            return None
        return document

    def _untouched_since(self, path: VaultPath, started_at: datetime) -> bool:
        if not isinstance(self._documents, FileStatReportingStore):
            return False
        stat = self._documents.file_stat(path)
        if stat is None:
            return False
        return datetime.fromtimestamp(stat.modified_at, UTC) <= started_at

    def _home(self, session: Session, into: ConceptId | None) -> _Home:
        """Where the log goes, and whether that is where the session opened.

        The session's own workspace is looked up by identity, not by the path
        it had when the session opened, so a workspace that was moved or
        archived in the meantime is found where it went (`core/06` section 3).
        """
        target = into if into is not None else session.workspace
        manifest = self._documents.get(target)
        if manifest is None:
            if into is not None:
                raise SessionStateError(
                    f"{into} is not in the vault; --into names the workspace or "
                    "life area the log belongs in"
                )
            raise WorkspaceGoneError(
                f"workspace {session.workspace} is no longer in the vault: it was "
                "deleted, or moved somewhere that is not a workspace, after the "
                "session opened. Say where the log belongs with --into <id>: the "
                "workspace it became, or the life area that now holds it. An "
                "archived workspace still takes its own log without this."
            )
        # Directing a log away from a workspace that still exists is not the
        # case this exists for, and it is not refused either: a session that
        # opened in one workspace and finished in another has a reason, and
        # the log says where it came from.
        redirected = session.workspace if target != session.workspace else None
        match manifest.frontmatter.get("type"):
            case "workspace":
                return _Home(target, None, redirected)
            case "life_area":
                area = life_area_directory_of(manifest.path)
                assert area is not None
                year = VaultPath((*area.segments, LOGS_DIRECTORY, str(session.started_at.year)))
                return _Home(None, year, redirected)
            case kind:
                raise SessionStateError(
                    f"{target} is {_a(str(kind))}; a log belongs in a workspace or a life area"
                )

    def _create(
        self,
        session: Session,
        checkpoints: Sequence[Checkpoint],
        title: str,
        into: ConceptId | None,
    ) -> Wrapped:
        home = self._home(session, into)
        created = self._content.create_concept(
            "activity_log",
            title,
            workspace=home.workspace,
            in_directory=home.directory,
            # The day the session was about, not the day it was wrapped.
            fields={"occurred_at": session.started_at.isoformat()},
            # Provenance through the argument for it, not a field: a supplied
            # `generated` is refused.
            actor=self._producer(session),
        )
        document = replace(
            created.document, body=_body(title, checkpoints, redirected_from=home.redirected_from)
        )
        self._documents.put(document)
        self._sessions.mark_wrapped(session.id, created.concept_id)
        declared = _declared(checkpoints)
        return Wrapped(
            session.id,
            document,
            len(checkpoints),
            decisions=declared.decisions,
            memories=declared.memories,
            work=declared.work,
            outstanding_work=self._outstanding(session.id, declared.work),
            redirected_from=home.redirected_from,
            context=declared.context,
            context_given=tuple(self._sessions.given_context(session.id)),
            outstanding_context=self._outstanding_context(session, declared.context),
        )

    def _producer(self, session: Session) -> str:
        """Who the log says wrote it -- one rule, used by both wraps.

        An explicit actor on the service wins; absent one, the session's
        recorded actor is the observed fact. The first wrap and a rewrite must
        use the same order, or `--actor` is honoured by one and ignored by the
        other.
        """
        if self._content.actor != OWNER_ACTOR:
            return self._content.actor
        return session.actor

    def _rewrite(
        self, session: Session, checkpoints: Sequence[Checkpoint], title: str | None
    ) -> Wrapped:
        """Rewrite the log this session already produced.

        The identity is the document's own -- a second wrap is the same session
        saying more, not a new record -- and `generated.at` moves because it
        changed.

        **The title moves too, when one is passed.** The reason to wrap again
        is that more happened, and the later work often changes what the
        session was about. Without a passed title the document's own title
        stands, so a second wrap never re-derives a chosen title from the first
        checkpoint behind the caller's back.

        **And the filename follows the title.** `core/06` section 3 makes
        identity independent of path, so the move costs nothing an id depends
        on. The cost is a link written to the old name. Vault links are
        Markdown links (`core/02` section 18), and the one place that
        routinely links to dated logs is the generated year index, which is
        refreshed here so it follows the file.
        """
        assert session.wrapped_log is not None
        existing = self._documents.get(session.wrapped_log)
        if existing is None:
            raise SessionStateError(
                f"session {session.id} recorded log {session.wrapped_log}, "
                "which is no longer in the vault; it was moved or deleted outside Never4gA"
            )
        frontmatter = dict(existing.frontmatter)
        frontmatter["generated"] = {
            "by": self._producer(session),
            "at": format_timestamp(self._now()),
        }
        retitled = title or str(frontmatter.get("title", "")) or _title_from(checkpoints)
        frontmatter["title"] = retitled
        path = _retitled_path(existing.path, retitled, session.started_at.date())
        document = replace(
            existing, path=path, frontmatter=frontmatter, body=_body(retitled, checkpoints)
        )
        self._documents.put(document)
        moved = path != existing.path
        if moved:
            # The listing names the file, so a move without this leaves the
            # year index pointing at a name that is gone.
            self._content.refresh_generated(path)
        declared = _declared(checkpoints)
        return Wrapped(
            session.id,
            document,
            len(checkpoints),
            updated=True,
            decisions=declared.decisions,
            memories=declared.memories,
            work=declared.work,
            outstanding_work=self._outstanding(session.id, declared.work),
            renamed_from=existing.path if moved else None,
            context=declared.context,
            context_given=tuple(self._sessions.given_context(session.id)),
            outstanding_context=self._outstanding_context(session, declared.context),
        )


def _retitled_path(existing: VaultPath, title: str, day: date) -> VaultPath:
    """Where a retitled log belongs: its own directory, its own date, new subject.

    Only the subject half of the name is the title's to change. The date is
    the one the log already carries -- the day the session was about rather
    than the day somebody wrapped it again -- so a rename cannot quietly
    re-decide which year the record files under.

    A log that is not dated at all keeps that shape too: `wrap` writes dated
    logs, but a document placed by hand under a different rule is not this
    function's to reorganise.
    """
    name = existing.name
    dated = name[:10] if _DATE_PREFIX.match(name) else None
    filename = dated_filename_for(title, day) if dated else filename_for(title)
    return VaultPath((*existing.segments[:-1], filename))


def _title_from(checkpoints: Sequence[Checkpoint]) -> str:
    """A title from the first thing the session said it did.

    Better than a bare date, which two sessions on one day would collide on,
    and better than asking: a session that has said what it did has already
    supplied the words.
    """
    first = _as_prose(checkpoints[0].note.strip()).splitlines()[0]
    return first if len(first) <= _TITLE_LIMIT else f"{first[:_TITLE_LIMIT].rstrip()}…"


#: A `[[...]]` run, and whatever backticks already sit either side of it.
_LINK_SHAPED = re.compile(r"(`*)(\[\[[^\[\]]*\]\])(`*)")


def _as_prose(note: str) -> str:
    """A checkpoint note, with link-shaped runs defused into code spans.

    A note is stored verbatim and rendered into the log. Without this, a
    session writing *about* wikilink syntax would put a live wikilink into a
    document Never4gA generated itself, and `doctor` would report
    `broken_link` against it. Editing the log would not help: the next wrap
    re-renders from the stored checkpoints.

    Link checking follows link *targets* and not code spans: a link is an
    author saying "go here", while a code span may be prose *about* a path.
    So a mention of `[[foo]]` becomes a code span.

    The whole note is treated as prose, with no check on whether the target
    resolves. A checkpoint note is the one part of the log that is somebody's
    own words; every section `wrap` builds around it -- work items, decisions
    -- it writes itself, as Markdown links. Nothing authors vault navigation
    inside a free-text note, and a rule that depended on what happens to exist
    at write time would defuse a note today and not tomorrow.

    Backticks already present are absorbed rather than doubled: a note that
    said `` `[[Note]]` `` was already prose, and wrapping it again would
    render the backticks literally.
    """
    return _LINK_SHAPED.sub(lambda match: f"`{match.group(2)}`", note)


class _Home(NamedTuple):
    """Where a first wrap writes: a workspace, or a year folder under an area."""

    workspace: ConceptId | None
    directory: VaultPath | None
    redirected_from: ConceptId | None


def _a(kind: str) -> str:
    return f"an {kind}" if kind[:1] in "aeiou" else f"a {kind}"


def _body(
    title: str, checkpoints: Sequence[Checkpoint], *, redirected_from: ConceptId | None = None
) -> str:
    """The `activity_log` body shape, filled from what the session recorded.

    Only the sections there is evidence for. `Decided`, `Corrected` and `Next`
    belong to a person reading this back, and `wrap` has no evidence to
    invent them from.

    A redirected log says so first: a reader of this folder's history
    finds a session that opened somewhere else, and should not have to guess.
    """
    lines = [f"# {title}", ""]
    if redirected_from is not None:
        lines.extend(
            [
                f"_This session opened against workspace `{redirected_from}`, which is no "
                "longer in the vault. Whoever wrapped it directed the log here._",
                "",
            ]
        )
    lines.extend(["## What happened", ""])
    for checkpoint in checkpoints:
        lines.append(f"- **{checkpoint.recorded_at:%H:%M}** — {_as_prose(checkpoint.note.strip())}")
    actions = [action for checkpoint in checkpoints for action in checkpoint.actions]
    if actions:
        lines.extend(["", "## Actions", ""])
        lines.extend(f"- {action}" for action in actions)

    declared = _declared(checkpoints)
    if declared.work:
        lines.append("")
        lines.append("## Work items that may need an update")
        lines.append("")
        lines.append(
            "_Declared during the session. Never4gA wrote nothing to a tracker: "
            "if a ticket needs something, somebody writes that._"
        )
        lines.append("")
        lines.extend(f"- {item}" for item in declared.work)
    if declared.context:
        lines.extend(
            [
                "",
                "## Context documents this session changed",
                "",
                "_Declared during the session. Each should now say what the session "
                "changed; `wrap` reports any whose content did not move._",
                "",
            ]
        )
        lines.extend(f"- {_as_prose(item)}" for item in declared.context)
    if declared.decisions:
        # Present because the agent said so, not because `wrap` inferred it.
        # The note under the heading is load-bearing: a decision in a
        # log is a claim about a session, and only an ADR is a decision in
        # force.
        lines.extend(
            [
                "",
                "## Decided",
                "",
                "_Declared during the session and recorded as claimed. Nothing here "
                "is an accepted decision until somebody writes the ADR._",
                "",
            ]
        )
        lines.extend(f"- {decision}" for decision in declared.decisions)
    if declared.memories:
        lines.extend(["", "## Worth remembering", ""])
        lines.extend(f"- {memory}" for memory in declared.memories)

    lines.extend(
        [
            "",
            "## Open",
            "",
            "_Not recorded. A checkpoint says what happened; what is still open "
            "is for whoever reads this back._",
            "",
        ]
    )
    return "\n".join(lines)


class _Declared(NamedTuple):
    """Everything the agent declared across a session, in the order it said it."""

    decisions: tuple[str, ...]
    memories: tuple[str, ...]
    work: tuple[str, ...]
    context: tuple[str, ...]


def _declared(checkpoints: Sequence[Checkpoint]) -> _Declared:
    return _Declared(
        decisions=tuple(d for c in checkpoints for d in c.decisions),
        memories=tuple(m for c in checkpoints for m in c.memories),
        work=tuple(w for c in checkpoints for w in c.work),
        context=tuple(x for c in checkpoints for x in c.context),
    )


def _declared_item(declaration: str) -> str | None:
    """The tracker id a `--work` declaration opens with, if it opens with one."""
    head = declaration.strip().split(":", 1)[0].strip().split()
    if not head:
        return None
    candidate = head[0].lstrip("#")
    return candidate if candidate.isalnum() and any(c.isdigit() for c in candidate) else None
