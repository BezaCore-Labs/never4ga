"""Keeping a vault's canonical Skills current with the ones Never4gA ships.

`core/01` seeds `50_System/Skills/` at `init`, and `core/04` section 20 makes
the vault canonical for a Skill so that a user edit is honoured exactly as an
edited template is. This module is how an *improved* Skill reaches a vault that
already exists. `VaultInitializer` writes "unless something is already there",
so without it every vault keeps the text it was born with, and `adapters
doctor`, which compares clients against the vault, reports that as healthy. A
Skill that tells an agent to use a flag the product has since replaced is worse
than a missing one: it is confidently wrong.

Section 25 states the ownership rules for the vault-to-client hop. This module
applies the same ones to the shipped-to-vault hop, because the hazard is
identical -- rule 1 and rule 4 (never destroy an edit), rule 5 (report drift
rather than resolve it) and rule 7 (dry run).

**Section 25's mechanism does not carry over, and that is the design problem
here.** It resolves ownership with a machine-local manifest, which works because
a deployed copy belongs to one machine. A vault does not: it is the thing that
travels, and a manifest on this laptop says nothing about the same vault cloned
onto another. So provenance lives in the file. Each seeded Skill records the
hash of the body it was seeded with, and "did somebody edit this?" is answered
by rehashing rather than by remembering having written it.

That leaves one honest gap: a Skill seeded without a hash cannot be *proven*
untouched. Those report `UNKNOWN_PROVENANCE` and are
never overwritten silently -- `--force` is the way through, and it is the user
saying what Never4gA cannot know.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.layout import VaultRoot
from never4ga.ports.vault_files import VaultFileStore
from never4ga.services.adapters import SKILLS_DIRECTORY
from never4ga.services.scaffold import SHIPPED_TEMPLATES, SKILLS, skill_body_hash

__all__ = ["SEED_MANIFEST", "SkillLibrary", "SkillRefresh", "SkillState", "SkillStatus"]

_VERSION_KEY: Final = "never4ga-version"
_SEED_KEY: Final = "never4ga-seed-hash"

#: Where a template's provenance lives, because it cannot live in the template.
#:
#: A Skill has a `metadata` block Never4gA owns, so its seed hash sits there and
#: travels with the file. A template is a *concept skeleton*: its frontmatter is
#: placeholders a person copies in Obsidian to start a note, so a field added
#: there would be copied into every concept made from it.
#:
#: In the vault rather than beside the index, because provenance has to travel
#: with the vault. `50_System/Templates/` is a declared
#: foreign-format island (`core/02` section 3.3) where nothing is validated or
#: indexed as a concept, which is what makes it a legitimate home for JSON.
SEED_MANIFEST: Final = "seed-manifest.json"

TEMPLATES_DIRECTORY: Final = f"{VaultRoot.SYSTEM}/Templates"


class SkillState(StrEnum):
    """Where one vault Skill stands against the one this build ships."""

    CURRENT = "current"
    """Same version, and the body still hashes to its seed. Nothing to do."""

    OUTDATED = "outdated"
    """An older version, provably untouched. The one case safe to refresh."""

    EDITED = "edited"
    """The body no longer hashes to its seed. Somebody's work; never ours."""

    UNKNOWN_PROVENANCE = "unknown_provenance"
    """Seeded before provenance was recorded, so an edit cannot be ruled out."""

    MISSING = "missing"
    """Shipped, and not in the vault at all."""


@dataclass(frozen=True, slots=True)
class SkillStatus:
    name: str
    state: SkillState
    vault_version: str | None
    shipped_version: str
    #: Whether the shipped body differs from what this copy was seeded with.
    #: Distinct from a version change, and reported differently: "0.3.0 ->
    #: 0.3.0" tells a reader nothing about why their Skill is being replaced.
    body_moved_on: bool = False

    @property
    def needs_a_person(self) -> bool:
        """Whether refreshing this one is a decision rather than a chore."""
        return self.state in (SkillState.EDITED, SkillState.UNKNOWN_PROVENANCE)


