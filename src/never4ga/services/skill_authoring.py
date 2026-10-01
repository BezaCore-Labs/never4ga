"""`skills new`: a Skill we author, started from the template.

The shape of a Skill we write should not depend on who is writing it. A Skill
authored freehand into the vault has no provenance record, and `doctor` reports
that as permanent drift. Here the record is written in the same act as the
Skill, so there is no moment at which one exists without the other.

The constraints, which are the point:

- the template governs Skills *we* author. It is a generator, not a gate;
- a third-party or vendored Skill is never reformatted to fit it. It arrives
  in whatever shape its author chose and keeps it;
- no validation may fail because a Skill does not match the template.
  Nothing in `doctor`, `validate` or the gate treats conformance as a
  requirement.

So this module writes and never checks. The template is read from the vault,
not from the product, so an edit to `50_System/Templates/skill.md` is honoured
the way an edited concept template is (core/04 section 20); the shipped text
is only the fallback for a vault that does not have it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.vendoring import Origin, OriginKind
from never4ga.errors import Never4gaError
from never4ga.ports.vault_files import VaultFileStore
from never4ga.services.adapters import SKILLS_DIRECTORY
from never4ga.services.authoring import Clock, format_timestamp, utc_now
from never4ga.services.scaffold import (
    SKILL_TEMPLATE,
    SKILL_TEMPLATE_FILENAME,
    SKILLS,
    authored_skill,
)
from never4ga.services.skill_library import TEMPLATES_DIRECTORY
from never4ga.services.vendoring import VendoringService

__all__ = ["Authored", "SkillAuthor", "SkillAuthoringError"]

#: The Agent Skills contract for a name: lowercase, digits and hyphens, no
#: hyphen at either end, at most 64 characters. A Skill's directory is its
#: name, so this is also what keeps the path portable.
_NAME: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_LIMIT: Final = 64


class SkillAuthoringError(Never4gaError):
    """A Skill could not be started: the name, or something already there."""


@dataclass(frozen=True, slots=True)
class Authored:
    """What `skills new` wrote: the Skill, and the record of where it came from."""

    name: str
    path: VaultPath
    provenance: VaultPath


class SkillAuthor:
    """Starts a Skill of ours from the vault's template, with its provenance."""

    def __init__(
        self,
        files: VaultFileStore,
        vendoring: VendoringService,
        *,
        actor: str,
        now: Clock = utc_now,
    ) -> None:
        self._files = files
        self._vendoring = vendoring
        self._actor = actor
        self._now = now

    def author(self, name: str, description: str) -> Authored:
        if not _NAME.match(name) or len(name) > _NAME_LIMIT:
            raise SkillAuthoringError(
                f"{name!r} is not a Skill name: lowercase letters, digits and single "
                f"hyphens, at most {_NAME_LIMIT} characters, as the Agent Skills format asks"
            )
        if name in SKILLS:
            raise SkillAuthoringError(
                f"{name} is one of the Skills Never4gA ships; it is refreshed, not authored"
            )
        if not description.strip():
            raise SkillAuthoringError("a Skill needs a description; it is what a client lists")
        subject = f"{SKILLS_DIRECTORY}/{name}"
        directory = VaultPath.parse(subject)
        if self._files.exists(directory) or self._files.is_directory(directory):
            raise SkillAuthoringError(
                f"{subject} already exists; a Skill is authored once and then edited in place"
            )

        text = authored_skill(name, description.strip(), template=self._template())
        path = VaultPath.parse(f"{subject}/SKILL.md")
        self._files.ensure_directory(directory)
        self._files.write_text(path, text)

        # The provenance record, in the same act. `authored` rather than
        # `local`: written here is a different fact from copied from somewhere
        # on this machine, and the two share a silence a reader could not
        # tell apart.
        record = self._vendoring.record(
            subject, Origin(kind=OriginKind.AUTHORED, fetched_at=format_timestamp(self._now()))
        )
        identity = ConceptId.new()
        provenance = self._vendoring.write(
            record,
            concept_id=identity,
            base=self._vendoring.base_frontmatter(record, concept_id=identity, actor=self._actor),
        )
        return Authored(name, path, provenance)

    def _template(self) -> str:
        """The vault's copy, and the shipped text only when there is none."""
        text = self._files.read_text(
            VaultPath.parse(f"{TEMPLATES_DIRECTORY}/{SKILL_TEMPLATE_FILENAME}")
        )
        return text if text is not None else SKILL_TEMPLATE
