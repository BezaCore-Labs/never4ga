"""Clearing the Inbox by accounting for what is in it.

`capture` exists so a thought can be kept without deciding what it is
(`services/capture.py`), and `context/inbox_signals.py` puts the resulting
pile in front of every session. This is the other end: the verb that takes an
item *out*, and the rule it enforces.

The rule: an item is filed as a ticket, a knowledge note or whatever it turns
out to be; **nothing leaves the Inbox until it is
accounted for, and once it is accounted for it leaves.** Both halves matter.
An item filed and left behind makes the pile permanent, which is how an inbox
stops being read at all; an item removed without being filed is a thought
thrown away, which is what capturing it was meant to prevent.

So "accounted for" is checked rather than asserted, and the check follows the
line `wrap` already draws. A concept must **exist in the vault**. A work item
must have a write this session **actually recorded** -- an agent saying it
filed a ticket is a declaration, and `SessionStore.work_actions` is an
observation. Never4gA believes the second kind.

What this deliberately does not do is decide *where* an item belongs. That is
classification, `core/07` section 1 keeps it off the mechanical path, and the
reader of the pack is the one who knows. This verb is only the accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from never4ga.context.structural import (
    VAULT_WIDE_PACK_TYPES,
    WORKSPACE_SCOPED_PACK_TYPES,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.errors import Never4gaError
from never4ga.layout import (
    INBOX_RESERVED_NAMES,
    INBOX_SCRATCHPAD_NAME,
    VaultRoot,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.session_store import SessionStore
from never4ga.ports.vault_files import VaultFileStore
from never4ga.services.scaffold import INBOX_SCRATCHPAD

__all__ = ["InboxError", "InboxService", "Resolved", "ScratchLine"]

_BULLET: Final = "- "


class InboxError(Never4gaError):
    """The item could not be accounted for, so it was left where it is.

    ``hint`` carries what to do about *this* refusal when the generic one would
    mislead. "File it first, then clear it" is right when the destination does
    not exist and wrong when it exists and simply cannot be seen again -- the
    item in that case is already filed, and repeating the advice sends someone
    to do what they just did.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True, slots=True)
class ScratchLine:
    """One thought on the scratchpad, and where it sits.

    ``number`` is 1-based and positional, which is what a person reading the
    file sees. It is not an identity: draining line 2 renumbers what was line
    3, exactly as it does on paper.
    """

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Resolved:
    """One item, cleared."""

    path: VaultPath
    #: What it became -- a concept id or a work item's identifier. `None` for
    #: a discard, which is an accounting too: a reason rather than a home.
    became: str | None
    reason: str | None = None


