"""Where vendored capability material came from, and what arrived.

Named `vendoring` rather than `provenance` because `domain/provenance.py`
already means something else: the acquisition provenance `core/07` section 12
puts on every Context Pack item. Two different facts share one English word,
and one module for both would leave every reader asking which sense was meant.

`core/04` section 25 governs the hop from vault to client, and Never4gA's own
shipped Skills reach the vault by a second hop. Both assume Never4gA wrote the
content. Material vendored from somewhere else travels a third hop that neither
covers, and core/02 section 21.21 records it.

The three parts answer three different questions and none substitutes for
another:

    origin      where it came from
    integrity   what arrived
    upstream    what is there now

Origin without integrity detects nothing: there is no record of what arrived
to compare against. Integrity without origin can never ask whether upstream
moved at all. Together they separate four states, and the fourth is the only
one that needs a person: unchanged, changed locally, changed upstream, and
changed on both sides.

Values only. Computing a digest needs a file store and asking a remote needs a
network, so both live in `services`; this layer is what they agree on.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from never4ga.domain.deployments import tree_hash

__all__ = [
    "DIGEST_ALGORITHM",
    "Drift",
    "Integrity",
    "Origin",
    "OriginKind",
    "SkillProvenance",
    "Upstream",
    "UpstreamStatus",
    "file_digests",
    "integrity_of",
]

#: One algorithm, named in the record so a future second one is a migration
#: rather than an ambiguity.
DIGEST_ALGORITHM: Final = "sha256"


class OriginKind(StrEnum):
    """How the material got here."""

    #: A Git remote. The only kind that can be re-checked without a network
    #: round trip being a download, and the only one requiring `commit`.
    GIT = "git"
    #: Fetched over HTTP with no revision identity of its own.
    HTTP = "http"
    #: Copied from somewhere on this machine.
    LOCAL = "local"
    #: Written here. Recorded so that "no provenance" stays distinguishable
    #: from "authored here", which are different facts with the same silence.
    AUTHORED = "authored"


class UpstreamStatus(StrEnum):
    """What the last check found, or that there has not been one."""

    #: Upstream is at the commit this copy was taken from.
    CURRENT = "current"
    #: Upstream has moved past it.
    AHEAD = "ahead"
    #: Not checked, or the check could not complete. An honest answer rather
    #: than a failure: offline is a normal state, and a stored "unavailable"
    #: with a timestamp would make a missed check look like a result.
    UNKNOWN = "unknown"


class Drift(StrEnum):
    """Which side moved.

    Expressible only because both halves are recorded. `BOTH` is the only
    value that needs a human.
    """

    NONE = "none"
    LOCAL = "local"
    UPSTREAM = "upstream"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where the material came from."""

    kind: OriginKind
    fetched_at: str
    url: str | None = None
    #: What was asked for, such as `refs/heads/main`. A branch is not identity.
    ref: str | None = None
    #: What was actually taken. Identity, for a Git origin.
    commit: str | None = None
    #: Which directory within the origin, when it is not the root.
    subpath: str | None = None
    #: Recorded because a licence obligation that is not written down cannot be
    #: honoured, and vendored corpora are routinely mixed-licence.
    license: str | None = None
    author: str | None = None

    def __post_init__(self) -> None:
        if self.kind is OriginKind.GIT and not (self.url and self.commit):
            raise ValueError("a git origin must record both url and commit")


@dataclass(frozen=True, slots=True)
class Integrity:
    """What arrived, as one digest and a per-file breakdown."""

    tree_digest: str
    algorithm: str = DIGEST_ALGORITHM
    #: Optional, and worth its size: without it a mismatch says only that
    #: something changed.
    files: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Upstream:
    """What the last look at the origin found."""

    status: UpstreamStatus = UpstreamStatus.UNKNOWN
    last_checked_at: str | None = None
    last_seen_commit: str | None = None


@dataclass(frozen=True, slots=True)
class SkillProvenance:
    """One vendored directory's whole record."""

    skill_path: str
    origin: Origin
    integrity: Integrity
    upstream: Upstream = field(default_factory=Upstream)
    #: Whether the local copy no longer matches what arrived. A report, not a
    #: merge strategy: what happens when both sides have changed is deliberately
    #: left undecided.
    modified: bool = False

    def drift(self) -> Drift:
        """Which side moved, from what the record already holds."""
        upstream_moved = self.upstream.status is UpstreamStatus.AHEAD
        match (self.modified, upstream_moved):
            case (True, True):
                return Drift.BOTH
            case (True, False):
                return Drift.LOCAL
            case (False, True):
                return Drift.UPSTREAM
            case _:
                return Drift.NONE


def file_digests(files: Mapping[str, str]) -> dict[str, str]:
    """A digest per file, so a mismatch can name which one."""
    return {name: hashlib.sha256(files[name].encode("utf-8")).hexdigest() for name in sorted(files)}


def integrity_of(files: Mapping[str, str], *, per_file: bool = True) -> Integrity:
    """Hash a directory as it stands.

    The tree digest is `tree_hash`, unchanged -- the same construction that
    hashes a shipped Skill's body, so a first-party and a vendored record are
    comparable rather than merely similar.
    """
    return Integrity(
        tree_digest=tree_hash(files),
        files=file_digests(files) if per_file else {},
    )
