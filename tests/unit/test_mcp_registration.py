"""Registering the MCP server with the clients on this machine.

Specification:
- core/09 -- MCP servers may be managed centrally; never overwrite an unmanaged
  one. Calling a client's own verb is the strongest reading of that: Never4gA
  learns no format and opens no file of somebody else's.
- core/04 section 9 -- a client path is descriptor data, never a core constant.
  The same holds for a client *command*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.domain.clients import ClientDescriptor
from never4ga.ports.client_registry import RegistrationOutcome
from never4ga.services.mcp_registration import (
    SERVER_COMMAND,
    SERVER_NAME,
    McpRegistrationService,
)


class RecordingRegistry:
    """A registry that records rather than runs."""

    def __init__(self, *, ok: bool = True) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._ok = ok

    def register(
        self, descriptor: ClientDescriptor, command: tuple[str, ...]
    ) -> RegistrationOutcome:
        self.calls.append((descriptor.client_id, command))
        return RegistrationOutcome(
            descriptor.client_id, command, ok=self._ok, detail="" if self._ok else "refused"
        )


def descriptor(
    client_id: str = "claude_code",
    *,
    marker: str,
    add: tuple[str, ...] = ("mcp", "add", "--scope", "user", "{name}", "{command}"),
) -> ClientDescriptor:
    return ClientDescriptor(
        client_id=client_id,
        title=client_id.replace("_", " ").title(),
        global_skills_path="~/.somewhere/skills",
        detect_paths=(marker,),
        detect_command="a-client",
        mcp_add_arguments=add,
    )


#: Where the executable is, as the CLI resolves it: an absolute path, because
#: a client spawned from a desktop session does not have a shell's PATH.
SERVER = Path("/usr/local/bin/never4ga-mcp")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


class TestPlanning:
    def test_the_command_is_the_descriptor_s_with_the_names_substituted(self, home: Path) -> None:
        (home / ".claude").mkdir()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")], home=home, registry=RecordingRegistry(), server=SERVER
        )
        [plan] = service.plan()
        assert plan.command == (
            "a-client",
            "mcp",
            "add",
            "--scope",
            "user",
            SERVER_NAME,
            str(SERVER),
        )

    def test_a_vault_travels_as_an_argument_a_person_can_read(
        self, home: Path, tmp_path: Path
    ) -> None:
        # Rather than as environment: the command shown in the dry run has to
        # be the command that runs, or the dry run is not one.
        (home / ".claude").mkdir()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")],
            home=home,
            registry=RecordingRegistry(),
            server=SERVER,
            vault=tmp_path / "vault",
        )
        [plan] = service.plan()
        assert plan.command[-2:] == ("--vault", str(tmp_path / "vault"))

    def test_a_client_that_is_not_installed_is_skipped_and_says_so(self, home: Path) -> None:
        service = McpRegistrationService(
            [descriptor(marker="~/.absent")], home=home, registry=RecordingRegistry(), server=SERVER
        )
        [plan] = service.plan()
        assert plan.runnable is False
        assert "not installed" in str(plan.skipped)

    def test_a_client_whose_descriptor_is_silent_is_skipped_and_says_which(
        self, home: Path
    ) -> None:
        # A fact about the client rather than a gap to route around.
        (home / ".claude").mkdir()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude", add=())],
            home=home,
            registry=RecordingRegistry(),
            server=SERVER,
        )
        [plan] = service.plan()
        assert plan.runnable is False
        assert "does not say how" in str(plan.skipped)

    def test_the_three_shapes_measured_on_this_machine_render_differently(self) -> None:
        # The reason this is descriptor data: no two of the three agree.
        from never4ga.client_descriptors import load_descriptors

        shipped = {one.client_id: one for one in load_descriptors(Path("/nonexistent"))}
        claude = shipped["claude_code"].mcp_add_command("n", "c")
        codex = shipped["codex_cli"].mcp_add_command("n", "c")
        agy = shipped["antigravity_cli"].mcp_add_command("n", "c")
        # Two of the three need a `--` before the command, and they need it
        # for the same reason: without it the *server's* flags are parsed as
        # the client's: Claude Code answers `unknown option '--vault'`. Its
        # usage line does not mention the separator; only an example does.
        assert claude == (
            "claude",
            "mcp",
            "add",
            "--scope",
            "user",
            "-t",
            "stdio",
            "n",
            "--",
            "c",
        )
        assert codex == ("codex", "mcp", "add", "n", "--", "c")
        # Antigravity does not, and taking one where none is wanted would be a
        # different bug. No two of the three agree, which is the whole reason
        # this is descriptor data.
        assert agy == ("agy", "mcp", "add", "-t", "stdio", "n", "c")


class TestRegistering:
    def test_nothing_runs_without_apply(self, home: Path) -> None:
        (home / ".claude").mkdir()
        registry = RecordingRegistry()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")], home=home, registry=registry, server=SERVER
        )
        outcome = service.register()
        assert outcome.applied is False
        assert registry.calls == []
        assert "nothing was run" in outcome.render()

    def test_apply_runs_exactly_the_command_that_was_shown(self, home: Path) -> None:
        (home / ".claude").mkdir()
        registry = RecordingRegistry()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")], home=home, registry=registry, server=SERVER
        )
        planned = service.plan()[0].command
        outcome = service.register(apply=True)
        assert outcome.applied is True
        assert registry.calls == [("claude_code", planned)]

    def test_a_skipped_client_is_never_run(self, home: Path) -> None:
        (home / ".claude").mkdir()
        registry = RecordingRegistry()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude"), descriptor("codex_cli", marker="~/.absent")],
            home=home,
            registry=registry,
            server=SERVER,
        )
        service.register(apply=True)
        assert [one for one, _ in registry.calls] == ["claude_code"]

    def test_one_client_refusing_does_not_stop_the_others(self, home: Path) -> None:
        # Registering three clients must not stop at the first unhappy one.
        (home / ".claude").mkdir()
        (home / ".codex").mkdir()
        registry = RecordingRegistry(ok=False)
        service = McpRegistrationService(
            [descriptor(marker="~/.claude"), descriptor("codex_cli", marker="~/.codex")],
            home=home,
            registry=registry,
            server=SERVER,
        )
        outcome = service.register(apply=True)
        assert len(outcome.outcomes) == 2
        assert all(not one.ok for one in outcome.outcomes)
        assert "failed" in outcome.render()


class TestTheAdapter:
    def test_a_command_that_is_not_on_path_is_reported_rather_than_raised(self) -> None:
        from never4ga.adapters.clients import SubprocessClientRegistry

        built = descriptor(marker="~/.claude")
        outcome = SubprocessClientRegistry().register(
            built, ("never4ga-no-such-client-anywhere", "mcp", "add")
        )
        assert outcome.ok is False
        assert "not on PATH" in outcome.detail

    def test_a_command_that_fails_reports_its_last_line(self) -> None:
        from never4ga.adapters.clients import SubprocessClientRegistry

        built = descriptor(marker="~/.claude")
        outcome = SubprocessClientRegistry().register(built, ("false",))
        assert outcome.ok is False

    def test_a_command_that_works_reports_ok(self) -> None:
        from never4ga.adapters.clients import SubprocessClientRegistry

        built = descriptor(marker="~/.claude")
        outcome = SubprocessClientRegistry().register(built, ("true",))
        assert outcome.ok is True


class TestTheExecutable:
    def test_an_absolute_path_is_registered_rather_than_a_bare_name(self, home: Path) -> None:
        # A server registered by name is "not found in $PATH" when
        # `never4ga-mcp` is installed in a virtualenv the client's environment
        # cannot see.
        (home / ".claude").mkdir()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")],
            home=home,
            registry=RecordingRegistry(),
            server=SERVER,
        )
        [plan] = service.plan()
        assert str(SERVER) in plan.command
        assert SERVER_COMMAND not in plan.command

    def test_no_executable_means_no_registration_rather_than_a_broken_one(self, home: Path) -> None:
        # Registering a server a client can never start is worse than
        # registering none: the failure surfaces later, somewhere else.
        (home / ".claude").mkdir()
        service = McpRegistrationService(
            [descriptor(marker="~/.claude")],
            home=home,
            registry=RecordingRegistry(),
            server=None,
        )
        [plan] = service.plan()
        assert plan.runnable is False
        assert "not installed anywhere" in str(plan.skipped)
