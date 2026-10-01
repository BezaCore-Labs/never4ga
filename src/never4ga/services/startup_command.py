"""The one place the documented startup command is written.

`--actor` is a global flag: it is added to the top-level parser, never to the
`context startup` subparser, so argparse only accepts it *before* the
subcommand. The startup Skill in `scaffold.py` and the `AGENTS.md` pointer in
`repository_pointers.py` both show the command, and a flag in the wrong place
gives every agent that follows them an argparse error on its first command.

Comparing a rendered artifact against its generator cannot catch that, because
each template is consistent with itself. So the invocation is authored once
here, and `tests/architecture/test_generated_commands_parse.py` feeds every
command this module emits to the real parser.

It matters beyond the error: an agent that drops the flag and reruns succeeds,
and then writes `generated.by` with no actor, which defeats the `core/02`
section 5.2 requirement the command exists to meet.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DOCUMENTED_STARTUP_COMMAND",
    "PLACEHOLDER_ACTOR",
    "PLACEHOLDER_CLIENT",
    "startup_command",
]

#: What a document shows in place of a real client id.
PLACEHOLDER_CLIENT: Final = "<your-client-id>"

#: What a document shows in place of a real actor. `core/02` section 5.2 wants
#: `<producer>/<version>`, so the placeholder has to show the slash.
PLACEHOLDER_ACTOR: Final = "<your-client-id>/<your-model>"


def startup_command(
    client: str = PLACEHOLDER_CLIENT,
    actor: str = PLACEHOLDER_ACTOR,
    *,
    cwd: str = '"$PWD"',
) -> str:
    """The startup invocation, with every global flag ahead of the subcommand.

    Callers pass placeholders when writing a document and real values when
    writing something a person will run. Both orderings would look equally
    plausible to a reader, which is the whole reason this is a function.
    """
    return f"never4ga --actor {actor} context startup --client {client} --cwd {cwd}"


#: The form every generated document shows. A single line: a backslash
#: continuation lets the flag drift onto a second line, out of sight of the
#: subcommand it has to precede.
DOCUMENTED_STARTUP_COMMAND: Final = startup_command()
