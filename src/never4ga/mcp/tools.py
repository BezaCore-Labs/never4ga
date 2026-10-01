"""What a tool is, and what makes one worth exposing.

`details/api-cli-mcp-contract.md` section 5 asks for user-meaningful operations
rather than many small storage-level tools. A tool here is a verb a person would
use: resolve this workspace, start a session, search the vault.

A tool carries its own JSON Schema because that is how a model learns to call
it. The schemas are written by hand rather than derived from service signatures,
for the reason `core/05` section 15 keeps pydantic inside `api`: a derived
schema would make the service's shape the contract, and the contract is
`details/api-cli-mcp-contract.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Tool", "optional_string", "schema"]

type ToolRun = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    """One user-meaningful operation, and how a model calls it."""

    name: str
    description: str
    run: ToolRun
    input_schema: Mapping[str, Any] = field(default_factory=lambda: schema())

    def describe(self) -> dict[str, Any]:
        """What `tools/list` says about this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


def schema(
    properties: Mapping[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A JSON Schema object for a tool's arguments.

    ``additionalProperties`` stays open on purpose. `core/02` tolerates unknown
    fields rather than refusing them, and a client that sends a hint this
    version does not read should not be rejected for it.
    """
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
    }


def optional_string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}
