"""Deploying canonical Skills to agent clients.

Against real files, with `HOME` pointed at the test's own directory by the
autouse fixture in `conftest.py`, so a descriptor's `~/.claude/skills` resolves
inside `tmp_path` and a test can never write to the machine it runs on.

Adding a client is a descriptor and a test, and touches no engine code:
`TestAFourthClientTouchesNoEngineCode` syncs to a client that does not exist,
described entirely by a TOML file it writes itself.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import (
    DeploymentFile,
    FileSystemClientFileStore,
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
)
from never4ga.client_descriptors import load_descriptors
from never4ga.domain.clients import ClientDescriptor, DeploymentMechanism
from never4ga.domain.deployments import Deployment
from never4ga.domain.extensions import SyncActionKind
from never4ga.services import VaultInitializer
from never4ga.services.adapters import (
    LOCAL_EDIT,
    AdapterService,
    canonical_agents,
    canonical_skills,
)


def fixed_clock() -> datetime:
    return datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir(exist_ok=True)
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def build(
    vault: Path, home: Path, tmp_path: Path, descriptors: list[ClientDescriptor]
) -> AdapterService:
    return AdapterService(
        descriptors,
        FileSystemVaultFileStore(vault),
        DeploymentFile(tmp_path / "state" / "deployments.json"),
        FileSystemClientFileStore(),
        home=home,
        vault_root=vault,
        now=fixed_clock,
    )


def probe_descriptor(**overrides: object) -> ClientDescriptor:
    fields: dict[str, object] = {
        "client_id": "probe",
        "title": "Probe",
        "global_skills_path": "~/.probe/skills",
        "detect_paths": ("~/.probe",),
    }
    fields.update(overrides)
    return ClientDescriptor(**fields)  # type: ignore[arg-type]


@pytest.fixture
def installed(home: Path) -> Path:
    (home / ".probe").mkdir()
    return home / ".probe" / "skills"


class TestReadingTheCanonicalLibrary:
    def test_the_eight_skills_are_found(self, vault: Path) -> None:
        skills = canonical_skills(FileSystemVaultFileStore(vault))
        assert [skill.name for skill in skills] == [
            "never4ga-capture",
            "never4ga-checkpoint",
            "never4ga-context",
            "never4ga-create",
            "never4ga-decide",
            "never4ga-doctor",
            "never4ga-startup",
            "never4ga-wrap",
        ]

    def test_a_skill_carries_its_whole_directory(self, vault: Path) -> None:
        (vault / "50_System/Skills/never4ga-doctor/references").mkdir(parents=True)
        (vault / "50_System/Skills/never4ga-doctor/references/codes.md").write_text(
            "# Finding codes\n", encoding="utf-8"
        )
        skills = {s.name: s for s in canonical_skills(FileSystemVaultFileStore(vault))}
        assert set(skills["never4ga-doctor"].files) == {"SKILL.md", "references/codes.md"}

    def test_a_directory_without_a_skill_file_is_not_a_skill(self, vault: Path) -> None:
        (vault / "50_System/Skills/notes").mkdir()
        (vault / "50_System/Skills/notes/thoughts.md").write_text("mine", encoding="utf-8")
        names = [s.name for s in canonical_skills(FileSystemVaultFileStore(vault))]
        assert "notes" not in names

    def test_the_hash_covers_every_file(self, vault: Path) -> None:
        store = FileSystemVaultFileStore(vault)
        before = {s.name: s.content_hash for s in canonical_skills(store)}
        (vault / "50_System/Skills/never4ga-doctor/extra.md").write_text("x", encoding="utf-8")
        after = {s.name: s.content_hash for s in canonical_skills(store)}
        assert after["never4ga-doctor"] != before["never4ga-doctor"]
        assert after["never4ga-startup"] == before["never4ga-startup"]


class TestDetection:
    def test_an_absent_client_is_not_planned_for(
        self, vault: Path, home: Path, tmp_path: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        assert service.plan() == ()

    def test_an_installed_client_is(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        assert len(service.plan()) == 1

    def test_syncing_never_creates_the_directory_it_detects_on(
        self, vault: Path, home: Path, tmp_path: Path
    ) -> None:
        # Otherwise Never4gA installs the evidence that the client is there.
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        assert not (home / ".probe").exists()


class TestSyncWritesNothingWithoutApply:
    def test_planning_leaves_the_client_untouched(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        plans = service.plan()
        assert plans[0].actions
        assert not installed.exists()

    def test_planning_writes_no_manifest(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.plan()
        assert not (tmp_path / "state" / "deployments.json").exists()


class TestApplying:
    def test_every_skill_lands(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        assert sorted(p.name for p in installed.iterdir()) == [
            "never4ga-capture",
            "never4ga-checkpoint",
            "never4ga-context",
            "never4ga-create",
            "never4ga-decide",
            "never4ga-doctor",
            "never4ga-startup",
            "never4ga-wrap",
        ]

    def test_the_deployed_file_is_the_canonical_one(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        deployed = (installed / "never4ga-startup" / "SKILL.md").read_text(encoding="utf-8")
        canonical = (vault / "50_System/Skills/never4ga-startup/SKILL.md").read_text(
            encoding="utf-8"
        )
        assert deployed == canonical

    def test_a_second_sync_changes_nothing(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        kinds = {action.kind for plan in service.plan() for action in plan.actions}
        assert kinds == {SyncActionKind.NOOP}

    def test_a_changed_canonical_skill_updates(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        source = vault / "50_System/Skills/never4ga-doctor/SKILL.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")

        updates = [
            action
            for plan in service.plan()
            for action in plan.actions
            if action.kind is SyncActionKind.UPDATE
        ]
        assert [action.name for action in updates] == ["never4ga-doctor"]

        service.apply(service.plan())
        assert (installed / "never4ga-doctor" / "SKILL.md").read_text(
            encoding="utf-8"
        ) == source.read_text(encoding="utf-8")


class TestTheRulesOfSectionTwentyFive:
    def test_an_unmanaged_skill_of_the_same_name_is_a_conflict(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        # Rule 1: never overwrite an unmanaged existing Skill directory.
        (installed / "never4ga-doctor").mkdir(parents=True)
        (installed / "never4ga-doctor" / "SKILL.md").write_text("mine", encoding="utf-8")

        service = build(vault, home, tmp_path, [probe_descriptor()])
        conflicts = [
            action
            for plan in service.plan()
            for action in plan.actions
            if action.kind is SyncActionKind.CONFLICT
        ]
        assert [action.name for action in conflicts] == ["never4ga-doctor"]

        service.apply(service.plan())
        assert (installed / "never4ga-doctor" / "SKILL.md").read_text(encoding="utf-8") == "mine"

    def test_an_unrelated_skill_is_preserved_and_never_removed(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        # Rule 3: never delete an unmanaged Skill.
        (installed / "someone-elses").mkdir(parents=True)
        (installed / "someone-elses" / "SKILL.md").write_text("theirs", encoding="utf-8")

        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        assert (installed / "someone-elses" / "SKILL.md").read_text(encoding="utf-8") == "theirs"

    def test_a_locally_edited_generated_skill_survives_and_is_reported(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        # Rules 4 and 5, and the distinction `plan_capability_sync` alone cannot
        # make: this is not a collision with something we never deployed, it is
        # an edit to something we did.
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        edited = installed / "never4ga-capture" / "SKILL.md"
        edited.write_text("my own version\n", encoding="utf-8")

        actions = [
            action
            for plan in service.plan()
            for action in plan.actions
            if action.name == "never4ga-capture"
        ]
        assert [action.kind for action in actions] == [SyncActionKind.CONFLICT]
        assert actions[0].reason == LOCAL_EDIT

        service.apply(service.plan())
        assert edited.read_text(encoding="utf-8") == "my own version\n"

    def test_unmanaged_configuration_is_byte_identical_after_apply(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        # Checked by bytes and mtime rather than by eye.
        (installed / "someone-elses").mkdir(parents=True)
        theirs = installed / "someone-elses" / "SKILL.md"
        theirs.write_text("theirs\n", encoding="utf-8")
        before = theirs.stat().st_mtime_ns, theirs.read_bytes()

        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        assert (theirs.stat().st_mtime_ns, theirs.read_bytes()) == before


class TestTheOwnershipManifest:
    def test_it_records_what_section_twenty_five_requires(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())

        store = DeploymentFile(tmp_path / "state" / "deployments.json")
        recorded = {item.name: item for item in store.load()}
        entry = recorded["never4ga-startup"]
        assert entry.client_id == "probe"
        assert entry.source_path == "50_System/Skills/never4ga-startup"
        assert entry.source_hash and entry.deployed_hash
        assert entry.deployed_at == "2026-08-25T12:00:00Z"
        assert entry.mechanism is DeploymentMechanism.COPY

    def test_without_it_a_deployed_skill_is_unmanaged(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        # Losing the manifest is not a cache miss. Everything already deployed
        # becomes something Never4gA may never overwrite.
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        (tmp_path / "state" / "deployments.json").unlink()

        kinds = {action.kind for plan in service.plan() for action in plan.actions}
        assert kinds == {SyncActionKind.CONFLICT}

    def test_it_round_trips(self, vault: Path, home: Path, tmp_path: Path, installed: Path) -> None:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        store = DeploymentFile(tmp_path / "state" / "deployments.json")
        first = list(store.load())
        store.save(first)
        assert list(store.load()) == first


class TestSymlinkIsOptInAndWorks:
    def test_a_descriptor_can_ask_for_a_link(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = build(
            vault,
            home,
            tmp_path,
            [probe_descriptor(deployment=DeploymentMechanism.SYMLINK)],
        )
        service.apply(service.plan())
        target = installed / "never4ga-startup"
        assert target.is_symlink()
        assert target.resolve() == (vault / "50_System/Skills/never4ga-startup").resolve()

    def test_it_is_refused_when_the_vault_path_is_unknown(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        from never4ga.services.adapters import AdapterSyncError

        service = AdapterService(
            [probe_descriptor(deployment=DeploymentMechanism.SYMLINK)],
            FileSystemVaultFileStore(vault),
            DeploymentFile(tmp_path / "state" / "deployments.json"),
            FileSystemClientFileStore(),
            home=home,
            now=fixed_clock,
        )
        with pytest.raises(AdapterSyncError, match="vault"):
            service.apply(service.plan())


class TestAFourthClientTouchesNoEngineCode:
    """A new client needs a descriptor file and no engine change.

    Nothing in this test imports anything client-specific, and nothing in the
    engine knows this client exists. It is a file.
    """

    def test_a_client_described_only_in_a_toml_file_receives_the_skills(
        self, vault: Path, home: Path, tmp_path: Path
    ) -> None:
        integrations = vault / "50_System" / "Integrations"
        integrations.mkdir(parents=True, exist_ok=True)
        (integrations / "fictional.toml").write_text(
            'client_id = "fictional"\n'
            'title = "A Client That Does Not Exist"\n'
            'global_skills_path = "~/.fictional/agent-skills"\n'
            'detect_paths = ["~/.fictional"]\n'
            'measured_on = "2026-08-25"\n',
            encoding="utf-8",
        )
        (home / ".fictional").mkdir()

        service = build(vault, home, tmp_path, list(load_descriptors(integrations)))
        service.apply(service.plan("fictional"))

        deployed = home / ".fictional" / "agent-skills" / "never4ga-startup" / "SKILL.md"
        assert deployed.is_file()
        assert deployed.read_text(encoding="utf-8").startswith("---\nname: never4ga-startup\n")

    def test_the_engine_names_no_client(self) -> None:
        # The constraint, checked rather than asserted in prose.
        source = (
            Path(__file__).parents[2] / "src" / "never4ga" / "services" / "adapters.py"
        ).read_text(encoding="utf-8")
        for client in ("claude", "codex", "antigravity", "gemini", "cursor"):
            assert client not in source.casefold(), client


class TestRemovalRequiresProofTheBytesAreOurs:
    """A deployed copy is removed only if it still holds the bytes we wrote.

    "We deployed this" is not enough to license a delete (`core/04` section 25
    rules 4 and 6). The manifest keeps a digest per file, so an edited or added
    file is detected and the copy is retained.

    The guard is tested directly rather than through a planned removal. A
    REMOVE is reachable only when a capability is disabled for a client, and
    driving the whole planner to reach it would test the planner, not this
    decision.
    """

    def _service(self, vault: Path, home: Path, tmp_path: Path) -> AdapterService:
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        return service

    def _recorded(self, tmp_path: Path, name: str = "never4ga-capture") -> Deployment:
        entries = DeploymentFile(tmp_path / "state" / "deployments.json").load()
        return next(item for item in entries if item.name == name)

    def test_an_untouched_copy_may_be_removed(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = self._service(vault, home, tmp_path)
        recorded = self._recorded(tmp_path)
        assert service._retained_reason(Path(recorded.target), recorded) is None

    def test_an_edited_copy_is_retained_and_says_which_file(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = self._service(vault, home, tmp_path)
        recorded = self._recorded(tmp_path)
        (Path(recorded.target) / "SKILL.md").write_text("mine now\n", encoding="utf-8")

        reason = service._retained_reason(Path(recorded.target), recorded)
        assert reason is not None
        assert "edited" in reason and "SKILL.md" in reason

    def test_an_added_file_counts_as_an_edit(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        """A file the deploy never wrote is an edit, not a rounding error."""
        service = self._service(vault, home, tmp_path)
        recorded = self._recorded(tmp_path)
        (Path(recorded.target) / "notes.md").write_text("mine\n", encoding="utf-8")

        reason = service._retained_reason(Path(recorded.target), recorded)
        assert reason is not None and "notes.md" in reason

    def test_a_copy_never_recorded_is_retained(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        """Rule 3: nothing proves Never4gA wrote it, so it is not ours to delete."""
        service = self._service(vault, home, tmp_path)
        reason = service._retained_reason(installed / "someone-elses", None)
        assert reason is not None and "manifest" in reason

    def test_an_already_absent_copy_is_not_an_error(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        service = self._service(vault, home, tmp_path)
        recorded = self._recorded(tmp_path)
        shutil.rmtree(recorded.target)
        assert service._retained_reason(Path(recorded.target), recorded) == "already gone"

    def test_the_manifest_records_a_digest_per_file(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        self._service(vault, home, tmp_path)
        assert "SKILL.md" in self._recorded(tmp_path).files

    def test_a_manifest_written_before_this_still_loads(self, tmp_path: Path) -> None:
        """An old manifest must keep working.

        Refusing one for want of a field it could not have had would make every
        deployed Skill unmanaged at once, and the next sync would treat the
        whole library as somebody else's.
        """
        path = tmp_path / "old" / "deployments.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "deployments": [
                        {
                            "client_id": "probe",
                            "name": "never4ga-capture",
                            "target": "/tmp/x",
                            "source_path": "50_System/Skills/never4ga-capture",
                            "source_hash": "abc",
                            "deployed_hash": "abc",
                            "mechanism": "copy",
                            "deployed_at": "2026-08-27T12:00:00Z",
                        }
                    ],
                }
            )
        )
        assert DeploymentFile(path).load()[0].files == {}


class TestAgentsAreASecondDeployedClass:
    """Agents in `50_System/Agents/` deploy to clients alongside Skills.

    An agent is a file where a Skill is a directory, and only some clients have
    anywhere to put one. That is a descriptor field, not a branch in the engine.
    """

    def _with_agent(self, vault: Path, name: str = "reviewer", text: str = "# Reviewer\n") -> Path:
        agents = vault / "50_System/Agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / f"{name}.md").write_text(text, encoding="utf-8")
        return agents

    def _taking_agents(self, **overrides: object) -> ClientDescriptor:
        return probe_descriptor(global_agents_path="~/.probe/agents", **overrides)

    def test_a_client_with_no_agents_path_receives_none(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        """Not a gap: a client without a subagent format is described, not worked around."""
        self._with_agent(vault)
        service = build(vault, home, tmp_path, [probe_descriptor()])
        service.apply(service.plan())
        assert not (home / ".probe" / "agents").exists()

    def test_an_agent_is_deployed_as_a_file(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        self._with_agent(vault)
        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())
        assert (home / ".probe" / "agents" / "reviewer.md").read_text(encoding="utf-8") == (
            "# Reviewer\n"
        )

    def test_an_agent_and_a_skill_may_share_a_name(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        """One manifest holds both, so their keys must not collide."""
        self._with_agent(vault, name="never4ga-capture", text="# Not the skill\n")
        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())

        assert (home / ".probe" / "agents" / "never4ga-capture.md").read_text(
            encoding="utf-8"
        ) == "# Not the skill\n"
        assert (installed / "never4ga-capture" / "SKILL.md").exists()

    def test_a_changed_agent_is_redeployed(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        agents = self._with_agent(vault)
        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())
        (agents / "reviewer.md").write_text("# Revised\n", encoding="utf-8")

        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())
        assert (home / ".probe" / "agents" / "reviewer.md").read_text(encoding="utf-8") == (
            "# Revised\n"
        )

    def test_an_edited_agent_in_the_client_is_not_overwritten(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        """Section 25 rule 4 reaches the second class too."""
        self._with_agent(vault)
        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())
        deployed = home / ".probe" / "agents" / "reviewer.md"
        deployed.write_text("mine now\n", encoding="utf-8")

        service = build(vault, home, tmp_path, [self._taking_agents()])
        outcomes = service.apply(service.plan())
        assert deployed.read_text(encoding="utf-8") == "mine now\n"
        assert any(o.action.kind is SyncActionKind.CONFLICT for o in outcomes)

    def test_an_unmanaged_agent_is_left_alone(
        self, vault: Path, home: Path, tmp_path: Path, installed: Path
    ) -> None:
        (home / ".probe" / "agents").mkdir(parents=True)
        (home / ".probe" / "agents" / "theirs.md").write_text("theirs\n", encoding="utf-8")
        self._with_agent(vault)

        service = build(vault, home, tmp_path, [self._taking_agents()])
        service.apply(service.plan())
        assert (home / ".probe" / "agents" / "theirs.md").read_text(encoding="utf-8") == "theirs\n"

    def test_a_non_markdown_file_is_not_an_agent(
        self, vault: Path, home: Path, tmp_path: Path
    ) -> None:
        """The island may hold whatever a user keeps there."""
        agents = self._with_agent(vault)
        (agents / "notes.txt").write_text("scratch\n", encoding="utf-8")
        found = canonical_agents(FileSystemVaultFileStore(vault))
        assert [agent.name for agent in found] == ["reviewer"]

    def test_the_shipped_descriptors_agree_on_who_takes_agents(self) -> None:
        """Exactly one measured client declares a place for them.

        Asserted rather than assumed: if a second client grows a subagent
        format, this is where the descriptor table is made to say so.
        """
        taking = [d.client_id for d in load_descriptors() if d.takes_agents]
        assert len(taking) == 1, taking
