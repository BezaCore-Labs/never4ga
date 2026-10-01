"""Git as a mechanical context source.

core/07 section 4 makes Git one of the external working-state sources, and
section 13 says a Git adapter "SHOULD support read-only context acquisition
first". Nothing here writes, and nothing here asks a model anything: a branch
name and a list of changed paths are facts, and core/07 section 9 wants exactly
those rather than a generated paragraph about them.
"""

from __future__ import annotations

from never4ga.adapters.git.signals import GitSignalProvider

__all__ = ["GitSignalProvider"]
