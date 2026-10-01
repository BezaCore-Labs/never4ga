"""Turning findings into a plan, and a plan into changes.

Repair modifies documents somebody else may have written. `init` creates the
structure, `concept create` writes a document, `wrap` writes a log; each of
those *creates something that did not exist*. Repair does not, so it follows
four rules:

1. repair is a verb, and it asks twice: ``repair`` prints a plan and writes
   nothing; ``repair --apply`` performs it. Dry-run is the default rather than
   an option, so the safe call is the short one.
2. a repair is idempotent, atomic per file, and revertible through ordinary
   ``git revert`` with **no Never4gA involvement** in the reversal.
3. only a mechanical finding is repairable.
4. never on a schedule, and never as a side effect.

Rule 3 needs no judgement of its own, because the findings already answer it. Every `doctor` finding
carries a ``repair_hint``, and the hints come in two shapes:

```text
index_is_stale       "run `never4ga index`"
missing_directory    "run `never4ga init` to restore the canonical structure"
skill_is_outdated    "run `never4ga skills refresh --apply`"

broken_link          "fix the link, OR create the document it points at"
closed_plan_is_open  "set its lifecycle to completed, OR to abandoned ..."
duplicate_id         "give one document a fresh UUIDv7 ..."   (which one?)
```

A hint that names a verb is a fix two people would write the same way. A hint
that offers a choice is one they would not, and rule 3's test is exactly that.

**So repair implements no fixes of its own.** It runs the verbs the hints
already name. Those are tested, already idempotent, and already refuse to
overwrite something a person edited -- :meth:`SkillLibrary.refresh` will not
touch a Skill whose provenance hash says it was changed, and
:meth:`VaultInitializer.initialize` preserves everything already there and
reports what it preserved. Reimplementing any of that inside repair would be a
second copy of a careful thing.

What is left is small: most rules answer no, and that is the right answer.

This module plans and describes. It does not read the ledger and it does not
open a session -- a composition root does both, so the same plan can be printed
by the CLI and, later, produced anywhere else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from never4ga.services.doctor import Finding

__all__ = ["Action", "RepairOutcome", "RepairPlan", "RepairPlanner", "describe"]


class Action(Enum):
    """A thing repair can do, each one an existing verb.

    Ordered as they must run. ``RESTORE_STRUCTURE`` before ``REINDEX`` is not
    cosmetic: restoring a directory changes what there is to index, so
    reindexing first would leave the index stale the moment the run finished --
    and ``--apply`` twice would then not be the no-op rule 2 requires.
    """

    RESTORE_STRUCTURE = "restore_structure"
    REGENERATE_NAVIGATION = "regenerate_navigation"
    REGENERATE_VIEWS = "regenerate_views"
    REFRESH_LIBRARY = "refresh_library"
    REINDEX = "reindex"


#: Which finding each action answers, and why that finding qualifies.
#:
#: An entry here is a claim that two people reading the finding would write the
#: same fix. Anything absent is *not* repairable, including a code invented
#: later: a rule must be added here deliberately rather than become repairable
#: by default.
_REPAIRS: Final[dict[str, Action]] = {
    # The whole fix is `mkdir`, and `init` preserves everything already there.
    "missing_directory": Action.RESTORE_STRUCTURE,
    # `index.md` and `home.md` at the vault root, whose content `init` owns.
    "missing_root_document": Action.RESTORE_STRUCTURE,
    # `00_Inbox/scratchpad.md`, which `init` writes and an older vault may
    # lack. Restoring the structure writes it, and leaves one that
    # is already there -- with its lines -- alone.
    "inbox_scratchpad_missing": Action.RESTORE_STRUCTURE,
    # A top-level directory added by hand after `init` (`core/01` section 1).
    # `init` registers it as foreign material and touches nothing inside it,
    # which is the whole fix and the same fix from anyone's hands.
    "foreign_material_unregistered": Action.RESTORE_STRUCTURE,
    # `core/01` sections 4 and 6 reserve `index.md` for navigation and keep
    # concept frontmatter out of it, which is exactly what makes the block
    # derivable: it carries no identity, no relations and no judgement. Two
    # people given the same directory write the same list.
    "navigation_is_outdated": Action.REGENERATE_NAVIGATION,
    # The same act: `refresh` writes a directory's index whether or not the
    # file was there, so a missing one is created rather than reported forever.
    # Creating a file is a larger write than rewriting a delimited block, and
    # it stays behind `--apply` for that reason -- but the content is still
    # derived, and a vault that loses every one of them rebuilds them exactly.
    "navigation_is_missing": Action.REGENERATE_NAVIGATION,
    # The dashboard's views block is navigation's shape in `workspace.md`:
    # derived from which types the workspace holds, delimited so the
    # manifest prose around it is never touched. Two people given the same
    # corpus write the same views.
    "workspace_views_outdated": Action.REGENERATE_VIEWS,
    # The same act on a manifest that has no block yet -- every workspace
    # written before the block existed. The splice appends at the end of the
    # file and owns only what sits between its own markers.
    "workspace_views_missing": Action.REGENERATE_VIEWS,
    # Not a vault write at all: the index is derived and rebuildable, and the
    # finding says the derived copy is behind the canonical one.
    "index_is_stale": Action.REINDEX,
    # The correct content is what this build ships, byte for byte. `refresh`
    # is called without `force`, so a Skill somebody edited is left alone and
    # goes on being reported -- which is the honest outcome, not a failure.
    "skill_is_outdated": Action.REFRESH_LIBRARY,
    "template_is_outdated": Action.REFRESH_LIBRARY,
}

#: Codes whose hint names a verb and which are *still* not repaired here, so the
#: omission reads as a decision rather than an oversight.
#:
#: ``no_vault_identity`` -- creating the manifest mints the UUID that *is* the
#: vault's identity, and every derived thing keys on it. Bringing a vault into
#: existence is a person's decision; a repair finding one missing means
#: something larger is wrong than a repair should paper over.
#:
#: ``agent_pointer_missing`` / ``agent_files_not_ignored`` -- their fix writes
#: into a *repository* rather than the vault, and
#: `details/security-configuration.md` section 7.1 requires that the user invoked
#: that command. Somebody who typed `repair` did not type `adapters sync`.
#:
#: ``stale_adapter_deployment`` -- the same argument, on a different filesystem.
#: Its fix writes into a client's own directory under `$HOME`, not into the
#: vault, and `core/09` section 11 governs what may be written there. Repair mends the
#: vault; `adapters sync` mends what is deployed to clients. Two acts on two
#: filesystems, and typing one is not typing the other.
#: ``untracked_document`` -- the hint names `adopt`, and adoption is exactly
#: the kind of act this table must not perform. An entry here claims two
#: people reading the finding would write the same fix; choosing a *type* for
#: a hand-written note is a judgement, and two people would not agree.
#: Placement is often not inferable at all, and whether prose should become a
#: concept in the first place is the writer's call.
_DELIBERATELY_EXCLUDED: Final = frozenset(
    {
        "no_vault_identity",
        "agent_pointer_missing",
        "agent_files_not_ignored",
        "stale_adapter_deployment",
        "untracked_document",
    }
)


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """What a repair would do, and what it would leave alone."""

    actions: tuple[Action, ...]
    #: The findings the actions answer.
    addressed: tuple[Finding, ...]
    #: Everything else, carried rather than dropped. A plan that showed only the
    #: fixable half would read as "all of it" to somebody deciding whether to
    #: type `--apply`.
    unrepairable: tuple[Finding, ...]

    @property
    def anything_to_do(self) -> bool:
        return bool(self.actions)


class RepairPlanner:
    """Sorts findings into what can be fixed mechanically and what cannot."""

    def plan(self, findings: Sequence[Finding]) -> RepairPlan:
        addressed: list[Finding] = []
        unrepairable: list[Finding] = []
        wanted: set[Action] = set()

        for finding in findings:
            action = _REPAIRS.get(finding.code)
            if action is None:
                unrepairable.append(finding)
                continue
            wanted.add(action)
            addressed.append(finding)

        return RepairPlan(
            actions=tuple(action for action in Action if action in wanted),
            addressed=tuple(addressed),
            unrepairable=tuple(unrepairable),
        )


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """What one action did, once it was actually performed."""

    action: Action
    detail: str


#: What each action is called when a plan is read by a person. The verb is named
#: because that is what makes the plan checkable: somebody can run it themselves
#: and get the same result, which is the whole reason repair does not implement
#: its own fixes.
_DESCRIPTIONS: Final[dict[Action, str]] = {
    Action.RESTORE_STRUCTURE: "restore the canonical structure (what `never4ga init` creates)",
    Action.REGENERATE_NAVIGATION: (
        "regenerate the navigation in each `index.md` (prose around it is left alone)"
    ),
    Action.REGENERATE_VIEWS: (
        "regenerate the views on each workspace dashboard (prose around them is left alone)"
    ),
    Action.REFRESH_LIBRARY: "refresh the Skills and templates this build ships",
    Action.REINDEX: "reindex the vault (derived state only; the vault is not written)",
}


def describe(action: Action) -> str:
    return _DESCRIPTIONS[action]
