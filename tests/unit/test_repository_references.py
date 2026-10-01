"""`doctor` reads the mapped repository's agent files.

`AGENTS.md` and `CLAUDE.md` are the documents every session reads first, and
they live in the repository rather than the vault.

**What this may conclude is narrow, and deliberately so.** The agent files name
paths, and some of those paths stop existing. That rule has real yield and
needs no judgement.

**Three roots, because both files legitimately name all three.** A path in an
agent file may resolve inside the repository, inside the workspace, or only at
the vault root. A rule with fewer roots reports correct paths as broken.

**Globs are excluded.** `Logs/*.md` and `tests/contracts/*_contract.py` are
patterns rather than paths.

A mapped repository that is not on disk is itself a finding, once, rather than
a finding per line. Otherwise `doctor`'s answer would get quietly smaller on a
machine where the repository is not checked out.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePath

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor
from never4ga.services.repository_pointers import POINTER_MARKER, RepositoryPointerSync

WORKSPACE_ID = "01a03428-7cbf-74eb-9a1a-fc12755a0c56"
#: A mapping names the *manifest*, not the directory holding it. A fixture
#: holding a directory would hide correct paths being reported as broken.
WORKSPACE_PATH = "10_Workspaces/Acme/workspace.md"
REPO = PurePath("/home/user/Projects/acme")

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return NOW


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    workspace = root / "10_Workspaces" / "Acme"
    (workspace / "Context").mkdir(parents=True)
    (workspace / "workspace.md").write_text(
        f"---\ntype: workspace\nid: {WORKSPACE_ID}\nschema: never4ga/0.1\n"
        f'title: Acme\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"lifecycle: active\nworkspace_type: product\n---\n\n# Acme\n"
    )
    (workspace / "Context" / "start-here.md").write_text(
        f"---\ntype: context\nid: 01a04ea0-1111-7000-8000-000000000001\n"
        f'schema: never4ga/0.1\ntitle: Start Here\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"workspace: {WORKSPACE_ID}\n---\n\n# Start Here\n"
    )
    return root


def a_locator(agents: str | None = None, *, files: tuple[str, ...] = ()) -> FakeRepositoryLocator:
    locator = FakeRepositoryLocator(roots=[REPO])
    if agents is not None:
        locator.put_file(REPO, "AGENTS.md", agents)
    for name in files:
        locator.put_file(REPO, name, "content\n")
    return locator


def diagnose(
    vault: Path,
    locator: FakeRepositoryLocator | None,
    *,
    mapped: bool = True,
    ships_agent_contract: bool = False,
) -> dict[str, str]:
    mappings = (
        [
            WorkspaceMapping(
                workspace_id=ConceptId.parse(WORKSPACE_ID),
                workspace_path=VaultPath.parse(WORKSPACE_PATH),
                repository_root=REPO,
                ships_agent_contract=ships_agent_contract,
            )
        ]
        if mapped
        else []
    )
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        now=fixed_clock,
        repositories=locator,
        mappings=mappings,
    ).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


class TestAPathAnAgentFileNamesThatIsGone:
    def test_is_reported(self, vault: Path) -> None:
        locator = a_locator("Read [the guide](docs/gone.md) before starting.\n")
        found = diagnose(vault, locator)
        assert "repository_reference_broken" in found
        assert "docs/gone.md" in found["repository_reference_broken"]

    def test_a_path_inside_the_repository_resolves(self, vault: Path) -> None:
        locator = a_locator(
            "Read `bootstrap/skills/tdd/SKILL.md`.\n",
            files=("bootstrap/skills/tdd/SKILL.md",),
        )
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_path_inside_the_workspace_resolves(self, vault: Path) -> None:
        """`Context/start-here.md` is in the vault, not the repository."""
        locator = a_locator("Read [start here](Context/start-here.md).\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_path_at_the_vault_root_resolves(self, vault: Path) -> None:
        """`50_System/system.md` resolves only at the vault root."""
        locator = a_locator("The vault manifest is [system.md](50_System/system.md).\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_markdown_link_target_is_checked(self, vault: Path) -> None:
        locator = a_locator("See [the plan](docs/gone.md).\n")
        assert "repository_reference_broken" in diagnose(vault, locator)

    def test_a_backticked_path_is_prose_and_is_not_checked(self, vault: Path) -> None:
        # A `[text](path)` is somewhere to go; a backticked path may be prose
        # *about* a path, such as a sentence saying a file does not exist on
        # purpose.
        #
        # Separating a warning from a pointer means reading English, which the
        # deterministic path does not do. Link syntax is the mechanical
        # substitute: an author who wants the path checked links it.
        locator = a_locator("`~/.claude/statusline.sh` is missing after a reinstall.\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_the_cost_of_that_is_a_real_pointer_written_as_a_code_span(self, vault: Path) -> None:
        # Stated as a test because it is the half of the rule that loses
        # something: a code span that is genuinely a pointer, and genuinely
        # broken, is silent. The remedy is to link it.
        locator = a_locator("Read `docs/gone.md` before starting.\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_the_message_names_the_file_it_was_found_in(self, vault: Path) -> None:
        locator = a_locator("Read [the guide](docs/gone.md).\n")
        assert "AGENTS.md" in diagnose(vault, locator)["repository_reference_broken"]

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        locator = a_locator("Read [the guide](docs/gone.md).\n")
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                now=fixed_clock,
                repositories=locator,
                mappings=[
                    WorkspaceMapping(
                        workspace_id=ConceptId.parse(WORKSPACE_ID),
                        workspace_path=VaultPath.parse(WORKSPACE_PATH),
                        repository_root=REPO,
                    )
                ],
            )
            .diagnose()
            .findings
            if f.code == "repository_reference_broken"
        ]
        assert finding.severity.value == "warning"
        assert finding.repair_hint is not None

    def test_claude_md_is_read_as_well(self, vault: Path) -> None:
        locator = FakeRepositoryLocator(roots=[REPO])
        locator.put_file(REPO, "CLAUDE.md", "Read [the guide](docs/gone.md).\n")
        found = diagnose(vault, locator)
        assert "CLAUDE.md" in found["repository_reference_broken"]


class TestWhatIsNotAPath:
    def test_a_glob_is_not_checked(self, vault: Path) -> None:
        """`Logs/*.md` and `tests/contracts/*_contract.py` are both real."""
        locator = a_locator(
            "Records live in [logs](Logs/*.md) and [suites](tests/contracts/*_contract.py).\n"
        )
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_spec_reference_without_an_extension_is_not_checked(self, vault: Path) -> None:
        """`core/05` names a specification, not a file on disk."""
        locator = a_locator("The rule is [core/05](core/05) section 15.\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_bare_filename_is_not_checked(self, vault: Path) -> None:
        """`AGENTS.md` with no directory is a name, not a location."""
        locator = a_locator("This file is [AGENTS.md](AGENTS.md).\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_command_is_not_checked(self, vault: Path) -> None:
        locator = a_locator("Run [the command](never4ga workspace resolve --path foo.md).\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_url_is_not_checked(self, vault: Path) -> None:
        locator = a_locator("See [the forge](https://github.com/acme/acme/blob/main/x.md).\n")
        assert "repository_reference_broken" not in diagnose(vault, locator)


class TestWhenTheRepositoryIsNotThere:
    def test_a_mapped_repository_that_is_absent_is_reported_once(self, vault: Path) -> None:
        """Silence would make `doctor`'s answer quietly machine-dependent."""
        locator = FakeRepositoryLocator(roots=[])
        codes = [
            f.code
            for f in Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                now=fixed_clock,
                repositories=locator,
                mappings=[
                    WorkspaceMapping(
                        workspace_id=ConceptId.parse(WORKSPACE_ID),
                        workspace_path=VaultPath.parse(WORKSPACE_PATH),
                        repository_root=REPO,
                    )
                ],
            )
            .diagnose()
            .findings
        ]
        assert codes.count("repository_absent") == 1
        assert "repository_reference_broken" not in codes

    def test_a_workspace_with_no_mapping_produces_nothing(self, vault: Path) -> None:
        found = diagnose(vault, a_locator("Read [the guide](docs/gone.md).\n"), mapped=False)
        assert "repository_reference_broken" not in found
        assert "repository_absent" not in found

    def test_a_repository_with_no_agent_files_produces_nothing(self, vault: Path) -> None:
        """Not every repository has one, and its absence is not a defect."""
        locator = FakeRepositoryLocator(roots=[REPO])
        locator.put_file(REPO, "README.md", "# Acme\n")
        found = diagnose(vault, locator)
        assert "repository_reference_broken" not in found
        assert "repository_absent" not in found

    def test_doctor_without_a_locator_checks_nothing(self, vault: Path) -> None:
        """The dependency is optional, like the index."""
        assert "repository_reference_broken" not in diagnose(vault, None)


