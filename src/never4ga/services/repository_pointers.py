"""The generated pointer a mapped repository holds instead of instructions.

`details/agent-instruction-layering.md` sections 2 and 3. An agent instruction
file in a repository is untracked and generated: it names the workspace, says
to run startup, lists the repository's own tracked documentation, and carries
nothing else. Everything a rule or a preference would have said lives in the
vault and reaches a session through the Context Pack.

Three jobs, and the third is the one that is easy to miss:

1. **Arrival is mechanical.** The global managed block is advisory (section
   3.1): some clients skip it on a trivial turn. A file in the repository does
   not depend on a model choosing to read it.
2. **Section 11's guidance report becomes an artifact.** The pointer lists the
   repository's own documentation, so a session that never calls startup still
   learns where the real instructions are.
3. **The filename is occupied.** A pointer at `CLAUDE.md` is what stops
   `claude /init` writing one there. Prevention is weak for *content*; this is
   the filesystem declining a second file at one path (section 5.1).

**Every write here is subject to the six conditions of section 6**, which
govern an ordinary write in every mapped repository. The two this
module enforces are the two it can: an unmapped repository is never written to,
and an existing file that Never4gA did not generate is never overwritten. The
condition that matters most -- *the user invoked the command; never on
discovery, never on a timer* -- is enforced by the caller, because it is about
when this runs rather than what it writes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import Final

from never4ga.domain.clients import ClientDescriptor
from never4ga.domain.extensions import (
    NEVER4GA_OWNER,
    ClientState,
    DeploymentMode,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
)
from never4ga.domain.scope import WorkspaceMapping
from never4ga.ports.extension_registry import ExtensionRegistry
from never4ga.ports.repository_locator import RepositoryLocator
from never4ga.services.authoring import Clock, format_timestamp, utc_now
from never4ga.services.startup_command import DOCUMENTED_STARTUP_COMMAND

__all__ = [
    "AGENT_FILENAMES",
    "GITIGNORE_BEGIN",
    "GITIGNORE_END",
    "POINTER_MARKER",
    "PointerAction",
    "PointerPlan",
    "RepositoryPointerSync",
    "documentation_in",
]

#: The canonical pointer, which every client either reads natively or is
#: pointed at by a file of its own.
CANONICAL_FILENAME: Final = "AGENTS.md"

#: Every agent instruction filename kept out of the Git index
#: (`details/agent-instruction-layering.md` section 6). The list grows with
#: the ecosystem; `details/tool-adapter-matrix.md` is where a new client is
#: recorded, and a client descriptor's `repository_rules_path` is what adds one
#: here in practice. These are the names gitignored even when no client on this
#: machine uses them, so a repository stays safe on a machine that installs one
#: later.
AGENT_FILENAMES: Final = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")

#: A generated file says so on its first line. Nothing parses this -- byte
#: equality against a regenerated pointer is the check
#: (`details/agent-instruction-layering.md` section 7) -- but a person opening
#: the file should not have to guess where it came from.
POINTER_MARKER: Final = "<!-- never4ga:pointer -->"

GITIGNORE_BEGIN: Final = "# BEGIN never4ga agent instruction files"
GITIGNORE_END: Final = "# END never4ga agent instruction files"

#: The begin line earlier versions wrote. A repository may still carry it, so
#: a block under it is found, replaced rather than repeated, and rewritten by
#: the next sync even when it ignores the same files.
_LEGACY_GITIGNORE_BEGIN: Final = f"{GITIGNORE_BEGIN} (ADR-0025)"

#: Either begin line, the longer first so the shorter cannot match its prefix.
_BEGIN: Final = rf"(?:{re.escape(_LEGACY_GITIGNORE_BEGIN)}|{re.escape(GITIGNORE_BEGIN)})"

#: What counts as documentation a session should be told to read. Ordered, so
#: the pointer is stable rather than dependent on directory order.
_DOCUMENTATION: Final = ("README.md", "CONTRIBUTING.md", "docs/README.md", "docs/index.md")


class PointerAction(StrEnum):
    WRITE = "write"
    NOOP = "noop"
    SKIP_EXCEPTED = "skip_excepted"
    SKIP_ABSENT = "skip_absent"
    SKIP_UNMANAGED = "skip_unmanaged"


@dataclass(frozen=True, slots=True)
class PointerPlan:
    """One file `adapters sync` would write, and why."""

    repository_root: PurePath
    relative: str
    action: PointerAction
    content: str | None = None
    reason: str = ""
    #: What a write replaces when it is an adoption: the file somebody else
    #: wrote, kept on the plan so applying it can record what it took over.
    replaced: str | None = None

    @property
    def writes(self) -> bool:
        return self.action is PointerAction.WRITE

    @property
    def adopts(self) -> bool:
        return self.writes and self.replaced is not None


def documentation_in(locator: RepositoryLocator, root: PurePath) -> tuple[str, ...]:
    """The repository's own tracked documentation, as the pointer will list it.

    The repository keeps its own documentation and only the agent-addressed
    filename is vacated (`details/agent-instruction-layering.md` section 2).
    This is the list `core/04` section 11 says startup should report, written
    into the file instead so that a client which never calls startup still gets
    it.
    """
    return tuple(name for name in _DOCUMENTATION if locator.exists(root, name))


def pointer_body(mapping: WorkspaceMapping, documentation: Sequence[str]) -> str:
    """The canonical `AGENTS.md` pointer for one mapped repository.

    Derivable from the workspace and the repository alone, which is what makes
    section 7's byte-equality verification a comparison rather than a heuristic.
    """
    lines = [
        POINTER_MARKER,
        "",
        "# Agent instructions",
        "",
        "This file is generated by Never4gA and is not tracked. Editing it is",
        "pointless: `never4ga adapters sync` rewrites it, and `never4ga doctor`",
        "reports it as modified in the meantime.",
        "",
        "## Before substantive work",
        "",
        "```bash",
        DOCUMENTED_STARTUP_COMMAND,
        "```",
        "",
        f"This repository is workspace `{mapping.workspace_id}`",
        f"(`{mapping.workspace_path}`). The startup pack carries its state, the",
        "standards that bind this work, the decisions already taken, and what",
        "the last session left open.",
        "",
        "Without Never4gA available, say so and work from the documentation",
        "below rather than guessing at project state.",
        "",
    ]
    if documentation:
        lines += ["## This repository's own documentation", ""]
        lines += [f"- [{name}]({name})" for name in documentation]
        lines += [""]
    lines += [
        "## Precedence",
        "",
        "`AGENTS.md` is this repository's authoritative agent instruction file.",
        "A tool-specific instruction file beside it is subordinate, and a",
        "discrepancy is reported rather than silently followed.",
        "",
    ]
    return "\n".join(lines)


def subordinate_body(filename: str) -> str:
    """The thin file that occupies a client-specific name.

    It carries no instruction of its own, which is the point: two instruction
    files with content can contradict each other, and nothing detects that.
    """
    return "\n".join(
        [
            POINTER_MARKER,
            "",
            f"# {filename}",
            "",
            "Generated by Never4gA, and not tracked.",
            "",
            "Read [AGENTS.md](AGENTS.md). It is authoritative and this file is",
            "subordinate to it; nothing here overrides anything there.",
            "",
            "This file exists so the name is taken. A client that generates its",
            "own instruction file would otherwise write one here, and nobody",
            "would have authored it.",
            "",
        ]
    )


def gitignore_block() -> str:
    """The delimited block added to a repository's `.gitignore`."""
    return "\n".join(
        [
            GITIGNORE_BEGIN,
            "# Agent instruction files are generated on each machine and never",
            "# committed.",
            *AGENT_FILENAMES,
            GITIGNORE_END,
        ]
    )


