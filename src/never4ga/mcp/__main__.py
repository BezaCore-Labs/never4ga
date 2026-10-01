"""`never4ga-mcp`: the server as a process a client spawns.

The transport is stdio, so the streams are the protocol. Nothing may write to
stdout except :func:`never4ga.mcp.jsonrpc.write_message`: a stray `print` is a
corrupt frame, and the client then cannot parse the next message either.
Diagnostics go to stderr, which a client ignores.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from never4ga.config import default_vault_root
from never4ga.mcp.server import McpServer
from never4ga.mcp.toolbox import DEFAULT_ACTOR, Toolbox

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="never4ga-mcp",
        description="Never4gA's tools over MCP, on stdio.",
    )
    parser.add_argument(
        "--vault",
        default=None,
        help="the vault to serve; defaults to NEVER4GA_VAULT or the configured one",
    )
    parser.add_argument(
        "--actor",
        default=DEFAULT_ACTOR,
        help=f"who anything written records as its author (default: {DEFAULT_ACTOR})",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="never route through a running service, even when one answers",
    )
    arguments = parser.parse_args(argv)

    root = _vault_root(arguments.vault)
    if root is None:
        # The protocol has not started, so no client is listening for a
        # JSON-RPC error yet. A plain message on stderr is the right answer.
        print(
            "never4ga-mcp: no vault directory. Pass --vault, set NEVER4GA_VAULT, or configure one.",
            file=sys.stderr,
        )
        return 2

    toolbox = Toolbox(root, actor=arguments.actor, local=arguments.local)
    McpServer(toolbox.tools()).serve(sys.stdin, sys.stdout)
    return 0


def _vault_root(given: str | None) -> Path | None:
    """Resolve the vault the same three ways the CLI does, in the same order.

    Two interfaces on one machine must not disagree about which vault they serve.
    """
    root = Path(given or default_vault_root()).expanduser().resolve()
    return root if root.is_dir() else None


if __name__ == "__main__":  # pragma: no cover -- the executable's own entry
    raise SystemExit(main())
