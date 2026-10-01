"""The structured error every interface returns.

details/api-cli-mcp-contract.md section 12: an error carries a code, a message,
details, whether retrying could help, and a repair hint -- "do not force agents
to parse English-only stack traces".

It lives beside the exception hierarchy because the CLI, the HTTP API and the
MCP server all render it, and none of them may own a private version. An
interface decides how to *transport* it; what it contains is one definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["StructuredError"]


@dataclass(frozen=True, slots=True)
class StructuredError:
    """details/api-cli-mcp-contract.md section 12."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    repair_hint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "repair_hint": self.repair_hint,
        }