class InboxService:
    """Lists what is waiting, and removes what has been accounted for."""

    def __init__(
        self,
        files: VaultFileStore,
        documents: DocumentStore,
        sessions: SessionStore | None = None,
    ) -> None:
        self._files = files
        self._documents = documents
        self._sessions = sessions

    def pending(self) -> tuple[VaultPath, ...]:
        """Every unprocessed capture, oldest first.

        The same rule the startup signal applies, and deliberately the same
        code path's shape: a listing that disagreed with the pack would send a
        session to file items it cannot see.
        """
        prefix = f"{VaultRoot.INBOX.value}/"
        return tuple(
            sorted(
                path
                for path in self._files.iter_paths()
                if str(path).startswith(prefix) and path.name not in INBOX_RESERVED_NAMES
            )
        )

    def resolve(
        self,
        path: VaultPath,
        *,
        concept: ConceptId | None = None,
        work_item: str | None = None,
        session: SessionId | None = None,
        discard: bool = False,
        reason: str | None = None,
    ) -> Resolved:
        """Clear one item, having established where it went.

        Refuses before it removes, always. Every refusal leaves the file
        exactly where it was: the failure mode this must never have is losing
        a thought to a mistyped destination.
        """
        self._require_inbox_item(path)
        became, why = self._account_for(str(path), concept, work_item, session, discard, reason)
        self._files.remove(path)
        return Resolved(path=path, became=became, reason=why)

    # -- the scratchpad ---------------------------------------------------

    def scratch(self, text: str) -> ScratchLine:
        """Append one thought, creating the scratchpad if it is not there yet.

        `capture` writes a file per thought, which is right for a paragraph and
        heavy for a line: eight of them make eight files to read back and eight
        to clear. This is the other size.
        """
        thought = " ".join(text.split())
        if not thought:
            raise InboxError("there is nothing to write down")
        path = self._scratchpad_path()
        if self._files.read_text(path) is None:
            self._files.ensure_directory(VaultPath.parse(str(VaultRoot.INBOX)))
            self._files.write_text(path, INBOX_SCRATCHPAD)
        texts = (*self._scratch_texts(), thought)
        self._write_scratch(path, texts)
        return ScratchLine(number=len(texts), text=thought)

    def scratch_lines(self) -> tuple[ScratchLine, ...]:
        """Every thought on the scratchpad, in the order it was written."""
        return tuple(
            ScratchLine(number=index, text=text)
            for index, text in enumerate(self._scratch_texts(), start=1)
        )

    def resolve_scratch(
        self,
        number: int,
        *,
        concept: ConceptId | None = None,
        work_item: str | None = None,
        session: SessionId | None = None,
        discard: bool = False,
        reason: str | None = None,
    ) -> Resolved:
        """Take one thought off, having established where it went.

        The accounting is the file's, unchanged. A line is smaller than a
        capture; it is not more expendable, and a line removed without an
        accounting is a thought thrown away just as surely.
        """
        path = self._scratchpad_path()
        texts = self._scratch_texts()
        if not 1 <= number <= len(texts):
            raise InboxError(
                f"there is no line {number} on the scratchpad; it has "
                f"{len(texts)} line(s). Run `never4ga scratch list` to see them"
            )
        became, why = self._account_for(
            f"{path} line {number}", concept, work_item, session, discard, reason
        )
        self._write_scratch(path, texts[: number - 1] + texts[number:])
        return Resolved(path=path, became=became, reason=why)

    def _scratchpad_path(self) -> VaultPath:
        return VaultPath.parse(f"{VaultRoot.INBOX.value}/{INBOX_SCRATCHPAD_NAME}")

    def _scratch_texts(self) -> tuple[str, ...]:
        text = self._files.read_text(self._scratchpad_path())
        if text is None:
            return ()
        return tuple(
            line.strip()[len(_BULLET) :].strip()
            for line in text.splitlines()
            if line.startswith(_BULLET) and line.strip() != _BULLET.strip()
        )

    def _write_scratch(self, path: VaultPath, texts: tuple[str, ...]) -> None:
        """Rewrite the file, keeping every line that is not a thought.

        Prose survives because a scratchpad somebody has written a heading into
        is still theirs. Only bullets move.
        """
        original = self._files.read_text(path) or INBOX_SCRATCHPAD
        kept = [line for line in original.splitlines() if not line.startswith(_BULLET)]
        while kept and not kept[-1].strip():
            kept.pop()
        bullets = [f"{_BULLET}{text}" for text in texts]
        body = "\n".join([*kept, "", *bullets]) if bullets else "\n".join(kept)
        self._files.write_text(path, body.rstrip("\n") + "\n")

    # -- internals --------------------------------------------------------

    def _account_for(
        self,
        subject: str,
        concept: ConceptId | None,
        work_item: str | None,
        session: SessionId | None,
        discard: bool,
        reason: str | None,
    ) -> tuple[str | None, str | None]:
        """Where this went, or a refusal. Removes nothing.

        One rule, used by the captured file and by a line on the scratchpad.
        The two are different sizes of the same thing, and a second copy of the
        rule would drift the moment one of them grew a case.
        """
        if discard:
            if not (reason or "").strip():
                raise InboxError(
                    f"discarding {subject} needs a reason; say why it is not worth keeping"
                )
            return None, reason
        if concept is not None:
            return str(self._reachable_concept(subject, concept)), None
        if work_item is not None:
            return self._recorded_work_item(subject, work_item, session), None
        raise InboxError(
            f"{subject} has not been accounted for; name the concept it became "
            "(--into), the work item it became (--work), or discard it with a reason"
        )

    def _require_inbox_item(self, path: VaultPath) -> None:
        """This verb removes files, so it may only ever see one of its own.

        A path check rather than a caller's promise: the difference between a
        capture and somebody's note is one directory, and the consequence of
        getting it wrong is a deleted document.
        """
        if (
            not str(path).startswith(f"{VaultRoot.INBOX.value}/")
            or path.name in INBOX_RESERVED_NAMES
        ):
            raise InboxError(
                f"{path} is not an Inbox item; this only clears {VaultRoot.INBOX.value}/"
            )
        if not self._files.exists(path):
            raise InboxError(f"{path} is not there; nothing to clear")

    def _reachable_concept(self, subject: str, concept: ConceptId) -> ConceptId:
        document = self._documents.get(concept)
        if document is None:
            raise InboxError(
                f"{concept} is not in this vault, so {subject} is not accounted for; "
                "create the concept first, then clear the item"
            )
        unreachable = _why_it_will_not_resurface(document)
        if unreachable is not None:
            concept_type = str(document.frontmatter.get("type", "concept"))
            raise InboxError(
                f"{subject} would become {document.path}, and a {concept_type} there "
                f"will not resurface: {unreachable}. The Inbox is a surface that "
                "does; every startup pack carries what is waiting in it. So the "
                "item stays where it is, and gets asked about again.",
                hint="the destination exists, so this is not a filing error: give it "
                "a scope a structural lane can match, choose a destination that "
                "resurfaces, or discard the item with a reason",
            )
        return concept

    def _recorded_work_item(self, subject: str, work_item: str, session: SessionId | None) -> str:
        """A write this session actually made, not a claim that it made one.

        The same line `wrap` draws. An agent saying it filed a ticket is a
        declaration; `SessionStore.work_actions` is an observation.
        """
        if self._sessions is None or session is None:
            raise InboxError(
                f"{subject} names work item {work_item}, but without a session Never4gA "
                "cannot see the write that would account for it; pass --session"
            )
        acted = {action.item for action in self._sessions.work_actions(session)}
        if work_item not in acted:
            raise InboxError(
                f"this session recorded no write against work item {work_item}, so "
                f"{subject} is not accounted for; file it with `never4ga work create "
                "--session <id> --apply` first"
            )
        return work_item


