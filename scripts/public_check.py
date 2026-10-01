"""Keep what is private, and what is internal, out of what is pushed.

This repository is public, so anything pushed is published: files, commit
messages, branch names. What must not be published is listed in a file that
lives in the maintainer's private vault, never here. A denylist in a public
tree publishes its own list, and the first version of this check did exactly
that.

Two uses:

    python scripts/public_check.py --pre-push     # .githooks/pre-push runs this
    python scripts/public_check.py                # every tracked file, as it is now

Before a push, it reads every commit about to leave this machine: each one's
message and each line it adds. A marker added in one commit and removed in the
next is still published, so the removal does not excuse the addition.

The list is `NEVER4GA_PRIVATE_MARKERS`, else `private-markers.txt` in the
workspace this repository resolves to. Where there is no vault, as for a
contributor, there is no list and nothing to check. Where the vault resolves
but the file is missing, that is an error rather than a quiet pass.

It also reads the comments and docstrings of Python files in `src/`, `tests/`
and `scripts/`, and the `#` comments of configuration files and hooks anywhere
in the tree, for references to records a reader cannot open: decision records,
open questions, work items and milestones. Those are written for a
stranger and cite a public specification section instead. A reference quoted
as code, such as ``ADR-0002`` in an example, is allowed. This part needs no
vault, so it runs everywhere.

Repository infrastructure, like `scripts/export_specs.py`: not in the wheel.
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_specs import REPOSITORY, find_workspace

MARKERS_VARIABLE = "NEVER4GA_PRIVATE_MARKERS"
MARKERS_FILE = "private-markers.txt"
_NO_COMMIT = "0" * 40

#: Where comments and docstrings are held to the rule.
PROSE_ROOTS = ("src/", "tests/", "scripts/")

#: Files whose comments start with `#`, held to the rule wherever they are.
HASH_COMMENTED = (".toml", ".yml", ".yaml", ".sh", ".cfg")
HOOKS = ".githooks/"

#: A reference to a record that is not public: a decision record, an open
#: question, a work item or a milestone.
_INTERNAL_REFERENCE = re.compile(
    r"\bADR-\d{4}\b|\bQ-\d{3}\b|\bwork item \d+|\bMilestone \d+[a-z]?\b|\(\d{3,4}\)",
    re.IGNORECASE,
)

#: Inline code, which quotes an example rather than citing a record.
_CODE_SPAN = re.compile(r"``[^`]*``|`[^`]*`")


class MarkersMissingError(Exception):
    """The vault is here, and its list of private markers is not."""


@dataclass(frozen=True, slots=True)
class Finding:
    where: str
    marker: str

    def __str__(self) -> str:
        return f"{self.where}: {self.marker!r}"


def load_markers(env: Mapping[str, str] | None = None) -> tuple[str, ...] | None:
    """The private markers, or ``None`` where there is no vault to read them from."""
    environment = os.environ if env is None else env
    named = environment.get(MARKERS_VARIABLE)
    if named:
        path = Path(named).expanduser()
    else:
        found = find_workspace(env)
        if found is None:
            return None
        path = found[0] / MARKERS_FILE
    if not path.is_file():
        raise MarkersMissingError(f"no private markers at {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(
        stripped for line in lines if (stripped := line.strip()) and not stripped.startswith("#")
    )


def scan(text: str, markers: Iterable[str], where: str) -> list[Finding]:
    folded = text.casefold()
    return [Finding(where, marker) for marker in markers if marker.casefold() in folded]


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=REPOSITORY, capture_output=True, text=True, check=True
    ).stdout


def check_tree(markers: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in _git("ls-files", "-z").split("\0"):
        if not relative:
            continue
        try:
            text = (REPOSITORY / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError, FileNotFoundError:
            continue
        findings += scan(text, markers, relative)
    return findings


def commits_to_push(local: str, remote: str) -> list[str]:
    """The commits a push would publish: new to the remote, or the whole branch."""
    if remote == _NO_COMMIT:
        listed = _git("rev-list", local, "--not", "--remotes")
    else:
        listed = _git("rev-list", f"{remote}..{local}")
    return listed.split()


def check_commits(commits: Iterable[str], markers: Sequence[str]) -> list[Finding]:
    findings: list[Finding] = []
    for commit in commits:
        short = commit[:7]
        findings += scan(_git("log", "-1", "--format=%B", commit), markers, f"{short} message")
        patch = _git("show", "--format=", "--unified=0", "--no-color", commit)
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        findings += scan(added, markers, f"{short} added lines")
    return findings


def _prose(source: str) -> list[tuple[int, str]]:
    """Every comment and docstring in a Python source, with its line."""
    found: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found.append((token.start[0], token.string))
        tree = ast.parse(source)
    except tokenize.TokenError, SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                found.append((getattr(node, "lineno", 1), docstring))
    return found


def _hash_comments(source: str) -> list[tuple[int, str]]:
    """Every `#` comment in a configuration file or shell script, with its line.

    A `#` inside a quoted value is data, not a comment. Quotes are tracked one
    line at a time, which is what TOML, YAML and shell need for a comment, and
    an apostrophe inside a comment cannot open a quote that hides the next one.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if line.startswith("#!"):
            continue
        quote = ""
        for index, character in enumerate(line):
            if quote:
                if character == quote:
                    quote = ""
            elif character in "\"'":
                quote = character
            elif character == "#" and (index == 0 or line[index - 1].isspace()):
                found.append((number, line[index:]))
                break
    return found


