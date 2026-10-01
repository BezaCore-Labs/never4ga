"""Reading client descriptors.

A third leaf beside :mod:`never4ga.platform_paths` and :mod:`never4ga.config`,
and it is a leaf for the same reason they are: only the composition root reads
it, and the sync engine takes descriptors as arguments so it stays a function of
what it was given.

Descriptors come from two places and the order matters:

    shipped defaults      `never4ga/clients/*.toml`, measured clients
    the user's own        `50_System/Integrations/*.toml` in the vault

A vault descriptor with a `client_id` that is already shipped *replaces* it.
That is `core/04` section 9's promise: when a provider moves its discovery
path, the fix is an edit rather than a release.

Every field is checked and an unregistered one is an error rather than an
ignored line: a typo in a descriptor is a path that silently does nothing, which
is the exact failure the descriptor model exists to prevent.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Final

from never4ga.domain.clients import ClientDescriptor, DeploymentMechanism
from never4ga.errors import Never4gaError

__all__ = [
    "SHIPPED_DIRECTORY",
    "DescriptorError",
    "load_descriptors",
    "parse_descriptor",
    "shipped_descriptors",
]

#: The descriptors that travel with the package. Data, not a Python constant.
SHIPPED_DIRECTORY: Final = Path(__file__).parent / "clients"

_REQUIRED: Final = ("client_id", "title", "global_skills_path")

_OPTIONAL: Final = (
    "workspace_skills_path",
    "global_agents_path",
    "skill_format",
    "deployment",
    "detect_paths",
    "detect_command",
    "global_rules_path",
    "repository_rules_path",
    "mcp_add_arguments",
    "mcp_list_arguments",
    "mcp_remove_arguments",
    "measured_on",
    "measured_version",
    "notes",
)


class DescriptorError(Never4gaError):
    """A client descriptor could not be read as written."""


def parse_descriptor(data: dict[str, Any], source: str) -> ClientDescriptor:
    """One descriptor from already-parsed TOML."""
    missing = [name for name in _REQUIRED if not data.get(name)]
    if missing:
        raise DescriptorError(f"{source} is missing {', '.join(missing)}")

    unknown = sorted(set(data) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        raise DescriptorError(
            f"{source} declares {', '.join(unknown)}, which no descriptor field is called"
        )

    raw_deployment = data.get("deployment", DeploymentMechanism.COPY.value)
    try:
        deployment = DeploymentMechanism(raw_deployment)
    except ValueError as error:
        allowed = ", ".join(mechanism.value for mechanism in DeploymentMechanism)
        raise DescriptorError(
            f"{source} asks for deployment {raw_deployment!r}; Never4gA can {allowed}"
        ) from error

    return ClientDescriptor(
        client_id=str(data["client_id"]),
        title=str(data["title"]),
        global_skills_path=str(data["global_skills_path"]),
        workspace_skills_path=_text(data.get("workspace_skills_path")),
        global_agents_path=_text(data.get("global_agents_path")),
        skill_format=str(data.get("skill_format", "agent_skills")),
        deployment=deployment,
        detect_paths=tuple(str(item) for item in data.get("detect_paths", ())),
        detect_command=_text(data.get("detect_command")),
        mcp_add_arguments=_arguments(data.get("mcp_add_arguments")),
        mcp_list_arguments=_arguments(data.get("mcp_list_arguments")),
        mcp_remove_arguments=_arguments(data.get("mcp_remove_arguments")),
        global_rules_path=_text(data.get("global_rules_path")),
        repository_rules_path=_text(data.get("repository_rules_path")),
        measured_on=str(data.get("measured_on", "")),
        measured_version=str(data.get("measured_version", "")),
        notes=str(data.get("notes", "")),
    )


def shipped_descriptors() -> tuple[ClientDescriptor, ...]:
    """The three clients that were measured, in a stable order."""
    return _read(SHIPPED_DIRECTORY)


def load_descriptors(user_directory: Path | None = None) -> tuple[ClientDescriptor, ...]:
    """The shipped descriptors, with the user's own added or replacing them."""
    by_id = {descriptor.client_id: descriptor for descriptor in shipped_descriptors()}
    if user_directory is not None:
        for descriptor in _read(user_directory):
            by_id[descriptor.client_id] = descriptor
    return tuple(by_id[name] for name in sorted(by_id))


def _read(directory: Path) -> tuple[ClientDescriptor, ...]:
    if not directory.is_dir():
        # A vault with no Integrations/ is not an error; it is a vault that has
        # not been asked to describe a client yet.
        return ()
    return tuple(_read_one(path) for path in sorted(directory.glob("*.toml")))


def _read_one(path: Path) -> ClientDescriptor:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as error:
        raise DescriptorError(f"{path.name} could not be read: {error}") from error
    return parse_descriptor(data, path.name)


def _arguments(value: object) -> tuple[str, ...]:
    """A descriptor's argument list, or nothing if it declares none."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _text(value: object) -> str | None:
    return None if value is None else str(value)
