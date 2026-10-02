"""The generated pointer written into a mapped repository.

`details/agent-instruction-layering.md` section 3 puts the pointer there, and
section 6 lists the conditions on writing into a repository. Every one of them
is a test here, because the spec calls them "the part most likely to be
loosened by accident".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath

import pytest

from never4ga.adapters.fakes import InMemoryExtensionRegistry
from never4ga.adapters.fakes.repository_locator import FakeRepositoryLocator
from never4ga.client_descriptors import shipped_descriptors
from never4ga.domain.clients import ClientDescriptor
from never4ga.domain.document import VaultPath
from never4ga.domain.extensions import DeploymentMode, ExtensionClass, ExtensionRecord
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.services.repository_pointers import (
    AGENT_FILENAMES,
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    POINTER_MARKER,
    PointerAction,
    RepositoryPointerSync,
    content_hash,
    with_gitignore_block,
)
from never4ga.services.startup_command import DOCUMENTED_STARTUP_COMMAND

REPO = PurePath("/src/thing")
OTHER = PurePath("/src/other")
WORKSPACE = ConceptId.new()


def a_mapping(
    *,
    root: PurePath | None = REPO,
    ships_agent_contract: bool = False,
) -> WorkspaceMapping:
    return WorkspaceMapping(
        workspace_id=WORKSPACE,
        workspace_path=VaultPath.parse("10_Workspaces/Thing/workspace.md"),
        repository_root=root,
        ships_agent_contract=ships_agent_contract,
    )


def a_client(client_id: str, repository_rules_path: str | None) -> ClientDescriptor:
    return ClientDescriptor(
        client_id=client_id,
        title=client_id,
        global_skills_path="~/x/skills",
        repository_rules_path=repository_rules_path,
    )


CLIENTS = (a_client("claude_code", "CLAUDE.md"), a_client("codex_cli", None))


@pytest.fixture
def locator() -> FakeRepositoryLocator:
    found = FakeRepositoryLocator(roots=[REPO, OTHER])
    found.put_file(REPO, "README.md", "# Thing\n")
    return found


def sync(
    locator: FakeRepositoryLocator,
    *mappings: WorkspaceMapping,
    clients: tuple[ClientDescriptor, ...] = CLIENTS,
) -> RepositoryPointerSync:
    return RepositoryPointerSync(locator, mappings or (a_mapping(),), clients)


def written(locator: FakeRepositoryLocator, relative: str, root: PurePath = REPO) -> str | None:
    return locator.read_text(root, relative)


def body(locator: FakeRepositoryLocator, relative: str, root: PurePath = REPO) -> str:
    """As :func:`written`, where the test has already asserted it is there."""
    found = locator.read_text(root, relative)
    assert found is not None, f"{relative} was not written"
    return found


class TestWhatGetsWritten:
    def test_the_canonical_pointer_is_agents_md(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert written(locator, "AGENTS.md", REPO) is not None

    def test_it_says_it_was_generated(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert body(locator, "AGENTS.md").startswith(POINTER_MARKER)

    def test_it_names_the_workspace(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert str(WORKSPACE) in body(locator, "AGENTS.md")

    def test_it_names_the_startup_command(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert DOCUMENTED_STARTUP_COMMAND in body(locator, "AGENTS.md")

    def test_it_lists_the_repositorys_own_documentation(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # `details/agent-instruction-layering.md` section 3: naming the
        # repository's own documentation is what makes `core/04` section 11's
        # guidance report reach a session that never calls startup.
        locator.put_file(REPO, "CONTRIBUTING.md", "# How\n")
        s = sync(locator)
        s.apply(s.plan())
        found = body(locator, "AGENTS.md")
        assert "[README.md](README.md)" in found
        assert "[CONTRIBUTING.md](CONTRIBUTING.md)" in found

    def test_documentation_that_is_not_there_is_not_named(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # A pointer that names a path that does not exist sends a reader
        # nowhere. The pointer must not manufacture one.
        s = sync(locator)
        s.apply(s.plan())
        assert "CONTRIBUTING.md" not in body(locator, "AGENTS.md")

    def test_a_client_specific_name_is_occupied(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 5.1: occupying the
        # name is what stops `claude /init` writing here.
        s = sync(locator)
        s.apply(s.plan())
        assert written(locator, "CLAUDE.md") is not None

    def test_the_subordinate_file_carries_no_instruction_of_its_own(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # Two instruction files with content can contradict each other.
        s = sync(locator)
        s.apply(s.plan())
        found = body(locator, "CLAUDE.md")
        assert "AGENTS.md" in found
        assert DOCUMENTED_STARTUP_COMMAND not in found

    def test_a_client_that_reads_agents_md_natively_gets_no_file(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # Codex. The file Never4gA does not write is the one that cannot be wrong.
        s = sync(locator, clients=(a_client("codex_cli", None),))
        s.apply(s.plan())
        assert written(locator, "CLAUDE.md") is None
        assert written(locator, "AGENTS.md") is not None


class TestTheGitignoreBlock:
    def test_the_agent_filenames_are_ignored(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        found = body(locator, ".gitignore")
        for name in AGENT_FILENAMES:
            assert name in found

    def test_an_unrelated_line_survives(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 6: an existing file
        # receives a delimited managed block, never a wholesale overwrite.
        locator.put_file(REPO, ".gitignore", ".venv/\n__pycache__/\n")
        s = sync(locator)
        s.apply(s.plan())
        found = body(locator, ".gitignore")
        assert ".venv/" in found
        assert "__pycache__/" in found

    def test_the_block_is_replaced_rather_than_repeated(
        self, locator: FakeRepositoryLocator
    ) -> None:
        s = sync(locator)
        s.apply(s.plan())
        s2 = sync(locator)
        s2.apply(s2.plan())
        assert body(locator, ".gitignore").count(GITIGNORE_BEGIN) == 1
        assert body(locator, ".gitignore").count(GITIGNORE_END) == 1

    def test_a_repository_with_no_gitignore_gets_one(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert body(locator, ".gitignore").startswith(GITIGNORE_BEGIN)

    def test_its_comments_say_nothing_about_where_knowledge_is_kept(self) -> None:
        # The block lands in a tracked file, so it is published by every mapped
        # repository, public ones included (`details/agent-instruction-layering.md`,
        # "Nothing published"). It may say what the ignored files are; where
        # the knowledge behind them lives is not the repository's to say.
        comments = [
            line
            for line in with_gitignore_block(None).splitlines()
            if line.startswith("#") and line not in (GITIGNORE_BEGIN, GITIGNORE_END)
        ]
        assert comments
        for line in comments:
            assert "vault" not in line.lower()
            assert "context pack" not in line.lower()

    def test_a_block_that_differs_only_in_wording_is_left_alone(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # A tracked .gitignore rewritten for its comments alone leaves every
        # mapped repository with an uncommitted change.
        old = "\n".join([GITIGNORE_BEGIN, "# older wording", *AGENT_FILENAMES, GITIGNORE_END])
        locator.put_file(REPO, ".gitignore", f".venv/\n\n{old}\n")
        (plan,) = [p for p in sync(locator).plan() if p.relative == ".gitignore"]
        assert plan.action is PointerAction.NOOP

    def test_a_block_whose_files_changed_is_rewritten(self, locator: FakeRepositoryLocator) -> None:
        short = "\n".join([GITIGNORE_BEGIN, *AGENT_FILENAMES[:-1], GITIGNORE_END])
        locator.put_file(REPO, ".gitignore", f"{short}\n")
        (plan,) = [p for p in sync(locator).plan() if p.relative == ".gitignore"]
        assert plan.action is PointerAction.WRITE

    def test_a_block_without_its_end_marker_is_rewritten(
        self, locator: FakeRepositoryLocator
    ) -> None:
        locator.put_file(REPO, ".gitignore", f"{GITIGNORE_BEGIN}\n{AGENT_FILENAMES[0]}\n")
        (plan,) = [p for p in sync(locator).plan() if p.relative == ".gitignore"]
        assert plan.action is PointerAction.WRITE

    def test_replacing_an_old_block_keeps_the_rest(self) -> None:
        existing = f"a\n{GITIGNORE_BEGIN}\nstale\n{GITIGNORE_END}\nb\n"
        result = with_gitignore_block(existing)
        assert "a" in result
        assert "b" in result
        assert "stale" not in result


class TestWhatIsRefused:
    def test_an_excepted_repository_is_not_written_to(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 8: a repository whose
        # agent contract is part of what it ships tracks its own AGENTS.md.
        s = sync(locator, a_mapping(ships_agent_contract=True))
        performed = s.apply(s.plan())
        assert performed == ()
        assert written(locator, "AGENTS.md") is None

    def test_and_it_is_reported_rather_than_silently_skipped(
        self, locator: FakeRepositoryLocator
    ) -> None:
        (plan,) = sync(locator, a_mapping(ships_agent_contract=True)).plan()
        assert plan.action is PointerAction.SKIP_EXCEPTED
        assert "ships" in plan.reason

    def test_a_workspace_with_no_repository_produces_nothing(
        self, locator: FakeRepositoryLocator
    ) -> None:
        assert sync(locator, a_mapping(root=None)).plan() == ()

    def test_a_mapped_repository_that_is_not_checked_out_is_skipped(self) -> None:
        # A mapping outlives a checkout. Writing would create the repository,
        # which is not what sync means.
        empty = FakeRepositoryLocator(roots=[])
        (plan,) = sync(empty).plan()
        assert plan.action is PointerAction.SKIP_ABSENT

    def test_a_file_never4ga_did_not_write_is_never_overwritten(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # `core/04` sections 25.1 and 26.1. This keeps an authored AGENTS.md
        # safe even if the excepted-repository flag is missing, so the flag is
        # not the only defence.
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n\nDo not clobber.\n")
        s = sync(locator)
        s.apply(s.plan())
        assert written(locator, "AGENTS.md") == "# Hand written\n\nDo not clobber.\n"

    def test_and_that_is_reported(self, locator: FakeRepositoryLocator) -> None:
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        actions = {p.relative: p.action for p in sync(locator).plan()}
        assert actions["AGENTS.md"] is PointerAction.SKIP_UNMANAGED

    def test_an_unmapped_repository_is_untouched(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 6: an unmapped
        # repository is never written to.
        s = sync(locator)
        s.apply(s.plan())
        assert written(locator, "AGENTS.md", OTHER) is None


class TestIdempotence:
    def test_planning_twice_after_applying_writes_nothing(
        self, locator: FakeRepositoryLocator
    ) -> None:
        # `details/api-cli-mcp-contract.md` section 11.
        s = sync(locator)
        s.apply(s.plan())
        assert [p for p in sync(locator).plan() if p.writes] == []

    def test_every_second_plan_is_a_noop(self, locator: FakeRepositoryLocator) -> None:
        s = sync(locator)
        s.apply(s.plan())
        assert {p.action for p in sync(locator).plan()} == {PointerAction.NOOP}

    def test_a_regenerated_pointer_is_byte_identical(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 7 verifies by byte
        # equality, which only works if generation is deterministic.
        s = sync(locator)
        s.apply(s.plan())
        first = written(locator, "AGENTS.md")
        s2 = sync(locator)
        s2.apply(s2.plan())
        assert written(locator, "AGENTS.md") == first

    def test_an_edited_pointer_is_restored(self, locator: FakeRepositoryLocator) -> None:
        # It admits to being generated, so it is Never4gA's to rewrite. What is
        # *not* silently restored is an unmanaged file -- that is the case above.
        s = sync(locator)
        s.apply(s.plan())
        locator.put_file(REPO, "AGENTS.md", f"{POINTER_MARKER}\n\nedited\n")
        s2 = sync(locator)
        s2.apply(s2.plan())
        assert DOCUMENTED_STARTUP_COMMAND in body(locator, "AGENTS.md")


class TestPlanningIsNotWriting:
    def test_planning_alone_writes_nothing(self, locator: FakeRepositoryLocator) -> None:
        # `details/agent-instruction-layering.md` section 6: a diff is shown
        # before anything is written, which needs a plan that is inert.
        sync(locator).plan()
        assert written(locator, "AGENTS.md") is None
        assert written(locator, ".gitignore") is None


class TestTheBundledClients:
    def test_the_shipped_descriptors_name_their_repository_files(self) -> None:
        names = {c.client_id: c.repository_rules_path for c in shipped_descriptors()}
        assert names["claude_code"] == "CLAUDE.md"
        assert names["antigravity_cli"] == "GEMINI.md"
        assert names["codex_cli"] is None

    def test_every_named_file_is_one_the_gitignore_block_covers(self) -> None:
        # Otherwise sync writes a file the same sync leaves tracked, which
        # `details/agent-instruction-layering.md` section 5.3 reports.
        for client in shipped_descriptors():
            if client.repository_rules_path:
                assert client.repository_rules_path in AGENT_FILENAMES


class TestAdoption:
    """`core/09` sections 12-13: discover, register, optionally adopt."""

    @pytest.fixture
    def registry(self) -> InMemoryExtensionRegistry:
        return InMemoryExtensionRegistry()

    def _adopting(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry, **kw: object
    ) -> RepositoryPointerSync:
        return RepositoryPointerSync(
            locator,
            (a_mapping(**kw),),  # type: ignore[arg-type]
            CLIENTS,
            adopt=True,
            registry=registry,
            now=lambda: datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
        )

    def test_adopting_replaces_a_file_never4ga_did_not_write(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        s = self._adopting(locator, registry)
        s.apply(s.plan())
        assert DOCUMENTED_STARTUP_COMMAND in body(locator, "AGENTS.md")

    def test_it_says_that_is_what_it_is_doing(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        # The diff is shown first (`details/agent-instruction-layering.md`
        # section 6), and a plan that silently replaced somebody's file would
        # satisfy the letter and not the point.
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        plans = {p.relative: p for p in self._adopting(locator, registry).plan()}
        assert plans["AGENTS.md"].reason == "adopting a file Never4gA did not write"
        assert plans["AGENTS.md"].adopts

    def test_it_is_not_the_default(self, locator: FakeRepositoryLocator) -> None:
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        s = RepositoryPointerSync(locator, (a_mapping(),), CLIENTS)
        s.apply(s.plan())
        assert body(locator, "AGENTS.md") == "# Hand written\n"

    def test_adoption_does_not_reach_an_excepted_repository(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        # The flag says "replace an unmanaged file with a pointer", not "ignore
        # the excepted class". An authored contract must survive --adopt.
        locator.put_file(REPO, "AGENTS.md", "# The implementation contract\n")
        s = self._adopting(locator, registry, ships_agent_contract=True)
        s.apply(s.plan())
        assert body(locator, "AGENTS.md") == "# The implementation contract\n"


class TestAnAdoptionIsRecorded:
    """`core/09` section 13 and `details/agent-instruction-layering.md` section
    5.2: an adoption records origin, review date and hash. Without that record
    an adopted file would be indistinguishable from one that was always
    generated."""

    STAMP = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)

    @pytest.fixture
    def registry(self) -> InMemoryExtensionRegistry:
        return InMemoryExtensionRegistry()

    @pytest.fixture
    def adopted(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> ExtensionRecord:
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        s = RepositoryPointerSync(
            locator, (a_mapping(),), CLIENTS, adopt=True, registry=registry, now=lambda: self.STAMP
        )
        s.apply(s.plan())
        record = registry.get(f"pointer:{REPO}/AGENTS.md")
        assert record is not None
        return record

    def test_it_is_an_adopted_rule(self, adopted: ExtensionRecord) -> None:
        assert adopted.extension_class is ExtensionClass.RULE
        assert adopted.deployment_mode is DeploymentMode.ADOPTED

    def test_it_names_where_it_came_from(self, adopted: ExtensionRecord) -> None:
        assert adopted.origin == f"{REPO}/AGENTS.md"

    def test_it_carries_the_review_date(self, adopted: ExtensionRecord) -> None:
        assert adopted.adopted_at == "2026-09-11T20:00:00Z"

    def test_it_hashes_what_was_replaced(
        self, adopted: ExtensionRecord, locator: FakeRepositoryLocator
    ) -> None:
        # The old file is gone; its hash is what lets the adoption be checked
        # against the repository's own history later.
        assert adopted.replaced_hash == content_hash("# Hand written\n")
        assert adopted.ownership is not None
        assert adopted.ownership.content_hash == content_hash(body(locator, "AGENTS.md"))
        assert adopted.replaced_hash != adopted.ownership.content_hash

    def test_the_canonical_file_is_read_by_every_client(self, adopted: ExtensionRecord) -> None:
        assert set(adopted.clients) == {"claude_code", "codex_cli"}

    def test_a_client_file_is_read_by_that_client(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        locator.put_file(REPO, "CLAUDE.md", "# Claude's own\n")
        s = RepositoryPointerSync(
            locator, (a_mapping(),), CLIENTS, adopt=True, registry=registry, now=lambda: self.STAMP
        )
        s.apply(s.plan())
        record = registry.get(f"pointer:{REPO}/CLAUDE.md")
        assert record is not None
        assert set(record.clients) == {"claude_code"}

    def test_a_plain_write_records_nothing(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        # Nothing was there, so nothing was adopted: a registry entry here
        # would claim a review that never happened.
        s = RepositoryPointerSync(locator, (a_mapping(),), CLIENTS, adopt=True, registry=registry)
        s.apply(s.plan())
        assert registry.list_records() == ()

    def test_planning_records_nothing(
        self, locator: FakeRepositoryLocator, registry: InMemoryExtensionRegistry
    ) -> None:
        locator.put_file(REPO, "AGENTS.md", "# Hand written\n")
        RepositoryPointerSync(
            locator, (a_mapping(),), CLIENTS, adopt=True, registry=registry
        ).plan()
        assert registry.list_records() == ()

    def test_adopting_with_nowhere_to_record_is_refused(
        self, locator: FakeRepositoryLocator
    ) -> None:
        with pytest.raises(ValueError, match="registry"):
            RepositoryPointerSync(locator, (a_mapping(),), CLIENTS, adopt=True)
