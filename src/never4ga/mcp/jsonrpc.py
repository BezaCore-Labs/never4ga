"""JSON-RPC 2.0 over a newline-delimited stream.

Never4gA implements this itself rather than depending on an SDK with a large
dependency tree. The surface is small and closed, and this module is all of it:
read messages, write messages, and one error type that knows the protocol's
shape.

**Framing is one JSON object per line.** A value containing a newline is
therefore a correctness concern: `json.dumps` escapes it, and
:func:`write_message` never writes anything it did not encode.

**A notification is a request without an ``id``, and must not be answered.**
A request whose id is null is still a request. The difference is absence, not
emptiness, and confusing them means answering something the client is not
listening for.

Never4gA's errors carry more than JSON-RPC's code and message
(`details/api-cli-mcp-contract.md` section 12: a code, details, whether retrying
helps, a repair hint). The protocol's one place for that is ``data``, and
:class:`JsonRpcError` puts it there so MCP clients get the repair hint too.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import IO, Any, Final

__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "JsonRpcError",
    "Request",
    "read_messages",
    "write_message",
]

#: The only value the ``jsonrpc`` member may take.
PROTOCOL_VERSION: Final = "2.0"

# The protocol's own reserved codes, spelled out because this module owns the
# protocol rather than importing it.
PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603

#: Absence of an ``id``, which is what makes a message a notification. ``None``
#: cannot stand in for it: ``{"id": null}`` is a request with a null id.
_ABSENT: Final = object()


@dataclass(frozen=True, slots=True)
class Request:
    """One incoming message."""

    method: str
    #: The caller's correlation value, kept in whatever type it arrived as: a
    #: client that sent a string and got back a number cannot match them up.
    id: Any = None
    params: Mapping[str, Any] = field(default_factory=dict)
    #: True when the message carried no ``id`` at all.
    is_notification: bool = False


class JsonRpcError(Exception):
    """An error already shaped for the protocol."""

    def __init__(self, code: int, message: str, *, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_response(self, identifier: Any) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return {"jsonrpc": PROTOCOL_VERSION, "id": identifier, "error": error}


def read_messages(source: IO[str]) -> Iterator[Request]:
    """Every message on ``source``, in order, until it closes.

    Blank lines are skipped rather than refused: a stream that flushes a newline
    on its own is not a client making a mistake.
    """
    for line in source:
        text = line.strip()
        if not text:
            continue
        yield _parse(text)


def write_message(sink: IO[str], message: Mapping[str, Any]) -> None:
    """One message, one line, flushed.

    Flushed because the peer is a process waiting on a pipe: a buffered
    response is a client that hangs.
    """
    sink.write(json.dumps(dict(message), ensure_ascii=False) + "\n")
    sink.flush()


def _parse(text: str) -> Request:
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise JsonRpcError(PARSE_ERROR, f"not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise JsonRpcError(INVALID_REQUEST, "a message must be a JSON object")
    if payload.get("jsonrpc") != PROTOCOL_VERSION:
        raise JsonRpcError(INVALID_REQUEST, f"a message must declare jsonrpc {PROTOCOL_VERSION!r}")
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(INVALID_REQUEST, "a message must name a method")
    params = payload.get("params", {})
    if not isinstance(params, Mapping):
        # Positional params are legal JSON-RPC and MCP does not use them.
        # Accepting them would mean guessing which argument was which.
        raise JsonRpcError(INVALID_PARAMS, "params must be an object, not a list")
    identifier = payload.get("id", _ABSENT)
    return Request(
        method=method,
        id=None if identifier is _ABSENT else identifier,
        params=dict(params),
        is_notification=identifier is _ABSENT,
    )
