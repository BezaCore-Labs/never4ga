"""CLI rendering.

Two output modes, because a Skill or script must not have to parse English
(`details/api-cli-mcp-contract.md` section 4). The JSON mode is the contract; the
human mode is a rendering of the same data and never carries information the
JSON lacks.

Errors follow section 12: a stable code, a message, details, whether retrying
could help, and a repair hint.
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from typing import Any, TextIO

from never4ga.errors import StructuredError

__all__ = ["Format", "Reporter", "StructuredError"]


class Format(StrEnum):
    HUMAN = "human"
    JSON = "json"


class Reporter:
    """Writes one command's result in the requested format."""

    def __init__(
        self,
        output_format: Format,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._format = output_format
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr

    @property
    def is_json(self) -> bool:
        return self._format is Format.JSON

    def emit(self, payload: dict[str, Any], human: str) -> None:
        # Flushed rather than left to process exit: `never4ga serve` reports
        # what it is serving and then blocks, and a block-buffered pipe would
        # hold that line until the service stopped.
        if self.is_json:
            print(json.dumps(payload, indent=2, sort_keys=False), file=self._stdout, flush=True)
        elif human:
            print(human, file=self._stdout, flush=True)

    def note(self, message: str) -> None:
        """Something the user should know that is not this command's result.

        stderr, always, and never in the JSON payload: a caller parsing stdout
        is parsing the answer to what it asked, and a note about the machine's
        state is not part of that answer. It still has to be *said*: a silent
        note is how a stale service goes unnoticed.
        """
        print(f"note: {message}", file=self._stderr, flush=True)

    def fail(self, error: StructuredError) -> None:
        if self.is_json:
            print(json.dumps({"error": error.as_dict()}, indent=2), file=self._stderr, flush=True)
            return
        print(f"error: {error.message}", file=self._stderr, flush=True)
        if error.repair_hint:
            print(f"  hint: {error.repair_hint}", file=self._stderr, flush=True)


def bullet_list(items: list[str], empty: str) -> str:
    return "\n".join(f"  {item}" for item in items) if items else f"  {empty}"