@dataclass(frozen=True, slots=True)
class SkillRefresh:
    """One Skill a refresh would change, or did."""

    name: str
    state: SkillState
    applied: bool
    detail: str = ""


class SkillLibrary:
    """Compares `50_System/Skills/` against the Skills this build ships."""

    def __init__(self, files: VaultFileStore) -> None:
        self._files = files

    def status(self) -> tuple[SkillStatus, ...]:
        """Every shipped Skill and where the vault's copy stands. Reads only."""
        return tuple(self._status_of(name, text) for name, text in sorted(SKILLS.items()))

    def refresh(self, *, apply: bool, force: bool = False) -> tuple[SkillRefresh, ...]:
        """Bring outdated Skills up to date.

        Without `apply` this writes nothing and returns what it would do, which
        is section 25 rule 7. `force` extends it to Skills whose provenance says
        somebody may have edited them -- never the default, because the whole
        point of recording a seed hash is to not have to guess.
        """
        changes: list[SkillRefresh] = []
        for status in self.status():
            if not self._would_change(status, force=force):
                continue
            if apply:
                self._write(status.name)
            changes.append(
                SkillRefresh(
                    name=status.name,
                    state=status.state,
                    applied=apply,
                    detail=self._reason(status),
                )
            )
        return tuple(changes)

    # -- templates --------------------------------------------------------

    def template_status(self) -> tuple[SkillStatus, ...]:
        """Every shipped template and where the vault's copy stands.

        The same five states as a Skill, decided the same way, from a hash kept
        beside the library rather than inside the file. There is no version
        field to compare: a template carries no metadata of its own, so
        "outdated" is "the recorded seed is not what this build ships" and
        "edited" is "the file is not what the recorded seed says it was".
        """
        recorded = self._manifest()
        return tuple(
            self._template_status_of(name, text, recorded)
            for name, text in sorted(SHIPPED_TEMPLATES.items())
        )

    def refresh_templates(self, *, apply: bool, force: bool = False) -> tuple[SkillRefresh, ...]:
        """Bring outdated templates up to date, on the same terms as Skills."""
        changes: list[SkillRefresh] = []
        recorded = dict(self._manifest())
        for status in self.template_status():
            if not self._would_change(status, force=force):
                continue
            if apply:
                shipped = SHIPPED_TEMPLATES[status.name]
                self._files.write_text(_template_path(status.name), shipped)
                recorded[status.name] = skill_body_hash(shipped)
            changes.append(
                SkillRefresh(
                    name=status.name,
                    state=status.state,
                    applied=apply,
                    detail=self._reason(status),
                )
            )
        if apply and changes:
            self._save_manifest(recorded)
        return tuple(changes)

    def _template_status_of(
        self, name: str, shipped: str, recorded: Mapping[str, str]
    ) -> SkillStatus:
        shipped_hash = skill_body_hash(shipped)
        text = self._files.read_text(_template_path(name))
        if text is None:
            return SkillStatus(name, SkillState.MISSING, None, shipped_hash)

        seeded = recorded.get(name)
        if seeded is None:
            return SkillStatus(name, SkillState.UNKNOWN_PROVENANCE, None, shipped_hash)
        if skill_body_hash(text) != seeded:
            return SkillStatus(name, SkillState.EDITED, seeded, shipped_hash)
        state = SkillState.CURRENT if seeded == shipped_hash else SkillState.OUTDATED
        return SkillStatus(name, state, seeded, shipped_hash)

    def _manifest(self) -> Mapping[str, str]:
        """What Never4gA last wrote into `50_System/Templates/`.

        An unreadable or malformed manifest is an absent one: every template
        then reports `unknown_provenance`, which refuses to overwrite anything.
        Failing towards "do not touch the user's files" is the only safe
        direction for a file whose whole job is to authorise overwriting them.
        """
        raw = self._files.read_text(_manifest_path())
        if raw is None:
            return {}
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(k): str(v) for k, v in loaded.items() if isinstance(v, str)}

    def _save_manifest(self, recorded: Mapping[str, str]) -> None:
        self._files.write_text(
            _manifest_path(), json.dumps(dict(sorted(recorded.items())), indent=2) + "\n"
        )

    # -- internals --------------------------------------------------------

    def _status_of(self, name: str, shipped: str) -> SkillStatus:
        shipped_version = _metadata(shipped, _VERSION_KEY) or ""
        text = self._files.read_text(_path(name))
        if text is None:
            return SkillStatus(name, SkillState.MISSING, None, shipped_version)

        vault_version = _metadata(text, _VERSION_KEY)
        seeded = _metadata(text, _SEED_KEY)
        if seeded is None:
            return SkillStatus(name, SkillState.UNKNOWN_PROVENANCE, vault_version, shipped_version)
        if skill_body_hash(_body(text)) != seeded:
            return SkillStatus(name, SkillState.EDITED, vault_version, shipped_version)
        # The vault copy is untouched, so the only question left is whether the
        # *product* moved. Ask the seed hash, not the version string: the seed
        # records the body this copy was written from, so a shipped body that
        # no longer hashes to it is a change this vault has never seen.
        #
        # Comparing versions alone would miss a corrected Skill shipped without a
        # version bump, which would then report CURRENT permanently. Relying on
        # an author to bump a version is exactly the assumption the seed hash
        # exists to avoid. The version is kept as a second reason so a deliberate bump still
        # refreshes a Skill whose body did not change.
        moved_on = skill_body_hash(_body(shipped)) != seeded
        state = (
            SkillState.OUTDATED
            if moved_on or vault_version != shipped_version
            else SkillState.CURRENT
        )
        return SkillStatus(name, state, vault_version, shipped_version, body_moved_on=moved_on)

    @staticmethod
    def _would_change(status: SkillStatus, *, force: bool) -> bool:
        if status.state in (SkillState.OUTDATED, SkillState.MISSING):
            return True
        return force and status.needs_a_person

    @staticmethod
    def _reason(status: SkillStatus) -> str:
        match status.state:
            case SkillState.MISSING:
                return f"not in the vault; ships at {status.shipped_version}"
            case SkillState.OUTDATED if status.body_moved_on:
                version = (
                    f"{status.vault_version} -> {status.shipped_version}"
                    if status.vault_version != status.shipped_version
                    else f"still {status.shipped_version}"
                )
                return f"the shipped text changed ({version})"
            case SkillState.OUTDATED:
                return f"{status.vault_version} -> {status.shipped_version}"
            case SkillState.EDITED:
                return "edited in the vault; replaced because --force was given"
            case _:
                return "provenance unknown; replaced because --force was given"

    def _write(self, name: str) -> None:
        path = _path(name)
        self._files.ensure_directory(VaultPath(path.segments[:-1]))
        self._files.write_text(path, SKILLS[name])


