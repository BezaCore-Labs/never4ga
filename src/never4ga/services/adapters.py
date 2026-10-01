"""One sync engine over a table of client descriptors.

**An adapter is a descriptor, not a class.** Nothing below knows the name of
any particular client. It knows how to detect one from its descriptor, read what
is installed, compute what should be, diff the two, write only what it can prove
it owns, and report the rest. Supporting another client is a row in a TOML file
and a test.

:func:`~never4ga.domain.extensions.plan_capability_sync` encodes the rules of
`core/04` section 25: preserve the unmanaged, conflict on a name collision,
never remove without ownership proof. This module supplies the two things the
planner cannot know and takes its answer:

    discovery   what is actually in the client's directory, and whose it is
    application what to do about the plan it hands back

The one distinction the port cannot make is between a copy that is *stale*
(canonical moved on) and one that was *edited locally* (section 25 rule 4: do
not destroy the edit silently). Both look like "fingerprint does not match", so
this module decides which by consulting the ownership manifest before planning,
and says which in the reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from never4ga.domain.clients import ClientDescriptor, DeploymentMechanism
from never4ga.domain.deployments import Deployment, tree_hash
from never4ga.domain.document import VaultPath
from never4ga.domain.extensions import (
    NEVER4GA_OWNER,
    ClientState,
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
    SyncAction,
    SyncActionKind,
    SyncPlan,
    plan_capability_sync,
)
from never4ga.domain.vendoring import file_digests
from never4ga.errors import Never4gaError
from never4ga.layout import VaultRoot
from never4ga.ports.client_files import ClientFileStore
from never4ga.ports.deployments import DeploymentStore
from never4ga.ports.vault_files import VaultFileStore
from never4ga.services.authoring import Clock, format_timestamp, utc_now

__all__ = [
    "AGENTS_DIRECTORY",
    "SKILLS_DIRECTORY",
    "AdapterFinding",
    "AdapterService",
    "AdapterSyncError",
    "CanonicalAgent",
    "CanonicalSkill",
    "ClientStatus",
    "SyncOutcome",
    "canonical_agents",
    "canonical_skills",
]

#: Where canonical Skills live (core/04 section 20).
SKILLS_DIRECTORY: Final = f"{VaultRoot.SYSTEM}/Skills"

#: Where canonical agent definitions live, reserved by `core/01`.
AGENTS_DIRECTORY: Final = f"{VaultRoot.SYSTEM}/Agents"

#: The reason a conflict carries when the client's copy was edited after we
#: deployed it, as distinct from a name we never deployed at all.
LOCAL_EDIT: Final = "locally_edited_generated_skill"


class AdapterSyncError(Never4gaError):
    """A sync could not be planned or applied as asked."""


@dataclass(frozen=True, slots=True)
class CanonicalSkill:
    """One Skill as the vault holds it.

    A Skill is a *directory* -- `SKILL.md` plus optional `scripts/`,
    `references/` and `assets/` (core/04 section 20) -- so everything here works
    on whole trees. Half a deployed Skill is worse than none.
    """

    name: str
    source_path: VaultPath
    files: Mapping[str, str]

    @property
    def content_hash(self) -> str:
        return tree_hash(self.files)


@dataclass(frozen=True, slots=True)
class ClientStatus:
    descriptor: ClientDescriptor
    installed: bool
    skills_directory: Path

    @property
    def client_id(self) -> str:
        return self.descriptor.client_id


@dataclass(frozen=True, slots=True)
class AdapterFinding:
    """Something `adapters doctor` wants a person to know."""

    code: str
    client_id: str
    name: str
    detail: str

    @property
    def is_actionable(self) -> bool:
        """Whether somebody has to decide something.

        A Skill that is simply missing is not: `sync --apply` deploys it. A
        conflict is, because Never4gA will not resolve it either way.
        """
        return self.code == "collision"


#: Agents and Skills share one manifest and one planner, so their names must
#: not collide: a Skill and an agent may legitimately be called the same thing.
#: The prefix is the whole disambiguation and it is deliberately greppable.
_AGENT_PREFIX: Final = "agent:"


def _agent_id(name: str) -> str:
    return f"{_AGENT_PREFIX}{name}"


def _is_agent(capability: str) -> bool:
    return capability.startswith(_AGENT_PREFIX)


def _agent_name(capability: str) -> str:
    return capability.removeprefix(_AGENT_PREFIX)


#: Which plan actions are worth reporting, and as what. NOOP and PRESERVE are
#: the healthy cases and say nothing.
_FINDING_OF: Final = {
    SyncActionKind.CONFLICT: "collision",
    SyncActionKind.INSTALL: "not_deployed",
    SyncActionKind.UPDATE: "stale",
    SyncActionKind.REMOVE: "to_remove",
}


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What applying a plan actually did."""

    action: SyncAction
    applied: bool
    detail: str = ""


