"""The local background service (core/05 sections 10 to 13, 18 and 19).

One per-user process that owns the index, watches the vault and answers on
loopback. It is a composition root: with the CLI and the MCP server, it is one
of the places that choose concrete adapters.

This package is the only home of `uvicorn`. The API layer above it knows
nothing about how it is served.
"""

from __future__ import annotations

from never4ga.service.runtime import (
    ReconcileLoop,
    ServiceSettings,
    ServiceStartupError,
    StartupReport,
    StartupStep,
    VaultRuntime,
)
from never4ga.service.server import LocalService, ServiceNotRunningError, serve

__all__ = [
    "LocalService",
    "ReconcileLoop",
    "ServiceNotRunningError",
    "ServiceSettings",
    "ServiceStartupError",
    "StartupReport",
    "StartupStep",
    "VaultRuntime",
    "serve",
]
