"""What `never4ga repair` will do, before it does anything.

Repair is a dry run by default, and `--apply` is the only thing in the system
that may perform one. **A rule earns a repair when the correct fix is derivable
from the finding with no judgement.** The test is not how confident the rule
is, but whether two people reading the same finding would write the same fix.

The codes already answer that. Every finding carries a ``repair_hint``, and the
hints fall into two shapes:

```text
index_is_stale      "run `never4ga index`"
missing_directory   "run `never4ga init` to restore the canonical structure"
skill_is_outdated   "run `never4ga skills refresh --apply`"

broken_link         "fix the link, OR create the document it points at"
closed_plan_is_open "set its lifecycle to completed, OR to abandoned ..."
misfiled_workspace  "move the document into the workspace it names, OR correct ..."
```

The word *or* is the tell. A hint that names a verb is a fix two people would
write the same way; a hint that offers a choice is one they would not. So repair
implements no fixes of its own. **It runs the verbs the hints already name**,
which are tested, idempotent, and refuse to overwrite something a person
edited.

Most rules therefore get no repair, and that is the right answer.

Two codes whose hints name a verb are still excluded, and the reasons are in
:mod:`never4ga.services.repair`.
"""

from __future__ import annotations

from never4ga.domain.document import VaultPath
from never4ga.schema import Severity
from never4ga.services.doctor import Finding
from never4ga.services.repair import Action, RepairPlan, RepairPlanner


def a_finding(code: str, path: str | None = "30_Knowledge/Notes/one.md") -> Finding:
    return Finding(
        code=code,
        message=f"{code} happened",
        severity=Severity.WARNING,
        path=None if path is None else VaultPath.parse(path),
    )


def plan_for(*codes: str) -> RepairPlan:
    return RepairPlanner().plan([a_finding(code) for code in codes])


class TestWhatEarnsARepair:
    def test_a_stale_index_is_repairable(self) -> None:
        assert plan_for("index_is_stale").actions == (Action.REINDEX,)

    def test_a_missing_directory_is_repairable(self) -> None:
        assert plan_for("missing_directory").actions == (Action.RESTORE_STRUCTURE,)

    def test_an_unregistered_foreign_directory_is_repairable(self) -> None:
        # `init` registers it and touches nothing inside it, so two people
        # reading the finding write the same fix.
        assert plan_for("foreign_material_unregistered").actions == (Action.RESTORE_STRUCTURE,)

    def test_a_missing_scratchpad_is_repairable(self) -> None:
        # `init` writes it and leaves anything already there alone, so
        # restoring the structure is the whole fix.
        assert plan_for("inbox_scratchpad_missing").actions == (Action.RESTORE_STRUCTURE,)

    def test_an_outdated_skill_is_repairable(self) -> None:
        assert plan_for("skill_is_outdated").actions == (Action.REFRESH_LIBRARY,)

    def test_an_outdated_template_is_the_same_action(self) -> None:
        assert plan_for("template_is_outdated").actions == (Action.REFRESH_LIBRARY,)


class TestWhatDoesNot:
    """Every one of these needs a judgement, and the hint says which."""

    def test_a_broken_link_is_a_choice(self) -> None:
        # "fix the link, or create the document it points at" -- two people
        # would not write the same fix, and one of them would be wrong.
        assert plan_for("broken_link").actions == ()

    def test_a_duplicate_id_is_a_choice(self) -> None:
        # Which of the two documents keeps the identity everything points at?
        assert plan_for("duplicate_id").actions == ()

    def test_stale_content_is_a_judgement_about_truth(self) -> None:
        # `core/02` §15.2: stale content is flagged, never silently deleted.
        # Whether it is still true is not a thing a program knows.
        assert plan_for("content_is_stale").actions == ()

    def test_too_much_required_reading_is_a_judgement_about_material(self) -> None:
        # History to a roll-up, a how-to to a runbook: a person decides.
        assert plan_for("required_reading_too_large").actions == ()

    def test_a_schema_error_is_not_repairable_either(self) -> None:
        # The correct `created_at` is a fact about the past. `core/02` §5.2
        # forbids fabricating `generated.at`, and inventing this is the same
        # act with a different field name.
        assert plan_for("invalid_timestamp").actions == ()

    def test_near_duplicate_tags_is_a_choice_of_vocabulary(self) -> None:
        assert plan_for("near_duplicate_tags").actions == ()

    def test_an_unknown_code_is_never_assumed_repairable(self) -> None:
        # A rule added later must not become repairable by default. Silence
        # here is the safe answer and the loud one is a new mapping entry.
        assert plan_for("something_invented_next_year").actions == ()


class TestTheOnesDeliberatelyLeftOut:
    def test_a_missing_vault_identity_is_not_repaired(self) -> None:
        """Its hint names `init`, and it is still excluded.

        Creating the manifest mints the UUID that *is* the vault's identity, and
        everything derived keys on it. A person should decide to bring a vault
        into existence; `repair` finding it missing means something larger is
        wrong than a repair should paper over.
        """
        assert plan_for("no_vault_identity").actions == ()

    def test_the_agent_pointer_codes_are_not_repaired(self) -> None:
        """Agent pointer codes are fixed by `adapters sync`, not by `repair`.

        They write into a *repository* rather than the vault, which
        `details/security-configuration.md` §7.1 permits only when the user
        invoked the command explicitly. Somebody who typed `repair` did not
        type `adapters sync`.
        """
        assert plan_for("agent_pointer_missing", "agent_files_not_ignored").actions == ()


