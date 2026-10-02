"""The MCP server loop: initialize, list tools, call one, repeat.

This is all of MCP that a tools-only server needs. Four methods and one
notification:

```text
initialize                 -> protocolVersion, capabilities, serverInfo
notifications/initialized  -> nothing; a notification must not be answered
tools/list                 -> the tools, with their JSON schemas
tools/call                 -> one tool's result
ping                       -> {}
```

**One request at a time, in order.** stdio is a pipe and Never4gA's services are
synchronous; concurrency here would buy nothing and cost the ordering guarantee
a client reasonably expects.

**Errors keep their vocabulary.** A tool that fails returns a *tool* error --
MCP's `isError`, so the model sees it and can act -- while a malformed request
gets a JSON-RPC error, because that is a client bug rather than something a
model should reason about. `details/api-cli-mcp-contract.md` section 12's code,
detail and repair hint survive both routes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any, Final

from never4ga import __version__
from never4ga.errors import Never4gaError, StructuredError
from never4ga.mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcError,
    Request,
    read_messages,
    write_message,
)
from never4ga.mcp.tools import Tool

__all__ = ["MCP_PROTOCOL_VERSION", "SERVER_NAME", "McpServer", "serve"]

#: The MCP revision this server implements. Sent in `initialize`; a client that
#: speaks a different one is told what this is rather than guessed at.
MCP_PROTOCOL_VERSION: Final = "2025-06-18"

SERVER_NAME: Final = "never4ga"


@dataclass(frozen=True, slots=True)
class ToolFailureError(Exception):
    """A tool that ran and could not do what was asked.

    Distinct from a protocol error on purpose: this is a result the model
    should see and may act on -- "the vault has not been indexed, run
    `never4ga index`" -- rather than a fault in the message it sent.
    """

    error: StructuredError


class McpServer:
    """One vault's tools, over one pair of streams."""

    def __init__(self, tools: Sequence[Tool], *, version: str = __version__) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._version = version

    # -- the loop ---------------------------------------------------------

    def serve(self, source: IO[str], sink: IO[str]) -> None:
        """Read until the stream closes, answering everything that wants one."""
        while True:
            try:
                for message in read_messages(source):
                    response = self.handle(message)
                    if response is not None:
                        write_message(sink, response)
                return
            except JsonRpcError as error:
                # A malformed message has no id to correlate against, so the
                # protocol says answer with null and keep the connection.
                write_message(sink, error.as_response(None))

    def handle(self, message: Request) -> dict[str, Any] | None:
        """One message in, at most one message out.

        ``None`` for a notification, which must not be answered at all.
        """
        try:
            result = self._dispatch(message)
        except JsonRpcError as error:
            return None if message.is_notification else error.as_response(message.id)
        if message.is_notification:
            return None
        return {"jsonrpc": "2.0", "id": message.id, "result": result}

    # -- methods ----------------------------------------------------------

    def _dispatch(self, message: Request) -> dict[str, Any]:
        match message.method:
            case "initialize":
                return self._initialize()
            case "notifications/initialized" | "notifications/cancelled":
                return {}
            case "ping":
                return {}
            case "tools/list":
                return {"tools": [tool.describe() for tool in self._tools.values()]}
            case "tools/call":
                return self._call(message.params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"this server does not implement {message.method!r}")

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            # Only tools. Resources would restate what a tool already answers,
            # and two ways to ask one question are two things to keep in step.
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": self._version},
        }

    def _call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise JsonRpcError(INVALID_PARAMS, "tools/call needs a tool name")
        tool = self._tools.get(name)
        if tool is None:
            raise JsonRpcError(METHOD_NOT_FOUND, f"there is no tool called {name!r}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise JsonRpcError(INVALID_PARAMS, "arguments must be an object")
        try:
            payload = tool.run(dict(arguments))
        except ToolFailureError as failure:
            return _content(failure.error.as_dict(), is_error=True)
        except Never4gaError as error:
            # Anything a service raised rather than reported. It is still the
            # tool failing rather than the protocol, so the model sees it.
            return _content(
                StructuredError(type(error).__name__, str(error)).as_dict(), is_error=True
            )
        except Exception as error:
            raise JsonRpcError(INTERNAL_ERROR, f"{name} raised {type(error).__name__}") from error
        return _content(payload)


def _content(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    """An MCP tool result carrying Never4gA's payload verbatim.

    The structured half is byte-identical to what the CLI emits for the same
    request, because both come from `never4ga.rendering`. It travels twice, as
    JSON text a model can read and as `structuredContent` a client can parse.
    Both are built from the same object, so they cannot drift apart.
    """
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": dict(payload),
        "isError": is_error,
    }


def serve(tools: Sequence[Tool], source: IO[str], sink: IO[str]) -> None:
    McpServer(tools).serve(source, sink)
