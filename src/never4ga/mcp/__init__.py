"""Never4gA's MCP server: another interface to the same services.

`core/05` section 13 makes this a thin client over :mod:`never4ga.services`,
like the CLI and the API. It carries no business logic. The structured payload
an MCP tool returns is byte-identical to the CLI's for the same request, because
both render one service result through one renderer.

**The protocol is implemented here rather than imported.** The MCP SDK pulls in
many more packages than this project depends on, including a second HTTP client
and several that serve a socket stdio never opens. The surface needed is small
and closed, and `jsonrpc.py` is all of it.

**stdio only.** `core/05` section 14 puts streamable HTTP later.

**Nothing here depends on the daemon.** The route is chosen per tool call, as
the CLI chooses per command: over the local API when the service answers, in
process when it does not (`core/05` section 19). A server that decided once at
spawn would keep a dead route for as long as a client holds it open.
"""

from __future__ import annotations

from never4ga.mcp.jsonrpc import JsonRpcError, Request

__all__ = ["JsonRpcError", "Request"]
