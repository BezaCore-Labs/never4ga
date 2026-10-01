"""How far Never4gA may go with a workspace's external tracker.

`core/03` section 15 puts two fields on the workspace declaration, and between
them they decide everything this module answers. ``mode`` says whether there is
an external tracker at all -- ``native`` means Never4gA's own ``Tasks/`` is
authoritative, ``none`` means there is no work management -- and ``sync_policy``
says how far Never4gA may go on its own: ``reference`` knows the mapping and
ingests nothing, ``read`` may query and build derived views, ``read_write`` may
also "create/update tracker work items when explicitly authorized".

That last clause is the reason :class:`WritePolicy` is not simply a boolean.
Section 22 requires Never4gA to distinguish three things -- read operations,
draft/proposed writes, and actual writes -- so the declaration alone cannot be
the whole answer. There is a second gate: the workspace says whether writing
is possible, and ``--apply`` on one invocation says whether *this* write
happens. Without it, a verb proposes and sends nothing.

**Everything here fails closed.** An absent declaration, an absent policy, a
policy spelled differently, a `read_write` beside `mode: native` -- each
refuses. A vault file is text a person edits, and a typo in one must never be
the difference between a proposal and a change to somebody's system of record.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from never4ga.errors import WriteNotPermittedError

__all__ = [
    "EXTERNAL_MODE",
    "INGESTING_POLICIES",
    "WRITING_POLICIES",
    "WriteDisposition",
    "WritePolicy",
]

#: `core/03` section 15's declaration lives under this key on the workspace.
WORK_MANAGEMENT_KEY: Final = "work_management"

#: The only mode with an external tracker to reach.
EXTERNAL_MODE: Final = "external"

#: The policies that permit reading on Never4gA's own initiative. ``reference``
#: is deliberately absent: section 15 says it "does not automatically
#: ingest/synchronize ticket data", and a startup pack is about as automatic as
#: it gets.
INGESTING_POLICIES: Final = frozenset({"read", "read_write"})

#: The policies that permit writing. One, and section 15 says why it should stay
#: one for now: "for the first implementation, `read` SHOULD precede
#: `read_write`".
WRITING_POLICIES: Final = frozenset({"read_write"})

#: What an undeclared ``sync_policy`` means. Not permission to do more -- the
#: same rule the read path applies, stated once so both halves share it.
DEFAULT_POLICY: Final = "read"


class WriteDisposition(enum.StrEnum):
    """What should happen to a write that has been asked for.

    Imperative rather than past tense: this is a decision about a write that
    has not happened yet, and naming it ``APPLIED`` would invite a caller to
    read it as a receipt.
    """

    #: The workspace does not permit this at all.
    REFUSE = "refuse"
    #: Permitted, but this invocation did not ask to send it. Section 22's
    #: draft/proposed state.
    PROPOSE = "propose"
    #: Permitted and asked for. Send it.
    APPLY = "apply"


@dataclass(frozen=True, slots=True)
class WritePolicy:
    """The two gates, as one question with one answer.

    Kept as a value rather than as a pair of conditions restated at each call
    site, because a gate that is re-derived is a gate that will eventually be
    derived differently in one place.
    """

    mode: str = ""
    sync_policy: str = DEFAULT_POLICY
    #: Whether this particular invocation asked to send (the second gate).
    applying: bool = False

    @classmethod
    def from_declaration(
        cls,
        declared: Mapping[str, Any] | None,
        *,
        applying: bool = False,
    ) -> WritePolicy:
        """Read `core/03` section 15's declaration, defaulting the way reads do.

        Anything that is not a mapping is treated as no declaration at all: a
        workspace whose ``work_management`` is a string or a list is malformed,
        and `doctor` is where a fault is reported, not here.
        """
        if not isinstance(declared, Mapping):
            return cls(applying=applying)
        return cls(
            mode=str(declared.get("mode", "")),
            sync_policy=str(declared.get("sync_policy", DEFAULT_POLICY)) or DEFAULT_POLICY,
            applying=applying,
        )

    @property
    def disposition(self) -> WriteDisposition:
        """Refuse, propose, or apply."""
        if not self.has_external_tracker:
            return WriteDisposition.REFUSE
        if self.sync_policy.strip() not in WRITING_POLICIES:
            return WriteDisposition.REFUSE
        return WriteDisposition.APPLY if self.applying else WriteDisposition.PROPOSE

    @property
    def has_external_tracker(self) -> bool:
        return self.mode.strip() == EXTERNAL_MODE

    def require_permitted(self, what: str) -> None:
        """Raise unless ``what`` may at least be proposed.

        A proposal is not a write, so this stands in front of both proposing
        and applying only when the *workspace* forbids the operation. The
        difference between proposing and applying is not an error; it is
        :attr:`disposition`.
        """
        if self.disposition is not WriteDisposition.REFUSE:
            return
        if not self.has_external_tracker:
            declared = self.mode.strip() or "none"
            raise WriteNotPermittedError(
                f"cannot {what}: this workspace declares mode {declared!r} and has no "
                "external tracker to write to"
            )
        raise WriteNotPermittedError(
            f"cannot {what}: this workspace declares sync_policy "
            f"{self.sync_policy.strip()!r}, and writing needs 'read_write'"
        )