def with_gitignore_block(existing: str | None) -> str:
    """Add or replace the managed block, preserving every unrelated line.

    `details/agent-instruction-layering.md` section 6: an existing file
    receives a delimited managed block and is never rewritten wholesale. `.gitignore` is a file the
    repository owns and Never4gA did not author, so it gets the same treatment
    as any other.
    """
    block = gitignore_block()
    if existing is None:
        return block + "\n"
    without = re.sub(
        rf"^{_BEGIN}\n(?:.*\n)*?{re.escape(GITIGNORE_END)}\n?",
        "",
        existing,
        flags=re.MULTILINE,
    ).rstrip("\n")
    # An empty remainder means the block was the whole file, which is the
    # ordinary case on the second run. Falling through to the join below would
    # prefix it with two newlines and make sync non-idempotent -- and section 11
    # requires idempotence, so this is a correctness branch rather than tidiness.
    if not without.strip():
        return block + "\n"
    return without + "\n\n" + block + "\n"


def _ignored_by_block(text: str) -> tuple[str, ...] | None:
    """The entries of the managed block in `text`, without its comments.

    ``None`` when there is no complete block: a missing block, or a begin
    marker with no end, is always rewritten.
    """
    match = re.search(
        rf"^{_BEGIN}\n((?:.*\n)*?){re.escape(GITIGNORE_END)}$",
        text,
        re.MULTILINE,
    )
    if match is None:
        return None
    return tuple(
        line.strip()
        for line in match.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _has_legacy_begin(text: str) -> bool:
    """Whether the block in `text` opens with the begin line earlier versions wrote."""
    return re.search(rf"^{re.escape(_LEGACY_GITIGNORE_BEGIN)}$", text, re.MULTILINE) is not None


def is_generated(content: str | None) -> bool:
    """Whether Never4gA wrote this, as far as the file itself admits."""
    return content is not None and content.lstrip().startswith(POINTER_MARKER)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:32]