def canonical_skills(files: VaultFileStore) -> tuple[CanonicalSkill, ...]:
    """Every Skill in `50_System/Skills/`, whole.

    A directory without a `SKILL.md` is not a Skill and is skipped rather than
    refused: `50_System/Skills/` is a foreign-format island and a user may keep
    whatever they like in it.
    """
    prefix = tuple(SKILLS_DIRECTORY.split("/"))
    grouped: dict[str, dict[str, str]] = {}
    sources: dict[str, VaultPath] = {}

    for path in files.iter_paths():
        segments = path.segments
        if segments[: len(prefix)] != prefix or len(segments) <= len(prefix) + 1:
            continue
        name = segments[len(prefix)]
        relative = "/".join(segments[len(prefix) + 1 :])
        content = files.read_text(path)
        if content is None:
            continue
        grouped.setdefault(name, {})[relative] = content
        sources[name] = VaultPath((*prefix, name))

    return tuple(
        CanonicalSkill(name=name, source_path=sources[name], files=tree)
        for name, tree in sorted(grouped.items())
        if "SKILL.md" in tree
    )


@dataclass(frozen=True, slots=True)
class CanonicalAgent:
    """One agent definition as the vault holds it.

    A file, not a directory. `core/04` section 20 makes a Skill a directory
    because it may carry scripts and references; a subagent is one Markdown
    file with frontmatter, which is the client's layout and not a choice here.
    """

    name: str
    source_path: VaultPath
    text: str

    @property
    def content_hash(self) -> str:
        return tree_hash({self.filename: self.text})

    @property
    def filename(self) -> str:
        return f"{self.name}.md"


def canonical_agents(files: VaultFileStore) -> tuple[CanonicalAgent, ...]:
    """Every agent in `50_System/Agents/`.

    The root is reserved by `core/01` and seeded empty by `init`. Anything that
    is not a Markdown file is skipped rather than refused: the directory is a
    foreign-format island and a user may keep what they like in it.
    """
    prefix = tuple(AGENTS_DIRECTORY.split("/"))
    found: list[CanonicalAgent] = []
    for path in files.iter_paths():
        segments = path.segments
        if segments[: len(prefix)] != prefix or len(segments) != len(prefix) + 1:
            continue
        name = segments[-1]
        if not name.endswith(".md"):
            continue
        text = files.read_text(path)
        if text is None:
            continue
        found.append(CanonicalAgent(name=name.removesuffix(".md"), source_path=path, text=text))
    return tuple(sorted(found, key=lambda agent: agent.name))


