"""`never4ga note` -- writing a note without deciding a type or a folder first.

For when you already know what the thing is and want it written without the
type and directory `never4ga concept create` needs. `capture` is the other case,
where classifying now would interrupt; this is not that.

The two forms are the two tiers:

    never4ga note "Hybrid retrieval"           -> 30_Knowledge/Notes/
    never4ga note "Week 3 reading" --for X     -> X's Research/

Durable reusable knowledge goes to the flat root. What you learn *from* a
bounded piece of work is scoped to the work that produced it, and graduates to
the flat root when it turns out to be reusable outside it -- which is the
existing meaning of `30_Knowledge/`, not a new rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.cli import main


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    assert main(["--vault", str(root), "init"]) == 0
    return root


def _run(vault: Path, *arguments: str) -> int:
    return main(["--vault", str(vault), "--local", *arguments])


class TestTheFlatDefault:
    def test_a_note_lands_in_the_knowledge_root(self, vault: Path) -> None:
        assert _run(vault, "note", "Hybrid Retrieval") == 0
        assert (vault / "30_Knowledge" / "Notes" / "hybrid-retrieval.md").is_file()

    def test_it_needs_neither_a_type_nor_a_folder(self, vault: Path) -> None:
        # The whole point of the verb. If this ever needs another argument to
        # write an ordinary note, the verb has stopped doing its job.
        assert _run(vault, "note", "Something Worth Keeping") == 0

    def test_it_is_a_knowledge_concept(self, vault: Path) -> None:
        _run(vault, "note", "Hybrid Retrieval")
        written = (vault / "30_Knowledge" / "Notes" / "hybrid-retrieval.md").read_text()
        assert "type: knowledge" in written

    def test_a_description_and_tags_come_through(self, vault: Path) -> None:
        _run(
            vault,
            "note",
            "Hybrid Retrieval",
            "--description",
            "How lexical and vector retrieval combine.",
            "--tag",
            "search",
            "--domain",
            "software_development",
        )
        written = (vault / "30_Knowledge" / "Notes" / "hybrid-retrieval.md").read_text()
        assert "How lexical and vector retrieval combine." in written
        assert "search" in written
        assert "software_development" in written


class TestScopingItToWorkOnHand:
    """`--for` names a workspace by title, because that is how a person says it.

    An id is what the machine has; a title is what somebody typing at a prompt
    knows. Both are accepted, and an ambiguous title is refused rather than
    resolved by picking one -- two workspaces sharing a name would otherwise
    move the note between runs.
    """

    @pytest.fixture
    def workspace(self, vault: Path) -> str:
        assert _run(vault, "workspace", "create", "Education", "--type", "initiative") == 0
        return "Education"

    def test_it_lands_in_that_workspaces_research(self, vault: Path, workspace: str) -> None:
        assert _run(vault, "note", "Week 3 Reading", "--for", workspace) == 0
        assert (vault / "10_Workspaces" / "Education" / "Research" / "week-3-reading.md").is_file()

    def test_it_is_a_research_note_and_says_which_workspace(
        self, vault: Path, workspace: str
    ) -> None:
        _run(vault, "note", "Week 3 Reading", "--for", workspace)
        written = (
            vault / "10_Workspaces" / "Education" / "Research" / "week-3-reading.md"
        ).read_text()
        assert "type: research_note" in written
        assert "workspace:" in written

    def test_a_workspace_id_works_too(self, vault: Path, workspace: str) -> None:
        manifest = (vault / "10_Workspaces" / "Education" / "workspace.md").read_text()
        identity = next(
            line.split("id:", 1)[1].strip()
            for line in manifest.splitlines()
            if line.startswith("id:")
        )

        assert _run(vault, "note", "By Identity", "--for", identity) == 0
        assert (vault / "10_Workspaces" / "Education" / "Research" / "by-identity.md").is_file()

    def test_an_unknown_workspace_is_refused(self, vault: Path) -> None:
        assert _run(vault, "note", "Orphan", "--for", "No Such Workspace") != 0

    def test_and_says_so_rather_than_writing_it_somewhere(self, vault: Path) -> None:
        _run(vault, "note", "Orphan", "--for", "No Such Workspace")
        assert not list(vault.rglob("orphan.md"))