class TestPathsThatAreNotTheRepositorysToResolve:
    """Two shapes that are not the repository's to resolve.

    False findings are costly: a report that is mostly wrong stops anyone
    reading it.

    **A home-relative path is a real location.** Machine-wide agent conventions
    live at `~/.claude/CLAUDE.md`, and an `AGENTS.md` that points there is
    correct to do so. None of the repository, the workspace and the vault root
    is home, so such a path is resolved against home.

    **A placeholder is not a path.** `inventory/host_vars/<hostname>.yml` tells
    a reader where to put a file whose name depends on the host. Angle brackets
    are excluded for the same reason as globs.
    """

    def test_a_home_relative_path_that_exists_resolves(
        self, vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        locator = a_locator("Machine-wide conventions live in [CLAUDE.md](~/.claude/CLAUDE.md).\n")
        # Registered through the locator rather than written to disk, because
        # the answer must come from the port.
        locator.put_file(PurePath(home / ".claude"), "CLAUDE.md", "conventions\n")

        assert "repository_reference_broken" not in diagnose(vault, locator)

    def test_a_home_relative_path_that_is_gone_is_still_reported(
        self, vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Home paths are still checked: one that is genuinely absent is
        # reported.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        locator = a_locator("The status line is [statusline.sh](~/.claude/statusline.sh).\n")

        found = diagnose(vault, locator)
        assert "~/.claude/statusline.sh" in found["repository_reference_broken"]

    def test_a_placeholder_in_a_path_is_not_checked(self, vault: Path) -> None:
        locator = a_locator(
            "Host differences go in [host vars](inventory/host_vars/<hostname>.yml).\n"
        )

        assert "repository_reference_broken" not in diagnose(vault, locator)


class TestThePointerIsWhereItShouldBe:
    """`doctor` verifies the agent pointer itself, not only behind a separate verb."""

    def test_a_mapped_repository_with_no_pointer_is_reported(self, vault: Path) -> None:
        locator = FakeRepositoryLocator(roots=[REPO])
        assert "agent_pointer_missing" in diagnose(vault, locator)

    def test_a_repository_that_ships_its_contract_is_not(self, vault: Path) -> None:
        # `details/agent-instruction-layering.md` section 8: such a repository
        # tracks its own `AGENTS.md`. Reporting a pointer missing would be a
        # warning nobody can clear.
        locator = FakeRepositoryLocator(roots=[REPO])
        found = diagnose(vault, locator, ships_agent_contract=True)
        assert "agent_pointer_missing" not in found
        assert "agent_files_not_ignored" not in found

    def test_a_current_pointer_is_not_reported(self, vault: Path) -> None:
        locator = _synced(vault)
        found = diagnose(vault, locator)
        assert "agent_pointer_missing" not in found
        assert "agent_pointer_modified" not in found
        assert "agent_files_not_ignored" not in found

    def test_an_edited_pointer_is_reported_as_modified(self, vault: Path) -> None:
        # `details/agent-instruction-layering.md` section 7: byte equality
        # against the regenerated form, which is what makes this a comparison
        # rather than a heuristic.
        locator = _synced(vault)
        locator.put_file(REPO, "AGENTS.md", f"{POINTER_MARKER}\n\nedited by hand\n")
        assert "agent_pointer_modified" in diagnose(vault, locator)

    def test_a_file_never4ga_did_not_write_is_reported_differently(self, vault: Path) -> None:
        # A different code, because the remedy is different: one is regenerated
        # and the other needs an explicit adoption.
        locator = _synced(vault)
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        found = diagnose(vault, locator)
        assert "agent_file_unmanaged" in found
        assert "agent_pointer_modified" not in found

    def test_a_repository_that_does_not_ignore_agent_files_is_reported(self, vault: Path) -> None:
        # The mechanical proxy for `details/agent-instruction-layering.md`
        # section 5.3. It cannot see that a file is *tracked*, which needs
        # `git ls-files`, and this locator deliberately runs no subprocess. It
        # does catch every repository that would start tracking one.
        locator = _synced(vault)
        locator.put_file(REPO, ".gitignore", ".venv/\n")
        assert "agent_files_not_ignored" in diagnose(vault, locator)


def _synced(vault: Path) -> FakeRepositoryLocator:
    """A repository as `adapters sync --apply` would leave it."""
    locator = FakeRepositoryLocator(roots=[REPO])
    mapping = WorkspaceMapping(
        workspace_id=ConceptId.parse(WORKSPACE_ID),
        workspace_path=VaultPath.parse(WORKSPACE_PATH),
        repository_root=REPO,
    )
    sync = RepositoryPointerSync(locator, (mapping,), ())
    sync.apply(sync.plan())
    return locator