class AdapterService:
    """Detects clients, plans deployment, applies it, and reports drift."""

    def __init__(
        self,
        descriptors: Sequence[ClientDescriptor],
        vault_files: VaultFileStore,
        deployments: DeploymentStore,
        client_files: ClientFileStore,
        *,
        home: Path,
        vault_root: Path | None = None,
        now: Clock = utc_now,
    ) -> None:
        self._descriptors = tuple(descriptors)
        #: Needed only for symlink deployment, which points a client at the
        #: vault rather than copying out of it.
        self._vault_root = vault_root
        self._vault_files = vault_files
        self._deployments = deployments
        self._client_files = client_files
        self._home = home
        self._now = now

    # -- what is out there ------------------------------------------------

    def clients(self) -> tuple[ClientStatus, ...]:
        return tuple(
            ClientStatus(
                descriptor=descriptor,
                installed=descriptor.is_installed(self._home),
                skills_directory=descriptor.global_skills_directory(self._home),
            )
            for descriptor in self._descriptors
        )

    def _client(self, client_id: str) -> ClientStatus:
        for status in self.clients():
            if status.client_id == client_id:
                return status
        known = ", ".join(status.client_id for status in self.clients())
        raise AdapterSyncError(f"no descriptor for {client_id!r}; this machine knows {known}")

    # -- planning ---------------------------------------------------------

    def plan(self, client_id: str | None = None) -> tuple[SyncPlan, ...]:
        """What a sync would do. Writes nothing, ever."""
        skills = canonical_skills(self._vault_files)
        agents = canonical_agents(self._vault_files)
        targets = (
            [self._client(client_id)]
            if client_id is not None
            else [status for status in self.clients() if status.installed]
        )
        return tuple(
            self._plan_one(status, skills, agents) for status in targets if status.installed
        )

    def _plan_one(
        self,
        status: ClientStatus,
        skills: Sequence[CanonicalSkill],
        agents: Sequence[CanonicalAgent] = (),
    ) -> SyncPlan:
        discovered, edited = self._discover(status)
        records = list(_records_for(status.client_id, skills))
        # Agents go through the same planner, not a parallel one. Every rule
        # worth having here -- preserve the unmanaged, conflict on a name
        # collision, never remove without ownership proof -- is in
        # `plan_capability_sync`, and a second implementation of them would be
        # a second place for them to drift. What differs is only where the
        # bytes land, which is what the namespaced id carries.
        if status.descriptor.takes_agents:
            agent_discovered, agent_edited = self._discover_agents(status)
            discovered = [*discovered, *agent_discovered]
            edited |= agent_edited
            records.extend(_agent_records_for(status.client_id, agents))
        plan = plan_capability_sync(status.client_id, tuple(records), discovered)
        return SyncPlan(
            client_id=plan.client_id,
            actions=tuple(_relabel(action, edited) for action in plan.actions),
        )

    def _discover_agents(self, status: ClientStatus) -> tuple[list[DiscoveredCapability], set[str]]:
        """What is in the client's agents directory, and whose it is."""
        root = status.descriptor.global_agents_directory(self._home)
        recorded = {
            item.name: item
            for item in self._deployments.load()
            if item.client_id == status.client_id
        }
        found: list[DiscoveredCapability] = []
        edited: set[str] = set()
        if not self._client_files.exists(root):
            return found, edited
        for path in sorted(self._entries(root)):
            if path.is_dir() or not path.name.endswith(".md"):
                continue
            tree = self._client_files.read_tree(path.parent)
            text = (tree or {}).get(path.name)
            if text is None:
                continue
            name = _agent_id(path.name.removesuffix(".md"))
            actual = tree_hash({path.name: text})
            deployment = recorded.get(name)
            ours = deployment is not None and deployment.deployed_hash == actual
            if deployment is not None and not ours:
                edited.add(name)
            found.append(
                DiscoveredCapability(
                    client_id=status.client_id,
                    extension_class=ExtensionClass.SKILL,
                    name=name,
                    fingerprint=actual,
                    owner=NEVER4GA_OWNER if ours else None,
                    source_path=str(path),
                )
            )
        return found, edited

    def _discover(self, status: ClientStatus) -> tuple[list[DiscoveredCapability], set[str]]:
        """What is in the client's directory, and whose it is.

        The ownership manifest decides "ours". A Skill whose bytes no longer
        match what we recorded deploying is reported as *not* ours, which is how
        section 25 rule 4 becomes behaviour rather than intention: an unmanaged
        capability is one sync may never overwrite.
        """
        recorded = {
            item.name: item
            for item in self._deployments.load()
            if item.client_id == status.client_id
        }
        found: list[DiscoveredCapability] = []
        edited: set[str] = set()

        for name, tree in self._installed_trees(status).items():
            actual = tree_hash(tree)
            deployment = recorded.get(name)
            ours = deployment is not None and deployment.deployed_hash == actual
            if deployment is not None and not ours:
                edited.add(name)
            found.append(
                DiscoveredCapability(
                    client_id=status.client_id,
                    extension_class=ExtensionClass.SKILL,
                    name=name,
                    fingerprint=actual,
                    owner=NEVER4GA_OWNER if ours else None,
                    source_path=str(status.skills_directory / name),
                )
            )
        return found, edited

    def _installed_trees(self, status: ClientStatus) -> dict[str, Mapping[str, str]]:
        root = status.skills_directory
        if not self._client_files.exists(root):
            return {}
        trees: dict[str, Mapping[str, str]] = {}
        for tree_root in sorted(self._entries(root)):
            tree = self._client_files.read_tree(tree_root)
            if tree is not None:
                trees[tree_root.name] = tree
        return trees

    def _entries(self, root: Path) -> list[Path]:
        listing = self._client_files.read_tree(root)
        if listing is None:
            return []
        # `read_tree` flattens, so the first segment of each key is an entry.
        return [root / name for name in sorted({key.split("/")[0] for key in listing})]

    # -- application ------------------------------------------------------

    def diagnose(self) -> tuple[AdapterFinding, ...]:
        """What is deployed, what drifted, and what collided (section 4.3).

        Reports and stops, exactly as `doctor` does. Nothing here repairs, and
        nothing here writes: a drifted Skill is a decision for a person.
        """
        findings: list[AdapterFinding] = []
        #: Read at most once, and only when some client is installed. The list
        #: belongs to the vault, not to a client, so it cannot change between
        #: iterations.
        skills: tuple[CanonicalSkill, ...] | None = None
        for status in self.clients():
            if not status.installed:
                findings.append(
                    AdapterFinding(
                        "client_not_installed",
                        status.client_id,
                        "",
                        f"{status.descriptor.title} is not installed on this machine",
                    )
                )
                continue
            if skills is None:
                skills = canonical_skills(self._vault_files)
            for action in self._plan_one(status, skills).actions:
                finding = _FINDING_OF.get(action.kind)
                if finding is not None:
                    findings.append(
                        AdapterFinding(finding, status.client_id, action.name, action.reason)
                    )
        return tuple(findings)

    def apply(self, plans: Sequence[SyncPlan]) -> tuple[SyncOutcome, ...]:
        """Do what the plan said, and record what was done.

        Only INSTALL, UPDATE and REMOVE touch anything. A conflict is reported
        and left alone -- that is the whole point of it being a conflict.
        """
        skills = {skill.name: skill for skill in canonical_skills(self._vault_files)}
        manifest = {item.key: item for item in self._deployments.load()}
        outcomes: list[SyncOutcome] = []

        for plan in plans:
            status = self._client(plan.client_id)
            for action in plan.actions:
                outcomes.append(self._apply_one(action, status, skills, manifest))

        self._deployments.save(tuple(manifest.values()))
        return tuple(outcomes)

    def _apply_one(
        self,
        action: SyncAction,
        status: ClientStatus,
        skills: Mapping[str, CanonicalSkill],
        manifest: dict[tuple[str, str], Deployment],
    ) -> SyncOutcome:
        if _is_agent(action.name):
            return self._apply_agent(action, status, manifest)

        target = status.skills_directory / action.name
        key = (status.client_id, action.name)

        if action.kind in (SyncActionKind.INSTALL, SyncActionKind.UPDATE):
            skill = skills.get(action.name)
            if skill is None:
                return SyncOutcome(action, False, "no canonical source")
            self._deploy(status.descriptor, skill, target)
            manifest[key] = Deployment(
                client_id=status.client_id,
                name=skill.name,
                target=str(target),
                source_path=str(skill.source_path),
                source_hash=skill.content_hash,
                deployed_hash=skill.content_hash,
                mechanism=status.descriptor.deployment,
                deployed_at=format_timestamp(self._now()),
                files=file_digests(skill.files),
            )
            return SyncOutcome(action, True, str(target))

        if action.kind is SyncActionKind.REMOVE:
            # Removing on the strength of "we deployed this" is not the same as
            # "we deployed this *and it still has the bytes we wrote*", and only
            # the second keeps `core/04` section 25 rule 4 -- never destroy an
            # edit silently. Everything else is retained
            # and reported, which is rule 5.
            retained = self._retained_reason(target, manifest.get(key))
            if retained is not None:
                return SyncOutcome(action, False, retained)
            self._client_files.remove_tree(target)
            manifest.pop(key, None)
            return SyncOutcome(action, True, str(target))

        return SyncOutcome(action, False, action.reason)

    def _apply_agent(
        self,
        action: SyncAction,
        status: ClientStatus,
        manifest: dict[tuple[str, str], Deployment],
    ) -> SyncOutcome:
        """One agent file, under the same ownership rules as a Skill.

        Separate only because an agent is a file where a Skill is a directory.
        Everything that decides *whether* to write is upstream of here.
        """
        if not status.descriptor.takes_agents:
            return SyncOutcome(action, False, "this client has no agents directory")
        agents = {agent.name: agent for agent in canonical_agents(self._vault_files)}
        name = _agent_name(action.name)
        root = status.descriptor.global_agents_directory(self._home)
        key = (status.client_id, action.name)

        if action.kind in (SyncActionKind.INSTALL, SyncActionKind.UPDATE):
            agent = agents.get(name)
            if agent is None:
                return SyncOutcome(action, False, "no canonical source")
            # Merge, never replace. `write_tree` clears the directory first,
            # which is right for a Skill -- the root *is* the Skill -- and
            # catastrophic here, where the root is shared with every other
            # agent the client has, managed or not. Replacing it would delete
            # unmanaged capabilities, which `core/09` section 11 forbids
            # outright.
            beside = dict(self._client_files.read_tree(root) or {})
            self._client_files.write_tree(root, {**beside, agent.filename: agent.text})
            manifest[key] = Deployment(
                client_id=status.client_id,
                name=action.name,
                target=str(root / agent.filename),
                source_path=str(agent.source_path),
                source_hash=agent.content_hash,
                deployed_hash=agent.content_hash,
                mechanism=DeploymentMechanism.COPY,
                deployed_at=format_timestamp(self._now()),
                files=file_digests({agent.filename: agent.text}),
            )
            return SyncOutcome(action, True, str(root / agent.filename))

        if action.kind is SyncActionKind.REMOVE:
            recorded = manifest.get(key)
            present = self._client_files.read_tree(root) or {}
            filename = f"{name}.md"
            if filename not in present:
                manifest.pop(key, None)
                return SyncOutcome(action, True, "already gone")
            if (
                recorded is None
                or tree_hash({filename: present[filename]}) != recorded.deployed_hash
            ):
                return SyncOutcome(action, False, "retained -- edited since it was deployed")
            remaining = {k: v for k, v in present.items() if k != filename}
            self._client_files.write_tree(root, remaining)
            manifest.pop(key, None)
            return SyncOutcome(action, True, str(root / filename))

        return SyncOutcome(action, False, action.reason)

    def _retained_reason(self, target: Path, recorded: Deployment | None) -> str | None:
        """Why this copy must not be removed, or None if it may be.

        Three reasons: Never4gA did not write it; it cannot be read to check;
        or it no longer holds the bytes that were written. A manifest entry
        without per-file digests can still answer the question at tree level,
        which is why the fallback exists rather than a refusal.
        """
        if recorded is None:
            return "not in the ownership manifest; nothing proves Never4gA wrote it"
        if self._client_files.is_symlink(target):
            return "a symlink, which is the client's to manage"
        present = self._client_files.read_tree(target)
        if present is None:
            return "already gone"
        if tree_hash(present) == recorded.deployed_hash:
            return None
        changed = sorted(_changed_files(recorded.files, present)) if recorded.files else []
        named = f": {', '.join(changed[:3])}" if changed else ""
        return f"retained -- edited since it was deployed{named}"

    def _deploy(self, descriptor: ClientDescriptor, skill: CanonicalSkill, target: Path) -> None:
        """Copy, or link, exactly as the descriptor asks.

        Copy is the default and symlink is opt-in. Both work on the supported
        clients, but a symlinked Skill is a client writing into the Git-backed
        vault.
        """
        if descriptor.deployment is DeploymentMechanism.COPY:
            self._client_files.write_tree(target, dict(skill.files))
            return
        if self._vault_root is None:
            raise AdapterSyncError(
                f"{descriptor.client_id} asks for symlink deployment, which needs "
                "the vault's path on this machine"
            )
        self._client_files.link_tree(target, self._vault_root / str(skill.source_path))


