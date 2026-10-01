"""Null implementations.

A null implementation is a *valid* implementation of its port that provides no
capability. It exists so that optional subsystems can be absent without the
calling code branching on ``None`` (core/05 section 19).
"""

from never4ga.adapters.null.memory import (
    NullExternalMemoryProvider,
    NullTemporalGraphProvider,
)
from never4ga.adapters.null.service_manager import NullServiceManager
from never4ga.adapters.null.workspace_mappings import NullWorkspaceMappingStore

__all__ = [
    "NullExternalMemoryProvider",
    "NullServiceManager",
    "NullTemporalGraphProvider",
    "NullWorkspaceMappingStore",
]
