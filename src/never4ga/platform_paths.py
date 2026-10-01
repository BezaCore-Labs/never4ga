"""Where derived and runtime state lives (core/05 sections 7 and 8).

core/05 section 7: "By default, derived/local runtime state MUST live outside the
Git-backed vault." Canonical Markdown never depends on any of these files, and
deleting all of them must cost nothing but the time to rebuild.

Section 7 also says the exact paths are "obtained through a platform
abstraction". It requires the abstraction, not a library. XDG is resolved here
with the standard library; if a second platform needs `platformdirs`, this
module is the one place that changes.

Nothing here touches the filesystem. Asking where something belongs must not
create it -- the component that writes is the component that creates.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from never4ga.domain.identity import ConceptId

__all__ = ["PlatformPaths"]

#: The application's directory name inside each platform base directory.
APPLICATION: Final = "never4ga"

#: The per-vault database of core/05 section 8. One file, one vault.
INDEX_DATABASE: Final = "index.sqlite3"

#: The per-vault tracker cache, beside the index rather than inside it.
#: `rebuild` discards a projection and builds it again from Markdown; a
#: tracker's operational state cannot be rebuilt from Markdown at all, and one
#: file would force `rebuild` to learn which tables to spare.
TRACKERS_DATABASE: Final = "trackers.sqlite3"

#: The per-vault session store. Beside the other two for the same
#: reason, and unlike them it is **not** derived: nothing in the vault ever
#: held a checkpoint, so nothing can rebuild one. `rebuild` must leave it
#: alone, and it is worth backing up.
SESSIONS_DATABASE: Final = "sessions.sqlite3"

#: The protected local secret store (core/05 section 21,
#: details/security-configuration.md section 4). Machine-wide rather than
#: per-vault: a credential is a fact about this installation.
SECRETS_FILE: Final = "secrets.json"

#: The repo-to-workspace map (core/04 section 13, core/05 section 9). Per vault,
#: beside the session store, so a repository mapped from one vault does not
#: resolve from another. Machine-local, since the vault directory is outside the
#: vault, and durable like the session store: nothing can rebuild it, because
#: the vault does not know where a repository sits on this machine. The older
#: machine-wide file of the same name under state is read only to adopt what
#: was written there.
WORKSPACES_FILE: Final = "workspaces.json"

#: The core/04 section 25 ownership manifest: what Never4gA deployed to which
#: client, and the hashes that make drift detectable. Durable, like the
#: mappings: losing it hands every deployed Skill over as unmanaged.
DEPLOYMENTS_FILE: Final = "deployments.json"

#: The extension registry (core/09 section 5), as far as it exists: what this
#: machine adopted, and when. Machine-local because a repository checkout is.
EXTENSIONS_FILE: Final = "extensions.json"

_VAULTS: Final = "vaults"
_DERIVED: Final = "derived"

#: XDG base directories and their fallbacks, per the freedesktop specification.
_DEFAULTS: Final = {
    "XDG_CONFIG_HOME": (".config",),
    "XDG_DATA_HOME": (".local", "share"),
    "XDG_STATE_HOME": (".local", "state"),
    "XDG_CACHE_HOME": (".cache",),
}


@dataclass(frozen=True, slots=True)
class PlatformPaths:
    """The four platform base directories, namespaced to Never4gA."""

    config: Path
    data: Path
    state: Path
    cache: Path

    @classmethod
    def resolve(
        cls,
        environment: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> PlatformPaths:
        """Read the XDG variables, falling back to the specified defaults.

        Both arguments exist so that tests never depend on the machine they run
        on, and so paths can be resolved for a service that does not share the
        caller's environment.
        """
        environment = os.environ if environment is None else environment
        home = Path.home() if home is None else home
        resolved = {
            variable: _base_directory(environment.get(variable), home, fallback) / APPLICATION
            for variable, fallback in _DEFAULTS.items()
        }
        return cls(
            config=resolved["XDG_CONFIG_HOME"],
            data=resolved["XDG_DATA_HOME"],
            state=resolved["XDG_STATE_HOME"],
            cache=resolved["XDG_CACHE_HOME"],
        )

    @property
    def secrets_file(self) -> Path:
        """Where the fallback secret store keeps its file. Never in the vault."""
        return self.state / SECRETS_FILE

    @property
    def machine_workspaces_file(self) -> Path:
        """The older machine-wide mappings file, read only to adopt its entries."""
        return self.state / WORKSPACES_FILE

    @property
    def deployments_file(self) -> Path:
        """The core/04 section 25 ownership manifest. Never in the vault."""
        return self.state / DEPLOYMENTS_FILE

    @property
    def extensions_file(self) -> Path:
        """The core/09 extension registry. Never in the vault."""
        return self.state / EXTENSIONS_FILE

    def vault_directory(self, vault_id: ConceptId) -> Path:
        """Everything derived from one vault, addressed by its stable identity.

        The vault's own UUID keys the directory (core/05 section 8), so moving
        the vault on disk does not orphan its index, and two vaults never
        collide however they are named.
        """
        return self.data / _VAULTS / str(vault_id)

    def index_database(self, vault_id: ConceptId) -> Path:
        return self.vault_directory(vault_id) / INDEX_DATABASE

    def trackers_database(self, vault_id: ConceptId) -> Path:
        """The tracker cache. Derived and disposable, like the index."""
        return self.vault_directory(vault_id) / TRACKERS_DATABASE

    def workspaces_file(self, vault_id: ConceptId) -> Path:
        """This vault's repo-to-workspace mappings. Durable, like the sessions."""
        return self.vault_directory(vault_id) / WORKSPACES_FILE

    def sessions_database(self, vault_id: ConceptId) -> Path:
        """Working session state. Durable, not derived; see the constant."""
        return self.vault_directory(vault_id) / SESSIONS_DATABASE

    def derived_directory(self, vault_id: ConceptId) -> Path:
        """Derived material that is not the database itself."""
        return self.vault_directory(vault_id) / _DERIVED


def _base_directory(value: str | None, home: Path, fallback: tuple[str, ...]) -> Path:
    """One XDG base directory.

    The specification is explicit that a relative path "must be considered
    invalid and ignored", which covers the empty string a shell leaves behind
    when a variable is set to nothing.
    """
    if value is None or not value.strip():
        return home.joinpath(*fallback)
    candidate = Path(value)
    return candidate if candidate.is_absolute() else home.joinpath(*fallback)
