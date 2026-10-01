"""An adapter is a descriptor, not a class.

Never4gA has to work with most agent clients without per-client code.
`core/04` section 9 gives the reason: tool filesystem paths "are not part of
Never4gA's permanent core schema -- they are adapter implementation data",
because "provider tools can change their discovery paths".

So a descriptor is data, shipped as TOML and extensible in the vault. A fourth
client is a row and a test. Nothing here imports an engine, because there is no
per-client engine to import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.client_descriptors import (
    SHIPPED_DIRECTORY,
    DescriptorError,
    load_descriptors,
    shipped_descriptors,
)
from never4ga.domain.clients import ClientDescriptor, DeploymentMechanism

MEASURED = "2026-08-24"


def write(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestTheShippedThree:
    """Three descriptors ship, because three were measured."""

    def test_exactly_the_measured_clients_ship(self) -> None:
        # An adapter for a client nobody has measured is a guess wearing data's
        # clothing. Antigravity 2.x and Antigravity IDE get no descriptor.
        assert {d.client_id for d in shipped_descriptors()} == {
            "claude_code",
            "codex_cli",
            "antigravity_cli",
        }

    @pytest.mark.parametrize(
        ("client_id", "skills_path"),
        [
            ("claude_code", "~/.claude/skills"),
            ("codex_cli", "~/.agents/skills"),
            ("antigravity_cli", "~/.gemini/config/skills"),
        ],
    )
    def test_the_measured_skill_path(self, client_id: str, skills_path: str) -> None:
        by_id = {d.client_id: d for d in shipped_descriptors()}
        assert by_id[client_id].global_skills_path == skills_path

    def test_antigravity_does_not_carry_the_path_that_was_measured_wrong(self) -> None:
        # Antigravity CLI discovers nothing at
        # `~/.gemini/antigravity-cli/skills/`, so no descriptor may carry it.
        paths = [d.global_skills_path for d in shipped_descriptors()]
        assert "~/.gemini/antigravity-cli/skills" not in paths

    def test_every_shipped_descriptor_records_when_it_was_measured(self) -> None:
        # Adapter data goes stale. A descriptor that cannot say when it was last
        # checked cannot be revalidated on a schedule.
        assert all(d.measured_on == MEASURED for d in shipped_descriptors())

    def test_deployment_is_copy_everywhere(self) -> None:
        # Symlinks were measured to work on all three and are not the default:
        # a symlinked Skill is a client writing into the Git-backed vault.
        assert all(d.deployment is DeploymentMechanism.COPY for d in shipped_descriptors())

    def test_a_descriptor_is_data_on_disk_not_a_python_constant(self) -> None:
        assert SHIPPED_DIRECTORY.is_dir()
        assert sorted(p.name for p in SHIPPED_DIRECTORY.glob("*.toml")) == [
            "antigravity-cli.toml",
            "claude-code.toml",
            "codex-cli.toml",
        ]


class TestResolvingAgainstAMachine:
    def test_a_home_relative_path_resolves(self, tmp_path: Path) -> None:
        descriptor = ClientDescriptor(
            client_id="probe", title="Probe", global_skills_path="~/.probe/skills"
        )
        assert descriptor.global_skills_directory(tmp_path) == tmp_path / ".probe/skills"

    def test_an_absolute_path_is_left_alone(self, tmp_path: Path) -> None:
        descriptor = ClientDescriptor(
            client_id="probe", title="Probe", global_skills_path="/opt/probe/skills"
        )
        assert descriptor.global_skills_directory(tmp_path) == Path("/opt/probe/skills")

    def test_a_client_is_installed_when_a_signal_is_present(self, tmp_path: Path) -> None:
        descriptor = ClientDescriptor(
            client_id="probe",
            title="Probe",
            global_skills_path="~/.probe/skills",
            detect_paths=("~/.probe",),
        )
        assert not descriptor.is_installed(tmp_path)
        (tmp_path / ".probe").mkdir()
        assert descriptor.is_installed(tmp_path)

    def test_a_descriptor_with_no_signal_is_never_installed(self, tmp_path: Path) -> None:
        # Deploying to a client that is not there would create the directory it
        # was looking for, which is indistinguishable from installing the client.
        descriptor = ClientDescriptor(
            client_id="probe", title="Probe", global_skills_path="~/.probe/skills"
        )
        assert not descriptor.is_installed(tmp_path)


class TestAFourthClientIsARow:
    """A user adds a client by writing a file.

    No Python changes, and nothing in this test constructs anything
    client-specific.
    """

    def test_a_vault_descriptor_joins_the_shipped_ones(self, tmp_path: Path) -> None:
        write(
            tmp_path / "Integrations",
            "cursor",
            'client_id = "cursor"\n'
            'title = "Cursor"\n'
            'global_skills_path = "~/.cursor/skills"\n'
            'detect_paths = ["~/.cursor"]\n'
            'measured_on = "2026-09-01"\n',
        )
        descriptors = load_descriptors(tmp_path / "Integrations")
        assert "cursor" in {d.client_id for d in descriptors}
        assert len(descriptors) == len(shipped_descriptors()) + 1

    def test_a_vault_descriptor_overrides_a_shipped_one(self, tmp_path: Path) -> None:
        # core/04 section 9's promise, kept literally: when a provider moves its
        # discovery path, the fix is an edit rather than a release.
        write(
            tmp_path / "Integrations",
            "claude-code",
            'client_id = "claude_code"\n'
            'title = "Claude Code"\n'
            'global_skills_path = "~/.claude/agent-skills"\n'
            'measured_on = "2026-09-01"\n',
        )
        by_id = {d.client_id: d for d in load_descriptors(tmp_path / "Integrations")}
        assert by_id["claude_code"].global_skills_path == "~/.claude/agent-skills"
        assert len(by_id) == len(shipped_descriptors())

    def test_no_vault_directory_is_just_the_shipped_set(self, tmp_path: Path) -> None:
        assert load_descriptors(tmp_path / "nothing-here") == shipped_descriptors()

    def test_the_deployment_mechanism_is_the_descriptors_to_state(self, tmp_path: Path) -> None:
        # Symlink is measured to work and stays opt-in, per client.
        write(
            tmp_path / "Integrations",
            "cursor",
            'client_id = "cursor"\n'
            'title = "Cursor"\n'
            'global_skills_path = "~/.cursor/skills"\n'
            'deployment = "symlink"\n'
            'measured_on = "2026-09-01"\n',
        )
        by_id = {d.client_id: d for d in load_descriptors(tmp_path / "Integrations")}
        assert by_id["cursor"].deployment is DeploymentMechanism.SYMLINK


class TestAMalformedDescriptorIsRefused:
    def test_a_missing_required_field_names_it(self, tmp_path: Path) -> None:
        write(tmp_path / "Integrations", "broken", 'client_id = "broken"\n')
        with pytest.raises(DescriptorError, match="global_skills_path"):
            load_descriptors(tmp_path / "Integrations")

    def test_an_unknown_deployment_mechanism_is_refused(self, tmp_path: Path) -> None:
        write(
            tmp_path / "Integrations",
            "broken",
            'client_id = "broken"\n'
            'title = "Broken"\n'
            'global_skills_path = "~/.broken/skills"\n'
            'deployment = "telepathy"\n',
        )
        with pytest.raises(DescriptorError, match="telepathy"):
            load_descriptors(tmp_path / "Integrations")

    def test_unparseable_toml_names_the_file(self, tmp_path: Path) -> None:
        write(tmp_path / "Integrations", "broken", "this is not toml = = =\n")
        with pytest.raises(DescriptorError, match=r"broken\.toml"):
            load_descriptors(tmp_path / "Integrations")

    def test_an_unregistered_field_is_refused_rather_than_ignored(self, tmp_path: Path) -> None:
        # A typo in a descriptor would be a path that silently does nothing.
        write(
            tmp_path / "Integrations",
            "broken",
            'client_id = "broken"\n'
            'title = "Broken"\n'
            'global_skills_path = "~/.broken/skills"\n'
            'global_skils_path = "~/.broken/typo"\n',
        )
        with pytest.raises(DescriptorError, match="global_skils_path"):
            load_descriptors(tmp_path / "Integrations")