class TestThePlanIsHonest:
    def test_what_cannot_be_repaired_is_still_listed(self) -> None:
        """A plan that showed only the fixable half would read as "all of it".

        Somebody reading a plan is deciding whether to type `--apply`. Leaving
        out the findings it will not touch is how they conclude the vault is
        fixed when it is not.
        """
        plan = plan_for("broken_link", "index_is_stale")
        assert [f.code for f in plan.unrepairable] == ["broken_link"]

    def test_many_findings_collapse_into_one_action(self) -> None:
        # Twelve missing directories are one `init`, not twelve of them.
        plan = RepairPlanner().plan(
            [a_finding("missing_directory", f"0{n}_Root") for n in range(1, 6)]
        )
        assert plan.actions == (Action.RESTORE_STRUCTURE,)
        assert len(plan.addressed) == 5

    def test_an_action_says_which_findings_it_answers(self) -> None:
        plan = plan_for("index_is_stale", "broken_link")
        assert [f.code for f in plan.addressed] == ["index_is_stale"]

    def test_nothing_to_do_is_an_empty_plan_rather_than_an_error(self) -> None:
        plan = RepairPlanner().plan([])
        assert plan.actions == () and not plan.anything_to_do

    def test_a_plan_with_only_unrepairable_findings_has_nothing_to_do(self) -> None:
        plan = plan_for("broken_link", "duplicate_id")
        assert not plan.anything_to_do
        assert len(plan.unrepairable) == 2

    def test_the_order_of_actions_is_stable(self) -> None:
        """Structure before index, always.

        Restoring a missing directory changes what there is to index, so a plan
        that reindexed first would leave the index stale again the moment it
        finished -- and `--apply` twice would then not be a no-op.
        """
        plan = plan_for("index_is_stale", "skill_is_outdated", "missing_directory")
        assert plan.actions == (
            Action.RESTORE_STRUCTURE,
            Action.REFRESH_LIBRARY,
            Action.REINDEX,
        )


class TestTheMappingCannotDriftFromTheRules:
    """A rule that gains a mechanical fix must not miss out silently.

    The hints are the evidence for the no-judgement test, so they are also
    what holds the mapping honest: a hint that starts *"run `never4ga ..."* names a verb,
    which means somebody decided the fix is derivable. Every such code must
    therefore be repairable, or be excluded on purpose in
    `_DELIBERATELY_EXCLUDED` with the reason written beside it.

    Without this, a new rule with a mechanical fix would be reported forever
    and repaired never, and nothing would say so.
    """

    @staticmethod
    def _hints() -> dict[str, str]:
        import ast
        import inspect
        import pathlib

        from never4ga.services import doctor

        # By the module rather than by a relative path: the test suite runs
        # from a temporary directory on purpose, so the repository root is not
        # where the process happens to be standing.
        source = inspect.getsourcefile(doctor)
        assert source is not None
        tree = ast.parse(pathlib.Path(source).read_text())
        found: dict[str, str] = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Finding"
            ):
                continue
            code = hint = None
            if node.args and isinstance(node.args[0], ast.Constant):
                code = node.args[0].value
            if len(node.args) >= 5 and isinstance(node.args[4], ast.Constant):
                hint = node.args[4].value
            for keyword in node.keywords:
                if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                    code = keyword.value.value
                if keyword.arg == "repair_hint" and isinstance(keyword.value, ast.Constant):
                    hint = keyword.value.value
            if isinstance(code, str) and isinstance(hint, str):
                found.setdefault(code, hint)
        return found

    def test_the_hints_are_actually_readable(self) -> None:
        # The test above is worthless if the parse silently finds nothing.
        hints = self._hints()
        assert len(hints) > 15
        assert hints["index_is_stale"].startswith("run `never4ga index`")

    def test_every_code_whose_hint_names_a_verb_is_accounted_for(self) -> None:
        from never4ga.services.repair import _DELIBERATELY_EXCLUDED, _REPAIRS

        mechanical = {
            code for code, hint in self._hints().items() if hint.startswith("run `never4ga")
        }
        unaccounted = mechanical - set(_REPAIRS) - _DELIBERATELY_EXCLUDED
        assert unaccounted == set(), (
            f"these findings name a verb that fixes them but repair ignores them: "
            f"{sorted(unaccounted)}. Add them to _REPAIRS, or to "
            f"_DELIBERATELY_EXCLUDED with the reason."
        )

    def test_nothing_is_both_repairable_and_excluded(self) -> None:
        from never4ga.services.repair import _DELIBERATELY_EXCLUDED, _REPAIRS

        assert set(_REPAIRS) & _DELIBERATELY_EXCLUDED == set()