def _is_hash_commented(path: str) -> bool:
    return path.endswith(HASH_COMMENTED) or path.startswith(HOOKS)


def internal_references(source: str, where: str) -> list[Finding]:
    """References to non-public records in the comments and docstrings of `source`."""
    path = where.rsplit(":", 1)[0]
    comments = _hash_comments(source) if _is_hash_commented(path) else _prose(source)
    return [
        Finding(f"{where}:{line}", match.group(0))
        for line, text in comments
        for match in _INTERNAL_REFERENCE.finditer(_CODE_SPAN.sub("", text))
    ]


def _held_to_the_rule(path: str) -> bool:
    return (path.endswith(".py") and path.startswith(PROSE_ROOTS)) or _is_hash_commented(path)


def check_tree_prose() -> list[Finding]:
    findings: list[Finding] = []
    for relative in _git("ls-files", "-z").split("\0"):
        if _held_to_the_rule(relative):
            text = (REPOSITORY / relative).read_text(encoding="utf-8")
            findings += internal_references(text, relative)
    return findings


def _files_changed(oldest: str, local: str, remote: str) -> list[str]:
    """The files a push adds, modifies or renames, as of its tip.

    A new branch is compared with the parent of its oldest new commit. A root
    commit has no parent, so every file at the tip is new. A git failure
    stops the check rather than reading as a push that changed nothing.
    """
    if remote != _NO_COMMIT:
        base = remote
    elif _git("rev-list", "--parents", "-n", "1", oldest).split()[1:]:
        base = f"{oldest}^"
    else:
        return [
            name for name in _git("ls-tree", "-r", "-z", "--name-only", local).split("\0") if name
        ]
    listed = _git("diff", "--name-only", "-z", "--diff-filter=AMR", base, local)
    return [name for name in listed.split("\0") if name]


def check_push_prose(updates: Iterable[str]) -> list[Finding]:
    """The rule, applied to the files a push changes, as they are at its tip."""
    findings: list[Finding] = []
    for update in updates:
        parts = update.split()
        if len(parts) != 4 or parts[1] == _NO_COMMIT:
            continue
        _local_ref, local, _remote_ref, remote = parts
        commits = commits_to_push(local, remote)
        if not commits:
            continue
        for relative in _files_changed(commits[-1], local, remote):
            if _held_to_the_rule(relative):
                findings += internal_references(_git("show", f"{local}:{relative}"), relative)
    return findings


def check_push(updates: Iterable[str], markers: Sequence[str]) -> list[Finding]:
    """`updates` are the lines git hands a pre-push hook on standard input."""
    findings: list[Finding] = []
    for update in updates:
        parts = update.split()
        if len(parts) != 4:
            continue
        local_ref, local, _remote_ref, remote = parts
        findings += scan(local_ref, markers, "branch name")
        if local == _NO_COMMIT:
            continue  # a deletion publishes nothing
        findings += check_commits(commits_to_push(local, remote), markers)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pre-push", action="store_true", help="check what a push would publish")
    arguments = parser.parse_args(argv)
    try:
        markers = load_markers()
    except MarkersMissingError as error:
        print(f"public check: {error}; refusing, because the vault is here", file=sys.stderr)
        return 1
    updates = list(sys.stdin) if arguments.pre_push else []
    private: list[Finding] = []
    try:
        if markers is not None:
            private = check_push(updates, markers) if arguments.pre_push else check_tree(markers)
        internal = check_push_prose(updates) if arguments.pre_push else check_tree_prose()
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip() or f"exit status {error.returncode}"
        print(
            f"public check: git could not say what this publishes ({detail}); refusing",
            file=sys.stderr,
        )
        return 1
    if private:
        print("\n  This would publish something private:\n", file=sys.stderr)
        for finding in private:
            print(f"    {finding}", file=sys.stderr)
    if internal:
        print("\n  These comments cite records a reader cannot open:\n", file=sys.stderr)
        for finding in internal:
            print(f"    {finding}", file=sys.stderr)
        print("\n  Cite a public spec section, or explain the rule itself.", file=sys.stderr)
    if private or internal:
        print("\n  This repository is public. Fix it and push again.\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