def _path(name: str) -> VaultPath:
    return VaultPath.parse(f"{SKILLS_DIRECTORY}/{name}/SKILL.md")


def _metadata(text: str, key: str) -> str | None:
    """One `metadata:` value, read without a YAML codec.

    A Skill is a foreign-format island (core/04 section 20) and the services
    layer owns no YAML parser -- `ruamel.yaml` is confined to
    `adapters/filesystem/`. These four fields are written by
    `scaffold._skill` as quoted scalars on their own line, so reading them back
    is a line scan and not a parse. Anything more structured than that is a sign
    the file stopped being one Never4gA wrote.
    """
    for line in _frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            _, _, value = stripped.partition(":")
            return value.strip().strip('"')
    return None


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 3)
    return text[4:end] if end != -1 else ""


def _body(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 3)
    return text if end == -1 else text[end + len("\n---\n") :]


def _template_path(name: str) -> VaultPath:
    return VaultPath.parse(f"{TEMPLATES_DIRECTORY}/{name}")


def _manifest_path() -> VaultPath:
    return VaultPath.parse(f"{TEMPLATES_DIRECTORY}/{SEED_MANIFEST}")


def template_seed_manifest() -> str:
    """The manifest a fresh vault is seeded with, for `init` to write."""
    recorded = {name: skill_body_hash(text) for name, text in sorted(SHIPPED_TEMPLATES.items())}
    return json.dumps(recorded, indent=2) + "\n"
