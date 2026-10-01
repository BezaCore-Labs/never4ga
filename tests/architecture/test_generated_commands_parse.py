"""Every command a generated document tells an agent to run must parse.

`--actor` belongs to the top-level parser and not to the `context startup`
subparser, so argparse accepts it only before the subcommand. A template that
writes it after still renders as written, and every token in the line exists,
so neither a freshness check nor a reference check can see the mistake. Only
the parser knows whether the tokens compose.

So the parser is the oracle. This walks the artifacts Never4gA generates,
extracts every line that invokes the CLI, and requires the real parser to accept
it. A flag in the wrong position fails here on the commit that introduces it.

This proves only that a command's shape is legal, not that it succeeds.
Running a documented command against a real vault belongs in
`tests/integration/`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from never4ga.cli import _build_parser
from never4ga.services.scaffold import SKILLS, TEMPLATES

pytestmark = pytest.mark.architecture

#: A fenced block, whatever language it claims.
_FENCE: Final = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)

#: What a documented placeholder becomes so argparse sees a real token. Every
#: placeholder must be listed: an unrecognised one fails loudly rather than
#: being passed through, because a silently-substituted placeholder would let a
#: broken command look fine.
_SUBSTITUTIONS: Final = {
    "<your-client-id>/<your-model>": "claude-code/claude-opus-5",
    "<your-client-id>": "claude-code",
    "<the thought, in full>": "a thought",
    "<what happened, briefly>": "something happened",
    "<what it was about>": "the thing it was about",
    "<id>": "01a054b9-e2b1-72a5-a8d1-37991afc72d1",
    "<workspace-id>": "01a03428-7d75-703a-8b55-58b8d820bbb6",
    "<type>": "note",
    "<title>": "A Title",
    "<terms>": "some words",
    "<subject>": "50_System/Skills/example",
    "<concept-id>": "01a054b9-e2b1-72a5-a8d1-37991afc72d1",
    "<item>": "970",
    # A context declaration names its document by id or vault path.
    "<id or path>": "01a054b9-e2b1-72a5-a8d1-37991afc72d1",
    "<ref>": "01a054b9-e2b1-72a5-a8d1-37991afc72d1",
    # A scratchpad line is addressed by its position, as a reader sees it.
    "<n>": "1",
    "<why>": "not worth keeping",
    "<path>": "/tmp/scratch",
    '"$PWD"': "/tmp/scratch",
    "$PWD": "/tmp/scratch",
}

_PLACEHOLDER: Final = re.compile(r"<[^>]+>")


def _emitted_commands(text: str) -> Iterator[str]:
    """Every `never4ga …` invocation inside a fenced block.

    Fenced blocks are the *only* place worth looking and the place a reference
    checker would normally skip as "just an example". For a command-line tool
    the example is the claim.
    """
    for fence in _FENCE.findall(text):
        for raw in fence.splitlines():
            line = raw.strip().removeprefix("$ ").strip()
            if line.startswith("never4ga ") and not line.endswith("\\"):
                yield line


def _argv(command: str) -> list[str]:
    """Tokenise, substituting placeholders and refusing unknown ones."""
    resolved = command
    for placeholder, value in _SUBSTITUTIONS.items():
        resolved = resolved.replace(placeholder, value)
    if leftover := _PLACEHOLDER.findall(resolved):
        pytest.fail(
            f"{command!r} carries placeholders with no substitution: {leftover}. "
            f"Add them to _SUBSTITUTIONS rather than letting one through unchecked."
        )
    # comments=True because a documented command may carry a trailing `# why`,
    # which is part of the documentation and not part of the invocation.
    return shlex.split(resolved, comments=True)[1:]


def _generated_documents() -> Iterator[tuple[str, str]]:
    """Everything Never4gA generates that can carry a command."""
    for name, text in sorted(SKILLS.items()):
        yield f"skill:{name}", text
    for name, text in sorted(TEMPLATES.items()):
        yield f"template:{name}", text


def _assert_parses(source: str, command: str, parser: argparse.ArgumentParser) -> None:
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            parser.parse_args(_argv(command))
    except SystemExit as exit_signal:
        if exit_signal.code:
            pytest.fail(
                f"{source} documents a command the CLI rejects.\n"
                f"  command: {command}\n"
                f"  argparse: {stderr.getvalue().strip().splitlines()[-1:]}"
            )


def test_every_generated_command_parses() -> None:
    """No generated document may tell an agent to run something argparse refuses."""
    parser = _build_parser()
    seen = 0
    for source, text in _generated_documents():
        for command in _emitted_commands(text):
            _assert_parses(source, command, parser)
            seen += 1
    assert seen, "found no commands to check -- the extractor has stopped working"


def test_the_startup_command_is_checked() -> None:
    """Guard the guard.

    A regex that silently stops matching turns the test above into a pass that
    proves nothing. The startup invocation is the command most likely to carry
    a misplaced `--actor`.
    """
    startup = SKILLS["never4ga-startup"]
    commands = list(_emitted_commands(startup))
    assert any("context startup" in command for command in commands), (
        "the startup Skill no longer yields its own command to the extractor"
    )


def test_actor_is_rejected_after_the_subcommand() -> None:
    """`--actor` after the subcommand is refused, asserted directly.

    Without this, a future change that made `--actor` legal in both positions
    would leave the test above passing for a reason nobody chose.
    """
    parser = _build_parser()
    with pytest.raises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
        parser.parse_args(["context", "startup", "--client", "claude-code", "--actor", "x/y"])


#: Commands named inside a repair hint or an error message, which are
#: instructions to a reader exactly as a documented command is.
_BACKTICKED: Final = re.compile(r"`(never4ga [^`]+)`")

#: The modules that tell somebody what to run. Not every module: a command
#: quoted in an explanatory docstring is not an instruction, and this should
#: stay a chosen list rather than a sweep of the package.
_INSTRUCTING_MODULES: Final = (
    "never4ga/services/doctor.py",
    "never4ga/services/repair.py",
    "never4ga/cli/__init__.py",
)


def _hinted_commands() -> Iterator[tuple[str, str]]:
    root = Path(__file__).resolve().parents[2] / "src"
    for relative in _INSTRUCTING_MODULES:
        path = root / relative
        if not path.exists():
            continue
        for command in sorted(set(_BACKTICKED.findall(path.read_text(encoding="utf-8")))):
            yield relative, command


#: What a hint may legitimately leave out. "run `never4ga workspace create`"
#: names a verb rather than offering a line to paste, and demanding every
#: required positional would push hints towards fictional example values --
#: which is a worse failure than an incomplete one, because it looks runnable.
_INCOMPLETE: Final = "the following arguments are required"


def _assert_names_something_real(
    source: str, command: str, parser: argparse.ArgumentParser
) -> None:
    """Fail on an unrecognised flag or subcommand, but not a missing positional.

    A flag in a position the parser does not accept, or an unknown subcommand,
    is a broken instruction. A missing positional means the hint named a verb,
    which is allowed.
    """
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            parser.parse_args(_argv(command))
    except SystemExit as exit_signal:
        message = stderr.getvalue().strip()
        if exit_signal.code and _INCOMPLETE not in message:
            pytest.fail(
                f"{source} names a command the CLI rejects.\n"
                f"  command: {command}\n"
                f"  argparse: {message.splitlines()[-1:]}"
            )


def test_every_command_a_hint_names_parses() -> None:
    """A repair hint is an instruction, so the command it names must parse.

    A hint saying "run `never4ga skills provenance record`" when no such verb
    exists is the same mistake as a misplaced flag in a template: a command
    written by hand and never executed.
    """
    parser = _build_parser()
    seen = 0
    for source, command in _hinted_commands():
        _assert_names_something_real(source, command, parser)
        seen += 1
    assert seen, "found no hinted commands -- the extractor has stopped working"