class RepositoryPointerSync:
    """Plans, and on request performs, the repository pointer writes."""

    def __init__(
        self,
        locator: RepositoryLocator,
        mappings: Sequence[WorkspaceMapping],
        clients: Sequence[ClientDescriptor],
        *,
        adopt: bool = False,
        registry: ExtensionRegistry | None = None,
        now: Clock = utc_now,
    ) -> None:
        if adopt and registry is None:
            # core/09 section 13 and `details/agent-instruction-layering.md`
            # section 5.2: an adoption records origin, review date and hash.
            # Without a record an adopted file is indistinguishable from one
            # that was always generated, so adopting without somewhere to
            # record it is refused at assembly rather than at apply.
            raise ValueError("adoption records what it replaces; it needs a registry")
        self._locator = locator
        self._mappings = mappings
        self._clients = clients
        self._adopt = adopt
        self._registry = registry
        self._now = now

    def plan(self) -> tuple[PointerPlan, ...]:
        planned: list[PointerPlan] = []
        for mapping in self._mappings:
            planned.extend(self._plan_one(mapping))
        return tuple(planned)

    def apply(self, plans: Sequence[PointerPlan]) -> tuple[PointerPlan, ...]:
        performed = []
        for plan in plans:
            if plan.content is None or not plan.writes:
                continue
            self._locator.write_text(plan.repository_root, plan.relative, plan.content)
            if plan.adopts:
                assert self._registry is not None
                self._registry.register(self._adoption(plan))
            performed.append(plan)
        return tuple(performed)

    def _adoption(self, plan: PointerPlan) -> ExtensionRecord:
        """The record core/09 section 13 asks for, for one replaced file.

        A `rule` (section 3) in `adopted` mode: it was somebody's file and a
        person chose to take it over. `ownership` hashes the pointer now in
        place; `replaced_hash` hashes what was there, so the adoption stays
        auditable against the repository's own history once the old file is
        gone. Which clients read it is which clients name that filename.
        """
        assert plan.content is not None and plan.replaced is not None
        stamp = format_timestamp(self._now())
        location = f"{plan.repository_root}/{plan.relative}"
        return ExtensionRecord(
            capability_id=f"pointer:{location}",
            extension_class=ExtensionClass.RULE,
            title=plan.relative,
            deployment_mode=DeploymentMode.ADOPTED,
            portability=PortabilityLevel.ADAPTABLE,
            clients=dict.fromkeys(self._readers_of(plan.relative), ClientState.ENABLED),
            permissions=Permissions(read_only=True),
            ownership=OwnershipProof(
                owner=NEVER4GA_OWNER, content_hash=content_hash(plan.content), generated_at=stamp
            ),
            origin=location,
            adopted_at=stamp,
            replaced_hash=content_hash(plan.replaced),
        )

    def _readers_of(self, relative: str) -> tuple[str, ...]:
        """Which clients read a file of this name. The canonical one, every client."""
        if relative == CANONICAL_FILENAME:
            return tuple(sorted(client.client_id for client in self._clients))
        return tuple(
            sorted(
                client.client_id
                for client in self._clients
                if client.repository_rules_path == relative
            )
        )

    # -- internals --------------------------------------------------------

    def _plan_one(self, mapping: WorkspaceMapping) -> list[PointerPlan]:
        root = mapping.repository_root
        if root is None:
            # Not a repository at all. Nothing to write into.
            return []
        if mapping.ships_agent_contract:
            return [
                PointerPlan(
                    root,
                    CANONICAL_FILENAME,
                    PointerAction.SKIP_EXCEPTED,
                    reason="its agent contract is part of what it ships",
                )
            ]
        if not self._locator.exists(root, "."):
            # The repository is mapped, which is the first condition of
            # section 6, but a mapping can outlive a checkout. Writing would create the
            # repository, which is not what "sync" means.
            return [
                PointerPlan(
                    root,
                    CANONICAL_FILENAME,
                    PointerAction.SKIP_ABSENT,
                    reason="mapped, but nothing is at that path",
                )
            ]

        documentation = documentation_in(self._locator, root)
        planned = [self._plan_file(root, CANONICAL_FILENAME, pointer_body(mapping, documentation))]
        for name in self._subordinate_names():
            planned.append(self._plan_file(root, name, subordinate_body(name)))
        planned.append(self._plan_gitignore(root))
        return planned

    def _subordinate_names(self) -> tuple[str, ...]:
        names = {
            client.repository_rules_path for client in self._clients if client.repository_rules_path
        }
        return tuple(sorted(names - {CANONICAL_FILENAME}))

    def _plan_file(self, root: PurePath, relative: str, content: str) -> PointerPlan:
        current = self._locator.read_text(root, relative)
        if current == content:
            return PointerPlan(root, relative, PointerAction.NOOP, reason="already current")
        if current is not None and not is_generated(current) and not self._adopt:
            # `core/04` section 25.1 and 26.1: never overwrite what somebody
            # else wrote. Reported, and the reporting is the whole remedy --
            # adopting it is a separate, explicit act (`core/09` sections 12-13),
            # which is what `adopt` is and why it is not the default.
            return PointerPlan(
                root,
                relative,
                PointerAction.SKIP_UNMANAGED,
                reason="a file is there that Never4gA did not generate",
            )
        adopting = current is not None and not is_generated(current)
        return PointerPlan(
            root,
            relative,
            PointerAction.WRITE,
            content=content,
            reason="adopting a file Never4gA did not write" if adopting else "",
            replaced=current if adopting else None,
        )

    def _plan_gitignore(self, root: PurePath) -> PointerPlan:
        existing = self._locator.read_text(root, ".gitignore")
        if (
            existing is not None
            and _ignored_by_block(existing) == _ignored_by_block(gitignore_block())
            and not _has_legacy_begin(existing)
        ):
            # A tracked file rewritten for its comments alone leaves the
            # repository with an uncommitted change and nothing gained.
            return PointerPlan(
                root, ".gitignore", PointerAction.NOOP, reason="the block ignores the same files"
            )
        wanted = with_gitignore_block(existing)
        if existing == wanted:
            return PointerPlan(root, ".gitignore", PointerAction.NOOP, reason="already current")
        return PointerPlan(root, ".gitignore", PointerAction.WRITE, content=wanted)
