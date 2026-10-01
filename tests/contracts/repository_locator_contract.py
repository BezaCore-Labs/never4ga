"""RepositoryLocator contract.

Two mechanical questions about a directory on this machine: which repository
contains it, and whether that repository names a workspace.

The marker is `.never4ga.toml` holding a workspace UUID and nothing else. A
UUID rather than a name because identity is a UUIDv7 and a name breaks on
rename (core/02 section 5.1).

**This port is the mechanism, never the policy.** The conditions in
details/security-configuration.md section 7.1 -- explicit command, mapped
repository only, a diff first -- are enforced by the service above. A locator
that refused to write would make those conditions untestable at the level that
owns them.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from never4ga.domain.identity import ConceptId
from never4ga.errors import RepositoryMarkerError
from never4ga.ports.repository_locator import MARKER_FILENAME, RepositoryLocator

WORKSPACE = ConceptId.new()


class RepositoryLocatorContract:
    @pytest.fixture
    def locator(self) -> RepositoryLocator:
        raise NotImplementedError("supply a RepositoryLocator fixture")

    @pytest.fixture
    def repository(self) -> PurePath:
        raise NotImplementedError("supply a path that is a repository root")

    @pytest.fixture
    def outside(self) -> PurePath:
        raise NotImplementedError("supply a path inside no repository")

    @pytest.fixture
    def hidden(self) -> PurePath:
        raise NotImplementedError("supply a path this locator cannot see")

    def put_raw_marker(self, repository: PurePath, text: str) -> None:
        """Place marker content the port itself would never write.

        A test affordance rather than part of the interface: reading damage is
        behaviour worth pinning, and writing damage is not something a
        RepositoryLocator should be able to do.
        """
        raise NotImplementedError("supply a way to write raw marker content")

    def put_file(self, repository: PurePath, relative: str, text: str) -> None:
        """Place an ordinary file inside the repository.

        A second affordance, for reading files inside a repository. The port
        reads files it never writes, so the suite needs a way to put one there
        that does not go through the port.
        """
        raise NotImplementedError("supply a way to place a file in a repository")

    def test_a_repository_root_locates_itself(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        assert locator.repository_root(repository) == repository

    def test_a_nested_path_locates_the_repository_above_it(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        assert locator.repository_root(repository / "src" / "never4ga") == repository

    def test_a_path_in_no_repository_locates_nothing(
        self, locator: RepositoryLocator, outside: PurePath
    ) -> None:
        # Never guess: an unrelated directory must not resolve to a workspace.
        assert locator.repository_root(outside) is None

    def test_a_repository_is_visible(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        assert locator.visible(repository)

    def test_a_directory_in_no_repository_is_still_visible(
        self, locator: RepositoryLocator, outside: PurePath
    ) -> None:
        # Being in no repository and not being there at all are different
        # answers, and only the second one means the path cannot be resolved.
        assert locator.visible(outside)

    def test_a_path_that_is_not_there_is_not_visible(
        self, locator: RepositoryLocator, hidden: PurePath
    ) -> None:
        # A sandboxed service may not see a path its caller can, and reporting
        # it as unmapped would send the reader after the wrong problem.
        assert not locator.visible(hidden)

    def test_an_ordinary_repository_is_no_other_repository_s_worktree(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # Only a linked worktree names a main one. A clone answers for
        # itself, so resolution never has a second place to look.
        assert locator.main_worktree(repository) is None

    def test_an_unmarked_repository_reads_no_marker(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        assert locator.read_marker(repository) is None

    def test_a_written_marker_reads_back(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        locator.write_marker(repository, WORKSPACE)
        assert locator.read_marker(repository) == WORKSPACE

    def test_writing_reports_where_it_wrote(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        written = locator.write_marker(repository, WORKSPACE)
        assert written == repository / MARKER_FILENAME

    def test_a_marker_is_replaced_rather_than_appended_to(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        other = ConceptId.new()
        locator.write_marker(repository, WORKSPACE)
        locator.write_marker(repository, other)
        assert locator.read_marker(repository) == other

    def test_a_marker_naming_no_workspace_is_refused(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # Refused rather than ignored: a marker that exists and says nothing
        # usable is a mistake worth surfacing, not a silent absence.
        self.put_raw_marker(repository, "workspace = 'not-a-uuid'")
        with pytest.raises(RepositoryMarkerError):
            locator.read_marker(repository)

    def test_a_marker_that_is_not_parseable_is_refused(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        self.put_raw_marker(repository, "this is not toml = = =")
        with pytest.raises(RepositoryMarkerError):
            locator.read_marker(repository)

    # -- reading inside a repository -----------------------------------------

    def test_a_file_that_is_there_is_reported(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        self.put_file(repository, "AGENTS.md", "# Contract\n")
        assert locator.exists(repository, "AGENTS.md")

    def test_a_file_that_is_not_there_is_not(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        assert not locator.exists(repository, "NOTHING.md")

    def test_a_nested_file_is_reported(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        self.put_file(repository, "bootstrap/skills/tdd/SKILL.md", "# TDD\n")
        assert locator.exists(repository, "bootstrap/skills/tdd/SKILL.md")

    def test_a_directory_counts_as_present(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # An agent file may name a directory. Its presence is the question.
        self.put_file(repository, "bootstrap/skills/tdd/SKILL.md", "# TDD\n")
        assert locator.exists(repository, "bootstrap/skills/tdd")

    def test_contents_come_back_unchanged(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        self.put_file(repository, "AGENTS.md", "# Contract\n\nRead `core/05`.\n")
        assert locator.read_text(repository, "AGENTS.md") == "# Contract\n\nRead `core/05`.\n"

    def test_reading_something_absent_is_none_rather_than_an_error(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # `doctor` reports rather than repairs, and one unreadable file must not
        # end the diagnosis of everything else.
        assert locator.read_text(repository, "NOTHING.md") is None

    def test_written_text_reads_back(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # Writing a generated pointer into a mapped repository is the normal
        # path (details/agent-instruction-layering.md section 6). The port
        # carries the mechanism; details/security-configuration.md section
        # 7.1's conditions are the calling service's.
        locator.write_text(repository, "AGENTS.md", "# Pointer\n")
        assert locator.read_text(repository, "AGENTS.md") == "# Pointer\n"

    def test_writing_replaces_what_was_there(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        locator.write_text(repository, "AGENTS.md", "first\n")
        locator.write_text(repository, "AGENTS.md", "second\n")
        assert locator.read_text(repository, "AGENTS.md") == "second\n"

    def test_writing_the_same_content_twice_is_a_no_op(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # Idempotence is what lets `adapters sync` run repeatedly, which
        # `details/api-cli-mcp-contract.md` section 11 requires of it.
        locator.write_text(repository, "AGENTS.md", "same\n")
        locator.write_text(repository, "AGENTS.md", "same\n")
        assert locator.read_text(repository, "AGENTS.md") == "same\n"

    def test_a_nested_file_can_be_written(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        # `.claude/CLAUDE.md` and the like: the directory may not exist yet.
        locator.write_text(repository, ".never4ga/pointer.md", "nested\n")
        assert locator.read_text(repository, ".never4ga/pointer.md") == "nested\n"

    def test_a_written_file_is_reported_as_existing(
        self, locator: RepositoryLocator, repository: PurePath
    ) -> None:
        locator.write_text(repository, "AGENTS.md", "# Pointer\n")
        assert locator.exists(repository, "AGENTS.md")
