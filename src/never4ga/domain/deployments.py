"""What Never4gA deployed, where (core/04 section 25).

The ownership manifest. Section 25 requires that for each generated destination
the sync engine records the canonical source, the source hash, the deployed
hash, the adapter and a timestamp -- and every one of its seven rules depends on
having them:

    1. Never overwrite an unmanaged existing Skill directory.
    2. Detect name collisions.
    3. Never delete an unmanaged Skill.
    4. If a generated Skill was edited locally, do not destroy the edit silently.
    5. Report drift and require reconciliation.
    6. Removed canonical Skills may only remove copies proven to be managed.
    7. Support dry-run.

Two hashes rather than one, because they answer different questions. The source
hash moving means the vault changed and the copy is stale. The deployed hash no
longer matching what is on disk means somebody edited the copy, and rule 4 says
that edit is not ours to destroy.

This is durable machine-local state, like the workspace mappings: nothing can
rebuild it, because the vault does not know what is installed on any particular
machine.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from never4ga.domain.clients import DeploymentMechanism

__all__ = ["Deployment", "tree_hash"]

_SEPARATOR: Final = "\x1f"


def tree_hash(files: Mapping[str, str]) -> str:
    """A stable hash of a Skill directory.

    Over the whole tree, not just `SKILL.md`: a Skill may carry `scripts/`,
    `references/` and `assets/` (core/04 section 20), and a change to any of
    them is a change to the Skill. Sorted, so two machines agree.
    """
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(_SEPARATOR.encode("utf-8"))
        digest.update(files[name].encode("utf-8"))
        digest.update(_SEPARATOR.encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Deployment:
    """One Skill, as this machine deployed it to one client."""

    client_id: str
    name: str
    #: Absolute path on this machine. Recorded rather than recomputed, so that a
    #: descriptor whose path changes cannot orphan what an older one deployed.
    target: str
    #: Where it came from in the vault.
    source_path: str
    #: The canonical content when it was deployed.
    source_hash: str
    #: What was actually written. Equal to the source hash for a copy; for a
    #: symlink there is no second copy, so it is the same by construction.
    deployed_hash: str
    mechanism: DeploymentMechanism = DeploymentMechanism.COPY
    #: When. Never fabricated: the caller supplies the clock.
    deployed_at: str = ""
    #: A digest per deployed file. The tree hash above says *that* a copy
    #: changed; this says *which file*. Without it the promise in `core/04`
    #: section 25 rule 4 -- never destroy a local edit -- cannot be kept,
    #: because a directory-level record cannot tell an edited file from an
    #: untouched one. Empty on an older manifest that predates
    #: this field, so nothing may require it.
    files: Mapping[str, str] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.client_id, self.name)