def _records_for(client_id: str, skills: Sequence[CanonicalSkill]) -> tuple[ExtensionRecord, ...]:
    """The canonical Skills as registry records, enabled for this client.

    Built per call rather than persisted. What is durable here is the ownership
    manifest; the Skills themselves are the vault's, and a second store of them
    would be a second place for them to be wrong.
    """
    return tuple(
        ExtensionRecord(
            capability_id=skill.name,
            extension_class=ExtensionClass.SKILL,
            title=skill.name,
            deployment_mode=DeploymentMode.MANAGED,
            portability=PortabilityLevel.PORTABLE,
            clients={client_id: ClientState.ENABLED},
            permissions=Permissions(read_only=True),
            ownership=OwnershipProof(owner=NEVER4GA_OWNER, content_hash=skill.content_hash),
            origin=str(skill.source_path),
        )
        for skill in skills
    )


def _relabel(action: SyncAction, edited: set[str]) -> SyncAction:
    """Say which kind of conflict this is.

    `plan_sync` sees one fact -- the fingerprint does not match what we own --
    and calls it a collision with unmanaged configuration. When the manifest
    says we *did* deploy this name, the truth is narrower and more useful: the
    generated copy was edited locally, and section 25 rule 4 forbids destroying
    it.
    """
    if action.kind is SyncActionKind.CONFLICT and action.name in edited:
        return replace(action, reason=LOCAL_EDIT)
    return action


