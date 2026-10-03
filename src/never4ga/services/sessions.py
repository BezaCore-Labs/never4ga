"""Working session state: opening one, and recording what happens inside it.

`core/08` section 1 keeps this layer in local runtime state, and it stays there
for the whole life of a session: a checkpoint never touches the vault.
What reaches the vault is the single ``activity_log`` that `wrap` writes from
the accumulation, and that is the episodic record.

The service is thin on purpose. It owns two things the store cannot: the clock,
so a caller cannot backdate a checkpoint, and the fact that a note has to say
something.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Checkpoint, GivenContext, Session, WorkAction, body_digest
from never4ga.ports.session_store import SessionStore
from never4ga.services.authoring import Clock, utc_now

__all__ = ["SessionService"]


class SessionService:
    """Sessions and their checkpoints, for one vault on one machine."""

    def __init__(self, store: SessionStore, *, now: Clock = utc_now) -> None:
        self._store = store
        self._now = now

    def open(self, *, workspace: ConceptId, actor: str, client: str | None = None) -> Session:
        """Begin a session against a workspace.

        The id is minted here rather than accepted. A caller that has
        one already is a caller that has confused two sessions.
        """
        session = Session.opened(
            workspace=workspace, actor=actor, started_at=self._now(), client=client
        )
        self._store.open(session)
        return session

    def checkpoint(
        self,
        session: SessionId,
        note: str,
        *,
        actions: Sequence[str] = (),
        decisions: Sequence[str] = (),
        memories: Sequence[str] = (),
        work: Sequence[str] = (),
        context: Sequence[str] = (),
        walkthroughs: Sequence[str] = (),
    ) -> Checkpoint:
        """Record what has happened since the last checkpoint.

        Stamped from this machine's clock, not from anything the caller passes.
        A checkpoint is evidence about when something happened, and evidence a
        caller can set is not evidence.

        A walkthrough step is declared like a changed context document, and
        kept with them: `wrap` holds both to having changed and tells them
        apart by the type of document each names (core/04 section 37).
        """
        recorded = Checkpoint(
            session=session,
            recorded_at=self._now(),
            note=note,
            actions=tuple(actions),
            # Recorded verbatim. Never4gA does not interpret a declaration;
            # rewording one would be having an opinion about it.
            decisions=tuple(decisions),
            memories=tuple(memories),
            work=tuple(work),
            context=(*context, *walkthroughs),
        )
        self._store.append(recorded)
        return recorded

    def record_given_context(
        self, session: SessionId, documents: Sequence[StoredDocument]
    ) -> tuple[GivenContext, ...]:
        """Record the context documents a startup pack carried (core/04 section 37).

        The digest is of each body as it is on disk now, whether the pack
        carried it or only a reference to it: a document too large for the
        budget still reached the session by name.
        """
        given = tuple(
            GivenContext(
                session=session,
                concept_id=document.concept_id,
                path=document.path,
                title=str(document.frontmatter.get("title") or document.path.name),
                digest=body_digest(document.body),
            )
            for document in documents
        )
        self._store.record_given_context(given)
        return given

    def given_context(self, session: SessionId) -> Sequence[GivenContext]:
        """What the session's startup carried from `Context/`, in order."""
        return self._store.given_context(session)

    def record_work_action(
        self, session: SessionId, *, item: str, verb: str, detail: str = ""
    ) -> WorkAction:
        """Record a tracker write this session performed.

        Called by the write verbs themselves rather than by an agent, which is
        the whole point: a declaration is a claim, and this is an observation.
        """
        action = WorkAction(
            session=session,
            recorded_at=self._now(),
            item=item,
            verb=verb,
            detail=detail,
        )
        self._store.record_work_action(action)
        return action

    def work_actions(self, session: SessionId) -> Sequence[WorkAction]:
        """Every tracker write recorded for one session, oldest first."""
        return self._store.work_actions(session)

    def outstanding_work(self, session: SessionId) -> tuple[str, ...]:
        """Items this session said may need an update and never touched.

        `core/04` section 37 asks `wrap` to report "what work items may need an
        update". Reporting the declarations alone would say the same thing
        whether or not anything was done about them, and leave a person to
        reconcile the tracker by hand.

        Resolution is deliberately shallow. `--work` is free text and Never4gA
        reads no intent out of prose: an item is whatever leading token the
        declaration starts with, and a declaration naming none is still reported
        as a candidate but is not something this can call unactioned. Guessing
        would produce the one failure that matters -- a `wrap` that objects to
        work nobody ever asked for.
        """
        acted = {action.item for action in self.work_actions(session)}
        declared: list[str] = []
        for checkpoint in self.checkpoints(session):
            for declaration in checkpoint.work:
                item = _declared_item(declaration)
                if item is not None and item not in acted and item not in declared:
                    declared.append(item)
        return tuple(declared)

    def checkpoints(self, session: SessionId) -> Sequence[Checkpoint]:
        """Everything recorded in one session, oldest first."""
        return self._store.checkpoints(session)

    def recent(self, workspace: ConceptId, *, limit: int = 10) -> Sequence[Session]:
        """The workspace's most recently active sessions, newest first."""
        return self._store.recent(workspace, limit=limit)


def _declared_item(declaration: str) -> str | None:
    """The tracker id a `--work` declaration opens with, if it opens with one.

    The Skill's shape is ``"840: ready to close, the read-back is in"``, so the
    id is the first token before a colon or whitespace. Anything that is not a
    bare identifier is prose, and prose is a candidate for a person to read
    rather than something to hold a session to.
    """
    head = declaration.strip().split(":", 1)[0].strip().split()
    if not head:
        return None
    candidate = head[0].lstrip("#")
    return candidate if candidate.isalnum() and any(c.isdigit() for c in candidate) else None