def _why_it_will_not_resurface(document: StoredDocument) -> str | None:
    """Why a startup pack would never carry this document, or ``None``.

    An item may leave the Inbox only for a place that will resurface it later.
    That a destination **exists** is a different property: a goal filed where
    no lane matches it appears in no pack, and removing the Inbox copy would
    remove the only reminder.

    The question asked here is narrow on purpose. It is *not* "is this the right
    home" -- that is classification, `core/07` section 1 keeps it off the
    mechanical path, and a verb that started guessing would be wrong in both
    directions. It is:
    the pack carries this **type**, so could it carry *this one*? Reference
    material is untouched, because a `knowledge` note never promised to arrive
    unasked; it is found by looking for it, and filing a thought there is a
    complete disposal. A `goal` does promise it, and one no lane can match is a
    broken promise.
    """
    concept_type = str(document.frontmatter.get("type", "")).strip()
    if document.path.root == VaultRoot.ARCHIVE.value:
        # `is_detached`: archived material is excluded from every structural
        # lane, not ranked low within one, so a workspace id does not save it.
        return "90_Archive is excluded from every structural lane"
    if concept_type in VAULT_WIDE_PACK_TYPES:
        return None
    if concept_type not in WORKSPACE_SCOPED_PACK_TYPES:
        # Reference material, and reachable the way reference material is.
        return None
    if not str(document.frontmatter.get("workspace", "")).strip():
        scope = "an area" if document.path.root == VaultRoot.LIFE.value else "no workspace"
        return f"the {concept_type} lane queries by workspace and this one names {scope}"
    return None