def _changed_files(recorded: Mapping[str, str], present: Mapping[str, str]) -> set[str]:
    """Which files differ between what was written and what is there now."""
    now = file_digests(present)
    return set(recorded) ^ set(now) | {name for name in recorded if recorded[name] != now.get(name)}


def _agent_records_for(
    client_id: str, agents: Sequence[CanonicalAgent]
) -> tuple[ExtensionRecord, ...]:
    """The canonical agents as registry records, enabled for this client.

    `ADAPTABLE` rather than `PORTABLE` (`core/09` section 19). The *content* is
    ordinary Markdown that any tool could read; what is not portable is where
    it lands, and few clients declare anywhere to put it. Claiming `PORTABLE`
    would present a provider-native placement as though it travelled.
    `PROVIDER_NATIVE` would be wrong in the other direction -- the planner
    records those and never deploys them, which would leave every agent in the
    vault forever.

    Which client that is belongs in a descriptor and deliberately does not
    appear here; `test_the_engine_names_no_client` refuses the name even in a
    comment, which is how "adapters are data, not code" stays true.
    """
    return tuple(
        ExtensionRecord(
            capability_id=_agent_id(agent.name),
            extension_class=ExtensionClass.SKILL,
            title=agent.name,
            deployment_mode=DeploymentMode.MANAGED,
            portability=PortabilityLevel.ADAPTABLE,
            clients={client_id: ClientState.ENABLED},
            permissions=Permissions(read_only=True),
            ownership=OwnershipProof(owner=NEVER4GA_OWNER, content_hash=agent.content_hash),
            origin=str(agent.source_path),
        )
        for agent in agents
    )
